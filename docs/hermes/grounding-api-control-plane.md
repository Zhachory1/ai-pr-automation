# Grounding Brief: API-Driven Hermes Control Plane

## Objective

- workflow: deep plan-to-launch
- decision supported: replace host fork/exec dispatcher and launchd producers with Compose producers + queue controller calling host Hermes Runs API
- scope: queue claim/lease/settle, producers, per-kind concurrency, Hermes API/profile auth, deterministic doc/safety/memory post-processing, cutover/rollback
- non-goals: replace Postgres, Fleet Controller UI, host-native Hermes gateway/dashboard, provider OAuth, profile definitions, GitHub branch protection

## Source Inventory

| Source | Type | Pointer | Freshness | Relevance |
|---|---|---|---|---|
| User decision | primary | current conversation, 2026-09-21 | current | Compose should own deterministic producers/controller; use existing Hermes server API |
| Runs API docs | upstream | `~/.hermes/hermes-agent/website/docs/user-guide/features/api-server.md:430-470,644-706` | pinned local install | request/status/auth/idempotency/concurrency contract |
| Runs API implementation | upstream code | `~/.hermes/hermes-agent/gateway/platforms/api_server_runs.py:381-648,715-723` | pinned local install | durable admission, polling, restart semantics, output |
| Profile multiplex docs | upstream | `~/.hermes/hermes-agent/website/docs/user-guide/multi-profile-gateways.md:87-104,439-445` | pinned local install | one gateway serves installed profiles |
| Current dispatcher | repo code | `bin/hermes-dispatcher` | current main | current host claim/concurrency/spawn/drain behavior |
| Current executors | repo code | `bin/hermes-{queue-runner,doc-write-runner,pr-safety-runner,memory-curate}` | current main | role-specific controller/model boundaries |
| Queue API | repo SQL | `docker/initdb/09-hermes-yaml-authority.sql`, `12-hermes-lease-reclaim.sql` | current main | leases, nonce fencing, retry/reconcile |
| Existing host-native DD | prior decision | `docs/hermes/DD-host-native-agent-engine.md` | current but superseded in part | accepted OS-account/full-autonomy model |
| Existing plan | prior plan | `docs/hermes/plan-host-native-autonomous-hermes.md` | partially implemented | role invariants, rollout gates |
| Live evidence | runtime | pr-review #2273; safety #2285 | 2026-09-20/21 | proves host profiles run; exposed fork/exec and stale-lease complexity |

## Facts

- Hermes already supports `POST /p/<profile>/v1/runs`, status polling, stop, SSE, durable idempotency keys, and per-profile auth. Evidence: upstream Runs API docs/code. Confidence: high.
- Identical POST retries with the same idempotency key/body replay the original run ID; body mismatch returns 409. Idempotency persists across gateway restart for 24h after status update. Confidence: high.
- Gateway restart marks stale active runs `interrupted`; polling returns that terminal status. Confidence: high.
- Named profile routes require that profile's own `API_SERVER_KEY`; default key fails closed. Confidence: high.
- Gateway global run cap defaults to 10 and returns 429 for new runs at capacity. Confidence: high.
- Current host dispatcher duplicates Hermes lifecycle: forks CLI, tracks children, injects work root, renews DB lease. Producers are also host launchd jobs. Confidence: high.
- Current profiles are not OS sandboxes. Effective authority is the union of `hermes-agent` credentials. User explicitly accepted full autonomy. Confidence: high.
- Doc-write, pr-safety, and memory-curate have deterministic post-processing that must remain outside model output trust. Confidence: high.
- Docker Desktop can reach a host loopback Hermes service through `host.docker.internal`; current nginx-to-dashboard route proves this locally. Portability beyond Docker Desktop requires an explicit host-gateway contract. Confidence: medium.

## Prior Decisions And Context

- Keep Hermes host-native under dedicated `hermes-agent`; Compose is support/control plane. Source: host-native DD + user reaffirmation.
- Branch protection and scoped credentials are server-side walls; YAML grant is scope-of-attention only. Source: `DD-authority-and-memory.md`.
- Full autonomy means review/maintain/SWE profiles may perform GitHub/Git effects directly. Human owns merge. Source: user decision + host-native DD.
- Deterministic gates remain mandatory for doc exact-byte publication, safety incident routing, and memory writes. Source: current SQL/runners/tests.
- Consumer timers rejected. Queue work should start on arrival under per-kind caps. Source: user decision.

## Constraints

- No Docker socket, SSH-back-to-host, mounted provider OAuth, or mounted deploy keys in Compose.
- Compose controller sees only Hermes API keys, DB credentials, producer GitHub read token, and required deterministic-effect mounts.
- API server stays host loopback. Compose reaches it through Docker Desktop host gateway.
- Every queue attempt needs durable operation/run identity before or recoverably across POST.
- Unknown direct-profile effects must reconcile, never blind-retry.
- Per-kind caps: maintain 3; review 1; SWE 1; doc 1; memory 1; safety 1. Gateway global cap >=8.
- Existing Fleet Controller human approval paths and SQL invariants must remain.

## Risks And Unknowns

| Risk / unknown | Why it matters | Owner / next check |
|---|---|---|
| Per-profile API key provisioning | named route fails without unique key; keys must not enter repo/logs | implementation task: generated secret bundle + profile `.env` sync |
| Direct-effect run loses DB lease | host agent can continue after controller ownership loss | DD must define stop + reconcile; effectful rows mark uncertainty before submit |
| Crash after POST before storing run ID | duplicate or stranded run | persist attempt/idempotency key/body digest first; replay exact POST |
| Runs output schema is opaque text | controller cannot trust model output | strict per-kind parser/schema; malformed → failed/reconcile |
| Specialized post-processing mounts | doc/safety/memory need deterministic effects | Compose controller mounts shared runtime/inbox paths only |
| API reachability on non-Docker-Desktop | host.docker.internal semantics differ | launch target is macOS Docker Desktop; explicitly non-goal Linux portability in first slice |
| Existing docs contain stale enrollment claims | could mislead implementation | supersede/update DD and plan during doc gate |

## Proceed Gate

- status: council-first
- reason: architecture corrects a major seam, but direct-effect lease loss, durable admission, profile API keys, and specialized post-processing require explicit design
- next workflow step: PRD + DD, then one bounded four-person architecture/security/reliability/simplicity council
