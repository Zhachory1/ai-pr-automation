---
name: handoff-packaging
description: "Package workflow outputs for the next agent or human. Use for launch PR package, ML artifacts handoff, debug RCA handoff, roadmap approval packet, project ideation handoff, backprop changelog, implementation handoff. Produces summary, evidence, decisions, files, artifacts, metrics, known limitations, next steps, and telemetry."
---

# Handoff Packaging

Create a standard artifact bundle that makes workflow output consumable by the next agent, reviewer, approver, or operator.

## Core Principles

**Handoff transfers accountability context.** Include what was done, why, evidence, known limits, and what remains.

**Evidence beats narrative.** Link tests, metrics, logs, model artifacts, council verdicts, PR diffs, and source docs.

**Limitations are part of the output.** Hidden caveats become downstream failures.

**Next action must be obvious.** The recipient should know whether to review, approve, deploy, continue, or stop.

**Telemetry travels with work.** Include `run-telemetry` summary so `backprop` can evaluate workflow health.

## Inputs

- workflow name and run id.
- grounding brief and source artifacts.
- docs, plans, PRs, diffs, models, notebooks, logs, dashboards, council outputs.
- validation results and success metrics.
- known limitations and open questions.
- target recipient and requested decision.

## Execution

**Summarize outcome.** State what changed or was learned in one short paragraph.

**Attach evidence.** Link exact files, commits, PRs, tests, metrics, council transcripts, experiment artifacts, RCA evidence.

**State decisions.** List key choices, alternatives rejected, and owner.

**Expose limitations.** Record caveats, unresolved risks, data constraints, rollout risks, and follow-ups.

**Package next action.** Route to `human-approval-gate`, `ship`, launch PR, deployment, roadmap owner, or further investigation.

**Emit telemetry.** Record package completeness, artifact count, validation state, open-risk count, and handoff recipient via `run-telemetry`.

## Output Contract

```markdown
# Handoff Package

## Summary
- workflow:
- run id:
- outcome:
- recommendation:

## Evidence
- artifact:
  type:
  pointer:
  result:

## Decisions
- decision:
  rationale:
  owner:
  source:

## Validation
- check:
  command/source:
  result:
  caveat:

## Known Limitations
- limitation:
  impact:
  mitigation or owner:

## Telemetry Summary
- latency:
- cost/tokens:
- loop count:
- escalations:
- human edits:

## Next Action
- ask:
- owner:
- deadline:
- approval gate:
```

## Workflow Uses

- `plan-to-launch`: launch PR package with docs, rationale, tests, council verdicts.
- `ml-experiments`: model/code/data lineage/metrics bundle for deployment or further research.
- `debug-investigation`: RCA, fix evidence, prevention actions.
- `project-ideation`: framed candidate for roadmap scoring.
- `roadmapping`: approval packet with tradeoffs and capacity assumptions.
- `backprop`: proposed workflow change, measured cause, A/B result, changelog.

## Success Criteria

- recipient can act without re-reading full transcript.
- every evidence claim has pointer.
- limitations and open questions are explicit.
- next decision and owner are clear.
- `run-telemetry` event emitted.

## Common Failure Modes

**Victory lap without caveats.** Fix by requiring known limitations.

**Artifact soup.** Fix by tagging each artifact with purpose and result.

**Missing decision ask.** Fix by routing to `human-approval-gate` with exact yes/no.

**Telemetry dropped.** Fix by including run summary or stating unavailable fields.

## Self-Improvement

Track handoffs that caused recipient confusion, missing evidence requests, or rework. Feed patterns into `backprop` to improve package fields and required artifacts.
