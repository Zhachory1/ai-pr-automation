# Hermes Evaluations

Versioned offline evaluation contract for six Hermes profiles.

## Validate

```bash
python3 scripts/hermes-eval.py validate evals/manifest.json
```

Validation is read-only. It makes no model, network, database, or GitHub calls.

Score a recorded result:

```bash
python3 scripts/hermes-eval.py score evals/manifest.json <case-id> actual.json
```

`actual.json` contains `case_id`, normalized terminal status/effects/labels, and all ten hard-gate
counts. Any nonzero hard gate returns `FAIL_SAFETY`; deterministic contract mismatches return
`FAIL_QUALITY`.

## Rules

- Lock metrics and thresholds before candidate runs.
- Hard gates are zero-tolerance and override quality scores.
- Keep baseline and candidate on identical cases, repetitions, tools, policy, and evaluator version.
- Do not exact-match model prose. Score typed behavior, evidence, and effects.
- Do not commit secrets, credentials, customer data, or raw private production content.
- PR1 scans manifest values for common token/private-key patterns. PR2 adds complete fixture scanning.
- Add cases only after sanitization and human labeling.

## Case Contract

Each future entry in `manifest.json` uses:

```json
{
  "id": "profile-scenario-v1",
  "profile": "pr-review-v1",
  "path": "evals/cases/pr-review/profile-scenario-v1",
  "repetitions": 3
}
```

The validator checks the registry entry, required artifacts, metadata/result schemas, framed input
digest, and common token/private-key patterns across every case file.

Its directory contains:

```text
case.json
input/
expected.json
rubric.md
```

`case.json` binds fixture provenance and digests. `expected.json` defines deterministic outcomes,
required effects, forbidden effects, and labels. `rubric.md` covers subjective quality only.

## Verdicts

- `PASS`
- `PASS_WITH_QUALITY_REGRESSION`
- `FAIL_QUALITY`
- `FAIL_SAFETY`
- `INCONCLUSIVE`

No aggregate score can override a failed hard gate.

## Seed Corpus

The first sanitized corpus contains two cases per profile (12 total), weighted toward observed fleet
failures: review result binding and auth, maintenance thread handling, SWE repository binding,
PR-safety incident calibration, memory dedupe/org routing, and document approval contracts. These are
contract fixtures only; model replay and role scoring land in later PRs.
