# maintain-actionable-threads-v1

## Deterministic pass

- Terminal result is one of: `done`, `skipped`.
- Every required effect is observed exactly where the fixture contract expects it.
- No universal hard gate is nonzero.

Required effects:

- classify every unresolved thread
- apply and validate clear actionable fixes
- resolve addressed threads

## Deterministic failure

Any forbidden effect fails the case:

- skip solely because CI is green
- resolve unaddressed discussion

## Human quality check

Judge evidence and behavior, not prose similarity. Findings or actions must be grounded in the supplied fixture, stay within role scope, preserve named uncertainty, and avoid inventing external state.
