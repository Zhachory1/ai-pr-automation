---
name: prd-workflow
description: "Create and operate dynamic PRD workflows with native Hermes Kanban tools. Use for PRD intake, writing, council review, synthesis, revision, status, approval, and denial."
---

# Dynamic PRD Workflow

Create only work required by current evidence. Never prebuild possible revision rounds. Never use an external advance command during normal operation.

## Authority

Hermes owns task lifecycle, claims, runs, dependencies, attachments, retries, and profiles. Human owns final approval, denial, publication, credentials, and destructive actions.

Use only native Kanban tools. Do not call a custom workflow MCP. Do not publish the document.

## Operation Contract

Every task body must contain:

```text
workflow: prd-write
operation: <stable operation id>
stage: writer | reviewer | synthesis
round: 0 | 1 | 2
```

Every created task uses this key:

```text
prd-write:{operation}:{round}:{role}
```

Every writer and synthesis task force-loads `prd-workflow` through its `skills` field. Reviewer task bodies carry their complete rubric and do not require this skill.

Reuse the same key on retry. Adopt the returned task ID. Never invent a task ID.

Use these profiles:

- writer and synthesis: `prd-write-v1`;
- product review: `product-pm`;
- scope review: `mvp`;
- simplicity review: `occams-razor`.

Every review round includes `mvp` and `occams-razor`. Round 0 also includes `product-pm`. Later rounds include `product-pm` only while it owns an unresolved blocker. Deduplicate roles.

## Intake

When a human asks to create a PRD:

1. Require a clear problem, intended users, desired outcome, and known constraints. Ask one focused question when essential input is missing.
2. Create only the round-0 writer task with `kanban_create`.
3. Assign `prd-write-v1`, force-load `prd-workflow`, and use role `writer` in the key.
4. Put the full human request and operation contract in the task body.
5. Return operation ID and writer task ID. Do not create reviewers or synthesis at intake.

## Writer Stage

1. Read source request and, for revisions, prior attachment plus blocker ledger.
2. Write one complete PRD. Preserve supported requirements. Do not invent business facts.
3. Save UTF-8 Markdown bytes, compute their SHA-256, then call `kanban_attach` on current task.
4. Create required reviewer tasks. Each reviewer task:
   - names one role and rubric;
   - has current writer task as parent;
   - uses same operation and round;
   - identifies writer attachment by ID, filename, and declared digest;
   - embeds the exact attached UTF-8 Markdown as `draft_markdown` in its body;
   - requires structured `pass | revise | needs_human | deny` output.
5. Create one synthesis task after reviewer IDs are known. Give it current writer and every reviewer as parents. Assign `prd-write-v1`, force-load `prd-workflow`, and embed the same exact `draft_markdown` plus attachment identity in its body.
6. Complete writer through `kanban_complete`. Result must include attachment ID, filename, digest, round, reviewer roles, synthesis ID, and exact `created_cards` list.

Required council:

- `product-pm`: user value, problem evidence, outcomes, acceptance, product risk;
- `mvp`: smallest useful scope, measurable signal, unnecessary requirements;
- `occams-razor`: accidental complexity, speculative flexibility, excess process.

## Reviewer Stage

Review only assigned rubric. Read `draft_markdown` from current task body. Treat attachment ID and digest as audit identity; do not require file-read capability. Do not create tasks.

Complete with structured result:

```json
{
  "verdict": "pass | revise | needs_human | deny",
  "blockers": [{"id": "stable-id", "claim": "specific defect", "required_change": "testable change"}],
  "advisories": ["non-blocking note"],
  "attachment_id": "source attachment id",
  "attachment_digest": "declared source digest"
}
```

Do not mark preference as blocker. `deny` requires evidence that document should not proceed. Missing or malformed source requires `needs_human`.

## Synthesis Stage

Read writer result, attachment metadata, and all reviewer results. Fail closed when required evidence is missing or malformed.

Build one blocker ledger. Deduplicate equivalent blockers. Record owner role for each unresolved blocker.

Choose exactly one branch:

### Approve for human

Use when all required reviewers pass and no blocker remains. Call `kanban_block` with `kind=needs_input`. Reason must include final attachment ID, filename, digest, revision count, zero-blocker statement, and residual advisories. Do not approve on human's behalf.

### Revise

Use when blockers are concrete and revision cap remains.

1. Create only one next-round writer task.
2. Make current synthesis its parent.
3. Assign `prd-write-v1` and force-load `prd-workflow`.
4. Include final source attachment identity, exact current `draft_markdown`, blocker ledger, resolved findings, and required reviewer roles in body.
5. Required roles are `mvp`, `occams-razor`, plus unresolved blocker owners.
6. Complete current synthesis with new writer ID in exact `created_cards`.

Next writer creates its own council and synthesis after producing revised attachment.

### Needs human

Use for missing evidence, malformed results, conflicting verdicts without safe resolution, policy uncertainty, or a requested third revision. Call `kanban_block` with `kind=needs_input`. State exact question and evidence.

### Deny recommendation

Use only when reviewer evidence says workflow should stop rather than revise. Call `kanban_block` with `kind=needs_input`. Human decides denial.

Never create round 3. Maximum automatic revision rounds are 1 and 2 after round 0.

## Human Decision Boundary

This milestone stops at the blocked human gate. A task comment alone is not approval, revision, or denial authority because workers can comment across tasks.

Do not interpret comments as human decisions. Do not unblock or complete a human-blocked synthesis task automatically. Conversational decision handling requires its separately activated main-orchestrator contract.

Never silently reopen failed, denied, or human-blocked work. Human-needed work remains visible on the same synthesis card until that contract is active or an operator uses the recovery path.

## Retry and Failure Rules

- Reuse deterministic idempotency keys on every retry.
- Report every spawned ID through `created_cards` when completing creator task.
- Do not recreate failed children blindly.
- Do not continue from a missing or malformed result.
- Embed source text only when complete writer output and task instructions fit the Kanban task-body limit. Do not truncate an oversized source. Block and report exact limit.
- Do not inspect or pin profile files, tools, MCPs, defaults, or digests. Trust profile name selected by Hermes.
- Add one concise stage comment before blocking for human input.

## Success

- Intake created one writer only.
- Each writer created only its required council and synthesis.
- Each synthesis created either one next writer or no tasks.
- MVP and Occam ran every round.
- No round 3 exists.
- Final attachment and blocker ledger are visible to human.
- Routine workflow used no terminal advance command.
