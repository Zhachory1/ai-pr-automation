---
name: hermes-queue-client
description: Submit PR review or maintenance to host Hermes Runs API and check a run by ID. Use for "ask Hermes to review this PR", "handle PR feedback", "Hermes status", or request a document draft through the existing host Kanban client.
---

# Talk to host Hermes

Use the operator-provided invocation of `scripts/hermes-queue-client.py` **as `hermes-agent`**. Do not call the Hermes CLI directly, select a model/profile, use your personal Hermes home, or ask for API keys. If the invocation is unavailable, report that Hermes is unavailable.

**PR review:** Submit an exact open head on stdin:

```json
{"version":1,"kind":"pr-review","repository":"OWNER/REPO","pr":123,"head_sha":"0123456789abcdef0123456789abcdef01234567"}
```

**PR maintenance:** Submit `{"version":1,"kind":"pr-maintain","repository":"OWNER/REPO","pr":123}`. The host client checks repository authority, current GitHub metadata, and external feedback before submitting. Do not supply a head, feedback digest, round, model, profile, or command yourself. Both kinds use the same Hermes Runs API identity and idempotency contract as the Compose cron producers. Repeating the same identity returns the existing run, not a new review/fix.

Call `python3 scripts/hermes-queue-client.py request` with one JSON envelope on stdin. A PR response includes `operation_id`, `run_id`, `status`, and `replayed`. To check it, use **the returned run ID**, not operation ID:

```text
python3 scripts/hermes-queue-client.py status-check pr-review RUN_ID
python3 scripts/hermes-queue-client.py status-check pr-maintain RUN_ID
```

Status-check reads the run; it does not create work. A failed request returns an error rather than a fabricated task. Never infer that GitHub was updated merely from `status: started`; inspect the terminal run and the PR.

**Other existing kinds:** `prd-write`, `dd-write`, and `roadmap-write` still use host Kanban and check status by their returned `operation_id`. They produce drafts/tasks, **not** automatic inbox publication. `pr-safety` also remains a host Kanban request, but handoff delivery is undecided; do not describe it as a completed safety review. Document publication, memory writes, and PR-safety delivery await [#325](https://github.com/Zhachory1/ai-pr-automation/issues/325), [#326](https://github.com/Zhachory1/ai-pr-automation/issues/326), and [#324](https://github.com/Zhachory1/ai-pr-automation/issues/324).
