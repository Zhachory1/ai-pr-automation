# Grounding Brief: Hermes M2 Doc Runtime

## Objective

- workflow: plan-to-launch, deep mode
- decision: replace doc-writer model calls with pinned Hermes Runs API without moving queue, human loop, council policy, or inbox publisher into Hermes
- scope: M2 design, hermetic/runtime validation, shadow, ten-run pilot, rollback gate
- non-goals: M1 scheduler, PR review, maintenance, SWE, PR-safety, Kanban, Postgres retirement

## Source Inventory

| Source | Pointer | Finding |
| --- | --- | --- |
| M0 package | [`README.md`](README.md) | M0 merged; M2 activation still blocked |
| M0 council | [`council-m0-m2.md`](council-m0-m2.md) | M2 needs containment, attempt ownership, publication recovery, rollback, machine gate |
| Parity matrix | [`parity-matrix.md`](parity-matrix.md) | current doc effect-intent/failure/reclaim paths can duplicate output |
| Doc controller | [`../../bin/doc-writer-server`](../../bin/doc-writer-server) | Postgres claim/lease owner; must remain sole state writer and publisher |
| Doc harness | [`../../bin/doc-writer`](../../bin/doc-writer) | builds prompts, invokes model/council, parses questions, currently writes inbox directly |
| Queue | [`../../lib/queue.sh`](../../lib/queue.sh) | nonce fencing; generic stale reclaim requeues effect-bearing doc work |
| Queue schema | [`../../docker/initdb/02-agent-server.sql`](../../docker/initdb/02-agent-server.sql) | generic side-effect time and posted ref exist; no prepared path/digest |
| Human queue | [`../../docker/initdb/03-human-review-queue.sql`](../../docker/initdb/03-human-review-queue.sql) | one row per request; current insert and request completion are separate transitions |
| M0 Compose | [`../../docker-compose.yml`](../../docker-compose.yml) | pinned Hermes service is disabled, has no provider/config/doc mounts, no current dependency |
| Hermes Runs API | [pinned API docs](https://github.com/NousResearch/hermes-agent/blob/14efb46089250e8b9e56e59b74291cf8dce8b207/website/docs/user-guide/features/api-server.md#runs-api-streaming-friendly-alternative) | idempotent admission, poll, stop, opaque output; 24-hour retention |
| Hermes tool resolution | [pinned `tools_config.py`](https://github.com/NousResearch/hermes-agent/blob/14efb46089250e8b9e56e59b74291cf8dce8b207/hermes_cli/tools_config.py#L548-L662) | explicit `platform_toolsets.api_server: [no_mcp]` resolves no built-in or MCP toolsets when plugin discovery is off |
| Hermes memory docs | [pinned memory config](https://github.com/NousResearch/hermes-agent/blob/14efb46089250e8b9e56e59b74291cf8dce8b207/website/docs/user-guide/features/memory.md#L238-L278) | both built-in stores disabled only when both flags false; external provider must stay empty |
| Hermes safe mode | [pinned plugin manager](https://github.com/NousResearch/hermes-agent/blob/14efb46089250e8b9e56e59b74291cf8dce8b207/hermes_cli/plugins.py#L1213-L1229) | `HERMES_SAFE_MODE=1` skips plugin discovery; MCP loader and hooks also fail closed |
| Hermes API agent construction | [pinned `api_server.py`](https://github.com/NousResearch/hermes-agent/blob/14efb46089250e8b9e56e59b74291cf8dce8b207/gateway/platforms/api_server.py#L2116-L2179) | API toolsets come from profile config; concurrency cap is config-driven |
| Hermes security model | [pinned `SECURITY.md`](https://github.com/NousResearch/hermes-agent/blob/14efb46089250e8b9e56e59b74291cf8dce8b207/SECURITY.md#L32-L107) | prompt/tool checks are not containment; OS/container boundary is authoritative |
| Existing egress pattern | [`../../docker/pr-safety-egress.conf`](../../docker/pr-safety-egress.conf), [`../../tests/test-pr-safety-egress.sh`](../../tests/test-pr-safety-egress.sh) | internal worker network plus Squid allowlist already exists; doc tier needs separate allowlist |
| M0 baseline | live read-only collector, 2026-09-14 | 1,267 terminal requests/14d; failure 12.0%; retry 4.4%; reconcile 4.7%; seven pending human items; target audits unavailable |

## Facts

- M0 focused suite passed after merge.
- Hermes image stays pinned at `sha256:6d7285e1476d0661fc347e3d55245c99decb781d76d39d67582166d2c9561874`.
- Hermes `/v1/runs` does not enforce this repo's output schema or output-size contract.
- API idempotency is useful only when body and key stay exact. Unknown submission after retention cannot be replayed safely.
- A doc request row is immutable enough to own one Hermes draft run. Council phase needs a second phase key.
- Queue ownership nonce changes on reclaim. Hermes run identity must not use queue nonce.
- Current final document bytes are created inside harness and written directly to inbox before controller records done.
- Current `queue_mark_side_effect` call happens before model work and ignores failure. It does not describe actual publication boundary.
- Current open-question insert and request completion can split on crash, though unique request ID prevents duplicate queue rows.
- Full handbook is 214,433 bytes. Prompt embedding fits Hermes 10 MB request cap but increases tokens.
- Hermes can run with zero API toolsets using explicit `[no_mcp]`, safe mode, disabled built-in memory, empty external provider, disabled background review, one turn, and one concurrent run. Runtime conformance still must prove actual provider request has no tools.
- Stock image needs root s6 bootstrap, then drops gateway to Hermes user. `read_only` and `cap_drop: ALL` are not assumed compatible.
- Current doc writer receives localhost-operator input, not public webhook input. Trusted controller compromise is outside M2 threat model; prompt-controlled tool execution and network access remain in scope.

## Decisions

### Keep Controller And Publisher

`doc-writer-server` remains:

- sole Postgres writer;
- queue lease owner;
- human-review transition owner;
- final path/digest owner;
- inbox publisher;
- rollback switch owner.

Hermes receives prompt bytes and returns text. It gets no inbox, stage, handbook, code, DB, GitHub, Hindsight, Coderag, or Docker access.

### Direct Internal API, No Inbound Proxy

No runs proxy in M2.

Reason:

- controller is trusted and already has DB/inbox authority;
- Hermes API is on internal doc network with one caller and bearer key;
- proxy adds another retry/auth/health contract without containing trusted-controller compromise.

Revisit only if more callers join or controller compromise enters threat model.

### Separate Provider Egress Proxy

Use doc-specific Squid service and config.

- Hermes joins internal `hermes-doc` network only.
- Doc controller joins default plus internal doc network.
- Egress proxy joins default plus internal doc network.
- Proxy allows CONNECT only to `api.openai.com:443`.
- Direct outbound traffic from Hermes has no route.
- PR-safety proxy is not shared.

Squid contains prompt-influenced network access under no-tools model. It is not a boundary against compromised Hermes process or DNS tunneling; those remain outside M2 threat model.

### Embed Policy, Do Not Mount Handbook

Controller builds two fields:

- `instructions`: persona, exact selected handbook bundle, no-tool/no-memory notice, strict output contract;
- `input`: title, doc type, requirements or prior draft and answers.

Hermes gets no file tools and no handbook mount. Hindsight/Coderag lookup is removed from M2 runtime. Missing project context becomes an Open Question. Pilot quality gate decides whether this loss is acceptable.

### One Hermes Run Per Request Phase

Stable identity:

- draft: `doc:<request_id>:draft`;
- council: `doc:<request_id>:council`.

New request row means new operation. Queue reclaim adopts same phase run. Queue nonce never enters Hermes key.

## Constraints

- model/provider fixed to `openai-api` and `gpt-5.6-sol`; request cannot override;
- API profile toolsets exactly `[no_mcp]`;
- built-in memory and user profile false; external provider empty;
- background review false; skill creation nudge zero; safe mode on;
- max concurrent API runs one; agent max turns one; provider retries zero;
- no session ID, previous response, caller history, model options, or continuation headers;
- maximum request body below 1 MiB; maximum accepted output below existing harness limit plus explicit cap;
- no automatic new idempotency key after ambiguous submit, timeout, interruption, invalid output, or retention expiry;
- pilot has at most ten accepted Hermes draft runs plus one council run per finalized document;
- OpenAI project-level spend cap required before live pilot because pinned Runs API lacks caller output-token cap;
- no live route before runtime conformance, crash matrix, deterministic lost-state quarantine, rollback, and human gate pass.

## Risks And Unknowns

| Risk/Unknown | Impact | Next Check |
| --- | --- | --- |
| exact stock-image config file survives s6 startup read-only | no-tools config can drift or fail startup | local pinned-image conformance |
| empty toolsets still leak dynamic tool | prompt can cause action | inspect `/v1/toolsets` and captured provider request |
| provider SDK honors proxy env in pinned image | direct egress or failed runs | internal-network allow/deny runtime test |
| no output-token override in pinned Runs API | cost ceiling depends on provider project cap | require external cap; record usage |
| prompt-embedded handbook changes quality/cost | worse docs or excess spend | paired shadow sample before ten-run pilot |
| source and final publication recovery schema | duplicate or stuck doc | DD state machine and fault council |
| current council fallback behavior | finalized docs may still use old runtime | phase-specific idempotency and tests |
| state volume stores prompt/output | privacy and cross-run persistence | inspect actual volume; 24h+margin retention; no general backup; lost state quarantines attached work |
| exact rollback RTO | human gate cannot launch | scripted drill before pilot |

## Proceed Gate

- status: ready
- reason: M2a non-routing foundation passed council with nits. M2b live activation remains blocked.
- next step: execute Task 0 cheap-failure spikes from `plan-m2a-doc-foundation.md`.
