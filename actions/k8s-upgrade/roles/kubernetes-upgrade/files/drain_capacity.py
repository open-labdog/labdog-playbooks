#!/usr/bin/env python3
"""Would each node's pods fit on the rest of the cluster while it is drained?

Run by tasks/capacity.yml on the driver (first control-plane node), before
any node is cordoned. Read-only: it calls ``kubectl get`` twice and prints
one JSON document on stdout. Standard library only, Python 3.8 or newer, so
it runs on any node ansible-core can manage.

For each node the run will drain, in turn, with every other node as it is
now:

* the pods ``kubectl drain`` would evict: bound to the node, not finished,
  not owned by a DaemonSet, not a static (mirror) pod;
* each pod's effective resource requests, computed the way the scheduler
  does (containers, init containers, sidecars, pod overhead);
* the nodes it could land on: Ready, not cordoned, taints it tolerates,
  nodeSelector and required node affinity satisfied;
* a placement simulation: biggest pod first, each onto the candidate with
  the most room left, against what the node has allocatable minus what its
  pods already request, including the pod-count limit.

What it does not model, so a pass is not a guarantee: inter-pod affinity and
anti-affinity, topology spread constraints, PodDisruptionBudgets, volume
topology (a PV pinned to one zone or node), and pods without requests, which
always fit on paper. It does not refuse because of those.

Usage: drain_capacity.py NODE [NODE ...]
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from fractions import Fraction

_DECIMAL_SUFFIXES = {
    "n": Fraction(1, 10**9),
    "u": Fraction(1, 10**6),
    "m": Fraction(1, 10**3),
    "": Fraction(1),
    "k": Fraction(10**3),
    "M": Fraction(10**6),
    "G": Fraction(10**9),
    "T": Fraction(10**12),
    "P": Fraction(10**15),
    "E": Fraction(10**18),
}
_BINARY_SUFFIXES = {
    "Ki": Fraction(2**10),
    "Mi": Fraction(2**20),
    "Gi": Fraction(2**30),
    "Ti": Fraction(2**40),
    "Pi": Fraction(2**50),
    "Ei": Fraction(2**60),
}

# A trailing "E" alone is exa; "E" or "e" followed by digits is an exponent.
_QUANTITY = re.compile(
    r"([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))"
    r"(?:[eE]([+-]?[0-9]+)|(Ki|Mi|Gi|Ti|Pi|Ei|[numkMGTPE]?))"
)

# Resources a pod always consumes one of, whatever it requests.
_PODS = "pods"


def parse_quantity(text) -> Fraction:
    """A Kubernetes resource.Quantity ("250m", "1.5Gi", "1e3") as a number."""
    match = _QUANTITY.fullmatch(str(text).strip())
    if not match:
        raise ValueError(f"not a Kubernetes quantity: {text!r}")
    number, exponent, suffix = match.groups()
    value = Fraction(number)
    if exponent is not None:
        return value * Fraction(10) ** int(exponent)
    if suffix in _BINARY_SUFFIXES:
        return value * _BINARY_SUFFIXES[suffix]
    return value * _DECIMAL_SUFFIXES[suffix]


def _requests(container: dict) -> dict:
    raw = (container.get("resources") or {}).get("requests") or {}
    return {name: parse_quantity(value) for name, value in raw.items()}


def _add(a: dict, b: dict) -> dict:
    out = dict(a)
    for name, value in b.items():
        out[name] = out.get(name, Fraction(0)) + value
    return out


def _max(a: dict, b: dict) -> dict:
    out = dict(a)
    for name, value in b.items():
        out[name] = max(out.get(name, Fraction(0)), value)
    return out


def pod_requests(pod: dict) -> dict:
    """Effective requests, as the scheduler computes them.

    max(all containers + all sidecars, the largest init container plus the
    sidecars started before it), plus the RuntimeClass overhead.
    """
    spec = pod.get("spec") or {}
    regular: dict = {}
    for container in spec.get("containers") or []:
        regular = _add(regular, _requests(container))
    sidecars: dict = {}
    init_peak: dict = {}
    for container in spec.get("initContainers") or []:
        if container.get("restartPolicy") == "Always":
            sidecars = _add(sidecars, _requests(container))
            init_peak = _max(init_peak, sidecars)
        else:
            init_peak = _max(init_peak, _add(sidecars, _requests(container)))
    total = _max(_add(regular, sidecars), init_peak)
    overhead = {name: parse_quantity(v) for name, v in (spec.get("overhead") or {}).items()}
    total = _add(total, overhead)
    total[_PODS] = Fraction(1)
    return total


def _finished(pod: dict) -> bool:
    return (pod.get("status") or {}).get("phase") in ("Succeeded", "Failed")


def evicted_by_drain(pod: dict) -> bool:
    """Would `kubectl drain --ignore-daemonsets` evict this pod?"""
    if _finished(pod):
        return False
    meta = pod.get("metadata") or {}
    if "kubernetes.io/config.mirror" in (meta.get("annotations") or {}):
        return False
    for owner in meta.get("ownerReferences") or []:
        if owner.get("kind") == "DaemonSet" and owner.get("controller"):
            return False
    return True


def _ready(node: dict) -> bool:
    for cond in (node.get("status") or {}).get("conditions") or []:
        if cond.get("type") == "Ready":
            return cond.get("status") == "True"
    return False


def _tolerates(tolerations: list, taint: dict) -> bool:
    for tol in tolerations:
        effect = tol.get("effect") or ""
        if effect and effect != taint.get("effect"):
            continue
        key = tol.get("key") or ""
        operator = tol.get("operator") or "Equal"
        if not key:
            if operator == "Exists":
                return True
            continue
        if key != taint.get("key"):
            continue
        if operator == "Exists" or (tol.get("value") or "") == (taint.get("value") or ""):
            return True
    return False


def _expression_matches(expr: dict, values_of) -> bool:
    key, operator = expr.get("key"), expr.get("operator")
    values = expr.get("values") or []
    present, value = values_of(key)
    if operator == "In":
        return present and value in values
    if operator == "NotIn":
        return not present or value not in values
    if operator == "Exists":
        return present
    if operator == "DoesNotExist":
        return not present
    if operator in ("Gt", "Lt") and present and values:
        try:
            have, want = int(value), int(values[0])
        except ValueError:
            return False
        return have > want if operator == "Gt" else have < want
    return False


def _term_matches(term: dict, node: dict) -> bool:
    labels = (node.get("metadata") or {}).get("labels") or {}
    name = (node.get("metadata") or {}).get("name")

    def label(key):
        return (key in labels, labels.get(key))

    def field(key):
        return (key == "metadata.name", name)

    exprs = term.get("matchExpressions") or []
    fields = term.get("matchFields") or []
    if not exprs and not fields:
        return False  # an empty term matches nothing
    return all(_expression_matches(e, label) for e in exprs) and all(
        _expression_matches(f, field) for f in fields
    )


def pod_may_run_on(pod: dict, node: dict) -> bool:
    """Node-level constraints only: taints, nodeSelector, required node affinity."""
    spec = pod.get("spec") or {}
    tolerations = spec.get("tolerations") or []
    for taint in (node.get("spec") or {}).get("taints") or []:
        if taint.get("effect") in ("NoSchedule", "NoExecute") and not _tolerates(tolerations, taint):
            return False
    labels = (node.get("metadata") or {}).get("labels") or {}
    for key, value in (spec.get("nodeSelector") or {}).items():
        if labels.get(key) != value:
            return False
    required = (
        ((spec.get("affinity") or {}).get("nodeAffinity") or {}).get(
            "requiredDuringSchedulingIgnoredDuringExecution"
        )
        or {}
    )
    terms = required.get("nodeSelectorTerms")
    if terms is not None and not any(_term_matches(t, node) for t in terms):
        return False
    return True


def _allocatable(node: dict) -> dict:
    raw = (node.get("status") or {}).get("allocatable") or {}
    return {name: parse_quantity(value) for name, value in raw.items()}


def _room(free: dict, need: dict) -> bool:
    return all(free.get(name, Fraction(0)) >= value for name, value in need.items())


def _headroom(free: dict, alloc: dict) -> Fraction:
    """How empty a node is, for picking where a pod goes: like the
    scheduler's LeastAllocated score, the mean free share of cpu and memory."""
    shares = []
    for name in ("cpu", "memory"):
        total = alloc.get(name, Fraction(0))
        if total > 0:
            shares.append(free.get(name, Fraction(0)) / total)
    return sum(shares, Fraction(0)) / len(shares) if shares else Fraction(0)


def _fmt(name: str, value: Fraction) -> str:
    if name == "cpu":
        milli = value * 1000
        return f"{float(value):g}" if milli % 1000 == 0 else f"{round(float(milli))}m"
    if name == "memory" or name == "ephemeral-storage" or name.startswith("hugepages-"):
        for suffix, factor in (("Gi", 2**30), ("Mi", 2**20), ("Ki", 2**10)):
            if value >= factor:
                return f"{float(value / factor):.3g}{suffix}"
        return f"{float(value):g}"
    return f"{float(value):g}"


def describe(req: dict) -> str:
    parts = [f"{name} {_fmt(name, value)}" for name, value in sorted(req.items()) if name != _PODS]
    return ", ".join(parts) or "no requests"


def check(nodes_json: dict, pods_json: dict, drain: list) -> dict:
    nodes = {n["metadata"]["name"]: n for n in nodes_json.get("items") or []}
    pods = [p for p in pods_json.get("items") or [] if not _finished(p)]
    used: dict = {name: {} for name in nodes}
    for pod in pods:
        bound = (pod.get("spec") or {}).get("nodeName")
        if bound in used:
            used[bound] = _add(used[bound], pod_requests(pod))

    results = []
    for target in drain:
        if target not in nodes:
            results.append({"node": target, "fits": True, "moving": 0, "unplaced": [],
                            "no_requests": 0, "note": "not a cluster node; skipped"})
            continue
        moving = [
            p for p in pods
            if (p.get("spec") or {}).get("nodeName") == target and evicted_by_drain(p)
        ]
        free = {}
        for name, node in nodes.items():
            if name == target or (node.get("spec") or {}).get("unschedulable") or not _ready(node):
                continue
            alloc = _allocatable(node)
            free[name] = {k: alloc.get(k, Fraction(0)) - used[name].get(k, Fraction(0))
                          for k in set(alloc) | set(used[name])}

        def size(pod):
            r = pod_requests(pod)
            return (r.get("cpu", Fraction(0)), r.get("memory", Fraction(0)))

        unplaced = []
        no_requests = 0
        for pod in sorted(moving, key=size, reverse=True):
            need = pod_requests(pod)
            if set(need) == {_PODS}:
                no_requests += 1
            fits_on = [
                name for name in free
                if pod_may_run_on(pod, nodes[name]) and _room(free[name], need)
            ]
            meta = pod.get("metadata") or {}
            label = f"{meta.get('namespace', 'default')}/{meta.get('name', '?')}"
            if not fits_on:
                allowed = any(pod_may_run_on(pod, nodes[name]) for name in free)
                unplaced.append({
                    "pod": label,
                    "requests": describe(need),
                    "reason": "no node with enough room" if allowed
                    else "no other node it is allowed on (taints, nodeSelector or node affinity)",
                })
                continue
            best = max(fits_on, key=lambda n: (_headroom(free[n], _allocatable(nodes[n])), n))
            for k, v in need.items():
                free[best][k] = free[best].get(k, Fraction(0)) - v

        results.append({
            "node": target,
            "fits": not unplaced,
            "moving": len(moving),
            "unplaced": unplaced,
            "no_requests": no_requests,
        })

    lines = []
    for r in results:
        if r.get("note"):
            lines.append(f"{r['node']}: {r['note']}.")
        elif r["fits"]:
            extra = (f" ({r['no_requests']} of them set no requests and are not counted)"
                     if r["no_requests"] else "")
            lines.append(f"{r['node']}: {r['moving']} pod(s) to move, they fit on the other nodes{extra}.")
        else:
            pods_txt = "; ".join(f"{u['pod']} ({u['requests']}): {u['reason']}" for u in r["unplaced"])
            lines.append(
                f"{r['node']}: {len(r['unplaced'])} of {r['moving']} pod(s) would not fit "
                f"while it is drained: {pods_txt}."
            )
    return {
        "fits": all(r["fits"] for r in results),
        "short": [r["node"] for r in results if not r["fits"]],
        "nodes": results,
        "lines": lines,
    }


def _kubectl_json(*args: str) -> dict:
    out = subprocess.run(["kubectl", *args, "-o", "json"], check=True,
                         stdout=subprocess.PIPE, universal_newlines=True).stdout
    return json.loads(out)


def main(argv: list) -> int:
    if not argv:
        print("usage: drain_capacity.py NODE [NODE ...]", file=sys.stderr)
        return 2
    nodes_json = _kubectl_json("get", "nodes")
    pods_json = _kubectl_json("get", "pods", "--all-namespaces")
    json.dump(check(nodes_json, pods_json, argv), sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
