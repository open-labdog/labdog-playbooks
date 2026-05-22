# example — System Probe

An example action that exercises the full LabDog action lifecycle:

```
Proxmox snapshot → run → verify → (rollback on failure)
```

No production changes are made. The action writes a timestamped marker
file to the host and verifies it exists. Use it to:

- Confirm LabDog can reach and SSH into a host
- Test that Proxmox snapshot / rollback is wired up for a host
- Walk through the full UI flow without risk

## Parameters

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `marker_dir` | string | `/var/lib/labdog-example` | Where the probe marker is written |
| `fail_verify` | bool | `false` | Write a sentinel that causes verify to fail, triggering rollback |

## Lifecycle

1. **Snapshot** — LabDog snapshots the host's Proxmox VM (if mapped) before the playbook starts.
2. **Run** — `playbook.yml` gathers facts and writes `<marker_dir>/last-run`.
3. **Verify** — `verify.yml` checks the marker exists. If `fail_verify=true`, the sentinel file causes verify to fail.
4. **Rollback** — on verify failure, LabDog restores the host to the pre-run snapshot.
5. **Cleanup** — on verify success, the snapshot is deleted.

## Testing rollback

Set `fail_verify = true` when scheduling the action. The verify playbook
will assert false, LabDog will roll back the host, and you can confirm
the marker file is gone post-rollback.

## Targeting

Supports both **host** and **group** dispatch. Group dispatch runs a
single ansible-playbook invocation against all group members in parallel.
