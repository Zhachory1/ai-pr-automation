# Council: Adaptive PR Safety Kanban Council

- Level: full council.
- Lenses: architecture, reliability, adversarial security, cost/FinOps, MVP.
- Loop cap: two reviews.
- Final council state: blocked after second review; revised for human decision. No third council by design.
- Source: `docs/hermes/PRD-pr-safety-kanban-council.md`, `docs/hermes/DD-pr-safety-kanban-council.md`.

## Round 1

Verdicts:

- Architecture: pass with required changes.
- Reliability: block.
- Security/red-team: block.
- Cost/FinOps: pass with required changes.
- MVP: pass with required changes.

Required changes:

1. One durable route/fallback state. Separate shadow lifecycle. Keep snapshot until exact settlement.
2. Exact bridge API, idempotency, recovery, archive, and 429 behavior.
3. Unambiguous packet canonicalization and digest.
4. Versioned v2 profile/graph contract. Do not reinterpret old Haiku verifier graph.
5. Lossless dissent mapping into compatible result and handoff.
6. Hard deadline and token enforcement before spend.
7. Same-request legacy fallback on candidate failure.
8. Bridge auth, replay, Host, Origin, content-type, and cross-workflow binding.
9. Complete source retention and purge contract.
10. DB fence or stronger isolation so shadow cannot settle.
11. Dollar, throughput, sample-size, and Sonnet-ablation gates.
12. Offline quality signal before production bridge, lineage, alerts, or UI.

Revision:

- Moved packet/triage/adapter/offline replay before production infrastructure.
- Replaced controller shadow mode with bounded operator command lacking DB/handoff credentials.
- Added same-request legacy fallback.
- Added HMAC bridge, exact state, retention, token reserve, capacity, cost, and 90-case strata.
- Added v2 workflow contract and dissent/citation checks.
- Kept no new UI.

## Round 2: Delta Only

Verdicts:

- Architecture: block.
- Reliability: block.
- Security/red-team: block.

Resolved from round 1:

- packet digest and byte preimage;
- v2 profile migration;
- candidate-to-legacy fallback;
- retained running snapshot;
- bounded supervised shadow isolation;
- immediate 429 fallback;
- SQLite journal/reconcile/retention direction;
- bounded packet and aggregate token reserve;
- Git object/path defenses;
- no settlement capability in shadow command.

Remaining required changes:

1. Exact bridge create/status/terminal/tombstone/error schemas.
2. Durable packet path before `creating` so restart can rebuild graph.
3. Explicit cleanup-complete controller state.
4. Typed named dissent, disposition, rationale, residual risk, and deterministic union.
5. Candidate deadline starts at request claim. Reserve legacy fallback time.
6. Availability/latency SLOs, p99, saturation, and burn-rate alerts.
7. Authenticate bridge responses and bind request nonce/status/body digest.
8. Define trusted runtime attestation source. Never trust model attestation.
9. Approve policy v2 or sanitized-eval exception before any Anthropic replay.

Final revision applied:

- Added exact signed request and response protocol.
- Added exact request, active, terminal, archive, tombstone, and error shapes.
- Added packet file persistence, fsync order, digest check, and recovery precedence.
- Added `cleanup_complete` state.
- Added typed dissent/residual-risk contracts and deterministic union.
- Added 45-minute request deadline: 15-minute candidate budget plus 30-minute legacy reserve.
- Added SLOs, p50/p95/p99, saturation, fast/slow burn alerts, and ONCALL gate.
- Bound trusted attestation to controller submission state, Runs API terminal metadata, bridge profile hashes, Kanban run rows, and pinned manifest.
- Moved policy approval or reviewed exception before offline Anthropic inference.

## Named Dissent

- MVP: do not build bridge, lineage, dashboard, or production alerts before offline quality signal.
- Reliability/security: policy approval must precede real or sanitized Anthropic inference unless explicit evaluation exception exists.
- Cost/FinOps: Sonnet 5 must earn added cost through same-handoff ablation. User may accept measured tradeoff, but must set numeric monthly cap.
- Architecture: packet-only context can be valid, but eligibility and omitted context must remain visible. Do not grant broad host file tools to fix recall.

## Gate

- Docs: block pending human review because council loop cap ended on `BLOCK`.
- Implementation: not started.
- Recommendation: approve revised direction for technical planning only. Do not approve implementation, live shadow, or activation in this decision.

## Human Decision

Choose one:

- Approve revised PRD/DD for technical planning.
- Request specific doc changes.
- Reject adaptive migration and keep `pr-safety-v1`.

Approval does not authorize policy change, credentials, bridge activation, live data, canary routing, or merge.
