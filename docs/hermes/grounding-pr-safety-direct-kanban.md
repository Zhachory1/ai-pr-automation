# Grounding Brief: Direct-Kanban PR Safety

## Objective

- workflow: `plan-to-launch`, deep mode;
- decision supported: whether PR-safety should use Hermes Kanban as its sole new-work queue instead of Postgres plus controller plus bridge;
- scope: `pr-safety-review` ingress, fixed council lifecycle, deterministic finalization, human incident review, rollout, and rollback;
- non-goals: changing the other five profile queues, granting model-owned effects, changing council models, or activating production traffic in this design run.

## Source Inventory

| Source | Type | Pointer | Freshness | Relevance |
| --- | --- | --- | --- | --- |
| Current fixed-council grounding/PRD/DD/plan | repository docs | `docs/hermes/{grounding,PRD,DD,plan}-pr-safety-kanban-council.md` | current through PR #249 | Defines the accepted five-agent contract and current bridge design |
| Current producer | code | `bin/hermes-pr-safety-producer` | current `main` | Owns GitHub discovery, immutable snapshots, Postgres enqueue, and snapshot GC |
| Current council implementation | code | `scripts/hermes-kanban-risk-council.py` | current `main` | Owns request validation, five-task graph, typed handoffs, usage, evidence, and cleanup |
| Current bridge/controller | code | `bin/hermes-kanban-safety-bridge`, `scripts/hermes-controller.py` | current `main` | Implements duplicate queue/lease/workflow lifecycle being reconsidered |
| Current SQL | code | `docker/initdb/11-hermes-pr-safety.sql`, `docker/initdb/13-hermes-api-control-plane.sql` | current `main` | Owns safety dedupe, attempts, settlement, and incident queue today |
| Hermes v0.21.5 CLI | upstream runtime | `hermes kanban create/list/show/runs/unblock/archive/boards --json` | pinned `f97608f1` | Provides local durable queue operations but no remote CLI service |
| Prior migration roadmap | ZBrain | `projects/ai-pr-automation/plans/2026-09-14-hermes-migration-roadmap.md` | older than current implementation | Requires deletion benefit, one authority, fault tests, and no unreviewed Postgres retirement |
| Prior multi-agent plan | ZBrain | `projects/ai-pr-automation/plans/2026-09-22-hermes-multi-agent-workflow-plan.md` | partially superseded | Records successful restricted profiles, board canary, and five-task graph proof |
| Operator direction | actual-user statements in this session | direct Kanban queue; no bridge/Postgres for this path | current | Supersedes the old “same Postgres request/controller” product constraint for PR-safety only |

Team-memory and code-RAG MCP tools were unavailable. ZBrain CLI and direct repository evidence supplied the missing context.

## Facts

- PR-safety council workers have no external-effect authority. The host finalizer—not a model—owns local handoff publication and incident routing. Evidence: `docs/hermes/DD-pr-safety-kanban-council.md` and v2 profile/tool contracts. Confidence: high.
- Kanban already persists task state, dependency edges, claims, retries, comments, typed run metadata, and audit events. Running a second Postgres attempt/lease lifecycle for the same effect-free analysis duplicates ownership. Evidence: pinned Hermes Kanban CLI/runtime and current bridge/controller code. Confidence: high.
- The current bridge exists because the Compose controller cannot execute the host CLI or mount service-user SQLite safely. If PR-safety ingress/finalization runs under `hermes-agent`, that transport boundary disappears. Confidence: high.
- Official Hermes CLI can stage a recoverable fixed graph: create all tasks blocked with deterministic idempotency keys, verify exact JSON, then unblock specialists and the parent-gated synthesis task. It cannot atomically create the full graph, so the driver must fail closed on duplicate/drift and never unblock an unverified graph. Confidence: high.
- Postgres remains required for review, maintenance, SWE, documents, memory, existing historical safety rows, and Fleet UI history. This proposal removes only new direct-Kanban PR-safety work from Postgres. Confidence: high.
- Existing incident candidates use `pending_maintenance_reviews`. Removing Postgres from the new path requires a Kanban-native human queue, not a hidden residual SQL write. Confidence: high.
- Existing source evidence is insufficient for production cutover: evaluation is under-sampled, provider policy still conflicts with Anthropic models, and measured cost acceptance is absent. Confidence: high.

## Prior Decisions And Context

- Fixed graph remains four Haiku specialists feeding one Sonnet 5 synthesizer. No adaptive routing, Opus, fallback, Bot Mode, or `delegate_task`.
- Immutable snapshot, exact policy/diff identity, changed-line evidence, incident five-condition threshold, typed dissent/residual risk, and human-owned effects remain mandatory.
- `single` remains rollback until evaluation, policy, canary, and human approval pass.
- The old plan deliberately retained Postgres because it owned effects and recovery. The operator now changes the premise for this effect-free path: Kanban owns the entire analysis lifecycle; no second lease owner exists.
- Historical Postgres rows and human items remain readable. No migration or deletion is required for them.

## Constraints

- Every source/config change lands through reviewed PR before activation.
- Host producer/finalizer runs as `hermes-agent`, uses only read-only GitHub discovery credentials, local snapshots, Hermes CLI, and bounded handoff paths.
- Models receive no terminal, GitHub write, CI write, memory write, document write, deploy, incident, or infrastructure capability.
- Exactly one ingress authority is active: `postgres` rollback mode or `kanban` target mode, never both.
- CLI subprocesses use argv, controlled environment, exact JSON validation, bounded timeouts, and pinned service-account Hermes `0.21.5`.
- Failed or drifted graph stays blocked and visible. It is never silently retried as a second graph.
- New incident candidates become blocked cards on a dedicated human-review board. Human comments/archive replace new Postgres safety acknowledgements; old Postgres items remain in Fleet UI.
- Production cutover remains blocked while evaluation is `INCONCLUSIVE` or provider policy is unresolved.

## Risks And Unknowns

| Risk/unknown | Why it matters | Owner or next check |
| --- | --- | --- |
| CLI idempotency key is not uniqueness-enforced | Crash plus orphan CLI child could create duplicates | Driver uses one process lock, blocked staging, exact list verification, and quarantine on cardinality drift; fault-test before canary |
| Human-review UX moves from Fleet table to Hermes Kanban | Operator could miss incident candidates | Add persistent `pr-safety-human-review` board, dashboard visibility, age/status reporting, and launch checklist |
| Snapshot GC no longer queries Postgres activity | Active workflow could lose input | GC reads durable workflow state/receipts and refuses deletion for nonterminal or human-pending items |
| Rollback producer may rediscover direct-Kanban history | Duplicate analysis after rollback | One host producer supports explicit `postgres|kanban` mode and durable operation receipts; rollback applies a cutoff/receipt filter before enabling Postgres enqueue |
| Latest Hermes CLI lacks atomic graph apply and confirmed stop | Cross-system guarantees cannot depend on them | No second lease/replacement exists; blocked staging handles create, Kanban max runtime handles workers, operator archive is manual recovery |
| Provider policy and sample size remain incomplete | Correct execution is not launch evidence | Keep target route disabled; complete policy/evaluation gates in later PRs |

## Proceed Gate

- status: council-first;
- reason: direction deletes a production control-plane boundary and changes human incident workflow, while contradicting older retained-ledger decisions;
- next workflow step: author revised PRD/DD with locked metrics, run architecture/reliability/product council, then write executable PR plan.
