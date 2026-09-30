---
name: prd-workflow
description: "Create and operate dynamic PRD workflows with native Hermes Kanban tools. Use for PRD intake, writing, council review, synthesis, revision, status, approval, and denial."
---

# Dynamic PRD Workflow

Create only work required by current evidence. Never prebuild possible revision rounds. Never use an external advance command during normal operation.

## Authority

Hermes owns task lifecycle, claims, runs, dependencies, attachments, retries, and profiles. Human owns final approval, denial, publication, credentials, and destructive actions.

Use only native Hermes tools. Do not call a custom workflow MCP. Do not publish the document.

Runtime capability contract:

- main orchestrator `default`: Kanban, `read_file`, and `execute_code`;
- `prd-write-v1`: Kanban, `write_file`, `read_file`, and `execute_code`;
- reviewer profiles: Kanban and `read_file`.

If a required capability is unavailable, block with `kind=capability`. Do not inspect profile implementation to preflight this contract.

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

Kanban worker tasks do not force-load skills. Every writer, reviewer, and synthesis body carries its complete stage contract. `prd-workflow` guides conversational intake; task bodies are the durable worker interface.

Reuse the same key on retry. Adopt the returned task ID. Never invent a task ID.

Use these profiles:

- writer and synthesis: `prd-write-v1`;
- product review: `product-pm`;
- scope review: `mvp`;
- simplicity review: `occams-razor`.

Every review round includes `mvp` and `occams-razor`. Round 0 also includes `product-pm`. Later rounds include `product-pm` only while it owns an unresolved blocker. Deduplicate roles.

## Intake

When a human asks to create a PRD:

1. Require a clear title, problem, intended users, desired outcome, and known constraints. Ask one focused question when essential input is missing.
2. Use `execute_code` to canonicalize `{title, requester, requirements}` as sorted compact UTF-8 JSON and compute `operation = "prd-" + sha256(canonical_bytes)`.
3. Create only the round-0 writer task with `kanban_create` on board `prd-write` and tenant `operation`.
4. Assign `prd-write-v1`; use idempotency key `prd-write:{operation}:0:writer`; do not set task `skills`.
5. Put the canonical intake plus complete writer, reviewer, synthesis, revision, human-decision, and failure contracts in the task body.
6. Return operation ID and writer task ID. Do not create reviewers or synthesis at intake.

## Writer Stage

1. Read source request and, for revisions, prior attachment with `read_file` plus blocker ledger.
2. Write one complete PRD. Preserve supported requirements. Do not invent business facts.
3. Use `write_file` to save UTF-8 Markdown at an absolute path inside current scratch workspace. Require `verified=true`.
4. Use `execute_code` with Python `hashlib.sha256` to hash the exact saved bytes. Never invent or use a placeholder digest.
5. Create required reviewer tasks. Assign profiles exactly: `product-pm` role to `product-pm`, `mvp` role to `mvp`, and `occams-razor` role to `occams-razor`. Each reviewer task:
   - names one role and rubric;
   - has current writer task as parent;
   - uses same operation and round;
   - identifies source filename and declared digest;
   - instructs reviewer to use `read_file` on writer attachment from parent context;
   - requires structured `pass | revise | needs_human | deny` output.
6. Create one synthesis task after reviewer IDs are known. Give it current writer and every reviewer as parents. Assign `prd-write-v1`, do not set task `skills`, and put the complete synthesis and revision contract in its body.
7. Complete writer through `kanban_complete` with the absolute Markdown path in `artifacts`. Result must include filename, digest, round, reviewer roles, synthesis ID, and exact `created_cards` list.

Do not use `kanban_attach` for generated text. `kanban_complete.artifacts` preserves the verified workspace file as the durable attachment before dependents run.

Required council:

- `product-pm`: user value, problem evidence, outcomes, acceptance, product risk;
- `mvp`: smallest useful scope, measurable signal, unnecessary requirements;
- `occams-razor`: accidental complexity, speculative flexibility, excess process.

## Reviewer Stage

Review only assigned rubric. Read writer result, then use `read_file` on the durable writer attachment path in parent context. Do not create tasks.

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

Read writer result, attachment metadata, all reviewer results, current-task comments, and recent events with `kanban_show`. Fail closed when required evidence is missing or malformed.

If this task resumed from a human `needs_input` block, apply the Human Decision Protocol before reviewer synthesis. Otherwise build one blocker ledger. Deduplicate equivalent blockers. Record owner role for each unresolved blocker.

Choose exactly one branch:

### Approve for human

Use when all required reviewers pass and no blocker remains. Call `kanban_block` with `kind=needs_input`. Reason must include final attachment ID, filename, digest, revision count, zero-blocker statement, and residual advisories. Do not approve on human's behalf.

### Revise

Use when blockers are concrete and revision cap remains.

1. Create only one next-round writer task.
2. Make current synthesis its parent.
3. Assign `prd-write-v1`, do not set task `skills`, and copy the complete writer, reviewer, synthesis, revision, and failure contract into its body.
4. Include final source attachment identity, blocker ledger, resolved findings, and required reviewer roles in body. The revision writer reads attachment content with `read_file`.
5. Required roles are `mvp`, `occams-razor`, plus unresolved blocker owners.
6. Complete current synthesis with new writer ID in exact `created_cards`.

Next writer creates its own council and synthesis after producing revised attachment.

### Needs human

Use for missing evidence, malformed results, conflicting verdicts without safe resolution, policy uncertainty, or a requested third revision. Call `kanban_block` with `kind=needs_input`. State exact question and evidence.

### Deny recommendation

Use only when reviewer evidence says workflow should stop rather than revise. Call `kanban_block` with `kind=needs_input`. Human decides denial.

Never create round 3. Maximum automatic revision rounds are 1 and 2 after round 0.

## Conversational Control

The main `default` orchestrator supports these intents:

- create a PRD through Intake;
- list PRDs needing review with `kanban_list` filtered to board `prd-write` and human-blocked synthesis tasks;
- show status with `kanban_show`, including current round, blockers, final attachment ID, filename, digest, and advisories;
- show final document by reading the durable attachment with `read_file` when requested;
- approve, revise, or deny through the Human Decision Protocol.

Resolve a decision to exactly one operation and blocked synthesis task. If a title matches zero or multiple operations, ask the human; do not mutate any card.

## Human Decision Protocol

`default` is the only conversational decision-author profile. After the human clearly approves, revises, or denies:

1. Verify the exact operation, blocked synthesis task, current final attachment digest, and revision round with `kanban_show`.
2. Use `kanban_comment` to add one comment to that synthesis task with this exact prefix and compact JSON:

```text
human_decision_v1 {"operation":"prd-...","action":"approve|revise|deny","attachment_digest":"64-hex","reason":"human text"}
```

3. Call `kanban_unblock` on the same task. Never call `kanban_complete` on another worker's task.

On its resumed run, synthesis accepts a decision only when all conditions hold:

- newest decision comment author is exactly `default`;
- comment follows this task's latest `needs_input` block and precedes the latest unblock event;
- operation and attachment digest match current task evidence;
- action is `approve`, `revise`, or `deny`;
- `reason` is non-empty for revise and deny.

Then synthesis acts:

- `approve`: complete itself with `metadata.status=approved`, final attachment identity, revision count, and residual advisories;
- `revise`: create exactly one next writer with the human reason added to its blocker ledger, subject to round cap, then complete with exact `created_cards`;
- `deny`: complete itself with `metadata.status=denied` and the human reason.

Invalid, stale, mismatched, or non-`default` comments are not authority. Re-block `needs_input` with the exact validation failure. Never silently reopen failed or denied work. Human-needed work remains visible on the same synthesis card.

## Retry and Failure Rules

- Reuse deterministic idempotency keys on every retry.
- Report every spawned ID through `created_cards` when completing creator task.
- Do not recreate failed children blindly.
- Do not continue from a missing or malformed result.
- Do not truncate an oversized source. Block and report exact limit.
- A missing `verified=true`, missing SHA-256, placeholder digest, or unreadable attachment is malformed evidence. Block instead of continuing.
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
