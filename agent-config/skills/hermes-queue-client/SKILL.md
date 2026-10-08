---
name: hermes-queue-client
description: "Hand off PR review, PR feedback maintenance, PRD/design/roadmap drafts, or a status check to the local Hermes fleet instead of doing the work in a coding agent. Triggers: ask Hermes to review, queue PR review, handle PR feedback, queue maintenance, draft through Hermes, Hermes status."
triggers:
  - "ask Hermes to review"
  - "queue PR review"
  - "handle PR feedback"
  - "queue maintenance"
  - "draft through Hermes"
  - "Hermes status"
---

# Hand off work to host Hermes

Set `FLEET_REPO` to the operator-provided absolute path to the `ai-pr-automation` checkout containing `scripts/hermes-queue-client.py`. Run as the current local user; never use `sudo`, ask for API keys, invoke Hermes directly, or use another agent's review/maintenance workflow as a fallback. If the client is missing, stop and report it. Submit only when the user explicitly asks for this work: PR review may post to GitHub, maintenance may push/reply, and drafts may incur model costs.

**PR review.** Get the exact open head with `gh pr view 123 -R OWNER/REPO --json headRefOid --jq .headRefOid`. Serialize this JSON with the returned 40-character SHA as `head_sha` and send it on stdin:

```json
{"version":1,"kind":"pr-review","repository":"OWNER/REPO","pr":123,"head_sha":"0123456789abcdef0123456789abcdef01234567"}
```

**PR maintenance.** Send `{"version":1,"kind":"pr-maintain","repository":"OWNER/REPO","pr":123}`. The client checks repository authority, the open head, and actionable external feedback. Do not provide a head, feedback digest, round, model, profile, or command. If no actionable feedback exists, report the rejection; do not force a rerun. Identical PR identities replay the existing Hermes run.

**Document drafts.** Use `prd-write`, `dd-write` (design; `design-write` also works), or `roadmap-write`:

```json
{"version":1,"kind":"dd-write","title":"Design title","requirements":"Goals and constraints","repositories":["OWNER/REPO"]}
```

`title` must be nonblank and at most 256 characters; `requirements` must be nonblank and at most 2048 UTF-8 bytes; include 1–5 authorized repositories. Serialize dynamic values with `json.dumps` and pipe them directly to the client; do not interpolate untrusted text into a shell string or leave request files behind. Repeating identical intake adopts the same Kanban operation. These requests create drafts and human-review tasks, **not** inbox publication.

**Submit or inspect.** Use one typed envelope on stdin, then keep the returned ID:

```text
python3 "$FLEET_REPO/scripts/hermes-queue-client.py" request
python3 "$FLEET_REPO/scripts/hermes-queue-client.py" status-check pr-review RUN_ID
python3 "$FLEET_REPO/scripts/hermes-queue-client.py" status-check pr-maintain RUN_ID
python3 "$FLEET_REPO/scripts/hermes-queue-client.py" status-check dd-write OPERATION_ID
```

For documents use the returned `operation_id` and matching kind (`prd-write`, `dd-write`, `design-write`, or `roadmap-write`); for PR work use `run_id`, not `operation_id`. Status checks are read-only. On submission failure or uncertain outcome, inspect the returned run/operation and external state before trying again. `status: started` is not a completed GitHub effect; check terminal status and the PR. Report what was queued, its ID/status, and what still requires human action. Do not publish drafts, mark human decisions, or claim GitHub work completed on the agent's behalf.

`pr-safety` is also accepted by the client, but its handoff delivery is not complete; do not describe it as a finished safety review. Human-approved document publication is tracked separately in [#338](https://github.com/Zhachory1/ai-pr-automation/issues/338).
