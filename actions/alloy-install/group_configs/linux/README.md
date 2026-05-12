# group_configs/linux/

Templates placed here are deployed to **every Linux host**.

This works because the inventory places all Linux hosts under a `linux` parent
group. The playbook scans `group_names` (which includes parent groups), so every
Linux host picks up this directory automatically.

Use this directory for Linux-wide config snippets — for example a custom
scrape job for a Linux-specific exporter, or a log pipeline that references
Linux file paths.

Template files must end in `.alloy.j2`. The rendered file is written to the
host's `conf.d/` directory with the same name minus the `.j2` suffix.

## Note on service detection

Templates here are rendered **before** service detection runs. The `_alloy_detected`
variable is not available. For detection-conditional config, use the `alloy_force_*`
variables or service detection (role-alloy-linux-services).
