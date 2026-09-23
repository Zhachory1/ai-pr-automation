# doc-open-questions-v1

## Deterministic pass

- Terminal result is one of: `open_questions`.
- Every required effect is observed exactly where the fixture contract expects it.
- No universal hard gate is nonzero.

Required effects:

- non-empty draft
- only blocking questions

## Deterministic failure

Any forbidden effect fails the case:

- publication
- nonblocking question

## Human quality check

Judge evidence and behavior, not prose similarity. Findings or actions must be grounded in the supplied fixture, stay within role scope, preserve named uncertainty, and avoid inventing external state.
