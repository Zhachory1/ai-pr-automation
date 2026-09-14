# Council: Hermes M0–M2 Docs

Date: 2026-09-14

Mode: minimal, two rounds

Personas: software-architect, reliability-sentinel, red-team

Source: `PRD-m0-m2.md`, `DD-m0-m2.md`

Formal room: `council-hermes-m0m2-docs-20260914`

## Council Verdict: Split

### Ranked Issues

1. **BLOCKER — Live M2 lacks enforceable model containment.** Red-team raised. Hermes doc process still has tools, persistent state, provider credential, and outbound network. Output validation does not contain actions during inference. Define no-tools profile, memory disablement, effective non-root runtime, capability drops, no-new-privileges, writable paths, resource controls, and destination egress. Prove with injection and exfiltration tests.
2. **BLOCKER — Doc publication recovery is incomplete.** Reliability and red-team raised; architect conceded. Persist canonical path plus exact final byte digest before publication. Define absent/matching/mismatching recovery, no-clobber publish, and fence later attempts until reconciliation. Fault-test every DB/filesystem boundary.
3. **MAJOR — Run-attempt authority and 24-hour submit ambiguity are unresolved.** Architect and reliability raised. Domain controller must be sole Postgres writer. Adapter stays pure HTTP. Persist request digest, image/service/volume generation, submit deadline, and raw status before POST. Expired unknown submit becomes manual reconciliation, never fresh replay.
4. **MAJOR — Scheduler lacks durable slot accounting and route fencing.** All lenses raised. Add Postgres schedule-slot execution rows and one fenced route generation/lease shared by legacy and Hermes routes. Account for zero-result, overlapping, hung, and crashed runs.
5. **MAJOR — Rollback and pilot authorization are prose.** Reliability and red-team raised; architect conceded. Build executable quiesce, backup, checksum, restore, drain, reconcile, and legacy-start commands. Create one machine-verifiable gate manifest per pilot with named human approval.
6. **MAJOR — Compatibility and supply-chain contracts need versioning.** Architect and red-team raised. Store runtime digest/generation with attempts, version status translation and conformance fixtures, pin final derivative scheduler image, and define volume upgrade/rollback behavior.

## Decision By Scope

- **M0 inert docs, conformance tests, baseline instrumentation, disabled Compose scaffold: pass with changes.** No production route. No live provider or GitHub work. M0 must not lock unresolved run-attempt or publication schemas.
- **M2 doc-runtime activation: block.** Issues 1, 2, 3, and 5 must close first.
- **M1 scheduler activation: block.** Issues 4, 5, and 6 must close first.
- **Milestone reorder: accept.** Investigate M2 before M1 after M0 because official-image Runs API is smaller seam. Activation remains separately gated.

## Dissents

- Software-architect rejects mandatory narrow proxy as default. Existing trusted worker plus private network may be enough unless threat model includes compromised-controller containment.
- Red-team requires narrow proxy because one bearer key grants broad gateway authority.
- Red-team keeps overall block because full artifact asks for pilot approval. Other lenses allow inert M0 to generate evidence.

## Strongest Counterargument

Even inert M0 can create migration gravity around wrong schemas and security assumptions. Limit M0 to docs, conformance tests, baseline instrumentation, and disabled pinned service. Ship no route or attempt schema until child design passes delta council.

## Recommendation

Ship M0 evidence scaffold only. Treat M1 and M2 as blocked child designs with separate plan-to-launch gates.
