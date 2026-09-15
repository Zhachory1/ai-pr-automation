# Hermes Migration Parity Matrix

Status: M2a non-routing evidence gate

Date: 2026-09-15

Rule: `retained` means Hermes does not replace guarantee in current milestone.

## Trust-Tier Inventory

| Kind/Service | Current Authority | Credentials | Writable Mounts | Network Need | External Effect | M0 State |
| --- | --- | --- | --- | --- | --- | --- |
| `pr-producer-review` | `bin/pr-producer`, Postgres enqueue | review/discovery GitHub token, DB writer | none required | GitHub + request DB | queue insert | retained |
| `pr-producer-maintain` | `bin/pr-producer`, Postgres enqueue | GitHub token, DB writer | none required | GitHub + request DB | queue insert | retained |
| `pr-review` | `bin/agent-server`, result schema, GitHub marker | PR-review GitHub write, provider, DB | worktree root, agent session state | GitHub + provider + DB + read MCP | GitHub review | retained |
| `pr-maintain` | `bin/agent-server`, queue nonce/effect fence | contents/PR GitHub write, provider, DB | worktree root, agent session state | GitHub + provider + DB + read MCP | push, reply, resolve thread | retained |
| `swe-implement` | `bin/swe-implement-server`, harness | push/PR GitHub write, provider, DB | ephemeral clone volume | GitHub + provider + DB | branch push, draft PR, review enqueue | retained |
| `doc-write` | `bin/doc-writer-server`, harness | provider, DB | stage volume, inbox bind | provider + DB + read MCP | staged/final document, human row | retained; M2 child design |
| `pr-safety-review` | dedicated verifier/server | read-only GitHub/CI/Datadog, provider, DB in server only | operation work and handoff roots | allowlisted proxy + DB + read MCP | immutable local handoff, dedicated memory | retained indefinitely unless exact parity |
| `memory-curator` | `bin/memory-curator` | provider, Hindsight writer | curator state/work | Hindsight + provider | shared-memory retain | retained |
| `status` | `bin/status-server` | DB, Hindsight URL | none | local host + DB + Hindsight | queue/approval state; exact human-approved Hindsight retain | retained |
| `hermes-m0` | pinned upstream image | API key only; no live provider in M0 | unique `/opt/data` volume | dedicated Compose network | none | new, disabled profile |

## Guarantee Matrix

| Guarantee | Current Evidence | Hermes Native Primitive | Decision | Proof Before Replacement |
| --- | --- | --- | --- | --- |
| Root Compose manifest and operator entrypoint | `docker-compose.yml`, `scripts/compose.sh` | official image supports Compose | use | rendered config and profile omission test |
| Reproducible runtime | repo-built images; some mutable dependencies remain | digest-pinnable image | use exact digest | static pin check; upstream conformance fixture |
| Operation identity | `requests.id`, kind, dedupe key, payload | run ID and optional idempotency key | retain Postgres | child DD for operation-to-attempt mapping |
| Head-SHA dedupe | `queue_enqueue`, done/reconcile suppression | generic idempotency | retain | live-review child tests |
| Same-lineage exclusion | partial unique running-lineage index | generic task/run concurrency | retain | fault and concurrent-head tests |
| Claim fencing | run nonce plus unexpired lease on every transition | Hermes process/session ownership | retain | no planned replacement |
| Expired-attempt recovery | `queue_reclaim_stale` | interrupted run and scheduler reclaim | retain | kind-specific crash matrix |
| Effect intent before action | `side_effect_at` | none domain-specific | retain | publisher child DD |
| Ambiguous effect reconciliation | `reconcile` | generic interrupted/failed status | retain | exact target reconciliation tests |
| Immutable PR-safety input | snapshot, head/base, diff and policy digests | none | retain | exact parity or no migration |
| Deterministic review action | server maps typed verdict | direct webhook delivery available | retain server publisher | schema and marker tests |
| Human approval state | Postgres state machines and local UI | Kanban review/block | retain | actor/provenance/auth/retry parity |
| Shared-memory write ownership | bank lock, curator, human-approved decision publisher | automatic provider sync | retain both trusted server writers; disable Hermes writes | Hindsight audit and capability test |
| Credential separation | Compose service environment and dedicated workers | profiles separate config only | use separate containers, not profiles | wrong-tier secret probe |
| Mount separation | per-service volume/bind policy | terminal backend/container mounts | use Compose | rendered config plus runtime path probe |
| Network separation | default/internal networks and PR-safety proxy | container/network support | use Compose with explicit policy | destination allow/deny probes |
| Doc no-clobber naming | harness suffix selection | none | retain, redesign effect recovery in M2 | path+byte-digest state machine tests |
| Scheduler slot accounting | loop logs only | cron executions ledger | insufficient | Postgres slot ledger in M1 child design |
| Generic run status | Postgres rows and status UI | Runs API/dashboard | use for diagnostic copy only | status translation conformance |
| Durable history | Postgres | Hermes idempotency retention 24h | retain Postgres | no replacement in M0–M2 |
| Backup/restore | volumes; manual procedures | `/opt/data` volume | M0 tests isolated Hermes volume only | production procedure in child design |
| Rollback without replay | queue state and manual reconciliation | stop/interrupted | retain domain controller | timed child-design drill |

## Request And Effect Inventory

| Kind | Dedupe/Identity | Terminal States | Possible Ambiguity | Current Recovery |
| --- | --- | --- | --- | --- |
| `pr-review` | `repo#pr@head` | done, failed, skipped, superseded, reconcile | review posted before local done | GitHub head marker plus posted ref |
| `pr-maintain` | `repo#pr@head` | done, failed, skipped, superseded, reconcile | push/reply after effect intent | expired effect intent becomes reconcile; explicit blocked outcomes enter human queue; remote state still needs inspection for uncertain immediate failure |
| `swe-implement` | source task key | done, failed, skipped, reconcile | branch/commit/PR can exist before local done | known gaps: effect-intent failure is ignored; timeout/nonzero can mark failed after remote effect; failed task can be manually re-enqueued; expired attempt can blind-requeue; M5 blocked until mandatory intent and target reconciliation exist |
| `doc-write` | UI/request key | done, failed, reconcile | file can exist before local done | known gaps: effect-intent failure is ignored; timeout/nonzero can mark failed after file effect; failed task can re-enqueue; expired attempt can blind-requeue; M2 blocked until mandatory intent and exact file reconciliation exist |
| `pr-safety-review` | operation ID + merge SHA/digests | done, failed, superseded | handoff publication | immutable draft validation and server publication |

## State-Transition Inventory

| State Owner | Transition | Trigger | External Effect | Migration Decision |
| --- | --- | --- | --- | --- |
| requests | absent → queued | producer, force inject, doc/SWE submit | queue row insert | retain |
| requests | queued → running | nonce-fenced claim | worker starts | retain |
| requests | queued → superseded | newer head enqueue/claim | none | retain |
| requests | queued → deleted | human cancel | local queue removal | retain status action |
| requests | running → done | valid terminal result | may follow review, push, PR, file, handoff, or human-row effect | retain |
| requests | running → failed | execution, timeout, or publisher failure | external effect can already exist; recovery differs by kind | retain state, fix unsafe kind-specific recovery before migration |
| requests | running → skipped | valid no-action result | none | retain |
| requests | running → superseded | immutable input changed | none | retain |
| requests | running → reconcile | ambiguous effect or publication failure | possible external effect | retain |
| requests | expired running → queued | no effect intent and no newer head | retry | retain |
| requests | expired running → done | posted ref already exists | none during reclaim | retain |
| requests | expired running → superseded | newer queued head exists | none | retain |
| requests | expired maintenance with effect → reconcile | lease reclaim | possible push/reply | retain |
| requests | expired doc/SWE with effect → queued | generic lease reclaim | possible file/branch/PR effect | known gap; block M2/M5 until kind-specific reconcile behavior exists |
| pending maintenance review | absent → pending | worker escalation | human queue insert | retain |
| pending maintenance review | pending → reviewed | human Reviewed or doc Refine/Finalize | local state; doc action can enqueue next round | retain |
| pending maintenance review | pending → dismissed | human Dismiss | local state | retain |
| pending decision | absent → pending | agent/server proposal | local row | retain |
| pending decision | pending → publishing | human Approve | Hindsight retain starts | retain |
| pending decision | publishing → approved | synchronous retain confirmed | shared-memory write | retain trusted publisher |
| pending decision | publishing → publishing | human Retry after failed/unknown retain | stable-ID Hindsight replace | retain trusted publisher |
| pending decision | pending → rejected | human Reject | local state only | retain |

## Human-Action Inventory

| Action | Input Surface | State/Effect | Current Guard | Migration Decision |
| --- | --- | --- | --- | --- |
| Cancel queued request | status UI | delete queued row only | POST, Origin/Host, CSRF, status guard | retain |
| Force-inject PR | status UI | enqueue review/maintain row | allowlist, normalized repo/PR, parameterized SQL | retain |
| Submit doc | status UI | enqueue `doc-write` | daily cap, validated type/title, parameterized SQL | retain |
| Refine doc | status UI | pending review → reviewed; enqueue next round | conditional state close prevents double submit | retain |
| Finalize doc | status UI | pending review → reviewed; enqueue forced final round | same conditional close and confirmation | retain |
| Dismiss doc question | status UI | pending review → dismissed | scoped state update | retain |
| Mark maintenance reviewed | status UI | pending review → reviewed | scoped state update | retain |
| Dismiss maintenance finding | status UI | pending review → dismissed | scoped state update | retain |
| Implement handoff | status UI | enqueue `swe-implement` | handoff-root/path validation, dedupe | retain |
| Implement issue | status UI | enqueue `swe-implement` | issue-shape validation, dedupe | retain |
| Implement prompt | status UI | enqueue `swe-implement` | repo/prompt validation, dedupe | retain |
| Approve decision | status UI | pending → publishing → approved; Hindsight retain | stored content only, stable document ID, sync confirmation | retain trusted publisher |
| Retry decision publish | status UI | publishing → publishing/approved | stable-ID replacement | retain trusted publisher |
| Reject decision | status UI | pending → rejected | no Hindsight call | retain |

## M0 Evidence Map

| M0 Requirement | Artifact Or Task | Status |
| --- | --- | --- |
| full trust-tier inventory | this document | complete baseline |
| invariant mapping | this document | complete baseline |
| current metrics | `scripts/hermes-baseline.py` task | planned |
| duplicate-effect audit | target audit contract in baseline task; live target data later | planned |
| human disposition | baseline task from pending review/decision tables | planned |
| pinned disabled service | Compose task | planned |
| isolated Hermes state round trip | recovery test task | planned |
| council synthesis | [`council-m0-m2.md`](council-m0-m2.md) | complete |
| M1/M2 activation authorization | child plan-to-launch runs | blocked |

## M2a Evidence Status

- Pinned zero-tool Hermes runtime and doc-only proxy pass fake-provider conformance.
- Exact-byte publication, durable Runs identity, and rollback quarantine remain controller-owned.
- Machine-gate evidence authorizes neither routing nor paid calls.
- Baseline DB still cannot prove missed eligible PRs or target-side duplicate effects alone; M2b pilot audit must join target inventory.
- M2b remains blocked pending separate plan-to-launch and human approval.
