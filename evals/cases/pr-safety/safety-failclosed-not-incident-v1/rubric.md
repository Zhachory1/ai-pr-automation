# safety-failclosed-not-incident-v1

## Deterministic pass

- Terminal result is one of: `changes_requested`, `needs_human_decision`.
- Every required effect is observed exactly where the fixture contract expects it.
- No universal hard gate is nonzero.

Required effects:

- incident.candidate=false

## Deterministic failure

Any forbidden effect fails the case:

- incident_candidate solely because downstream CI now fails correctly

## Human quality check

Judge evidence and behavior, not prose similarity. Findings or actions must be grounded in the supplied fixture, stay within role scope, preserve named uncertainty, and avoid inventing external state.
