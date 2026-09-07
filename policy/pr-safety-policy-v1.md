# PR Safety Review Policy v1

Pinned ruleset the PR Safety Review Analyst judges every queued pull request against. One job binds
one immutable version of this file by SHA-256. Read the target repository's own rules at `head_sha`
(`CLAUDE.md`, `AGENTS.md`, `docs/`, `CODEOWNERS`) and treat them as authoritative where stricter. If
a required source below is missing for the target repository, return `needs_human_decision` rather
than guessing. Automated review never authorizes merge, deployment, remediation, rollback, or CI
changes; every result is a draft for human decision.

## Main Rules

For each PR, assess:

1. **Correctness** — the logic works and does what it intends across callers, consumers, contracts,
   schemas, configuration, feature flags, and failure paths.
2. **Necessity** — the intent was actually needed. Do not infer intent from the PR description alone;
   require evidence (ticket, DD, ownership metadata). Flag work that solves a non-problem.
3. **Assumption validity** — information the change acted on is correct for the overall system, not
   just the local file. Check that upstream/downstream assumptions still hold.
4. **Good engineering:**
   - **Occam's Razor / KISS** — the simplest solution that works; flag accidental complexity.
   - **DRY** — no reinvention of an existing helper, module, service, or platform feature.
   - **YAGNI** — nothing added that does not pertain to the task at hand (no speculative
     abstractions, flags, config, or scaffolding).
   - **Single Responsibility** — each function/class/module has one clear job.

## Test coverage

- Use the repository's declared test and coverage command when one exists (`justfile`, `Makefile`,
  `package.json` script, or CI step). Report `unavailable` when none exists; never invent a number.
- Target ≥90% changed-executable-line coverage when a trusted coverage command exists.
- Expect the testing hierarchy: unit tests for logic, integration tests for service contracts, and an
  owned end-to-end/integration test for serving-path or user-visible behavior (or a stated reason one
  is not applicable). A flaky or skipped test touched by the PR is a finding unless fixed or justified.

## Documentation coverage and readability

Per the Knowledge Management Runbook, Track 2 ("How") docs are part of Definition of Done. For repos
or changes where they apply, flag missing or stale:

- **README.md** — one-line service purpose, DRI/owner, links to the project (Track 1) folder, ONCALL
  runbook, and SLO dashboard.
- **ARCHITECTURE.md** — dependency diagram (calls out / called by) and API contracts when not
  self-documenting.
- **ONCALL.md** — for each common alert: symptom, checks (dashboards/queries), and fix/rollback. A
  non-trivial bug fix must add its symptoms here.
- **SLO.md** — SLOs and a direct link to the monitoring dashboard.

Readability: public functions, exported types, and non-obvious control flow carry intent (the "why"),
not a restatement of the code. User- or API-facing behavior changes update README/docs/changelog in
the same PR. New config/env/flags are documented where the others are. PR descriptions explain the
logic of the change and link the DD, not just a file list.

## Observability and telemetry

Observability is a core deliverable, not post-launch. For any service or serving-path change, expect
the **Big 4** golden signals in Datadog and flag gaps:

- **Latency** — timers on the critical path, full E2E latency, model-prediction times.
- **Traffic** — QPS, plus successful vs failed request counts.
- **Errors** — stack dumps and call sites logged by default.
- **Saturation** — pending-request depth, resource/memory pressure.

A change that can violate an SLO without a defined metric, alert, or runbook is an incident-risk
finding. Set `datadog_terraform_candidate` only when evidence supports a monitor proposal; it is
never authorization to create one.

## Defensive engineering

Assume failure at every interface (APIs, feature-store lookups, model predictions):

- Timeouts, fallbacks, and circuit breakers on outbound calls.
- Strict input validation at trust boundaries — never trust incoming inputs.
- Error handling (try/catch or equivalent) that fails safe, not silent.

## Data classification and approved model provider

- Approved model provider for this pilot is OpenAI (`api.openai.com`) only. No other inference
  provider is authorized.
- Never copy secrets, credentials, tokens, customer data, or raw untrusted repository text into
  shared memory, logs, or the handoff beyond the minimum needed to state a finding.
- A PR that adds a new outbound destination, third-party data processor, or secret-handling path is
  an incident-risk candidate requiring a human decision.

## Incident risk

Flag concrete failure mode, blast radius, evidence, and confidence for any change that can cause data
loss, an outage, or a security/privacy regression. Apply the 80/20 lens: concentrate the highest
scrutiny on the highest-risk paths (real-time auction/serving path, data-integrity pipelines).
