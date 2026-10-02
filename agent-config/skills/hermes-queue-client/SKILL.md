---
name: hermes-queue-client
description: Request and status-check host Hermes Kanban PR review, maintenance, merged-PR safety, PRD, design/DD, and roadmap workflows using a typed Python CLI reference. No new service or caller-selected board/profile/task body.
---

# Hermes queue

Use the operator-provided invocation of `scripts/hermes-queue-client.py`, running as the `hermes-agent` OS account against the **existing host Hermes CLI**. If that invocation is unavailable, report that the host queue is unavailable; never point `HERMES_HOME` at your personal Hermes instance, use `sudo`, or call `hermes kanban` directly.

**Request:** pass one JSON envelope on stdin to `python3 scripts/hermes-queue-client.py request`:

```json
{"version":1,"kind":"prd-write","title":"Document the change","requirements":"State the problem, intended outcome, and constraints.","repositories":["OWNER/REPO"]}
```

| Kind | Fixed board | Request fields beyond `version` and `kind` |
| --- | --- | --- |
| `pr-review` | `pr-review` | `repository`, `pr`, exact open-PR `head_sha` |
| `pr-maintain` | `pr-maintain` | `repository`, `pr` |
| `pr-safety` | `pr-safety-council` | `repository`, `pr`, exact merged commit `head_sha` |
| `prd-write` | `prd-write` | `title`, `requirements`, `repositories` |
| `dd-write` | `design-write` | `title`, `requirements`, `repositories` |
| `roadmap-write` | `roadmap-write` | `title`, `requirements`, `repositories`; board must be active |

For example, `{"version":1,"kind":"pr-maintain","repository":"OWNER/REPO","pr":123}`. The host checks repository grants, derives PR identity/feedback/round and safety snapshot, and uses existing enqueue scripts or Hermes CLI. For all three document kinds, intake sends repository names; the Hermes writer prepares and pins repository and knowledge snapshots before drafting or reviewer fan-out. External agents do not prepare cache snapshots. Do not submit profiles, models, providers, board names, commands, local paths, feedback digests, maintenance rounds, policy files, or task bodies. Repeating an exact request must adopt native work; never manually reopen a blocked card.

**Status-check:** `python3 scripts/hermes-queue-client.py status-check <kind> <operation_id>` reads the fixed Kanban board without claiming work. The response includes the board, operation, current task ID, and native status. This is not a human approve/revise/deny action or publication; those remain in Hermes's existing exact-digest operator workflow. If the host CLI is unavailable or a PR is ineligible, report the error. If document evidence cannot be prepared, the Hermes writer blocks; report that status rather than inventing or restarting a task.
