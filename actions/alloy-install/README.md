# alloy-install

Installs and configures [Grafana Alloy](https://grafana.com/oss/alloy/) on
Debian/Ubuntu hosts for **basic monitoring**: node metrics to
Prometheus/Mimir and journal + file logs to Loki, with optional Docker
auto-detection. Anything more advanced (database exporters, app-specific
scrapes) is left for you to add.

LabDog operators surface this as the **Install Alloy agent** action on the
host and group views. The manifest's UI-exposed parameters cover the common
operator knobs (endpoints, install method, detection toggle); deeper tuning
is done by forking this pack and editing role defaults.

## What it does

1. Detects existing state and picks a mode (see **Install modes** below).
2. On a fresh host, adds the Grafana APT repo and installs the `alloy`
   package (skipped when adopting an existing install, or in config-only
   mode).
3. Renders a base set of `.alloy` config files into the active config
   directory: remote-write endpoints, label transforms, default scrape jobs,
   node-exporter pipeline.
4. Detects a running Docker daemon (systemd + listening ports) and deploys
   its container metrics + log scrape config.
5. Enables and starts the `alloy` systemd unit; reloads (or restarts) on
   config change.

The action is **destructive** (mutates host state) and **idempotent** —
re-running with the same parameters converges. Set `alloy_config_only: true`
to skip install entirely and just refresh config.

## Install modes

`role-alloy-linux-state` runs first and inspects the host — is the `alloy`
package present, and what does `CONFIG_FILE` in `/etc/default/alloy` point at?
— then selects one of three modes automatically (no operator knob):

| Mode | When | What happens |
|---|---|---|
| **fresh** | Package absent, **or** the config dir is already labdog-owned | Install + configure as before. labdog owns `/etc/alloy/conf.d/`; files use their bare names. |
| **adopt_dir** | Package present and `CONFIG_FILE` is an operator-owned directory | labdog renders its configs **into that directory** with a `labdog_` filename prefix (so operator files are never overwritten) and **reloads** Alloy (SIGHUP, no downtime). |
| **adopt_file** | Package present and `CONFIG_FILE` is a single file (or unset) | labdog creates `/etc/alloy/conf.d/`, **copies the operator's existing config file in** (left in place on disk as a backup), adds its own configs, repoints `CONFIG_FILE` at the directory, and **restarts** Alloy. |

A labdog-owned directory is recognised by a `.labdog-managed` marker file (and,
for hosts installed by an older version of this pack, by being exactly the
default `/etc/alloy/conf.d` path). That keeps re-runs in **fresh** mode rather
than re-adopting labdog's own directory and duplicating every file.

Reload is used only in **adopt_dir** (and only if the unit defines an
`ExecReload`); every other case — and any unit without reload support —
restarts.

## Role architecture

`playbook.yml` composes seven single-purpose roles in order. Each role's
`when:` guard is set in the playbook (not the role) so the orchestration
stays readable.

| Role | Responsibility |
|---|---|
| `role-alloy-linux-state` | Detect existing install + config layout; select fresh/adopt_dir/adopt_file mode |
| `role-alloy-linux-repo` | Add Grafana GPG key + APT repository (fresh only) |
| `role-alloy-linux-install` | Install Alloy from the Grafana APT repo (fresh only) |
| `role-alloy-linux-configure` | Render base config template files |
| `role-alloy-linux-detect` | Detect running services / listening ports |
| `role-alloy-linux-services` | Render service-specific scrape configs |
| `role-alloy-linux-service` | Enable + start the `alloy` systemd unit |

`role-alloy-linux-state` exports the facts every later role keys off:
`_alloy_mode`, the effective `alloy_confd_dir`, the `alloy_config_prefix`
applied to labdog's filenames, and `_alloy_reload_only`.

All roles are private to this action: they live under `roles/` next to
`playbook.yml` and Ansible's playbook-adjacent role search picks them up
with no extra configuration. Base `.alloy.j2` templates live at
`roles/role-alloy-linux-configure/templates/` and ship with the pack —
no external fetch.

### Bundled templates

The pack ships these `.j2` templates at
`roles/role-alloy-linux-configure/templates/`:

| Template | Rendered to | Owner |
|---|---|---|
| `alloy-env.j2` | `/etc/default/alloy` | `role-alloy-linux-configure` |
| `endpoints.alloy.j2` | `conf.d/endpoints.alloy` | `role-alloy-linux-configure` |
| `alloy.alloy.j2` | `conf.d/alloy.alloy` | `role-alloy-linux-configure` |
| `linux_node_metrics.alloy.j2` | `conf.d/linux_node_metrics.alloy` | `role-alloy-linux-configure` |
| `linux_journal_logs.alloy.j2` | `conf.d/linux_journal_logs.alloy` | `role-alloy-linux-configure` |
| `linux_file_logs.alloy.j2` | `conf.d/linux_file_logs.alloy` | `role-alloy-linux-configure` |
| `docker.alloy.j2` | `conf.d/docker.alloy` (when detected) | `role-alloy-linux-services` |

## Manifest parameters

See [`manifest.yml`](./manifest.yml). The UI-exposed parameters are:

| Key | Type | Default | Notes |
|---|---|---|---|
| `alloy_prometheus_url` | string | required | Mimir/Prometheus remote-write endpoint (selected from a registered Grafana integration in the UI) |
| `alloy_loki_url` | string | required | Loki push endpoint (selected from a registered Grafana integration in the UI) |
| `alloy_config_only` | bool | `false` | Skip install, refresh config only |
| `alloy_detect_services` | bool | `true` | Auto-detect a running Docker daemon |

Tunables not exposed in the UI (edit role defaults to change) include:
the alloy unix user/group, Mimir org ID, TLS skip-verify, and the
`alloy_force_docker` override. See each role's `defaults/main.yml` for the
full list.

### LabDog integration

The manifest declares a `metrics_backend` block, so when this action is run
from **LabDog** the `alloy_prometheus_url` / `alloy_mimir_org_id` are filled
from LabDog's default **Mimir** instance and `alloy_loki_url` from its default
**Loki** instance (operator-supplied values still win). LabDog also
injects two identity vars per host — `labdog_host_id` and `labdog_hostname` —
which the config stamps on **both** metrics and logs: as
`prometheus.remote_write` `external_labels` for metrics, and via a shared
`loki.relabel "labdog"` that the base log sources forward through for logs.
That lets LabDog query a host's data back on its host page. Outside LabDog
these default to empty / `inventory_hostname` (relabel drops empty values),
so the pack still works standalone.

## Requirements

- **OS**: Debian / Ubuntu (`ansible_os_family == "Debian"`)
- **Privileges**: sudo/root on the target host
- **Network**: target host can reach the configured Prometheus + Loki endpoints

The playbook refuses to run on non-Debian-family hosts via a `pre_tasks` assert.
RHEL / Rocky / Alma support is on the roadmap; until it lands, fork the pack
and add `role-alloy-rhel-*` siblings.

## Customizing

To change behaviour for your fleet:

1. **Tweak parameters**: change defaults in the relevant role's
   `defaults/main.yml`, or override per-action-run via manifest parameters.
2. **Add a base config template**: drop a new `*.alloy.j2` under
   `role-alloy-linux-configure/templates/` and a matching `template:` task in
   `base_configs.yml`.
3. **Add a service to auto-detect**: add a `<service>.alloy.j2` template and
   extend `alloy_service_map` in both `role-alloy-linux-detect` and
   `role-alloy-linux-services` `defaults/main.yml` (this pack ships Docker
   only — DB/app exporters are intentionally out of scope).
4. **Override the whole action**: copy `actions/alloy-install/` into a
   higher-precedence pack and edit there — LabDog's pack overlay will pick
   it up.

