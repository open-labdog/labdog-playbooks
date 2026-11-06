# alloy-install

Installs and configures [Grafana Alloy](https://grafana.com/oss/alloy/) on
Debian/Ubuntu hosts for **basic monitoring**: node metrics to
Prometheus/Mimir and journal + file logs to Loki, with optional Docker
auto-detection. Anything more advanced (database exporters, app-specific
scrapes) is left for you to add.

LabDog operators surface this as the **Install Alloy agent** action on the
host and group views.

## What a run does

Everything before step 6 leaves the host's config and its running Alloy as
they were (apart from installing the package on a host that has none), so a
host that fails there is untouched. In a group run, a failing host fails
alone; the others carry on.

1. **Look** (`role-alloy-linux-state`). Is the package installed, what does
   `CONFIG_FILE` in `/etc/default/alloy` point at, and which of the files
   there did this pack write? Picks a mode (see **Install modes**).
2. **Refuse early** (`role-alloy-linux-preflight`). The run fails, naming
   the reason, when:
   - the host has no Mimir or no Loki destination;
   - a file this run would write exists and wasn't written by this pack;
   - files the pack didn't write already define `prometheus.remote_write`
     or `loki.write` (see **Existing pipelines**);
   - the installed Alloy has no `alloy validate` (upgrade it first);
   - a destination is unreachable from the host (see **Reachability**).
3. **Install** (`role-alloy-linux-repo`, `role-alloy-linux-install`), only
   when the package is absent and the run isn't config-only.
4. **Build in a staging directory** (`role-alloy-linux-configure`,
   `role-alloy-linux-services`). A copy of every file Alloy loads today,
   plus this pack's files rendered for this host, plus Docker's when a
   running Docker is detected.
5. **Validate** (`role-alloy-linux-validate`). `alloy validate` on the
   staging copy, as one config, the way Alloy will load it. A failure names
   the live file to fix (for example `/etc/alloy/conf.d/custom.alloy`), and
   nothing is changed.
6. **Apply** (`role-alloy-linux-apply`). Back up what will change, carry
   log positions over (see **Upgrading**), install the validated files,
   restart (or reload) Alloy, and check it came up on them
   (`role-alloy-linux-health`): `/-/ready` answers, the unit is active and
   isn't restarting, and `alloy_config_last_load_successful` is 1. If any of
   that fails, the previous files are put back, Alloy is restarted on them,
   and the run **still fails**, saying what went wrong and whether Alloy is
   back up.

LabDog then runs `verify.yml` (the same health check) as the action's
post-run verify, and on VM hosts restores its snapshot if that fails.

The action is idempotent: a rerun with the same settings changes nothing
and doesn't restart Alloy. Set `alloy_config_only: true` to skip install
and only refresh the config.

### Dry run

The run's preview (LabDog's dry-run toggle, which needs a LabDog that
passes `--check`, or `ansible-playbook --check`) runs the read-only checks,
reachability included, renders the staging copy, and reports what would
change. It doesn't install, write live files or restart anything.

## Install modes

| Mode | When | What happens |
|---|---|---|
| **fresh** | Package absent, **or** the config dir is LabDog's | LabDog owns `/etc/alloy/conf.d/`; its files use bare names. |
| **adopt_dir** | Package present, `CONFIG_FILE` is a directory LabDog doesn't own | LabDog's files go **into that directory** with a `labdog_` prefix, next to the operator's, and Alloy is **reloaded** (no downtime) when the unit supports it. |
| **adopt_file** | Package present, `CONFIG_FILE` is a single file (or unset) | LabDog creates `/etc/alloy/conf.d/`, **copies the operator's file in** unchanged (the original stays on disk as a backup), adds its own files, points `CONFIG_FILE` at the directory and **restarts** Alloy. |

### What counts as LabDog's

Every file this pack writes starts with `// Managed by Ansible`. A file
without that first line is the operator's: the action never overwrites,
rewrites or deletes it, and refuses to run if it would have to.

A directory is LabDog's when it has the `.labdog-managed` marker, or when
one of its files with a bare pack name (`endpoints.alloy`, ...) carries
the header, which is how installs from older versions of this pack are
recognised. Being `/etc/alloy/conf.d` is **not** enough on its own: an
operator's own directory at the default path is adopted (`adopt_dir`), not
taken over.

### Existing pipelines

Alloy loads every file in its directory together. If the operator's files
already define `prometheus.remote_write` or `loki.write`, adding LabDog's
pipeline would ship the host's metrics and logs twice, so the run fails and
names those files. Either move the hand-maintained endpoints onto LabDog's
settings (see **Moving a hand-maintained endpoints config onto LabDog**
below), or set **Allow existing pipelines**
(`alloy_allow_existing_pipelines`) if shipping twice is intended.

## Destinations

The pack has **no default URLs**: a host without a destination fails before
anything is touched.

From LabDog, each host ships to its own destinations. They are picked from
the Mimir and Loki instances registered under **Integrations → Grafana**, in
this order:

1. the host's own assignment (host page → **Metrics**);
2. its highest-priority group's assignment (group page → **Overview**);
3. the default instance.

Picking a destination in the run dialog sends every host in that run there
instead. The Mimir tenant comes from the Mimir instance's org ID
(`X-Scope-OrgID`); the Loki tenant from the Loki instance's org ID (empty
means no header, Loki's single-tenant mode). "Verify TLS" off on an
instance turns on `insecure_skip_verify` for that endpoint only.

Outside LabDog, pass the variables yourself:

| Variable | Default | Notes |
|---|---|---|
| `alloy_prometheus_url` | `""` | Mimir/Prometheus remote-write URL |
| `alloy_loki_url` | `""` | Loki push URL |
| `alloy_mimir_org_id` | `anonymous` | Mimir tenant |
| `alloy_loki_tenant_id` | `""` | Loki tenant; empty sends no header |
| `alloy_tls_insecure_skip_verify` | `false` | Both endpoints, unless overridden below |
| `alloy_prometheus_tls_insecure_skip_verify` | shared value | Mimir only |
| `alloy_loki_tls_insecure_skip_verify` | shared value | Loki only |

Ingest auth (basic auth, bearer tokens) and a private CA aren't passed yet;
an endpoint behind either needs the pack forked for now.

### Reachability

Before changing anything, each host checks both of its URLs itself, one
step at a time, each with a few short retries:

1. DNS: the host name resolves (`getent ahosts`);
2. TCP: the port accepts a connection;
3. TLS and HTTP: a `GET` gets any HTTP answer. A push URL answers 404, 405
   or 401 to a GET, and that counts as reachable. TLS verification follows
   the endpoint's skip-verify setting.

A failure names the URL and the step, for example `Loki URL
https://loki.example:3100/loki/api/v1/push is unreachable from this host:
TCP connection to loki.example:3100 failed`.

### Identity labels

LabDog injects `labdog_host_id` and `labdog_hostname` per host. The config
stamps both on metrics (`external_labels`) and on logs (the shared
`loki.relabel "labdog"`), so LabDog can query a host's data back on its
host page. Outside LabDog they default to empty / `inventory_hostname`, and
an empty label is dropped.

### Labels dropped before sending

cAdvisor copies every Docker label onto each container series. With
LabDog's two identity labels, image and compose metadata pushed some
series past Mimir's default limit of 30 labels, and those pushes got an
HTTP 400. The Mimir `remote_write` drops labels matching
`alloy_drop_label_regex`:

```
container_label_(org_opencontainers_image_(created|description|licenses|url)|org_label_schema_.*|com_docker_compose_(config_hash|oneoff|replace|version))
```

Set it to `""` to drop nothing.

## Upgrading from an older version of this pack

The pack renamed its components (`loki.source.file "file_logs"` became
`"labdog_file_logs"`, and so on). Alloy keeps each log source's read
position under `<storage>/<component id>/`, and a component with no saved
position starts over. So, the first time the new config is applied, the
action:

- stops Alloy, which saves its final positions (it writes them only every
  few seconds while running);
- copies each old position directory to the new name, once, never over an
  existing one, and only when nothing still defines the old component
  (`alloy_position_migrations` lists the renames, file and journal cursors
  and Docker included);
- starts Alloy on the new config, which carries on where the old one
  stopped.

`loki.source.file` also has `tail_from_end = true`, so a file with no saved
position at all starts at its end instead of re-sending its history.

The old position directories are left in place; they are small and
harmless.

## Moving a hand-maintained endpoints config onto LabDog

For a host whose operator-written file (often `endpoints.alloy`, without
the `// Managed by Ansible` header) defines its own `prometheus.remote_write`
or `loki.write`. The action refuses such a host until this is done (or
**Allow existing pipelines** is set).

1. Note the URLs, tenants (`X-Scope-OrgID` / `tenant_id`) and TLS settings
   in the file.
2. In LabDog, under **Integrations → Grafana**, register a Mimir and a Loki
   instance for each URL that isn't registered yet, with the tenant in
   **Org ID** and **Verify TLS** matching.
3. Assign them to the host (host page → **Metrics**) or to its group (group
   page → **Overview**). Check the host's **Metrics** tab shows the
   destinations you expect.
4. Find what else in the host's config refers to the file's components:
   `grep -rnE '(prometheus\.remote_write|loki\.write)\.<label>' <config dir>`
   (`<label>` is the quoted name, for example `default`). Those are
   `forward_to` lists in other files that the operator owns.
5. Point those references at LabDog's pipeline (`prometheus.remote_write.labdog.receiver`
   / `loki.relabel.labdog.receiver`), or delete the pipelines that are now
   duplicates of LabDog's own (node metrics, journal, `/var/log`, Docker).
6. Move the endpoints file out of Alloy's config directory (keep it as a
   backup) or delete it.
7. Run **Install Alloy agent** with **Dry run** on that host and check it
   passes. Then run it for real. If a file still refers to a component that
   no longer exists, the run fails at validation, names the file, and
   changes nothing.

The order matters: if the endpoints file goes before step 5, the action
refuses at validation (step 7) rather than breaking Alloy, so nothing is
lost, but the run won't succeed until the references are fixed.

## Role architecture

`playbook.yml` composes these roles in order. Each role's `when:` guard is
in the playbook, so the orchestration stays readable. The roles share facts
prefixed `_alloy_`.

| Role | Responsibility |
|---|---|
| `role-alloy-linux-state` | Read-only: package, config layout, ownership, unit state; selects the mode. Holds every shared default. |
| `role-alloy-linux-preflight` | Read-only refusals and reachability probes |
| `role-alloy-linux-repo` | Grafana GPG key + APT repository (fresh only) |
| `role-alloy-linux-install` | Install the package (fresh only) |
| `role-alloy-linux-detect` | Detect running services (Docker) |
| `role-alloy-linux-configure` | Create the staging directory, copy live files, render base templates |
| `role-alloy-linux-services` | Render service configs into the staging directory |
| `role-alloy-linux-validate` | `alloy validate` the staging directory |
| `role-alloy-linux-apply` | Back up, migrate positions, install files, restart/reload, health check; restore on failure |
| `role-alloy-linux-health` | Health check, shared with `verify.yml` |

All roles are private to this action. LabDog copies only `playbook.yml`
into its run directory and puts `roles/` on `ANSIBLE_ROLES_PATH`, so
everything the playbook needs is a role.

### Alloy versions

Any Alloy that has `alloy validate` is supported. Before 1.11,
`alloy validate` doesn't report a reference to a component that doesn't
exist (for example an operator file forwarding to `loki.write.default`
after that file is gone), so on those versions the pack also checks
references itself, with a small script over the staging copy.

### Templates

At `roles/role-alloy-linux-configure/templates/`:

| Template | Rendered to |
|---|---|
| `endpoints.alloy.j2` | `endpoints.alloy` |
| `alloy.alloy.j2` | `alloy.alloy` |
| `linux_node_metrics.alloy.j2` | `linux_node_metrics.alloy` |
| `linux_journal_logs.alloy.j2` | `linux_journal_logs.alloy` |
| `linux_file_logs.alloy.j2` | `linux_file_logs.alloy` |
| `docker.alloy.j2` | `docker.alloy` (when Docker runs) |

In `adopt_dir` mode each gets a `labdog_` prefix.

## Manifest parameters

See [`manifest.yml`](./manifest.yml).

| Key | Type | Default | Notes |
|---|---|---|---|
| `alloy_prometheus_url` | string | each host's own | Pick one to send every host in the run there |
| `alloy_loki_url` | string | each host's own | Same, for Loki |
| `alloy_config_only` | bool | `false` | Skip install, refresh config only (Alloy must be installed) |
| `alloy_detect_services` | bool | `true` | Auto-detect a running Docker daemon |
| `alloy_allow_existing_pipelines` | bool | `false` | Add LabDog's pipeline even if other files already ship |

Other tunables live in `roles/role-alloy-linux-state/defaults/main.yml`.

## Requirements

- **OS**: Debian / Ubuntu (`ansible_os_family == "Debian"`)
- **Privileges**: sudo/root on the target host
- **Network**: the host reaches its Mimir and Loki endpoints (checked)

## Customizing

1. **Tweak defaults**: `roles/role-alloy-linux-state/defaults/main.yml`.
2. **Add a base config**: drop a `*.alloy.j2` under
   `role-alloy-linux-configure/templates/`, add its rendered name to
   `alloy_pack_files`, and start it with the managed header.
3. **Add a service to auto-detect**: add a `<service>.alloy.j2` and extend
   `alloy_service_map` in the `defaults/main.yml` of both
   `role-alloy-linux-detect` and `role-alloy-linux-services` (this pack
   ships Docker only).
4. **Override the whole action**: copy `actions/alloy-install/` into another
   pack and pin it as the winner in LabDog.
