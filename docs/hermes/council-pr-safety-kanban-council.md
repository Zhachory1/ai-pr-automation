# Council: Kanban-Backed PR Safety Analysis

- Level: focused architecture, reliability, and simplicity council.
- Scope: same PR-safety use case; fixed Kanban graph replaces one model run.
- Final synthesized verdict: pass with required changes applied.

## Scope Correction

Initial draft introduced adaptive triage, eligibility routing, percentage canary, and extra lineage. User rejected that interpretation.

Accepted intent:

- same request;
- same snapshot/policy;
- same output and settlement;
- fixed multi-agent Kanban graph for every safety request;
- single-agent engine only as rollback.

Adaptive routing is removed.

## Round 1

### Architecture

Verdict: pass with required changes.

Required:

- exact evidence/dissent schemas and lossless handoff projection;
- crash-safe settlement then archive then attempt completion;
- durable per-attempt engine binding;
- versioned v2 profiles instead of mutating v1 profiles.

### Reliability

Verdict: block.

Required:

- authenticated stop path for deadline and lease loss;
- confirmed worker termination before lease recovery;
- recoverable `archiving` phase and retained tombstone;
- persisted engine identity across restart and rollback.

### Simplicity

Verdict: pass with changes.

Cuts:

- remove bridge Postgres request-ID coupling;
- remove model self-attestation fields already verified by bridge;
- keep mapping as one private pure controller function, not new component.

## Revision

DD now requires:

- typed evidence, dissent, and residual risk;
- deterministic dissent union and `coverage.council` projection;
- council context in existing controller-written handoff;
- separate `*-v2` profiles;
- exact read/search-only worker schema preflight;
- signed create/status/stop/archive bridge;
- `stopping`, `stopped`, `archiving`, and `archived` recovery states;
- atomic Kanban engine marker in claim transaction;
- settlement-only helper for Kanban;
- archive/tombstone confirmation before attempt completion;
- unchanged single-agent wrapper behavior.

## Delta Review

- Reliability: pass with nit. Nit was claimed-but-unbound crash window.
- Architecture: one remaining blocker. Kanban flow still named current `postprocess_safety()` despite required split.

Final revision fixed both:

- engine marker now lands atomically with claim;
- Kanban calls settlement-only helper, then archive, then attempt completion;
- single path keeps current wrapper semantics.

## Named Dissent

- Profiles are not sandboxes. Exact tool allowlist reduces capability but does not create OS isolation.
- Fixed five-agent graph costs more than one model run. Cutover still needs measured quality/cost evidence.
- Policy currently contradicts Anthropic use. Separate human-reviewed policy change remains mandatory before real cutover.

## Gate

- Product intent: pass.
- Design direction: pass with nits resolved in final revision.
- Implementation: may proceed only through atomic PR plan.
- Activation: not approved by this council.
