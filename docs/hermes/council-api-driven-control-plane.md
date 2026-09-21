# Council: API-Driven Hermes Control Plane

- level: full architecture/reliability/security/simplicity council
- rounds: 2 blind/reflection + delta-only gate
- personas: software-architect, reliability-sentinel, red-team, occams-razor
- verdict: PASS-WITH-NITS
- transcript: `~/.agent-fleet/agent-chat/rooms/council-hermes-api-control-plane/log.jsonl`

## Decision

Proceed to implementation planning.

Keep the architecture:

- Compose producers/controller;
- Postgres queue and durable attempt truth;
- host-native Hermes gateway/profiles;
- profile-scoped Runs API.

## Required Changes Applied To DD

1. One generalized four-state fleet Runs ledger. Migrate/retire doc-only ledger. No parallel state machine.
2. One route/cap table and one claim path.
3. Atomic claim+attempt reserve and final pre-submit route/generation CAS.
4. Durable operation-key exclusion across uncertainty and route changes.
5. Human-only reconciliation for uncertain direct effects in v1. No speculative auto-confirm adapters.
6. Per-profile auth generation with drain-before-rotation; emergency restart/interruption protocol.
7. Exact serialized request bytes + stable idempotency key + 23-hour replay cutoff.
8. First/replay predicates and bounded 8 POSTs/5-minute 429 policy.
9. One canonical `submitting` state for reserved/running/stop-unconfirmed; durable reconcile disposition.
10. Pinned Hermes/profile generation + conformance gate.
11. 60-second stop deadline; unconfirmed remains fail-closed.
12. Rollback RTO applies only when termination is confirmed; native fallback kept for 7 days.
13. Direct per-kind controller branches. No adapter/proxy framework.

## Accepted Risks

- Profiles are not sandboxes.
- Direct-effect profiles share `hermes-agent` authority.
- Controller holds all profile API invocation keys; compromise equals fleet profile invocation authority.
- First release targets macOS Docker Desktop only.

## Dissent

No architecture dissent remains after delta fixes. Reliability preserves one hard rule: if run
termination cannot be proven, safety overrides rollback RTO and the affected route stays closed.

## Next Gate

Owner approval of PRD/DD/plan, then PR 1 API conformance scaffold. Implementation must return to DD
on any contract mismatch; no patch-around.
