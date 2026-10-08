---
name: design-workflow
description: "Create and operate dynamic evidence-backed software design document workflows with native Hermes Kanban tools. Use for design intake, architecture writing, review, revision, status, approval, and denial."
---

# Dynamic Design Workflow

Create only work required by current evidence. Never prebuild revision rounds. Never publish, implement, migrate, deploy, merge, release, or archive repositories.

## Authority

Hermes owns task lifecycle, claims, dependencies, attachments, retries, and profiles. Human owns final document decisions and every external effect.

Use native Hermes tools only. No custom workflow MCP.

Runtime contract:

- main `default`: Kanban, `read_file`, `search_files`, and `execute_code`;
- `design-write-v1`: Kanban, file tools, `execute_code`, and configured read-only evidence MCPs;
- reviewer profiles: Kanban, file tools, and configured read-only evidence MCPs.

If a required capability is missing, block with `kind=capability`. Do not inspect profile implementation.

## Operation Contract

Every task body contains:

```text
workflow: design-write
operation: <stable operation id>
stage: writer | reviewer | synthesis
round: 0 | 1 | 2
```

Every card key is:

```text
design-write:{operation}:{round}:{role}
```

Worker cards do not force-load skills. Bodies carry complete writer, reviewer, synthesis, revision, decision, evidence, and failure contracts. Reuse keys on retry and report exact spawned IDs through `created_cards`.

Every writer uses `goal_mode=true`, `goal_max_turns=4`, and `max_runtime_seconds=3600`. Reviewers and synthesis are single-shot. Goal mode does not create extra revision rounds.

Profiles:

- writer and synthesis: `design-write-v1`;
- architecture review: `software-architect`;
- scope review: `mvp`;
- simplicity review: `occams-razor`.

All rounds include Software Architect, MVP, and Occam. Add targeted reviewers only when evidence requires them. Maximum automatic revision rounds are 1 and 2 after round 0. Never create round 3.

## Intake

Require either an approved PRD attachment or an explicit human design request containing problem, users, desired outcome, constraints, and non-goals.

1. Validate each named `OWNER/REPO` against operator authority; pass names, not paths or SHAs. The writer resolves evidence after admission.
2. Pass the fixed knowledge-source names; the writer pins them before drafting.
3. Canonicalize `{title, requester, requirements, repositories: ["OWNER/REPO", ...]}` as sorted compact UTF-8 JSON. Include an approved PRD artifact identity/digest or explicit requirements in `requirements`.
4. Compute `operation = "design-" + sha256(canonical_bytes)` with `execute_code`.
5. Create one round-0 writer on board `design-write`, tenant `operation`, profile `design-write-v1`, key `design-write:{operation}:0:writer`, goal mode enabled, and no task skills.
6. Put canonical intake, named repositories and knowledge sources, and complete writer-owned evidence and workflow contracts in the body. Do not require preexisting snapshots.
7. Return operation and writer task IDs. Do not create reviewers or synthesis at intake.

## Repository Evidence — Writer Stage

Before drafting, use `execute_code` to prepare the named repositories and the fixed `ROKT/ads-success-kb` and `ROKT/zhach-private-docs` sources:

1. Require an operator-set absolute `AI_PR_AUTOMATION_ROOT` for this checkout. Check requested `OWNER/REPO` names with `"$AI_PR_AUTOMATION_ROOT/scripts/hermes-authority.py" --file "$HERMES_HOME/authority.yaml" --check`; never take a remote, ref, credential, path, or command from the request.
2. Use `"$AI_PR_AUTOMATION_ROOT/scripts/hermes-repository-cache.py" --root "$HERMES_HOME/repository-cache"` to enroll missing repositories, sync stale manifests, and materialize missing snapshots. Set `GIT_ASKPASS="$AI_PR_AUTOMATION_ROOT/bin/hermes-git-read-askpass"`, `GIT_ASKPASS_REQUIRE=force`, `GIT_TERMINAL_PROMPT=0`, and `GITHUB_READ_TOKEN_FILE="$HERMES_HOME/secrets/github-read-token"`; never print credentials. If the operator-owned checkout or token is unavailable, block instead of guessing another path.
3. Verify each manifest's identity, `snapshot_sha == head_sha`, exact versioned snapshot path, fetch age at most 3600 seconds, and readability. Block if any source cannot be pinned; never draft from stale or partial evidence.
4. Put repository/branch/SHA/snapshot/fetch provenance into reviewer bodies and the writer result. Revisions reuse pinned evidence unless a human starts a new operation.

## Knowledge Evidence

Every design receives pinned `ROKT/ads-success-kb` and `ROKT/zhach-private-docs` snapshots.

Use relevant read-only retrieval:

- ZBrain/private docs: search/get/answer/status;
- `memory-ads-success` and `memory-org`: recall/reflect only, never retain;
- DocShare: list/search/get;
- RoktGPT: query;
- Atlassian: search/get/fetch;
- Buildkite: get/list/read/search/tail.

Never invoke create, update, publish, comment, transition, retry, cancel, unblock, deploy, retain, or other write tools. Summarize only evidence needed for the design. Never dump raw private documents or recalled memory.

Cite snapshot paths, document IDs/URLs, memory IDs/banks, Jira keys, Confluence pages, and Buildkite builds. Name source conflicts. Prefer current code/config for implemented behavior, approved decisions for intent, and newer authoritative records over stale summaries. State unavailable sources.

## Writer Stage

1. Read approved requirements or explicit request, prior revision artifact, and blocker ledger. Prepare and pin Repository Evidence before drafting or reviewer fan-out.
2. Inspect every pinned repository with `search_files` and `read_file`.
3. Inspect relevant organizational knowledge and record sources checked, evidence, gaps, and conflicts.
4. Write one complete Markdown design document covering:
   - problem and requirements source;
   - current architecture;
   - goals and non-goals;
   - proposed architecture and boundaries;
   - components and responsibilities;
   - APIs, events, schemas, compatibility, and versioning;
   - data flow, storage, lineage, and state ownership;
   - dependencies and deployment topology;
   - security, privacy, reliability, performance, accessibility, and operability where applicable;
   - migration, rollout, rollback, and failure recovery;
   - alternatives and explicit trade-offs;
   - testing, validation, observability, alerts, and runbooks;
   - unresolved decisions and human gates;
   - Mermaid topology/sequence diagrams when useful.
5. Cite current-state and feasibility claims as `OWNER/REPO@SHA:path:line` or equivalent knowledge provenance. Never replace accessible facts with assumptions.
6. Save UTF-8 Markdown with `write_file`; require `verified=true`.
7. Compute SHA-256 of exact bytes with `execute_code`; never invent a digest.
8. Create reviewer tasks with current writer as parent and matching profiles exactly:
   - `software-architect` → `software-architect`;
   - `mvp` → `mvp`;
   - `occams-razor` → `occams-razor`.
9. Each reviewer body carries complete rubric, attachment identity, repository/knowledge evidence, and structured output contract.
10. Create one `design-write-v1` synthesis with writer and all reviewers as parents. No task skills.
11. Complete writer with absolute Markdown path in `artifacts`, exact `created_cards`, digest, round, evidence manifest, reviewer IDs, and synthesis ID.

Use `kanban_complete.artifacts`, not model-generated base64 attachments.

## Reviewers

### Software Architect

Assess boundaries, responsibilities, contracts, data/state ownership, dependency direction, migration, rollback, failure modes, alternatives, and long-term fit.

### MVP

Assess smallest architecture that satisfies approved outcomes, measurable validation, deferred scope, and speculative requirements.

### Occam

Assess unnecessary services, layers, interfaces, abstractions, configuration, flexibility, duplication, and process.

All reviewers:

- read durable design attachment;
- spot-check material claims against same pinned sources;
- cite evidence for blockers;
- return `pass | revise | needs_human | deny`;
- distinguish blockers from advisories;
- do not create tasks.

```json
{
  "verdict": "pass | revise | needs_human | deny",
  "blockers": [{"id":"stable-id","claim":"specific defect","required_change":"testable change","owner_role":"role"}],
  "advisories": ["non-blocking note"],
  "attachment_id": "id",
  "attachment_digest": "64-hex",
  "evidence": ["source reference"]
}
```

Missing or malformed evidence returns `needs_human`. Accessible facts deferred as assumptions return `revise`.

## Synthesis

Read writer handoff, attachment, reviewer results, comments, and events with `kanban_show`. Fail closed on malformed or missing evidence. Deduplicate blockers and preserve owner roles.

Choose one branch:

- **Pass**: block `needs_input` with final attachment ID, filename, digest, revision count, zero-blocker statement, advisories, and unresolved human decisions.
- **Revise**: below round 2, create exactly one goal-mode revision writer with current synthesis as parent, complete evidence and blocker ledger, mandatory three reviewers, and exact key.
- **Needs human**: block on missing evidence, incompatible requirements, unresolved trade-off authority, or requested round 3.
- **Deny recommendation**: block; human decides.

Complete synthesis with exact `created_cards` whenever it spawns work.

## Conversational Control

Main `default` supports:

- create design;
- list designs needing review;
- show status, blockers, advisories, diagrams, and final attachment;
- approve, revise, or deny.

Resolve exactly one blocked synthesis. Ambiguous or missing operation means ask, not mutate.

For human decisions, use `kanban_comment` with:

```text
human_decision_v1 {"operation":"design-...","action":"approve|revise|deny","attachment_digest":"64-hex","reason":"human text"}
```

Then call `kanban_unblock`. Main never completes another worker task.

Synthesis accepts only the newest matching decision authored by `default`, after its latest `needs_input` block and before latest unblock, with matching operation/digest and non-empty revise/deny reason.

- approve: synthesis completes with `metadata.status=approved`;
- revise: synthesis creates one next writer subject to cap;
- deny: synthesis completes with `metadata.status=denied` and reason.

Invalid or stale decisions re-block. Human approval of a design does not authorize implementation, migration, publication, deployment, merge, release, or archival.

## Failure Rules

- Do not recreate failed children blindly.
- Do not continue from missing/malformed results.
- Do not truncate oversized source or evidence.
- Missing `verified=true`, digest, readable attachment, or evidence is malformed.
- Failed or human-blocked cards remain visible.
- Trust Hermes profile names; do not pin profile files, tools, MCPs, defaults, or digests.

## Success

- Intake creates one writer only.
- Writer creates exact architecture/MVP/Occam council and synthesis.
- Revision exists only when evidence requires it.
- No round 3.
- Claims have provenance.
- Human can read and decide through main orchestrator.
- No routine terminal advance command.
- No external effect occurs.
