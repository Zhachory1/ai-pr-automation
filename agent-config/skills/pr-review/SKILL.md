---
name: pr-review
description: "Agent PR review workflow for correctness, safety, maintainability, architecture, security, performance, tests, and fidelity to intent. A target PR URL defaults to publishing a GitHub review with the verdict-appropriate event unless the user asks for read-only, draft-only, report-only, or no posting. Produces prioritized findings, delta re-review, human merge recommendation, telemetry, and code RAG context."
---

# PR Review

> **AGENT-FLEET OVERRIDE (unattended containerized runs):** this deployment NEVER submits `APPROVE`.
> Map every non-blocking verdict to `COMMENT` and every blocking verdict to `REQUEST_CHANGES`.
> Ignore the upstream "submit APPROVE when no findings / verdict mapping" lines below — the
> agent-server prompt is authoritative and forbids APPROVE. Use the marker
> `<!-- ai-pr-automation head=<sha> -->` (not the autopraxis-pr-review marker) for idempotency.

Review a pull request against real intent, not guesswork. Produce prioritized, actionable feedback and preserve human accountability for merge.

## Core Principles

**Intent anchors review.** Gather ticket, PRD/DD, issue, roadmap goal, and author notes before judging code.

**Architecture before nits.** Check boundaries, coupling, abstractions, and scope creep before line-level style.

**Findings need evidence.** Blocking comments require file/line, behavior, risk, and suggested fix.

**Re-review the delta.** After author changes, focus on modified code and unresolved findings.

**Human owns merge.** Agent review submits GitHub `REQUEST_CHANGES` or `COMMENT` according to its evidence-backed verdict (never `APPROVE` in this deployment), and it never merges. Accountable merge remains human-owned.

**A target PR URL implies review delivery.** When the user invokes `pr-review` to review a specific pull-request URL, default to publishing one GitHub review on that PR. Do not silently downgrade to chat-only output. Incidental links do not trigger posting. Explicit `read-only`, `draft-only`, `report-only`, or `do not post` language overrides this default.

## Inputs

- PR URL, branch, diff, or patch.
- ticket/design docs and acceptance criteria.
- repo context and test commands.
- prior review comments and author responses.
- optional local validation budget.
- delivery override: `post` (default for PR URL) or `report-only`.
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

## Delivery Contract

- **Target PR URL input:** `post` mode when the user asks to review that PR, unless the user explicitly requests read-only, draft-only, report-only, or no posting. Incidental or ambiguous PR links require clarification rather than a write.
- **Diff, patch, or local branch without a PR URL:** `report-only` mode unless the user explicitly asks to publish to a named PR.
- **No write access or authentication:** return `blocked` delivery status with the exact missing capability; do not pretend the review was posted.
- **PR author reviewing own PR:** GitHub does not permit self-approval or self-request-changes. Submit `COMMENT` with the verdict recommendation. If GitHub rejects review submission, return blocked rather than falling back to an unpinned issue comment.
- **Verdict mapping (agent-fleet: never APPROVE):** `approve`, `approve-with-nits`, and `needs-info` submit `COMMENT`; `request-changes` and `block` submit `REQUEST_CHANGES`. This deployment never submits `APPROVE`. The body must preserve the actual recommendation.
- **No findings:** submit `COMMENT` stating no blockers (never `APPROVE`).
- **Blocking findings:** publish actionable inline comments and a `REQUEST_CHANGES` summary review when permitted; otherwise submit `COMMENT` with the blocking recommendation.
- **Prior-feedback ledger / avoid duplicates:** before analysis, retrieve every paginated issue comment, review, inline review comment, thread reply, and thread resolution/outdated state. Normalize them into a private ledger by concern, file/line, author response, and status. Treat every prior concern as covered—resolved, dismissed, replied to, or open—and do not repeat it. Revisit an area only when code changed or materially new evidence/risk exists; state what changed and reference the earlier feedback. Prefer one submitted review containing all new inline findings over several separate comments.
- **Idempotency:** add exactly `<!-- ai-pr-automation head=<sha> -->` (NOT an autopraxis-pr-review marker). Search all paginated reviews/comments authored by this account for that exact marker before posting and again after an ambiguous API failure; return the existing review URL instead of retrying.
- **Preview before publish:** write the exact review body to a file or structured API payload and preview it before posting. Preserve Markdown literally; never build multi-line bodies through shell interpolation.
- **Head safety:** capture the reviewed head SHA. Immediately before publishing, fetch the head again. If it changed, re-review the delta before posting. Submit the review with `commit_id` pinned to the verified reviewed SHA, verify the API response commit, then re-read the PR head and report if the posted review was superseded.
- **Published result:** capture the GitHub review/comment URL and include it in the final response.

## Execution

**Gather context.** Use `grounding-brief` to understand intent, scope, changed files, related docs, tests, CI, and all prior comments, replies, and thread states. Build the prior-feedback ledger before forming findings and determine the requested/default delivery mode.

**Architecture check.** Judge layer placement, boundaries, abstractions, coupling, data contracts, rollout, observability, and scope creep.

**Deep dive.** Inspect correctness, edge cases, error handling, security, performance, concurrency, data migrations, tests, docs, and maintainability.

**Local test when useful.** Run focused tests or static checks if feasible, cheap, documented, and relevant. Do not run destructive or broad expensive commands without approval.

**Construct feedback.** Compare every candidate against the prior-feedback ledger before drafting. Skip concerns already raised; when materially new code or evidence changes a previously discussed area, explain that delta and reference the earlier feedback. Prioritize blockers, majors, minors, and nits. Make every comment actionable. Map findings to current diff lines where possible.

**Publish feedback.** In `post` mode, re-check the head SHA, map the verdict to the GitHub event, compute/check the idempotency marker, preview the exact body/payload, then submit one GitHub review pinned with `commit_id` and containing new inline findings plus summary verdict. Verify the response commit, re-read the PR head, and capture the resulting URL. In `report-only` mode, state that nothing was posted.

**Author loop.** After revisions, re-review only changed files, unresolved findings, and newly introduced risk. Bound iterations. Do not post stale findings against a superseded head.

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
- reviewed head SHA:
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

## Delivery
- mode: post | report-only
- status: posted | report-only | skipped-by-user | blocked | failed
  - `posted`: GitHub review published for target PR URL
  - `report-only`: no target PR URL existed, so publishing was not requested by contract
  - `skipped-by-user`: target PR URL existed, but user explicitly said read-only, draft-only, report-only, no posting, or do not post
  - `blocked`: posting required but authentication, permission, or GitHub review submission was unavailable
  - `failed`: posting was attempted but failed after idempotent recovery checks
- GitHub event: REQUEST_CHANGES | COMMENT | none
- GitHub review/comment URL:
- posted head SHA:
- prior-feedback ledger: reviewed | unavailable
- duplicate findings skipped (with prior-comment references):

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
- target PR URL review invocations publish a verdict-appropriate GitHub review by default unless the user explicitly opts out.
- published feedback is pinned to the verified reviewed head, idempotent across retries, and its URL is reported.
- all available prior PR feedback is checked before drafting; already raised concerns are not repeated unless a documented code or evidence delta warrants it.
- final human approval package exists for merge.
- `run-telemetry` event emitted.

## Common Failure Modes

**Review without context.** Fix by requiring grounding brief sources.

**Nit flood.** Fix by prioritizing blockers and majors; nits only when cheap and useful.

**Static-only confidence.** Fix by running focused validation when feasible.

**Chat-only review for a target PR URL.** Fix by treating the requested PR as `post` mode, publishing the verified verdict-appropriate review, and returning its URL unless the user explicitly opted out.

**Stale-head posting.** Fix by delta-reviewing head changes, pinning submission with `commit_id`, verifying the response commit, and reporting a post-submit supersession.

**Duplicate review after ambiguous failure.** Fix by using a deterministic marker and searching all paginated reviews/comments before retrying.

**Repeat of earlier feedback.** Fix by building and checking the prior-feedback ledger before drafting. Skip the concern unless new code or material evidence changes it, then cite that delta and the prior feedback.

**Autopilot merge.** `REQUEST_CHANGES` or `COMMENT` records the review verdict; neither permits the agent to merge. Route merge readiness to `human-approval-gate`; never merge automatically.

## Self-Improvement

Track missed defects, false blockers, author edit rate, re-review churn, and human overrides. Feed trends into `backprop` to improve review rubrics, council escalation rules, and test-selection heuristics.
