# group_configs/all/

Templates placed here are deployed to **every** host the action runs against.

Use this directory for fleet-wide config: Alloy's remote-write endpoint
overrides, label transforms, recording rules, etc.

Template files must end in `.alloy.j2`. The rendered file is written to the
host's `conf.d/` directory with the same name minus the `.j2` suffix.

## Directory guide

| Directory | Deployed to | Use for |
|-----------|-------------|---------|
| `all/` | every host | Fleet-wide snippets (endpoints, label rules) |
| `linux/` | every host in the `linux` parent group | Linux-wide config |
| `<group>/` | hosts in that LabDog group | Group-specific config |

The `linux/` directory matches because the LabDog inventory places every host
under a `linux` parent group. The playbook uses `group_names` (which includes
parent groups) to select which directories to scan.

## Note on service detection

Group config templates in this directory are rendered **before** service detection
runs (`role-alloy-linux-detect`). The `_alloy_detected` variable is therefore **not**
available here. For config that should only appear when a service is detected, use
`alloy_force_*` variables or add a dedicated service to `alloy_service_map`.
