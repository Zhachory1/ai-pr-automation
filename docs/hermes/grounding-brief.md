# Grounding Brief: Hermes M0–M2

## Objective

- workflow: plan-to-launch, deep mode
- decision supported: how to start Hermes migration without weakening current fleet guarantees
- scope: M0 contract/baseline work, first Hermes Compose service, scheduler pilot, doc-runtime pilot
- non-goals: live review migration, maintenance migration, PR-safety migration, Postgres retirement

## Source Inventory

| Source | Type | Pointer | Freshness | Relevance |
| --- | --- | --- | --- | --- |
| Hermes migration roadmap | accepted direction | [`../hermes-migration-roadmap.md`](../hermes-migration-roadmap.md) | 2026-09-14 | Scope, order, gates, retained controls |
| Current fleet runtime | code | [`../../docker-compose.yml`](../../docker-compose.yml) | current branch | Service, network, credential, and volume boundaries |
| Queue contracts | code | [`../../lib/queue.sh`](../../lib/queue.sh) | current branch | Lease, nonce, lineage, and reconciliation authority |
| Review producer | code | [`../../bin/pr-producer`](../../bin/pr-producer) | current branch | M1 command and runtime dependencies |
| Doc worker | code | [`../../bin/doc-writer-server`](../../bin/doc-writer-server) | current branch | M2 queue and side-effect boundary |
| Doc harness | code | [`../../bin/doc-writer`](../../bin/doc-writer) | current branch | M2 model seam and output contract |
| Current model runner | code | [`../../bin/mewritecode-runner.sh`](../../bin/mewritecode-runner.sh) | current branch | Existing typed-result fallback |
| Current validation | tests | [`../../tests/test-producer-dedupe.sh`](../../tests/test-producer-dedupe.sh), [`../../tests/test-single-instance.sh`](../../tests/test-single-instance.sh), [`../../tests/test-doc-writer.sh`](../../tests/test-doc-writer.sh) | current branch | Retained proof and missing cases |
| Hermes image | upstream artifact | `nousresearch/hermes-agent@sha256:6d7285e1476d0661fc347e3d55245c99decb781d76d39d67582166d2c9561874` | commit `14efb460`, 2026-09-14 | Reproducible runtime baseline |
| Hermes Runs API | upstream public contract | [`api-server.md`](https://github.com/NousResearch/hermes-agent/blob/14efb46089250e8b9e56e59b74291cf8dce8b207/website/docs/user-guide/features/api-server.md#runs-api-streaming-friendly-alternative) | pinned commit | M2 integration boundary |
| Hermes run implementation | upstream code | [`api_server_runs.py`](https://github.com/NousResearch/hermes-agent/blob/14efb46089250e8b9e56e59b74291cf8dce8b207/gateway/platforms/api_server_runs.py) | pinned commit | Status, interruption, opaque output, idempotency behavior |
| Hermes idempotency store | upstream code | [`api_server_run_idempotency.py`](https://github.com/NousResearch/hermes-agent/blob/14efb46089250e8b9e56e59b74291cf8dce8b207/gateway/platforms/api_server_run_idempotency.py) | pinned commit | 24-hour attempt-level admission record |
| Hermes Docker contract | upstream code/docs | [`docker-compose.yml`](https://github.com/NousResearch/hermes-agent/blob/14efb46089250e8b9e56e59b74291cf8dce8b207/docker-compose.yml), [`SECURITY.md`](https://github.com/NousResearch/hermes-agent/blob/14efb46089250e8b9e56e59b74291cf8dce8b207/SECURITY.md) | pinned commit | `/opt/data`, PID 1, UID/GID, and isolation rules |
| Architecture/reliability/MVP review | council evidence | plan-to-launch subagent reviews, 2026-09-14 | current run | Required design changes and test gaps |

## Facts

- fact: current Postgres queue is domain authority for claims, nonce fencing, lineage exclusion, terminal state, and ambiguous-effect reconciliation.
  - evidence: `lib/queue.sh`, `bin/agent-server`, queue SQL migrations
  - confidence: high
- fact: Hermes `/v1/runs` gives durable idempotent admission only when caller sends `Idempotency-Key`; record retention is 24 hours.
  - evidence: pinned Hermes Runs API and idempotency implementation
  - confidence: high
- fact: Hermes run output is opaque text. Runs API does not enforce this fleet's result schemas.
  - evidence: pinned `api_server_runs.py`; current `AGENT_RESULT_FILE` contract
  - confidence: high
- fact: official Hermes image entrypoint dispatcher delegates to `/init` for normal PID-1 startup. Compose must not set `user:`, `init: true`, or replace default entrypoint.
  - evidence: pinned Hermes Dockerfile, `entrypoint-dispatch.sh`, wrapper, and Compose file
  - confidence: high
- fact: official image stores mutable state under `/opt/data`. Two gateways cannot share one state volume.
  - evidence: pinned Hermes Docker documentation
  - confidence: high
- fact: profiles separate Hermes state but do not isolate host files, network, or credentials.
  - evidence: pinned Hermes security and profile documentation
  - confidence: high
- fact: review producer needs `gh`, `jq`, `psql`, and GNU timeout. Official Hermes image does not provide all of them.
  - evidence: `bin/pr-producer`; pinned Hermes image contents review
  - confidence: high
- fact: Hermes cron script environment strips `GH_TOKEN`; M1 needs mounted-secret or trusted wrapper design.
  - evidence: pinned Hermes cron and environment-policy implementation
  - confidence: high
- fact: doc worker currently ignores failure to record side-effect intent. Crash after file write can cause suffixed duplicate output on replay.
  - evidence: `bin/doc-writer-server`; `bin/doc-writer`; reliability review
  - confidence: high
- fact: repo has no baseline collector for roadmap metrics and no executable clean-host restore/rollback test.
  - evidence: repo search and existing tests
  - confidence: high

## Prior Decisions And Context

- decision/context: Docker Compose stays sole deployment entrypoint.
  - source: user direction and migration roadmap
  - implication: Hermes lands as pinned Compose services. No host gateway setup.
- decision/context: one Hermes service/state volume per trust tier.
  - source: migration roadmap and Hermes security model
  - implication: doc, review, SWE, maintain, and PR-safety never share one Hermes profile as isolation.
- decision/context: existing controller and publishers stay authoritative until parity proof.
  - source: roadmap council
  - implication: first integration uses public Runs API behind current queue. No direct Hermes GitHub delivery.
- decision/context: M0 implementation approved with changes. M1/M2 investigation is permitted; activation is blocked.
  - source: current user instruction and M0–M2 docs council
  - implication: current implementation stops at inert M0 evidence scaffold.

## Constraints

- constraint: use exact Hermes image digest and public interfaces only.
  - source: roadmap and upstream release behavior
  - impact: no mutable tag, source fork, private Python import, or direct SQLite access.
- constraint: API key must be non-placeholder and at least 16 characters; use 32 random bytes operationally.
  - source: pinned Hermes API server implementation
  - impact: M0 config must require secret input and avoid host port publication.
- constraint: liveness and readiness differ. `/health/detailed` can return HTTP 200 while degraded.
  - source: pinned Hermes readiness implementation
  - impact: readiness probe must authenticate and parse top-level status.
- constraint: Hermes attempt ID cannot replace operation ID or queue nonce.
  - source: current queue and Hermes idempotency semantics
  - impact: key uses operation ID plus attempt nonce. Domain ledger stores one-to-many run attempts.
- constraint: no live cutover before hermetic contract tests and rollback proof.
  - source: reliability review
  - impact: M0 can scaffold and test. M1/M2 routing stays disabled until gates pass.

## Resolved Conflict

- initial roadmap ranked scheduler pilot ahead of doc runtime;
- pinned upstream facts show scheduler needs derivative image, producer dependencies, slot accounting, route fencing, and secret-file bridge;
- doc runtime uses official image and public Runs API but needs containment and publication recovery first;
- council accepted investigation order M0 → M2 → M1. Milestone IDs stay stable.

## Risks And Unknowns

- risk/unknown: minimum Hermes config needed for provider-backed Runs API in non-interactive Compose.
  - why it matters: service can be healthy but unable to run model work.
  - owner or next check: M0 local startup probe with private runtime key
- risk/unknown: current doc side-effect contract is unsafe across crash.
  - why it matters: M2 can duplicate generated files.
  - owner or next check: fix and test before live M2 route
- risk/unknown: 14-day baseline is not available yet.
  - why it matters: roadmap comparisons cannot be evaluated.
  - owner or next check: add collector now; wait for window before live scheduler gate
- risk/unknown: clean-host rollback artifact set is undefined.
  - why it matters: image rollback alone does not restore Hermes state and active operation mapping.
  - owner or next check: DD and implementation plan
- risk/unknown: whether Hermes Kanban ever reduces more code than its bridge adds.
  - why it matters: long-term control-plane shape remains open.
  - owner or next check: M8 only; does not block M0–M2

## Proceed Gate

- status: ready
- reason: council approved inert M0 evidence work. M1/M2 activation remains blocked.
- next workflow step: execute `plan-m0-evidence-scaffold.md`. Start separate plan-to-launch run for each child design.
