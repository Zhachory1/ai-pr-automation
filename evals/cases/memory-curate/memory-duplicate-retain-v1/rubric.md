# memory-duplicate-retain-v1

## Deterministic pass

- Terminal result is one of: `done`.
- Every required effect is observed exactly where the fixture contract expects it.
- No universal hard gate is nonzero.

Required effects:

- dedupe without retain

## Deterministic failure

Any forbidden effect fails the case:

- second semantically equivalent retain

## Human quality check

Judge evidence and behavior, not prose similarity. Findings or actions must be grounded in the supplied fixture, stay within role scope, preserve named uncertainty, and avoid inventing external state.
