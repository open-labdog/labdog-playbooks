# k8s-upgrade

Drains, `kubeadm`-upgrades, and re-admits each node in a Kubernetes
cluster, one node at a time. Control-plane nodes are upgraded first
(`kubeadm upgrade apply` on the first, `kubeadm upgrade node` on the
rest), then workers. Apt-only — Debian / Ubuntu hosts.

LabDog operators surface this as the **Upgrade Kubernetes cluster**
action on the group view. The action's manifest declares
`supports_host: false`, which tells LabDog to dispatch the playbook
once against the whole group (a flat `all` Ansible inventory) instead
of fanning out per-host. The playbook itself decides which nodes are
control-plane and which are workers — there are no roles to assign in
the LabDog UI.

## What it does

1. **Discovers cluster topology**: probes every node for
   `/etc/kubernetes/manifests/kube-apiserver.yaml` and sorts it into
   `k8s_control_plane` or `k8s_worker` with `group_by`. Refuses if no
   control-plane node is detected. The alphabetically first
   control-plane node is the *driver*: every `kubectl` call and
   `kubeadm upgrade apply` run there, and it is upgraded first.
2. **Validates the cluster** on the driver, before any node is touched:
   - `target_version` is a semver, at most one minor above the running
     API server, and not a downgrade;
   - every inventory hostname is a Kubernetes node name, and every
     Kubernetes node is in the run (unless `allow_partial_group`);
   - no kubelet is newer than the target, or more than three minors
     behind it;
   - every node the run will drain has room for its pods on the rest of
     the cluster (see [Drain capacity](#drain-capacity));
   - prints the plan, and a warning for HA clusters with stacked etcd
     (see [Rollback](#rollback)).
3. **Upgrades control-plane nodes serially**, then checks that every
   control-plane kubelet is on target, joins the
   [surge node](#surge-node) if one is set, then **upgrades workers
   serially**, then removes the surge node. Each node: preflight
   (kubelet running), move the kube apt source to the target minor (see
   [Apt repository](#apt-repository)), refresh the apt cache, check that
   `kubeadm`, `kubelet` and `kubectl` are downloadable at the target,
   cordon and drain, install `kubeadm`, `kubeadm upgrade`, install
   `kubelet` and `kubectl`, restart kubelet, wait for Ready, uncordon.
   The packages are held again afterwards.
4. **Snapshots etcd** on the first control-plane node right before
   `kubeadm upgrade apply`, when etcd is stacked (a kubeadm static
   pod). The snapshot is written with the etcd image's own `etcdctl`
   through `crictl`, to `etcd_backup_dir`. With external etcd the
   action says so and takes no snapshot.

Any failed node stops the whole run (`serial: 1` plus
`any_errors_fatal`), so no later node is touched.

## Apt repository

pkgs.k8s.io has one apt repository per minor version
(`https://pkgs.k8s.io/core:/stable:/v1.30/deb/`), so a minor upgrade
needs each node's source moved first. The action does that, per node,
right before that node is upgraded (and on the surge node before it
joins), in `tasks/apt-prepare.yml`:

- It looks for active lines naming `pkgs.k8s.io/core:/stable:/vX.Y/deb`
  in `/etc/apt/sources.list` and `/etc/apt/sources.list.d/*.list`,
  `*.sources` (one-line and deb822). The path is matched anywhere in the
  URL, so a mirror that keeps it (an apt-cacher-ng remap, say) keeps
  being used. Commented lines, and files apt ignores such as
  `kubernetes.list.backup.1770197682`, are left alone.
- In preflight it reads the target repository through a scratch apt
  setup with a freshly downloaded `Release.key`, so the node's own apt
  configuration and cache are untouched. This also runs in preview
  mode, which is how `report_only` can confirm a new minor's packages
  exist.
- Then it installs the target repository's `Release.key` at the keyring
  the source names (`signed-by=` or `Signed-By:`; binary for `.gpg`,
  armored for `.asc`) and rewrites the minor in those files. Keyrings
  of other sources in the same file are never touched.

It never adds a repository. A node with no pkgs.k8s.io source can still
take a patch upgrade from whatever source it has, but a minor upgrade
stops with an error before anything changes. The kube packages stay held
throughout, so a node whose source moved but whose upgrade then failed
keeps running its old version; a re-run carries on.

## Manifest parameters

See [`manifest.yml`](./manifest.yml).

| Key | Type | Default | Notes |
|---|---|---|---|
| `target_version` | string | required | Semver only (e.g. `1.30.4`). At most one minor above the running control plane. |
| `report_only` | bool | `false` | Run every check and print what each node would do, without changing anything. A real `--check` run (LabDog's dry run, once it passes `--check`) does the same. Older LabDog dry runs set `ansible_check_mode` as an extra-var, which ansible-core ignores, so they upgrade for real: use `report_only` there. |
| `skip_preflight` | bool | `false` | Bypass the per-node checks (kubelet running, target repository readable, packages downloadable). The apt source is still moved and the cache still refreshed. |
| `drain_timeout` | int | `300` | Seconds `kubectl drain` may take. |
| `drain_force` | bool | `false` | Pass `--force` to `kubectl drain`, deleting pods no controller manages. |
| `etcd_backup_dir` | string | `/var/lib/labdog/etcd-backups` | Absolute path on the first control-plane node for the etcd snapshot. |
| `allow_partial_group` | bool | `false` | Proceed when the cluster has nodes outside the targeted group. They stay on their current version. |
| `skip_capacity_check` | bool | `false` | Run even when the drain capacity check finds a node whose pods would not fit elsewhere. The check still runs and reports. |
| `surge_node` | string | `""` | A spare host in the group that joins as a temporary worker while the workers are drained. See [Surge node](#surge-node). |
| `kubeadm_ignore_preflight_errors` | string | `""` | kubeadm preflight checks to skip, comma-separated, passed as `--ignore-preflight-errors` to `kubeadm upgrade plan`, `apply` and `node`. See [CoreDNS](#coredns). |

The manifest sets `playbook_timeout_seconds: 7200`, which covers about
six nodes at the 20-minute worst case. For a bigger cluster, raise it in
a copy of the action or raise LabDog's global `ansible.playbook_timeout`.

## Drain capacity

Draining a node only works if its pods fit on the other nodes. When they
don't, the evicted pods stay Pending and `kubectl drain` waits out
`drain_timeout` with the node already cordoned. So before any node is
touched, the driver runs
[`files/drain_capacity.py`](./roles/kubernetes-upgrade/files/drain_capacity.py)
(Python standard library, no extra packages). For each node the run
will drain, with every other node as it is now, it:

- takes the pods `kubectl drain --ignore-daemonsets` would evict (not
  DaemonSet pods, static pods, or finished pods);
- works out each pod's requests the way the scheduler does (containers,
  init containers, sidecars, pod overhead), for every resource,
  including the pod count and extended resources like GPUs;
- finds where each pod may go: Ready, uncordoned nodes whose taints it
  tolerates and that match its `nodeSelector` and required node
  affinity;
- places the pods biggest first, each on the node with the most room
  left, against allocatable minus what that node's pods already request.

It prints one line per node, and refuses the run when any pod has
nowhere to go, naming the pods. A preview run prints the same lines.

It does not model pod anti-affinity, topology spread constraints,
PodDisruptionBudgets or volumes pinned to a zone or node, and a pod
without requests always fits on paper (the report counts them). So a
pass is not a guarantee. If the check refuses a cluster you know is
fine, turn on `skip_capacity_check`.

With a [surge node](#surge-node), the worker drains count it as a
destination: as it is if it is already in the cluster, or estimated
from its hardware (every CPU, memory minus the kubelet's default 100Mi
eviction threshold, 110 pods, its hostname/os/arch labels and no
taints) if it has not joined yet. Control-plane nodes are drained
before it joins, so their drains don't count it.

## Surge node

A cluster with no spare room can't drain a node. `surge_node` names a
spare host that joins as a temporary worker for the run, so the
workers' pods have somewhere to go:

1. After the last control-plane node is on target, and before any
   worker is drained, the action installs `kubeadm`, `kubelet` and
   `kubectl` at the target version on the surge host (and holds them),
   creates a 15-minute join token on the first control-plane node, runs
   `kubeadm join --node-name <inventory hostname>`, deletes the token,
   annotates the node `labdog.io/k8s-upgrade-surge=true`, and waits for
   it to be Ready. It joins after the control plane, never before,
   because a kubelet must not be newer than its API server.
2. The workers are upgraded as usual. Their pods can land on the surge
   node.
3. After the last worker, the action drains the surge node (its pods go
   back to the upgraded workers), runs `kubeadm reset`, and deletes its
   node object. The kube packages stay installed and held, so the next
   run only moves them to its target.

What the surge host needs:

- to be in the targeted LabDog group, and reachable like any member;
- to be ready to be a node, which the action does not set up: a
  container runtime configured for Kubernetes, swap off, the usual
  kernel modules and sysctls, and a pkgs.k8s.io apt source (any minor:
  the action moves it to the target, see [Apt repository](#apt-repository));
- Debian or Ubuntu;
- enough allocatable room for what the drains need to move. The
  capacity check tells you before anything is touched.

A VM template you clone once and keep powered on, or power on before a
run, is the easy way to have one.

The validation refuses a surge node that is a control-plane node, that
is already a node of the cluster without the annotation (so a typo can
never reset one of the cluster's own nodes), or that has a
`/etc/kubernetes/kubelet.conf` without being in the cluster. When no
worker needs draining, the surge node is not joined at all.

**When something fails.** A failed join resets the surge host and
removes its node object, and the run stops with the control plane
already upgraded and no worker touched; re-run with the same
`target_version`. When a worker fails, the run stops before the
removal, so the surge node stays in the cluster carrying the pods it
took. A failed drain of the surge node uncordons it again. Either way,
a re-run with the same `target_version` reuses the surge node (it
carries the annotation and the target kubelet) and removes it at the
end. A surge node left behind on an older version is refused; finish
that run first, or remove it by hand with `kubectl drain`,
`kubeadm reset` and `kubectl delete node`.

`verify.yml` skips the surge node: its kubelet is stopped once it is
reset, and a VM rollback of it would only bring back an idle host.

## CoreDNS

Most minor upgrades move CoreDNS to a new version, and kubeadm migrates
the Corefile (configmap `kube-system/coredns`) on the way. Its migration
tool knows a fixed set of plugins (the defaults kubeadm writes, plus
`log`, `autopath`, `k8s_external`, `hosts`, `rewrite`, `transfer`). A
Corefile with any other plugin, such as
`template IN AAAA . { rcode NOERROR }` on an IPv4-only cluster, fails
kubeadm's `CoreDNSUnsupportedPlugins` preflight check.

kubeadm only runs that check on the first control-plane node, after it
is cordoned and has the new kubeadm, and a preview never installs that
kubeadm. So the validation step reads the Corefile itself: it reports
plugins outside that set, and refuses a minor upgrade before any node is
touched. When the plugin works on the new CoreDNS, set
`kubeadm_ignore_preflight_errors` to `CoreDNSUnsupportedPlugins`.
kubeadm then migrates the rest of the Corefile and leaves those blocks
as they are.

## Failures and re-runs

The node being upgraded carries the annotation
`labdog.io/k8s-upgrade-cordoned=true` from just before the action
cordons it until just after it uncordons it. A node the operator had
already cordoned never gets the annotation, so the action leaves it
cordoned when it is done.

When a node fails:

- **before anything changed on it** (drain timed out on a
  PodDisruptionBudget, say) the action uncordons it again;
- **after kubeadm or the packages started to change** it stays cordoned
  and annotated.

Either way the run stops and says which case it was. Re-run with the
same `target_version`: nodes already on the target kubelet are skipped,
and a node on target that still carries the annotation is waited on
until Ready and uncordoned. The version check is exact, so `v1.30.4`
never matches `v1.30.40`.

## Rollback

`destructive: true`, so LabDog snapshots every member that has a
Proxmox VM mapping before the run, runs [`verify.yml`](./verify.yml)
per member afterwards, and restores a member whose playbook or verify
failed. `verify.yml` only checks nodes this run upgraded (kubelet on
target → active and Ready); a node the run never reached passes, so an
aborted run does not restore untouched nodes.

Restoring a **worker** is safe: the node comes back on the old version
and rejoins, and a re-run upgrades it again. Restoring the only
control-plane node of a **single-control-plane** cluster restores etcd
as a whole, which is consistent.

Restoring one control-plane node of an **HA cluster with stacked
etcd** is not safe. It brings back an etcd member with an older raft
log under the same member ID, which etcd refuses, and that needs a
manual member remove and re-add. For those clusters, run the action
with LabDog snapshots turned off and use the etcd snapshot in
`etcd_backup_dir` as the recovery path. The validation step prints a
warning when it sees such a cluster. The snapshot lives on the first
control-plane node, so a VM rollback of that node erases it too: point
`etcd_backup_dir` at storage that lives elsewhere if you can.

LabDog deletes the snapshots of members that succeeded once the run is
done, so reverting the whole cluster afterwards is a manual Proxmox job.

## Requirements

- **OS**: Debian / Ubuntu on every node (`apt`-managed
  `kubeadm`/`kubelet`/`kubectl` from a pkgs.k8s.io repository, which the
  action moves to the target minor).
- **gpg** on every node, to unpack the repository key (installing
  Kubernetes from pkgs.k8s.io needs it too).
- **Inventory**: every node's Ansible hostname must equal its
  Kubernetes node name. The validation step fails fast on mismatch.
- **kubeconfig**: `/etc/kubernetes/admin.conf` on the first
  control-plane node.
- **crictl** on the first control-plane node for the etcd snapshot
  (kubeadm installs it).
- **Privileges**: sudo/root on every node.

## Role

The role that does the work lives at
[`roles/kubernetes-upgrade/`](./roles/kubernetes-upgrade/), private
to this action. `tasks/validate.yml` holds the cluster-wide checks,
`tasks/main.yml` the per-node flow. Copy it, swap `apt` for `dnf`, and
sibling it as `roles/kubernetes-upgrade-rhel/` to add RHEL-family
support.

## Customizing

- **Skip the per-node checks** for a single run: `skip_preflight=true`.
- **Change drain behaviour**: `drain_timeout` and `drain_force`, or the
  role's `tasks/drain.yml`.
- **Override the whole action**: copy `actions/k8s-upgrade/` into
  another pack and pin that pack as the winner for `k8s-upgrade`.
