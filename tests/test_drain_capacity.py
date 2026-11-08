"""Unit tests for actions/k8s-upgrade/roles/kubernetes-upgrade/files/drain_capacity.py.

Run from the repo root: python -m unittest discover -s tests
"""

import importlib.util
import pathlib
import unittest
from fractions import Fraction

_PATH = (pathlib.Path(__file__).resolve().parents[1]
         / "actions/k8s-upgrade/roles/kubernetes-upgrade/files/drain_capacity.py")
_spec = importlib.util.spec_from_file_location("drain_capacity", _PATH)
dc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dc)


def node(name, cpu="4", memory="8Gi", pods="110", ready=True, unschedulable=False,
         taints=None, labels=None):
    return {
        "metadata": {"name": name, "labels": labels or {}},
        "spec": {"unschedulable": unschedulable, "taints": taints or []},
        "status": {
            "allocatable": {"cpu": cpu, "memory": memory, "pods": pods},
            "conditions": [{"type": "Ready", "status": "True" if ready else "False"}],
        },
    }


def pod(name, on, cpu=None, memory=None, ns="default", owner="ReplicaSet", phase="Running",
        **spec):
    req = {}
    if cpu:
        req["cpu"] = cpu
    if memory:
        req["memory"] = memory
    meta = {"name": name, "namespace": ns}
    if owner:
        meta["ownerReferences"] = [{"kind": owner, "controller": True}]
    body = {"nodeName": on, "containers": [{"resources": {"requests": req}}]}
    body.update(spec)
    return {"metadata": meta, "spec": body, "status": {"phase": phase}}


def run(nodes, pods, drain):
    return dc.check({"items": nodes}, {"items": pods}, drain)


class Quantity(unittest.TestCase):
    def test_forms(self):
        cases = {"250m": Fraction(1, 4), "2": 2, "1.5Gi": 3 * 2**29, "1e3": 1000,
                 "129M": 129 * 10**6, "123Mi": 123 * 2**20, ".5": Fraction(1, 2),
                 "2E": 2 * 10**18, "100n": Fraction(1, 10**7)}
        for text, want in cases.items():
            self.assertEqual(dc.parse_quantity(text), want, text)

    def test_garbage(self):
        with self.assertRaises(ValueError):
            dc.parse_quantity("lots")


class PodRequests(unittest.TestCase):
    def test_init_and_sidecars(self):
        p = {"spec": {
            "containers": [{"resources": {"requests": {"cpu": "100m"}}},
                           {"resources": {"requests": {"cpu": "100m"}}}],
            "initContainers": [
                {"restartPolicy": "Always", "resources": {"requests": {"cpu": "50m"}}},
                {"resources": {"requests": {"cpu": "1"}}},
            ],
            "overhead": {"cpu": "10m"},
        }}
        # init peak: sidecar 50m + init 1 = 1.05 > containers 200m + sidecar 50m.
        self.assertEqual(dc.pod_requests(p)["cpu"], Fraction(106, 100))


class Check(unittest.TestCase):
    def test_fits(self):
        r = run([node("a"), node("b")], [pod("w", "a", cpu="2", memory="2Gi")], ["a"])
        self.assertTrue(r["fits"])
        self.assertEqual(r["nodes"][0]["moving"], 1)

    def test_no_room(self):
        nodes = [node("a"), node("b")]
        pods = [pod("w", "a", cpu="3"), pod("busy", "b", cpu="2")]
        r = run(nodes, pods, ["a"])
        self.assertFalse(r["fits"])
        self.assertEqual(r["short"], ["a"])
        self.assertIn("default/w", r["lines"][0])
        self.assertIn("no node with enough room", r["lines"][0])

    def test_bin_packing_not_just_sum(self):
        # 2 cpu free in total, but 1 per node: a 2-cpu pod fits nowhere.
        nodes = [node("a"), node("b", cpu="2"), node("c", cpu="2")]
        pods = [pod("big", "a", cpu="2"), pod("x", "b", cpu="1"), pod("y", "c", cpu="1")]
        self.assertFalse(run(nodes, pods, ["a"])["fits"])

    def test_placement_is_cumulative(self):
        nodes = [node("a"), node("b", cpu="2")]
        pods = [pod("p1", "a", cpu="1500m"), pod("p2", "a", cpu="1500m")]
        r = run(nodes, pods, ["a"])
        self.assertFalse(r["fits"])
        self.assertEqual(len(r["nodes"][0]["unplaced"]), 1)

    def test_daemonsets_mirror_and_finished_pods_stay(self):
        mirror = pod("kube-apiserver", "a", cpu="3", owner=None)
        mirror["metadata"]["annotations"] = {"kubernetes.io/config.mirror": "x"}
        pods = [pod("ds", "a", cpu="3", owner="DaemonSet"), mirror,
                pod("done", "a", cpu="3", phase="Succeeded")]
        r = run([node("a"), node("b", cpu="1")], pods, ["a"])
        self.assertTrue(r["fits"])
        self.assertEqual(r["nodes"][0]["moving"], 0)

    def test_finished_pods_free_their_room(self):
        pods = [pod("w", "a", cpu="3"), pod("old", "b", cpu="4", phase="Failed")]
        self.assertTrue(run([node("a"), node("b")], pods, ["a"])["fits"])

    def test_cordoned_and_notready_nodes_are_no_destination(self):
        nodes = [node("a"), node("b", unschedulable=True), node("c", ready=False)]
        self.assertFalse(run(nodes, [pod("w", "a", cpu="1")], ["a"])["fits"])

    def test_taints_and_tolerations(self):
        taint = {"key": "node-role.kubernetes.io/control-plane", "effect": "NoSchedule"}
        nodes = [node("a"), node("cp", taints=[taint])]
        self.assertFalse(run(nodes, [pod("w", "a", cpu="1")], ["a"])["fits"])
        tolerant = pod("w", "a", cpu="1", tolerations=[
            {"key": "node-role.kubernetes.io/control-plane", "operator": "Exists",
             "effect": "NoSchedule"}])
        r = run(nodes, [tolerant], ["a"])
        self.assertTrue(r["fits"])
        everything = pod("w", "a", cpu="1", tolerations=[{"operator": "Exists"}])
        self.assertTrue(run(nodes, [everything], ["a"])["fits"])
        pref = {"key": "x", "effect": "PreferNoSchedule"}
        self.assertTrue(run([node("a"), node("b", taints=[pref])],
                            [pod("w", "a", cpu="1")], ["a"])["fits"])

    def test_disallowed_reason(self):
        nodes = [node("a"), node("b")]
        p = pod("w", "a", cpu="1", nodeSelector={"disk": "ssd"})
        r = run(nodes, [p], ["a"])
        self.assertFalse(r["fits"])
        self.assertIn("no other node it is allowed on", r["lines"][0])

    def test_node_selector_and_affinity(self):
        nodes = [node("a"), node("b", labels={"disk": "ssd", "gen": "5"})]
        self.assertTrue(run(nodes, [pod("w", "a", nodeSelector={"disk": "ssd"})], ["a"])["fits"])
        aff = {"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {
            "nodeSelectorTerms": [
                {"matchExpressions": [{"key": "gen", "operator": "Gt", "values": ["6"]}]},
                {"matchFields": [{"key": "metadata.name", "operator": "In", "values": ["b"]}]},
            ]}}}
        self.assertTrue(run(nodes, [pod("w", "a", affinity=aff)], ["a"])["fits"])
        aff_no = {"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {
            "nodeSelectorTerms": [
                {"matchExpressions": [{"key": "disk", "operator": "NotIn", "values": ["ssd"]}]},
            ]}}}
        self.assertFalse(run(nodes, [pod("w", "a", affinity=aff_no)], ["a"])["fits"])

    def test_pod_count_limit(self):
        nodes = [node("a"), node("b", pods="1")]
        pods = [pod("w", "a"), pod("there", "b")]
        self.assertFalse(run(nodes, pods, ["a"])["fits"])

    def test_extended_resource(self):
        p = pod("gpu", "a")
        p["spec"]["containers"][0]["resources"]["requests"]["nvidia.com/gpu"] = "1"
        self.assertFalse(run([node("a"), node("b")], [p], ["a"])["fits"])

    def test_no_requests_reported(self):
        r = run([node("a"), node("b")], [pod("w", "a")], ["a"])
        self.assertTrue(r["fits"])
        self.assertEqual(r["nodes"][0]["no_requests"], 1)
        self.assertIn("set no requests", r["lines"][0])

    def test_each_drain_is_independent(self):
        nodes = [node("a", cpu="2"), node("b", cpu="2")]
        pods = [pod("pa", "a", cpu="1"), pod("pb", "b", cpu="1")]
        r = run(nodes, pods, ["a", "b"])
        self.assertTrue(r["fits"])
        self.assertEqual(len(r["lines"]), 2)

    def test_unknown_node_skipped(self):
        r = run([node("a")], [], ["ghost"])
        self.assertTrue(r["fits"])
        self.assertIn("skipped", r["lines"][0])


if __name__ == "__main__":
    unittest.main()
