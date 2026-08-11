# example-ai-verify — AI Verify Probe

The same harmless probe as [`example`](../example/), with one difference:
the post-run verdict comes from LabDog's **AI verify step** rather than
from a verify playbook.

```
Proxmox snapshot → run → SSH health checks → AI verdict → (rollback on FAIL)
```

No production changes are made. Use it to:

- Confirm the AI verify path is wired up end to end on your instance
- See what evidence LabDog collects and hands to the model
- Watch the model judge something genuinely ambiguous

## Why this is a separate action

`verify_playbook` wins outright. When a manifest declares one, LabDog
runs it and never reaches the built-in health check that AI verification
lives behind — so an `ai_verify_prompt` sitting next to a
`verify_playbook` is silently ignored. `example` declares a verify
playbook, which is why this could not be a flag on it.

The distinction is worth keeping in mind when writing your own packs:

| | Decides success by |
|---|---|
| `verify_playbook` | Running your Ansible tasks. Deterministic, and you write the rules. |
| `ai_verify_prompt` | Asking a question in words about evidence LabDog collected. Judgement, and it can weigh things you did not anticipate. |

They are alternatives, not layers.

## Parameters

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `marker_dir` | string | `/var/lib/labdog-example` | Where the probe marker is written |
| `simulate_problem` | bool | `false` | Log a real error-priority journal entry for the model to weigh |

`simulate_problem` is **not** the equivalent of `example`'s
`fail_verify`. That one writes a sentinel the verify playbook is
hard-coded to fail on, so the outcome is certain. This one writes a
genuine `user.err` entry via `logger` and leaves the verdict open —
whether one log line constitutes a fault is exactly the judgement the AI
verify step exists to make. Expect the model to mention the entry and
then decide. Either answer is informative.

## Requirements

Beyond the usual snapshot/verify gate (destructive action, host with a
Proxmox VM mapping, verify enabled on the run):

- `ai.enabled` turned on in LabDog's settings
- An AI provider configured and enabled

Without those the AI step is skipped and the action's own result stands,
because `ai_verify_fail_closed` is left at its default of `false`. See
[the AI verification section of LabDog's action
docs](https://github.com/open-labdog/labdog/blob/main/docs/ui/actions.md#ai-verification).

## What you should see

- In the run detail: a `[verify] ai verdict=…` line, then an
  `=== AI verification ===` block with the model's reasoning.
- On the **Assistant** page: a new session badged `verify`, whose
  transcript shows the exact evidence pack the verdict was reached from.
- That session runs with **no tools**. It cannot open an SSH connection
  or look anything up — everything it judges was collected before it
  started. A verdict that can restore a snapshot should not also be
  reaching into the host it is deciding about.

## Verdicts

The model answers `PASS`, `FAIL`, or `INCONCLUSIVE` on the first line.

| Verdict | With `ai_verify_fail_closed: false` (this action) | With `true` |
|---|---|---|
| `PASS` | passes | passes |
| `FAIL` | fails, and rolls back if auto-rollback is on | same |
| `INCONCLUSIVE` | passes | fails |

`INCONCLUSIVE` also covers every case where no verdict was obtained at
all — AI switched off, no provider, budget spent, the backend erroring.
None of those say anything about the host, which is why they share the
inconclusive path rather than quietly passing.

This action leaves the flag at `false` deliberately: an unreadable
verdict on a probe that changes nothing should not roll a host back, and
this is where an operator first meets the setting. Set it to `true` on
actions where an unverifiable outcome is itself unacceptable — a kernel
or firmware upgrade you would rather revert than leave in an unknown
state.
