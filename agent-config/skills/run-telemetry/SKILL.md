---
name: run-telemetry
description: "Emit structured workflow telemetry for AI agent runs. Use for run metrics, step latency, cost, tokens, model, loop count, failure rate, human-edit rate, escalations, council verdicts, validation status, PR outcomes, ML metrics, debug evidence, backprop data. Writes JSONL events to .workflow-runs/<run-id>/telemetry.jsonl or caller-provided store for later optimization."
---

# Run Telemetry

Emit structured events for every workflow step so `backprop` can measure and improve the workflow library. The `run-telemetry` skill also emits telemetry for its own failures and schema changes. Use `autopraxis telemetry emit|validate|summarize` for local JSONL tooling. Without telemetry, optimization becomes anecdotes.

## Core Principles

**Telemetry is workflow evidence.** Capture enough metadata to compare runs, steps, loops, and outcomes.

**Low friction wins.** Prefer small JSONL events over perfect observability that agents skip.

**No secrets.** Store pointers and summaries, not credentials, customer data, or sensitive raw logs.

**Stable schema beats prose.** Backprop needs consistent field names across workflows.

**Emit at boundaries.** Record start, end, gate, loop, escalation, validation, and human response events.

## Inputs

- workflow name and step name.
- run id, repo, branch, artifact pointers.
- model/tool metadata if available.
- latency, cost, tokens, iterations, validation, verdicts, human edits.
- caller-provided telemetry path or default `.workflow-runs/<run-id>/telemetry.jsonl`.

## CLI Commands

```bash
autopraxis telemetry emit --workflow <name> --step <name> --event <event> --status <status> [--run-id <id>] [--path <file>]
autopraxis telemetry validate --path <file>
autopraxis telemetry summarize --path <file>
```

Schema reference: `references/telemetry-event-v1.schema.json`.

`emit` appends one JSONL event and creates parent directories. If `--path` is omitted, `--run-id` or a generated run id selects `.workflow-runs/<run-id>/telemetry.jsonl`.

## Event Store

Default path:

```text
.workflow-runs/<run-id>/telemetry.jsonl
```

Fallback path when no repo is available:

```text
~/.autopraxis/runs/<run-id>/telemetry.jsonl
```

Use existing run dirs when the workflow provides them. Do not write sensitive content to long-term memory unless explicitly approved.

## Event Schema

```json
{
  "schema_version": 1,
  "ts": "2026-07-08T00:00:00.000Z",
  "run_id": "workflow-20260708-abc123",
  "workflow": "plan-to-launch",
  "step": "council-on-docs",
  "event": "start|end|gate|loop|escalation|validation|human_response",
  "status": "ok|fail|blocked|skipped|inconclusive",
  "latency_ms": 0,
  "cost_usd": null,
  "cost_source": null,
  "tokens_in": null,
  "tokens_out": null,
  "token_source": null,
  "provider": null,
  "model": null,
  "tools": [],
  "artifact_refs": [],
  "metrics": {},
  "verdict": null,
  "loop_iteration": 0,
  "loop_cap": 0,
  "human_edit_rate": null,
  "escalation_reason": null,
  "notes": null
}
```

## Execution

**Create run id.** Use caller id if supplied. Else derive from workflow, timestamp, and short random suffix.

**Use schema v1.** Emit `schema_version: 1`. Validate against `references/telemetry-event-v1.schema.json` and the local CLI validator.

**Emit start.** Record workflow, step, source refs, model/tool availability, and budget cap.

**Emit gates.** Record pass/block, council verdicts, validation results, metrics, and reasons.

**Emit loops.** Record iteration number, delta scope, remaining budget, and stop decision.

**Emit escalations.** Record cap hit, human ask, missing data, destructive action confirmation, or high-risk blocker.

**Emit end.** Record outcome, latency, cost/tokens if available, artifacts, and next step.

**Summarize.** At handoff, aggregate events into latency, cost, loop count, failure, validation, and human-edit summary.

## Output Contract

```markdown
# Telemetry Event Emitted

- run id:
- path:
- workflow:
- step:
- event:
- status:
- notable metrics:
```

## Mode Metrics

When a workflow chooses or escalates a mode, put mode fields under `metrics`:

```json
{
  "metrics": {
    "workflow_mode": "lite|default|deep",
    "mode_budget": {
      "refs": "focused|selected|full",
      "artifacts": "one|selected|full",
      "council_level_max": "none|single-lens|minimal-council|full-council",
      "loop_cap": 1,
      "validation_scope": "focused|standard|broad"
    },
    "mode_escalation_reason": "short non-sensitive reason when mode changes"
  }
}
```

Do not store raw artifacts, logs, secrets, or customer data in mode reason fields.

## Council Metrics

When agent-fleet `/council` runs or a council gate is skipped, put council fields under `metrics`:

```json
{
  "metrics": {
    "council_level": "none|single-lens|minimal-council|full-council",
    "council_reason": "short non-sensitive reason",
    "persona_count": 0,
    "agent_fleet_invoked": false
  }
}
```

Do not store raw artifacts, logs, secrets, or customer data in council reason fields.

## Plan-To-Launch Gate Metrics

When `plan-to-launch` checks reuse or scope growth, record these metrics: reuse fields on grounding gate; expansion fields on scope-expansion gate/escalation; renewal and PR-budget fields on human-response event. Telemetry is evidence, not approval authority.

```json
{
  "metrics": {
    "reuse_candidate_count": 0,
    "reuse_candidate_results": ["reuse", "cannot-satisfy", "clarify-first"],
    "reuse_inventory_status": "complete|clarify-first|no-candidate",
    "scope_expansion": false,
    "scope_expansion_reason": "short non-sensitive reason when true",
    "renewed_approval_state": "not-required|pending|approved|rejected|revision-requested",
    "delivery_pr_count": 0,
    "approved_delivery_pr_cap": 2
  }
}
```

Resume only after human response approves recorded scope fingerprint; a telemetry write does not grant approval.

## Skill-Lifecycle Signal

The skill-lifecycle roadmap (`docs/roadmap/self-improving-skills-roadmap.md`) consumes three optional `metrics` fields. They are validated when present and must ride the correct event:

- `skills_invoked` / `agents_invoked`: arrays on the `end` event; emitted by the loader/harness (not agent memory) so absence means "not loaded," not "forgot to log."
- `run_disposition`: `accepted|edited|rejected` on a **late `human_response` event keyed by `run_id`** — a downstream fact, never the run's own `end`.
- `unmet_need` (bool) + `unmet_need_note` (short, non-sensitive): on the `escalation` event, set when the router returns a default/unmatched route.

Aggregate across runs read-only:

```bash
autopraxis telemetry lifecycle [--path <file>] [--min-runs <int>]
```

It refuses to emit add/prune signal below `--min-runs` (default 5), reporting `insufficient_signal`. At or above the floor it reports skill/agent usage, never-invoked skills, unmet-need notes, and disposition tallies. This is signal only; adding or retiring a skill remains gated by `backprop` + `/council` + `human-approval-gate`.

## Backprop Metrics

`backprop` should compute:

- per-step latency and cost.
- failure and block rates.
- rework and loop counts.
- human-edit and override rates.
- validation failure rates.
- missing-context rates.
- council blocker precision where outcome is known.
- PR merge, rollback, deploy, or experiment promotion outcomes.

## Success Criteria

- JSONL event written or unavailable store reported.
- event includes workflow, step, status, and timestamp.
- no secrets or raw sensitive data stored.
- run id links all workflow artifacts.
- event schema remains parseable.

## Common Failure Modes

**Telemetry skipped because it is annoying.** Fix by emitting minimal fields first and filling optional metrics later.

**Raw data leakage.** Fix by storing pointers and summaries only. `autopraxis telemetry validate` rejects sensitive-looking keys or values.

**Unstable field names.** Fix by preserving schema and adding new data under `metrics`.

**Backprop cannot join events.** Fix by consistent run id and artifact refs.

## Self-Improvement

Track fields that are frequently missing but useful for `backprop`, plus fields nobody uses. Propose schema changes through `backprop` with migration notes and council review before promotion.
