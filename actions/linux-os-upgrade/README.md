# linux-os-upgrade

Major-release distribution upgrade for Debian / Ubuntu hosts: rewrites
the release codename in `/etc/apt/sources.list` and
`/etc/apt/sources.list.d/`, then runs `apt full-upgrade`. Targets a
single host or a whole group.

LabDog operators surface this as the **Upgrade OS release** action on
the host and group views.

This action is intentionally separate from
[`linux-upgrade`](../linux-upgrade/) because the codename swap and
`full-upgrade` semantics differ from a routine package refresh —
crossing release boundaries warrants a deliberate operator choice.

## What it does

1. **Validates** that both `current_version` and `next_version` are
   set (asserted in `pre_tasks`).
2. **Rewrites the codename** in `/etc/apt/sources.list` and every
   file under `/etc/apt/sources.list.d/` — `{current_version}` →
   `{next_version}`. Backups (`.YYYY-MM-DD@HH:MM:SS~`) are kept.
3. `apt-get update` (with `--allow-releaseinfo-change`); fails fast
   with a clear message if the new codename or repos are unreachable.
4. `apt-get full-upgrade` with `force-confdef,force-confold` and
   `DEBIAN_FRONTEND=noninteractive` so the run never blocks on
   conffile prompts.
5. Re-discovers `python3` after the upgrade (the interpreter path can
   move across releases) and switches Ansible to it for the remaining
   tasks.
6. Optional `apt-get autoremove` + `apt-get clean` when `cleanup` is
   true.
7. **Reboots unconditionally** — `auto_reboot` is forced to `true` in
   the playbook's `vars:` because a major release upgrade always
   warrants a reboot, regardless of `/var/run/reboot-required`.

The action is **destructive** (`destructive: true`) — when the host
has a Proxmox VM mapping LabDog snapshots before the run and rolls
back on verify failure.

## Manifest parameters

See [`manifest.yml`](./manifest.yml).

| Key | Type | Default | Notes |
|---|---|---|---|
| `current_version` | string | required | Current release codename, e.g. `bookworm` (Debian 12) or `jammy` (Ubuntu 22.04). |
| `next_version` | string | required | Target release codename, e.g. `trixie` (Debian 13) or `noble` (Ubuntu 24.04). |
| `cleanup` | bool | `true` | Run `apt-get autoremove` + `apt-get clean` after the upgrade. |

The shared role
[`role-apt-upgrade`](../../roles/role-apt-upgrade/) verifies that
`ansible_facts['distribution_release']` matches `current_version`
before rewriting sources, preventing accidental version skips
(e.g. `bullseye -> trixie`).

## Targeting

- `supports_group: true`, `supports_host: true` — runs against either.
- `execution_mode: per_host` (default) — LabDog generates a
  single-host inventory per run and fans out across the group.

## Verify

This action does not declare a `verify_playbook` in its manifest, so
LabDog falls back to the built-in Python verify (SSH, load, disk,
systemd). Operators wanting a stricter post-upgrade gate can point
the manifest at [`verify/post-upgrade.yml`](../../verify/post-upgrade.yml)
or supply their own.

## Requirements

- **OS**: Debian / Ubuntu (`apt`-managed).
- **Privileges**: sudo/root on the target host.
- **Reachable repos**: the target codename must be downloadable from
  every apt source on the host (mirrors, third-party repos, etc.).
  The post-rewrite `apt update` will fail loudly otherwise.

## Role

This action uses the shared role
[`roles/role-apt-upgrade/`](../../roles/role-apt-upgrade/) with
`dist_upgrade: true`, which is also reused by
[`linux-upgrade`](../linux-upgrade/) (with `dist_upgrade: false`).
