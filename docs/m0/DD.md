# DD — M0 Compose Substrate

Run: m0-compose-20260902 · Mode: default
Sources verified 2026-09-02 via `gh` (repo contents/READMEs). Key facts cited inline.

## Architecture

Five services, one `docker-compose.yml` in `ai-pr-automation`. Split by transport, which is the
load-bearing design fact discovered while reading the repos:

| Service | Transport | Long-running? | Notes |
|---|---|---|---|
| our Postgres | TCP 5432 | yes | request tracking (`requests`, `pending_decisions`). |
| Redis | TCP 6379 | yes | dispatch (used M1+; stood up now). |
| hindsight | **HTTP :8888 / UI :9999** | yes | network service; per-bank MCP at `/mcp/{bank}/`. Has its own pgvector Postgres (`hindsight-db`). |
| swarmvault | **stdio MCP** (`swarmvault mcp`) + local watcher | yes | Compose watcher owns mounted vault. MCP still starts per client. |
| coderag | **stdio MCP** (static binary) + local UI | yes | Compose keeps daemon stdin open. Index is limited to `${CODE_ROOT}` read-only. |

### Transport now

MCP uses stdio. Shared local state needs long-running services.

- hindsight runs HTTP with healthcheck.
- swarmvault watcher runs in Compose over mounted vault. `swarmvault mcp` still starts per client.
- coderag keeps its native daemon and local UI alive. UI is localhost-only. Code mount is read-only.
- agent clients must use same `CBM_CACHE_DIR` before attaching to coderag daemon.

## Data / control flow (M0 scope only)

```
docker compose up
  ├─ postgres (ours)  ──── volume: pgdata ────────────► host
  ├─ redis            ──── volume: redisdata ─────────► host
  ├─ hindsight + hindsight-db  ── volume: hs_pgdata ──► host   (HTTP :8888)
  ├─ swarmvault watcher ─────── mounted vault ─────────► host
  └─ coderag daemon + UI ────── graph volume ──────────► host
```

No service consumes another in M0. Schema is created, not read. Redis is idle. This is
substrate-only.

## Schema (created M0, consumed M1+)

```sql
CREATE TABLE requests (
  id            BIGGENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  kind          TEXT NOT NULL,                 -- 'pr-review', ...
  payload       JSONB NOT NULL,                -- identifiers only, never secrets
  dedupe_key    TEXT NOT NULL,                 -- e.g. 'owner/repo#123@sha'
  status        TEXT NOT NULL DEFAULT 'queued' -- queued|running|done|failed
                  CHECK (status IN ('queued','running','done','failed')),
  fail_response TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at    TIMESTAMPTZ,
  finished_at   TIMESTAMPTZ
);
-- cron firing twice for same work while in flight = no-op
CREATE UNIQUE INDEX requests_dedupe_active
  ON requests (kind, dedupe_key)
  WHERE status IN ('queued','running');

CREATE TABLE pending_decisions (
  id           BIGGENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  request_id   BIGINT NOT NULL REFERENCES requests(id),
  kind         TEXT NOT NULL,
  proposal     JSONB NOT NULL,                 -- typed structured output from the agent
  provenance   JSONB NOT NULL,                 -- run_id, repo#PR@sha, status, written_by
  state        TEXT NOT NULL DEFAULT 'pending' -- pending|approved|rejected
                  CHECK (state IN ('pending','approved','rejected')),
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at   TIMESTAMPTZ
);
```
(`BIGGENERATED` above is shorthand — real DDL uses `BIGINT GENERATED ALWAYS AS IDENTITY`.)

Schema shipped as a migration file the Postgres service runs on first boot
(`/docker-entrypoint-initdb.d/`). No ORM, no migration tool in M0 — one `.sql` file.

## Mounts + secrets

- coderag: `${CODE_ROOT}:ro` — read-only, structurally cannot dirty the tree.
- Code-root is host-absolute via `.env` (`CODE_ROOT=${CODE_ROOT}`, e.g. `~/code`), never `./`.
- `.env` gitignored; `.env.example` committed with shape only (`CODE_ROOT`, `HINDSIGHT_DB_PASSWORD`,
  `HINDSIGHT_API_LLM_API_KEY` or keyless provider var, ports).
- Data volumes are named docker volumes bound to host — never in git.

## Tradeoffs / decisions

- **Adopt hindsight's own `external-pg` compose block** rather than authoring one. It ships a
  pgvector db + app wiring; we reference it. (Verified: `docker/docker-compose/external-pg`.)
- **hindsight keeps its own Postgres**, separate from our request Postgres. Rejected sharing one PG:
  hindsight's is pgvector-specific and its schema is theirs; coupling buys nothing and risks their
  migrations touching our tables.
- **One `.sql` init file, no migration framework.** M0 has one schema version; a tool is premature.
- **swarmvault/coderag as local sidecars.** User needs durable watcher and graph UI. MCP stays stdio.

## Observability / rollout

- A probe script (`scripts/m0-verify.sh`) runs six exit checks and prints pass/fail.
- Rollout: merge to main, then build and start optional sidecars. Reversible: stop sidecars and keep
  persisted data volumes.

## Risks

1. **coderag client wiring is unresolved.** Clients must share `CBM_CACHE_DIR` and exact build before
   using daemon state.
2. **hindsight may require an LLM API key even for keyless providers.** Its compose marks
   `HINDSIGHT_API_LLM_API_KEY` required (`:?`). README says `claude-code`/`codex` need none —
   resolve which env var selects keyless at implementation; do not commit a key regardless.
3. **coderag `install` mutates agent config files.** We want its MCP server + indexer only. Build
   the image to run the server directly, bypassing `install`'s client-config step.
4. **macOS Docker VM clock/sleep** — noted for M1 (workers), not M0 (no timing here).

## Alternatives considered

- SQLite instead of Postgres for the request table (council dissent): rejected for M0 because we're
  already running Postgres containers and the queue is M1's concern; M0 just needs the table to
  exist. Revisit only if M1 shows no contention need — but the cost of PG here is ~zero (a container).
- Homing infra in a separate `agent-fleet-infra` repo: rejected per operator — one repo owns the
  system.
