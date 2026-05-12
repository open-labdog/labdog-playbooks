# k8s-upgrade

Drains, `kubeadm`-upgrades, and re-admits each node in a Kubernetes
cluster, one node at a time. Control-plane nodes are upgraded first
(`kubeadm upgrade apply` on the first, `kubeadm upgrade node` on the
rest), then workers. Apt-only — Debian / Ubuntu hosts.

LabDog operators surface this as the **Upgrade Kubernetes cluster**
action on the group view. It is a **cluster-mode** action: LabDog
dispatches one Ansible run per group with a multi-host inventory
shaped under `all.children.{control_plane,workers}`. The operator
assigns the `control_plane` / `workers` role to each member from the
group's Members tab in the UI.

## What it does

1. **Validates prerequisites** on the first control-plane node:
   `target_version` looks like a semver, `kubectl` is on `PATH`, and
   every Ansible inventory hostname matches an existing Kubernetes
   node name.
2. **Upgrades control-plane nodes serially** (`serial: 1`) via the
   `kubernetes-upgrade` role with `node_role=control_plane`.
3. **Upgrades worker nodes serially** (`serial: 1`) via the same role
   with `node_role=worker`.

The playbook never upgrades two nodes concurrently — Kubernetes
requires this. `kubectl drain` / `uncordon` / wait-Ready tasks
delegate to the first control-plane host so a kubeconfig only needs
to live there, not on every worker.

## Manifest parameters

See [`manifest.yml`](./manifest.yml).

| Key | Type | Default | Notes |
|---|---|---|---|
| `target_version` | string | required | Semver only (e.g. `1.30.4`). Must already be downloadable from the apt repo configured on every node. |
| `skip_preflight` | bool | `false` | Bypass per-node sanity checks. Use sparingly. |
| `drain_timeout` | int | `300` | Maximum seconds to wait for `kubectl drain` to evict pods. |

## Targeting

- `supports_group: true`, `supports_host: false` — runs against a
  whole group, never a single host.
- `execution_mode: cluster` — single Ansible invocation per run, not
  the per-host fan-out used by the other actions in this pack.
- `destructive: true` — when the group's hosts have a Proxmox VM
  mapping, LabDog snapshots before the run and rolls back on verify
  failure.

## Requirements

- **OS**: Debian / Ubuntu on every node (`apt`-managed
  `kubeadm`/`kubelet`/`kubectl`).
- **Inventory**: every node's Ansible hostname must equal its
  Kubernetes node name. The preflight asserts this and fails fast on
  mismatch (DNS short vs FQDN, operator typos at bootstrap).
- **kubeconfig**: `/etc/kubernetes/admin.conf` on every control-plane
  node — the playbook sets `KUBECONFIG` to that path.
- **Privileges**: sudo/root on every node.

## Role

The single role that does the work lives at
[`roles/kubernetes-upgrade/`](./roles/kubernetes-upgrade/) — private
to this action. It's a useful template for adding RHEL-family support
(currently on the LabDog roadmap): copy it, swap `apt` for `dnf`, and
sibling it as `roles/kubernetes-upgrade-rhel/`.

## Customizing

- **Skip the preflight** for a single run: pass `skip_preflight=true`
  via the UI. For a permanent change, edit the `pre_tasks` in
  [`playbook.yml`](./playbook.yml).
- **Change drain behaviour**: edit the role's
  `defaults/main.yml`, or expose more knobs as manifest parameters.
- **Override the whole action**: copy `actions/k8s-upgrade/` into a
  higher-precedence pack and edit there.
