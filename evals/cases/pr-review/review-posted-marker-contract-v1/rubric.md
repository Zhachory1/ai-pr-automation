# review-posted-marker-contract-v1

## Deterministic pass

- Terminal result is one of: `done`.
- Every required effect is observed exactly where the fixture contract expects it.
- No universal hard gate is nonzero.

Required effects:

- one head-pinned review with exact marker

## Deterministic failure

Any forbidden effect fails the case:

- duplicate review
- wrong-head review

## Human quality check

Judge evidence and behavior, not prose similarity. Findings or actions must be grounded in the supplied fixture, stay within role scope, preserve named uncertainty, and avoid inventing external state.
