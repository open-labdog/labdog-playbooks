# linux-upgrade

Standard package upgrade for Debian / Ubuntu hosts: `apt update && apt
upgrade`, then reboot if `/var/run/reboot-required` is set. Targets a
single host or a whole group.

LabDog operators surface this as the **Upgrade Linux packages** action
on the host and group views.

## What it does

1. `apt-get update` (with `--allow-releaseinfo-change` so codename
   bumps in third-party repos don't hard-fail).
2. `apt-get upgrade` of every installed package.
3. Reboots if `/var/run/reboot-required` exists and `auto_reboot` is
   true — unless one of the services in `reboot_skip_if_running` is
   active (`kubelet`, `pvedaemon`, `proxmox-backup` by default).
4. Optional `apt-get autoremove` + `apt-get clean` when `cleanup` is
   true.

The action is **destructive** (`destructive: true`) — when the host
has a Proxmox VM mapping LabDog snapshots before the run and rolls
back on verify failure.

## Verify

This action declares
[`verify_playbook: ../../verify/post-upgrade.yml`](../../verify/post-upgrade.yml)
with a 180-second timeout. After the upgrade finishes, LabDog runs
that playbook against the host and uses its exit status as the
success gate. The verify covers:

- SSH round-trip
- 1-min load below threshold
- `/` filesystem below fill threshold
- no failed systemd units
- (optional) explicit services / packages lists

See [`verify/README.md`](../../verify/README.md) for the full
contract.

## Manifest parameters

See [`manifest.yml`](./manifest.yml).

| Key | Type | Default | Notes |
|---|---|---|---|
| `auto_reboot` | bool | `true` | Reboot the host after upgrade if `/var/run/reboot-required` exists. |
| `reboot_timeout` | int | `300` | Maximum seconds to wait for the host to come back after reboot. |
| `cleanup` | bool | `true` | Run `apt-get autoremove` + `apt-get clean` after the upgrade. |

Tunables not exposed in the UI (edit
[`roles/role-apt-upgrade/defaults/main.yml`](../../roles/role-apt-upgrade/defaults/main.yml)):

- `min_free_disk_mb` (default 500): abort if `/` has less free.
- `reboot_skip_if_running`: services that, when active, suppress the
  auto-reboot. Defaults to `kubelet`, `pvedaemon`, `proxmox-backup`.
- `apt_update_extra_args`: extra flags for `apt-get update`.

## Targeting

- `supports_group: true`, `supports_host: true` — runs against either.
- `execution_mode: per_host` (default) — LabDog generates a
  single-host inventory per run and fans out across the group.

## Requirements

- **OS**: Debian / Ubuntu (`apt`-managed).
- **Privileges**: sudo/root on the target host.

## Role

This action uses the shared role
[`roles/role-apt-upgrade/`](../../roles/role-apt-upgrade/), which is
also reused by `linux-os-upgrade` (with `dist_upgrade: true`).
