# Council: Hermes M2 Doc Runtime

Date: 2026-09-14

Council level: full, four lenses, four rounds

Personas: software-architect, reliability-sentinel, red-team, cost-finops

Formal room: `council-hermes-m2-doc-runtime-20260914`

## Initial Verdict

Block live M2. Pass M2 foundation with required changes.

## Ranked Issues

1. **BLOCKER — rollback waits on failing Hermes work.** Rollback must atomically quarantine every nonterminal Hermes phase and prepared effect, stop Hermes, and route only requests with no Hermes attachment to legacy. Ambiguous IDs reconcile offline. No provider wait in RTO path.
2. **BLOCKER — submit and spend caps are process-local.** Persist total POST reservations across crashes. One layer owns retries. No fresh key. Live spend needs generation-scoped admission/cost reservation and provider-side hard budget; without enforceable output cap, pilot activation stays blocked.
3. **BLOCKER — final model bytes lack exact-byte human approval.** Council output is advisory. Final bytes must be durably staged, hashed, shown from non-indexed stage, and approved by human against digest/runtime generation before inbox publication.
4. **MAJOR — publication needs doc-owned state and durable staging.** Use constrained `doc_publications` row, not generic request columns. Temp-write, fsync, atomic no-replace stage, directory fsync, then intent. Reconcile absent/matching/mismatching target deterministically.
5. **MAJOR — queue lease liveness and phase recovery are incomplete.** Existing heartbeat must remain active through render, POST, poll, stop, output stage, and publication. Lost lease stops adapter best effort and forbids stale settlement. Stable phase identity is request ID plus phase, never queue nonce.
6. **MAJOR — direct Runs API must earn trust through pinned conformance.** Test same-key replay, mismatched-body conflict, restart durability, TTL margin, zero tools, no memory/background writes, process tree, broad API auth, and state retention. Stock image failure returns to design.
7. **MAJOR — runtime ownership and deletion economics need dates.** Persist runtime generation when request phase first attaches to Hermes. Rollback does not reroute attached IDs. Legacy fallback expires after evidence review or explicit extension. Measure engineer effort, operator minutes, tokens, paid calls, and cost per approved document.

## Decision By Scope

- **M2a publication repair, exact-byte approval, durable phase ledger, pure HTTP adapter with fake server, and pinned runtime conformance: pass with required changes.** Default remains legacy. No live provider call or Hermes-routed request.
- **M2b paired live shadow and ten-run pilot: block.** Requires M2a evidence, enforceable all-in budget, provider-side cap, runtime containment pass, machine gate, rollback quarantine drill, and named human approval.
- **Direct internal Runs API: accepted conditionally.** Trusted-controller threat model does not justify inbound proxy. Add one only if more callers join or conformance shows practical bypass.
- **Squid egress: accepted for scoped pilot threat model only.** It limits destination, not provider capability. Document provider-wide key authority. TLS relay remains fallback if threat model expands.

## Dissents

- Red-team wants TLS-terminating provider relay and hardened Hermes derivative now.
- Architecture, reliability, and cost lenses reject those as foundation blockers under explicit local trusted-controller/non-malicious-image scope. Runtime conformance decides.
- Cost lens treats exact dollar budget as pre-pilot gate, not M2a blocker.

## Final Delta Verdict

After three design revisions and four rounds:

- **M2a foundation: pass with nits.** Plan and implement non-routing work only.
- **M2b paid shadow/live pilot: block.** Separate plan-to-launch and human approval required.

Resolved:

- sole `renameat2(RENAME_NOREPLACE)` publication contract;
- request quarantined before filesystem effect;
- both parent directories fsynced;
- exact-byte Publish/Dismiss approval;
- preapproval `invalid` versus postapproval `reconcile`;
- durable total submit count and one retry owner;
- quarantine-first rollback;
- distinct M2a/M2b gate profiles;
- runtime evidence bound to inspected container, process tree, run, provider capture, and trace interval.

Task-plan nits:

1. Estimate each M2a workstream. Stop before total exceeds 10 engineer-days.
2. Turn every DB/filesystem/HTTP boundary into expected-state fault case.
3. Kick design back on stock-image, `renameat2`, or trace conformance failure.

## Recommendation

Ship M2a as reversible, non-routing foundations. Return for separate human launch approval before any paid shadow or live document run.
