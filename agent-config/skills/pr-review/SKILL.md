---
name: pr-review
description: "Agent PR review workflow for correctness, safety, maintainability, architecture, security, performance, tests, and fidelity to intent. Use for PR reviewer, code review, review feedback, architecture check, deep dive, optional local test, author revision loop, human signoff. Produces prioritized blocking/non-blocking findings, delta re-review, approval package, telemetry, long-term memory and code RAG context."
---

# PR Review

Review a pull request against real intent, not guesswork. Produce prioritized, actionable feedback and preserve human accountability for merge.

## Core Principles

**Intent anchors review.** Gather ticket, PRD/DD, issue, roadmap goal, and author notes before judging code.

**Architecture before nits.** Check boundaries, coupling, abstractions, and scope creep before line-level style.

**Findings need evidence.** Blocking comments require file/line, behavior, risk, and suggested fix.

**Re-review the delta.** After author changes, focus on modified code and unresolved findings.

**Human owns merge.** Agent review informs, but does not auto-approve final accountability.

## Inputs

- PR URL, branch, diff, or patch.
- ticket/design docs and acceptance criteria.
- repo context and test commands.
- prior review comments and author responses.
- optional local validation budget.
- run id for `run-telemetry`.

## Tool Awareness

Use `grounding-brief` with code RAG for impacted paths, long-term memory MCP for prior decisions, git/gh for PR metadata and diff, CI status for validation, and agent-fleet `/council` when review stakes are high. Do not turn every PR into a council.

## Council Policy

Use agent-fleet council levels. Most PRs should use no council. Use `single-lens` for one domain concern; use minimal/full council only for high-stakes architecture, safety, ML/statistical, security/privacy/reliability, or conflicting reviewer judgments. Required `minimal-council`/`full-council` must block if agent-fleet preflight fails.

## Workflow Modes

- `lite`: small, clear diff; review changed files, tests, and obvious risk. Budget: no council, no broad repo archaeology, focused validation only.
- `default`: normal PR; add architecture/context check and optional local tests. Budget: focused refs, `council_level` max `single-lens`, one delta re-review loop.
- `deep`: high-risk architecture, security/privacy/reliability, ML/statistical, data migration, or conflicting reviewers. Budget: council allowed with reason and broader validation.
- Escalate: unclear intent, high blast radius, unsafe rollback, failing CI tied to diff, or conflicting review judgments.
- Load: start from PR/diff/ticket; load design docs, agent-fleet council protocol, or broader code RAG only if the diff risk requires it.

## Execution

**Gather context.** Use `grounding-brief` to understand intent, scope, changed files, related docs, tests, CI, and prior comments.

**Architecture check.** Judge layer placement, boundaries, abstractions, coupling, data contracts, rollout, observability, and scope creep.

**Deep dive.** Inspect correctness, edge cases, error handling, security, performance, concurrency, data migrations, tests, docs, and maintainability.

**Local test when useful.** Run focused tests or static checks if feasible, cheap, documented, and relevant. Do not run destructive or broad expensive commands without approval.

**Construct feedback.** Prioritize blockers, majors, minors, and nits. Make every comment actionable.

**Author loop.** After revisions, re-review only changed files, unresolved findings, and newly introduced risk. Bound iterations.

**Human signoff.** Use `handoff-packaging` and `human-approval-gate` for final merge recommendation.

**Emit telemetry.** Use `run-telemetry` for changed file count, findings, blocker count, local validation, iterations, human edits, and outcome.

## Loop Controls

**Author revision loop.** Feedback, author revises, delta re-review until approve, block, or iteration cap.

**Block escalation.** If same blocker persists after cap, escalate with exact unresolved issue and risk.

**Delta-only review.** Do not re-litigate settled files unless new changes touch them.

**Council escalation.** Record skipped reason for ordinary PRs, one lens for a single domain concern, and use agent-fleet minimal/full council only for high-stakes or conflicting judgments.

## Output Contract

```markdown
# PR Review

## Context
- PR:
- intent:
- scope:
- docs/tickets:
- CI status:

## Verdict
- status: approve | approve-with-nits | request-changes | block | needs-info
- confidence:
- human recommendation:

## Findings
- severity: blocker | major | minor | nit
  file/line:
  claim:
  evidence:
  risk:
  suggested fix:
  blocks merge: yes | no

## Validation
- command/source:
  result:
  caveat:

## Delta Re-Review State
- unresolved:
- settled:
- only re-review next:
- telemetry path:
```

## Success Criteria

- review states intent and scope.
- architecture fit is checked before line-level findings.
- blockers have evidence and suggested fix.
- optional local tests are run only when useful and safe.
- revision loop is bounded and delta-only.
- final human approval package exists for merge.
- `run-telemetry` event emitted.

## Common Failure Modes

**Review without context.** Fix by requiring grounding brief sources.

**Nit flood.** Fix by prioritizing blockers and majors; nits only when cheap and useful.

**Static-only confidence.** Fix by running focused validation when feasible.

**Autopilot approval.** Fix by routing final merge to `human-approval-gate`.

## Self-Improvement

Track missed defects, false blockers, author edit rate, re-review churn, and human overrides. Feed trends into `backprop` to improve review rubrics, council escalation rules, and test-selection heuristics.
