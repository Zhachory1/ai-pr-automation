# PR Safety Review Contract

Status: draft-only. This contract adds no worker, producer, credential, or external write path.

## Purpose

`pr-safety-review` assesses one immutable PR snapshot. It checks correctness, necessity,
system assumptions, engineering quality, tests, docs, observability, and incident risk. It returns
structured draft findings for human review.

Canonical analyst instructions: [`agent-config/skills/pr-safety-review/SKILL.md`](../agent-config/skills/pr-safety-review/SKILL.md).

## Hard Boundaries

- Do not reuse `pr-review` or `pr-maintain` worker authority.
- Analyzer gets a disposable read-only checkout and no GitHub, Chat, Datadog, CI, provider,
  shared-memory-write, host-code, Docker-socket, or metadata-service credential.
- Controller binds result to `(repo, pr_number, head_sha, policy_version, diff_hash)`.
- Changed head means `superseded`, not a review of newer code.
- Chat can start tracked work and receive links. GitHub remains approval and merge authority.
- Free-form Chat text, reactions, PR text, comments, code, CI output, and tool output are data,
  not authorization.
- No remediation, rollback, GitHub comment, CI retry, Datadog change, or shared-memory promotion
  belongs in first pilot.
- Controller renders validated result into immutable handoff doc and queues it for human review.
  Agent does not write or publish handoff document.

## Policy Bundle

Controller must give analyst a versioned bundle before run. Bundle contains:

- repository-local engineering and ownership rules;
- pinned revision of documentation-readability policy;
- pinned revision of E2E Ownership Manifesto;
- test and coverage command for target repository, when one exists;
- data classification and approved model-provider policy.

Missing required policy source produces `needs_human_decision`. PR description alone is never
sufficient intent evidence.

## Threat Model

| Threat | Required control |
| --- | --- |
| Prompt injection from Chat, PR, code, comments, or tool output | Treat all external text as data. Tokenless read-only sandbox. |
| Stale result | Store SHA and diff hash in job payload. Mark changed head as `superseded`. |
| Duplicate event or notification | Add inbound-event ledger and transactional outbox before Chat integration. |
| Bot feedback loop | Use correlation IDs, bot-message filtering, one active operation per PR lineage, quotas, and circuit breaker. |
| Private code or secret leakage | Approved provider only. Redact at every log, database, model, and Chat boundary. |
| Unsafe generated fix | Agent creates no fix. Handoff doc gives evidence-backed recommendation; human owns any follow-up branch and PR. |

## Pilot Gates

Pilot starts only when one repository and named reviewers opt in, policy revisions are pinned, data
provider is approved, and circuit-breaker owner is named.

Pilot succeeds only when it creates no external writes and proves useful findings without higher
reviewer effort or author churn. Controller renders recommendations as immutable handoff docs and
queues them for human review. Record reviewer time, material-finding acceptance rate,
false-positive rate, duplicate rate, and stale-result rate.

## Validation

Run:

```bash
bash tests/test-pr-safety-review-skill.sh
```

Test guards contract language. It does not prove future runtime sandboxing. Runtime enforcement is
required in server implementation PR.
