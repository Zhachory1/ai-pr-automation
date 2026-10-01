---
name: hermes-queue-client
description: Request a PRD, design, or activated roadmap draft on host-native Hermes Kanban and check its writer card. Use when a local agent needs Hermes document writing without choosing boards, profiles, models, paths, or Kanban task bodies.
---

# Hermes document queue

Use the operator-provided invocation of `scripts/hermes-queue-client.py`, running as the `hermes-agent` OS account against the **existing host Hermes CLI**. If that invocation is unavailable, report that the host queue is unavailable; never point `HERMES_HOME` at your personal Hermes instance, use `sudo`, or call `hermes kanban` directly.

Send one JSON request on stdin, not as shell arguments:

```json
{"version":1,"kind":"prd-write","title":"Document the change","requirements":"State the problem, intended outcome, and constraints.","repositories":["OWNER/REPO"]}
```

Supported kinds: `prd-write`, `design-write`, and `roadmap-write` when that board already exists. The host computes the operation ID and checks repository grants and snapshot freshness before admitting the first writer card. Repeating the exact request adopts existing work. No caller-chosen profile, provider, board, tool, path, or task state. Do not include secrets or private source documents in request text.

`python3 scripts/hermes-queue-client.py request` accepts the envelope. `python3 scripts/hermes-queue-client.py status <operation_id>` reads the initial writer card only. Output has `kind`, `operation_id`, `board`, `task_id`, and `writer_status`; **writer completion is not final council approval**. Approval, revision, denial, publication, PR requests, and repository refresh are not supported by this reference client.
