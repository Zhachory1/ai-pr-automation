---
name: roadmap-workflow
description: "Create and operate dynamic evidence-backed roadmap writing workflows with native Hermes Kanban tools. Use for roadmap intake, prioritization, review, revision, status, approval, and denial."
---

# Dynamic Roadmap Workflow

Create only work required by evidence. Never prebuild revision rounds. Never treat roadmap approval as a team, budget, date, launch, publication, implementation, migration, deployment, merge, release, or archival commitment.

## Authority

Hermes owns tasks, claims, dependencies, attachments, retries, and profiles. Human owns roadmap decisions and every external effect.

Use native Hermes tools only. No custom workflow MCP.

Runtime contract:

- main `default`: Kanban, file tools, and `execute_code`;
- `roadmap-write-v1`: Kanban, file tools, `execute_code`, and configured read-only evidence MCPs;
- reviewers: Kanban, file tools, and configured read-only evidence MCPs.

Missing capability blocks with `kind=capability`. Do not inspect profile internals.

## Operation Contract

Every body contains:

```text
workflow: roadmap-write
operation: <stable operation id>
stage: writer | reviewer | synthesis
round: 0 | 1 | 2
```

Keys:

```text
roadmap-write:{operation}:{round}:{role}
```

Worker cards do not force-load skills. Bodies carry complete contracts. Reuse keys on retry and report exact spawned IDs through `created_cards`.

Writers use `goal_mode=true`, `goal_max_turns=4`, and `max_runtime_seconds=3600`. Reviewers and synthesis are single-shot. Maximum automatic revisions are rounds 1 and 2 after round 0. Never create round 3.

Profiles:

- writer/synthesis: `roadmap-write-v1`;
- product review: `product-pm`;
- execution review: `vp-eng`;
- portfolio scope: `mvp`;
- simplicity: `occams-razor`.

All four reviewers run every round. Add targeted reviewers only when evidence requires them.

## Intake

Require explicit human goals or approved source documents. Inputs can include PRDs, designs, OKRs, customer evidence, incidents, capacity, dependencies, and operational constraints.

1. Resolve named repositories through Repository Evidence.
2. Resolve organizational knowledge through Knowledge Evidence.
3. Canonicalize title, requester, goals, constraints, source identities, repository SHAs, and knowledge SHAs as sorted compact UTF-8 JSON.
4. Compute `operation = "roadmap-" + sha256(canonical_bytes)`.
5. Create one goal-mode `roadmap-write-v1` writer on board `roadmap-write`, tenant `operation`, key `roadmap-write:{operation}:0:writer`, no task skills.
6. Put canonical intake and complete contracts in writer body.
7. Return operation/writer IDs. Do not create reviewers or synthesis at intake.

## Repository Evidence

For each named `OWNER/REPO`, read `/Users/Shared/ai-pr-automation-runtime/repositories/OWNER/REPO.json`.

Require exact identity, `snapshot_sha == head_sha`, versioned snapshot path, fetch age at most 3600 seconds, and readable snapshot. Record repository, branch, SHA, snapshot, fetched time, and age.

Agents never clone, fetch, or receive credentials. Missing/stale evidence blocks intake.

## Knowledge Evidence

Every roadmap receives pinned `ROKT/ads-success-kb` and `ROKT/zhach-private-docs` snapshots.

Use relevant read-only retrieval:

- ZBrain/private docs: search/get/answer/status;
- `memory-ads-success` and `memory-org`: recall/reflect only, never retain;
- DocShare: list/search/get;
- RoktGPT: query;
- Atlassian: search/get/fetch;
- Buildkite: get/list/read/search/tail.

Never invoke create, update, publish, comment, transition, retry, cancel, unblock, deploy, retain, or another write tool. Summarize only necessary evidence; never dump raw private documents or memory.

Cite snapshot paths, document IDs/URLs, memory IDs/banks, Jira keys, Confluence pages, and builds. Surface conflicts and unavailable sources. Prefer code/config for implementation state, approved decisions for intent, measured data for outcomes, and explicit current staffing/capacity records over guesses.

## Writer Stage

1. Read human goals, source documents, previous artifact, and blocker ledger.
2. Inspect pinned repositories and relevant organizational knowledge.
3. Record evidence checked, gaps, conflicts, and assumptions with owners.
4. Write one Markdown roadmap covering:
   - planning horizon and decision date;
   - goals, outcomes, and measurable success;
   - evidence, baselines, and assumptions;
   - candidate initiatives and explicit exclusions;
   - prioritization method and score rationale;
   - now/next/later sequencing;
   - dependencies and critical path;
   - capacity and staffing assumptions;
   - milestones, launch gates, and decision points;
   - risks, guardrails, rollback and de-scope options;
   - metrics, measurement owner, and review cadence;
   - unresolved decisions and accountable humans;
   - Mermaid dependency/timeline diagrams where useful.
5. Cite current-state, effort, dependency, capacity, and outcome claims. Do not fabricate precision.
6. Save UTF-8 Markdown with `write_file`; require `verified=true`.
7. Compute exact SHA-256 with `execute_code`.
8. Create matching reviewer tasks with current writer as parent:
   - `product-pm` → `product-pm`;
   - `vp-eng` → `vp-eng`;
   - `mvp` → `mvp`;
   - `occams-razor` → `occams-razor`.
9. Each reviewer body includes complete rubric, artifact identity, source evidence, and structured output contract.
10. Create one `roadmap-write-v1` synthesis with writer and all reviewers as parents.
11. Complete writer with artifact path, digest, evidence manifest, exact `created_cards`, reviewer IDs, and synthesis ID.

Use `kanban_complete.artifacts`, never model-generated base64.

## Reviewers

### Product PM

Assess user/company value, outcome clarity, evidence strength, prioritization, success metrics, and whether selected initiatives address real needs.

### VP Engineering

Assess capacity, staffing assumptions, ownership, dependencies, critical path, sequencing, delivery realism, operational load, and cross-team coordination.

### MVP

Assess smallest portfolio that creates useful signal, measurable learning, de-scope options, and initiatives that can wait.

### Occam

Assess unnecessary initiatives, process, coupling, abstraction, parallel work, speculative flexibility, and review overhead.

All reviewers read the artifact and spot-check claims against pinned sources. Evidence-backed blockers require citations. Accessible facts deferred as discovery return `revise`. Do not create tasks.

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

Missing or malformed evidence returns `needs_human`.

## Synthesis

Read writer handoff, artifact, all four reviewer results, comments, and events. Deduplicate blockers and preserve owners.

Choose one branch:

- **Pass**: block `needs_input` with attachment identity, revision count, zero blockers, advisories, planning assumptions, and unresolved human decisions.
- **Revise**: below round 2, create one goal-mode revision writer with current synthesis as parent and all four mandatory reviewers.
- **Needs human**: block on missing evidence, conflicting goals, absent capacity authority, unresolved priority authority, or requested round 3.
- **Deny recommendation**: block; human decides.

Complete with exact `created_cards` whenever spawning work.

## Conversational Control

Main `default` supports create, list, show, approve, revise, and deny. Resolve one blocked synthesis; ambiguity means ask, not mutate.

Decision comment:

```text
human_decision_v1 {"operation":"roadmap-...","action":"approve|revise|deny","attachment_digest":"64-hex","reason":"human text"}
```

Main calls `kanban_comment`, then `kanban_unblock`; it never completes another worker task.

Synthesis accepts only newest matching `default` decision after latest `needs_input` block and before latest unblock, with matching operation/digest and required reason.

- approve: complete with `metadata.status=approved`;
- revise: create one next writer under cap;
- deny: complete with `metadata.status=denied` and reason.

Invalid/stale decisions re-block. Approval authorizes the roadmap document only—not staffing, budget, dates, launches, publication, or execution.

## Failure Rules

- Do not recreate failed children blindly.
- Do not continue from missing/malformed evidence.
- Do not truncate sources.
- Missing verified write, digest, attachment, or evidence blocks.
- Failed/human-blocked work remains visible.
- Trust profile names; do not pin profile files, tools, MCPs, defaults, or digests.

## Success

- Intake creates one writer.
- Writer creates Product PM, VP Engineering, MVP, Occam, and synthesis.
- Revision exists only when evidence requires it.
- No round 3.
- Claims have provenance and assumptions have owners.
- Human can read and decide conversationally.
- No routine terminal advance.
- No roadmap commitment or external effect occurs.
