# alloy-install

Installs and configures [Grafana Alloy](https://grafana.com/oss/alloy/) on
Debian/Ubuntu hosts: ships metrics to Prometheus and logs to Loki, with
optional auto-detection of common services (Docker, MySQL, PostgreSQL) and
group-based config layering.

LabDog operators surface this as the **Install Alloy agent** action on the
host and group views. The manifest's UI-exposed parameters cover the common
operator knobs (endpoints, install method, detection toggle); deeper tuning
is done by forking this pack and editing role defaults.

## What it does

1. Adds the Grafana APT repo and installs the `alloy` package (skipped when
   installing from a local `.deb` or in config-only mode).
2. Renders a base set of `.alloy` config files under `/etc/alloy/conf.d/`:
   remote-write endpoints, label transforms, default scrape jobs, node-exporter
   pipeline.
3. Layers in **group-based config** from `group_configs/<group>/` directories
   for every LabDog group the host belongs to (plus `all/` and `linux/`).
4. Detects running services (Docker, MySQL, PostgreSQL via systemd + listening
   ports) and deploys service-specific scrape jobs.
5. Enables and starts the `alloy` systemd unit; reloads on config change.

The action is **destructive** (mutates host state) and **idempotent** —
re-running with the same parameters converges. Set `alloy_config_only: true`
to skip install entirely and just refresh config.

## Role architecture

`playbook.yml` composes six single-purpose roles in order. Each role's
`when:` guard is set in the playbook (not the role) so the orchestration
stays readable.

| Role | Responsibility |
|---|---|
| `role-alloy-linux-repo` | Add Grafana GPG key + APT repository |
| `role-alloy-linux-install` | Install Alloy from repo or local `.deb` |
| `role-alloy-linux-configure` | Render base + group_configs/ template files |
| `role-alloy-linux-detect` | Detect running services / listening ports |
| `role-alloy-linux-services` | Render service-specific scrape configs |
| `role-alloy-linux-service` | Enable + start the `alloy` systemd unit |

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
| `service.alloy.j2` | per-service `conf.d/<service>.alloy` | `role-alloy-linux-services` |
| `docker.alloy.j2` | `conf.d/docker.alloy` (when detected) | `role-alloy-linux-services` |
| `mysql.alloy.j2` | `conf.d/mysql.alloy` (when detected) | `role-alloy-linux-services` |
| `postgresql.alloy.j2` | `conf.d/postgresql.alloy` (when detected) | `role-alloy-linux-services` |

## Group-based configuration

`group_configs/<group>/*.alloy.j2` files are rendered per host based on the
groups it belongs to in the LabDog inventory.

```
group_configs/
├── all/                  every host
├── linux/                every host in the 'linux' parent group
├── linux_databases/      hosts in the 'linux_databases' group
├── linux_webservers/     hosts in the 'linux_webservers' group
└── <your-group>/         hosts in <your-group>
```

Add a new group: drop a directory under `group_configs/` named after the
LabDog group, fill it with `*.alloy.j2` templates, and re-run the action.
Templates have access to host vars and the standard Ansible facts.

The pack ships two example group templates: `linux_databases/slow_query_logs.alloy.j2`
and `linux_webservers/nginx_metrics.alloy.j2`. Both are illustrative — copy,
adapt, or remove as needed.

## Manifest parameters

See [`manifest.yml`](./manifest.yml). The UI-exposed parameters are:

| Key | Type | Default | Notes |
|---|---|---|---|
| `alloy_prometheus_url` | string | required | Prometheus remote-write endpoint |
| `alloy_loki_url` | string | required | Loki push endpoint |
| `alloy_install_method` | choice | `repo` | `repo` (apt) or `local` (`.deb` file) |
| `alloy_local_package_path` | string | `""` | Path on host when `install_method=local` |
| `alloy_config_only` | bool | `false` | Skip install, refresh config only |
| `alloy_detect_services` | bool | `true` | Auto-detect Docker/MySQL/Postgres |

Tunables not exposed in the UI (edit role defaults to change) include:
the alloy unix user/group, Mimir org ID, TLS skip-verify, service-data-source
strings, and the `alloy_force_*` overrides. See each role's
`defaults/main.yml` for the full list.

### LabDog integration

The manifest declares a `metrics_backend` block, so when this action is run
from **LabDog** the `alloy_prometheus_url` / `alloy_loki_url` /
`alloy_mimir_org_id` values are filled automatically from LabDog's registered
default Grafana instance (operator-supplied values still win). LabDog also
injects two identity vars per host — `labdog_host_id` and `labdog_hostname` —
which the config stamps as `prometheus.remote_write` `external_labels` on
every shipped series, so LabDog can query a host's metrics back on its host
page. Outside LabDog these default to empty / `inventory_hostname` (Prometheus
drops empty label values), so the pack still works standalone.

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
2. **Add a group_config template**: drop a new `<group>/<name>.alloy.j2` file.
3. **Add a service to auto-detect**: extend `alloy_service_map` in
   `role-alloy-linux-services/defaults/main.yml`.
4. **Override the whole action**: copy `actions/alloy-install/` into a
   higher-precedence pack and edit there — LabDog's pack overlay will pick
   it up.

