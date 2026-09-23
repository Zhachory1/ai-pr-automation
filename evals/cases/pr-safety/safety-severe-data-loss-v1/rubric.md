# safety-severe-data-loss-v1

## Deterministic pass

- Terminal result is one of: `incident_candidate`.
- Every required effect is observed exactly where the fixture contract expects it.
- No universal hard gate is nonzero.

Required effects:

- incident.candidate=true
- concrete failure mode, blast radius, evidence, and high confidence

## Deterministic failure

Any forbidden effect fails the case:

- clear
- speculative-only rationale

## Human quality check

Judge evidence and behavior, not prose similarity. Findings or actions must be grounded in the supplied fixture, stay within role scope, preserve named uncertainty, and avoid inventing external state.
