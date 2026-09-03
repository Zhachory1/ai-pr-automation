---
name: human-approval-gate
description: "Prepare decision package for qualified human signoff. Use for PR launch, merge/no-merge, roadmap approval, ML deploy handoff, metric changes, council blockers, destructive actions, production risk, budget cap escalation. Produces concise yes/no ask, recommendation, evidence, risks, alternatives, rollback, and telemetry; never auto-approves accountable decisions."
---

# Human Approval Gate

Create a concise decision package for a qualified human. Use when accountability, risk, budget, roadmap commitment, merge, deploy, metric change, or destructive action requires human ownership.

## Core Principles

**Ask one explicit question.** The human should answer yes/no or choose among named options.

**Recommendation is allowed, approval is not.** Agent may recommend; human owns the decision.

**Surface evidence and risk.** Include proof, caveats, blockers, rollback, and cost of waiting.

**Do not bury dissent.** Council dissents and unresolved concerns stay visible.

**Escalation stops loops.** When budget caps are hit, ask a human instead of continuing agent churn.

## Inputs

- handoff package or review result.
- decision owner and required approval type.
- council verdicts, validation, metrics, risks, alternatives.
- deadline, reversibility, rollback options.
- run id for `run-telemetry`.

## Execution

**Identify approval owner.** State who is qualified to decide and why.

**Frame the ask.** Convert workflow state into one decision: approve, reject, choose option, extend budget, or send back.

**Summarize evidence.** Use the smallest set of proof needed: metrics, tests, council verdicts, PR links, artifacts, RCA evidence.

**State risks.** Include blockers, unresolved questions, guardrail status, rollback plan, and downside of delay.

**Recommend.** Give agent recommendation with confidence and conditions.

**Emit telemetry.** Record approval type, risk level, evidence count, recommendation, and human response when known via `run-telemetry`.

## Output Contract

```markdown
# Human Approval Request

## Ask
- decision needed:
- owner:
- deadline:
- recommendation:
- confidence:

## Why This Is Ready Or Not
- evidence:
- council/review verdict:
- success criteria status:
- validation status:

## Risks
- risk:
  likelihood:
  impact:
  mitigation:
  rollback:

## Alternatives
- option:
  tradeoff:

## Explicit Response Options
- approve:
- reject:
- revise:
- extend budget:

## If Approved
- next action:
- owner:
- monitoring/follow-up:
```

## Workflow Uses

- `plan-to-launch`: final launch PR and merge readiness.
- `pr-review`: human merge accountability.
- `roadmapping`: leadership commitment.
- `ml-experiments`: deploy/promote model or extend compute.
- `debug-investigation`: escalate when evidence exhausted or fix risk is high.
- `backprop`: promote/rollback workflow changes.

## Success Criteria

- one clear decision ask.
- recommendation and confidence are explicit.
- evidence links support the ask.
- risks and rollback are visible.
- no agent auto-approval of human-owned decision.
- `run-telemetry` event emitted.

## Common Failure Modes

**Approval buried in prose.** Fix by putting ask first.

**Recommendation without evidence.** Fix by linking handoff package and validation.

**False certainty.** Fix by confidence and caveats.

**Loop cap ignored.** Fix by escalating once cap is reached.

## Self-Improvement

Track decisions where humans overrode the agent, requested missing evidence, or flagged unclear asks. Feed patterns into `backprop` to improve approval packages and escalation timing.
