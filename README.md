# labdog-playbooks

Default action pack for [LabDog](https://github.com/open-labdog/labdog).

LabDog admins add this repo as an action pack through the UI. The
recommended path is **Integrations → Git Repos → Add Repository** —
the onboarding wizard clones the repo, detects every pack, and
prompts for per-key winner decisions if any keys overlap with existing
packs. For a quick one-off, **Integrations → Action Packs → Add Pack**
works too. Packs are unordered; action-key collisions are resolved per
key via the Action Registry on the **Action Packs** page.

For the full user-facing guide — how packs load, how key collisions
resolve, how to write your own — see
[`docs/ui/actions.md`](https://github.com/open-labdog/labdog/blob/main/docs/ui/actions.md)
in the main labdog repo. This README covers the pack-author angle only.

## Pack layout

```
.
├── pack.yml                       metadata (name, description — not load-critical)
├── actions/
│   └── <key>/                     one directory per action
│       ├── manifest.yml           LabDog action definition
│       ├── playbook.yml           Ansible playbook
│       └── roles/                 (optional) action-private roles
│           └── <role-name>/       auto-resolved by Ansible
├── verify/                        reusable verify playbooks (see verify/README.md)
│   ├── post-upgrade.yml
│   ├── host-reachable.yml
│   └── services-active.yml
└── roles/                         shared roles, reusable across actions
    └── <role-name>/
```

An **action is a directory**: one folder under `actions/` per action,
containing a `manifest.yml` and a `playbook.yml` at minimum. To override
a single action in a downstream pack, copy that one directory.

LabDog discovers actions by globbing `actions/*/manifest.yml`. Every
manifest is validated against a pydantic schema with `extra="forbid"` —
typos fail loudly rather than silently.

**Roles.** Put a role under `actions/<key>/roles/<role-name>/` when it
is private to one action (Ansible's playbook-adjacent role search picks
it up with zero configuration). Use the top-level `roles/` for roles
that are genuinely shared across actions — LabDog adds it to
`ANSIBLE_ROLES_PATH` for every playbook in the pack.

## Actions in this pack

| Action | Targets | Summary |
|---|---|---|
| [`alloy-install`](./actions/alloy-install/) | host, group | Install and configure Grafana Alloy on Debian/Ubuntu hosts; ships metrics + logs to Prometheus and Loki with optional service auto-detection. Registers `alloy` package and `alloy.service` via `post_run_register` so LabDog manages them going forward. |
| [`k8s-upgrade`](./actions/k8s-upgrade/) | group only | Drain, `kubeadm`-upgrade, and re-admit each node serially across the `control_plane` and `workers` groups. Apt-only. |
| [`linux-upgrade`](./actions/linux-upgrade/) | host, group | `apt upgrade` all packages and reboot if `/var/run/reboot-required` is set. Uses `verify/post-upgrade.yml` as the success gate. |
| [`linux-os-upgrade`](./actions/linux-os-upgrade/) | host, group | Major-release distribution upgrade (e.g. Debian 12→13, Ubuntu 22.04→24.04) via `apt dist-upgrade`. |
| [`example`](./actions/example/) | host, group | Harmless probe that exercises the full snapshot → run → verify → rollback lifecycle. Decides success with a `verify_playbook`; `fail_verify` forces a rollback on demand. |
| [`example-ai-verify`](./actions/example-ai-verify/) | host, group | The same probe, but the verdict comes from LabDog's AI verify step instead of a verify playbook. `simulate_problem` gives the model something real to weigh. |

Each action ships its own README under `actions/<key>/` covering
parameters, prerequisites, and customization points.

## Manifest schema

```yaml
key: linux-upgrade                 # stable identifier
name: Upgrade Linux packages       # shown in the UI
description: …                     # one-paragraph description
icon: ArrowUpFromLine              # lucide-react icon name
playbook: playbook.yml             # filename relative to this manifest
version: "1.0"                     # bump on breaking parameter changes
estimated_duration: "5–15 min"
destructive: true                  # enables snapshot/verify/rollback when
                                   # the host has a Proxmox VM mapping
supports_group: true               # can target a group of hosts?
supports_host: true                # can target a single host?
supports_fleet: false              # can target every host? conservative
                                   # default; flip only for drift-check
                                   # style actions
verify_playbook: ../../verify/post-upgrade.yml   # (optional) pack-supplied
verify_timeout_seconds: 180                   # verify hook; replaces the
                                              # built-in SSH/services/
                                              # packages check. See
                                              # verify/README.md.
ai_verify_prompt: >-                # (optional) ask LabDog's AI verify step
  Confirm the host booted the new   # a question about the evidence it
  kernel and nothing in the         # collected, instead of deciding success
  journal indicates a problem.      # in Ansible. Mutually exclusive with
ai_verify_fail_closed: false        # verify_playbook -- that one wins, and
                                    # this is silently ignored beside it.
                                    # fail_closed decides only what an
                                    # INCONCLUSIVE verdict means; a stated
                                    # PASS or FAIL is honoured either way.
                                    # See actions/example-ai-verify/.
parameters:                        # passed as --extra-vars at runtime
  - key: auto_reboot
    label: Reboot if required
    type: bool                     # string | int | bool | choice
    default: true
    required: false
    help_text: Optional tooltip shown below the input.
  - key: severity
    label: Severity
    type: choice
    choices: ["low", "medium", "high"]   # required when type: choice
    default: low
```

Full manifest field reference and edge cases:
[docs/ui/actions.md#manifest-schema](https://github.com/open-labdog/labdog/blob/main/docs/ui/actions.md#manifest-schema).

## Verify playbooks

The [`verify/`](./verify/) directory holds reusable verify playbooks
admins can point their manifests at (`verify_playbook: ../../verify/…`),
or pack authors can `import_playbook:` from their own custom verify
files. They cover the same ground as LabDog's built-in Python verify
(SSH round-trip, load, disk, systemd failed-unit detection, explicit
service and package lists) but as inspectable, copyable Ansible.

See [`verify/README.md`](./verify/README.md) for the full list and
usage patterns.

## Adding a new action

1. Create `actions/<key>/` with `manifest.yml` and `playbook.yml`.
2. If your playbook needs a role:
   - **Action-private** (used only by this action): put it under
     `actions/<key>/roles/<role-name>/`. Ansible auto-resolves it via
     playbook-adjacent role search — no config needed.
   - **Shared** (used by multiple actions): put it under the top-level
     `roles/` — LabDog joins every loaded pack's `roles/` dir into
     `ANSIBLE_ROLES_PATH`, so `include_role: name: <role>` just works.
3. If the action is destructive and you want a custom success gate,
   add a `verify_playbook:` to the manifest pointing at one of the
   templates in `verify/` or your own.
4. Test against a LabDog dev instance (below).
5. Commit and push. LabDog instances that have this repo configured as
   a pack will pick up the change on next startup, or immediately when
   an admin clicks **Sync** on the pack in the UI.

## Testing against a local LabDog

Either:

- **Git sync flow** — push your branch somewhere LabDog can reach,
  add a GitRepository under **Integrations → Git Repos** with any
  credentials the repo needs, then add an Action Pack pointing at
  that repo. Uncontested keys win automatically; contested keys need
  a per-key pin via the Action Registry.
- **Local filesystem flow** — go to **Action Packs → Add Pack**,
  set source to **Local directory**, and fill in the **Filesystem
  path** field with the absolute path of your working copy. No clone
  happens; LabDog reads manifests in place. Fastest iteration loop.

After either, hit **Sync** (UI) or `POST /api/action-packs/{id}/sync`
and the action will appear. `GET /api/actions/` lists every resolved
action along with its winning pack name and override history.

## Playbook conventions

- `hosts: all` — LabDog generates a single-host inventory for per-host
  runs, or a flat multi-host inventory for group-dispatch actions
  (`supports_host: false`). Either way `hosts: all` matches.
- `become: true` when you need root.
- Parameters arrive as top-level Ansible variables from `--extra-vars`.
- `ansible_check_mode=true` is set on dry runs — respect it if the
  action has a dry-run path.
- Any Ansible-normal failure is how LabDog knows the action failed.
  No custom exit-code protocol — `failed_when` and
  `ansible.builtin.assert` are your friends.

## Precedence recap

Packs are **unordered**. Each action key in the registry has exactly
one winning pack. The resolution rules are:

| Case | What happens |
|---|---|
| **Uncontested** — one pack declares the key | That pack wins automatically. |
| **Contested + pinned** — multiple packs declare the key, operator chose a winner | The pinned pack wins. Status shown as **Pinned** (or **Frozen** for auto-pins awaiting confirmation). |
| **Contested + unresolved** — multiple packs declare the key, no pin yet | Action is *unrunnable*. The Run button is disabled; the row is amber-tinted in the Action Registry with a "Pick winner" prompt. |

To resolve a conflict, open **Integrations → Action Packs**. The
**Action Registry** table shows every key — click a contested row to
expand an inline radio group listing every candidate pack, then save.
The **Make winner for all keys** button on a pack's row in the **Pack
Sources** table bulk-pins every key that pack contributes.

The bundled pack appears as a read-only row in Pack Sources (no Sync /
Edit / Delete). It can be overridden for any key by adding a pack that
declares the same key and pinning it as winner.

## Group-dispatch actions

Most actions fan out per-host: LabDog generates one single-host
inventory per target and dispatches one Celery task per host. Some
actions need all hosts visible to a single ``ansible-playbook``
invocation — for example, `k8s-upgrade` must drain one node, upgrade
it, wait for it to re-join, then move to the next.

To write a group-dispatch action:

- Set ``supports_host: false`` and ``supports_group: true`` in the
  manifest. LabDog will only show the action on group views.
- The playbook receives a flat multi-host inventory. Use Ansible's
  ``serial:``, ``delegate_to:``, and ``run_once:`` primitives to
  control ordering across hosts. LabDog does not inject any special
  group structure — the playbook is responsible for its own
  orchestration.
- Cluster-wide ``kubectl`` calls can delegate to a single control-
  plane node: ``delegate_to: "{{ groups['all'] | first }}"``.

The [`actions/k8s-upgrade/`](./actions/k8s-upgrade) action is the
reference implementation — it discovers which hosts are control-plane
vs worker nodes in a setup play, then upgrades them serially. It is
apt-only today; RHEL support is on the LabDog roadmap.

## Examples

Starter packs demonstrating different patterns live in the main
labdog repo at
[`docs/examples/action-packs/`](https://github.com/open-labdog/labdog/tree/main/docs/examples/action-packs):

- `minimal-pack/` — smallest possible pack, one action.
- `reboot-pack/` — destructive action with a parameter.
- `with-role/` — pack that ships its own Ansible role.
- `with-verify/` — pack-supplied verify playbook with healthz/version
  probes.

## Contributing / CI

Every merge request targeting the default branch runs the
`validate-manifests` job in [`.gitlab-ci.yml`](./.gitlab-ci.yml). The
job clones the labdog repo, imports `app.actions.manifest.ActionManifest`
(the same Pydantic model the runtime uses), and validates every
`actions/*/manifest.yml` against it via
[`scripts/validate_manifests.py`](./scripts/validate_manifests.py).

Schema typos, missing required fields, and unknown keys (the model
sets `extra="forbid"`) fail the pipeline pre-merge instead of breaking
LabDog instances at pack-load time. Pin `LABDOG_REPO_REF` in the
GitLab project settings to a labdog tag once releases start cutting,
and bump it deliberately when you want this pack to track new schema.

To run the same check locally before pushing:

```bash
git clone --depth 1 https://github.com/open-labdog/labdog.git /tmp/labdog
LABDOG_SRC=/tmp/labdog python scripts/validate_manifests.py
```

## License

Licensed under the GNU Affero General Public License v3.0 or later
(**AGPL-3.0-or-later**). See [LICENSE](LICENSE) for the full text.

Copyright © 2026 Dennis Tyresson and contributors.
