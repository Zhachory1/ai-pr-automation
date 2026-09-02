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
| swarmvault | **stdio MCP** (`swarmvault mcp`) | **no — spawned per client** | Dockerfile is `docker run --rm -i`. Not a daemon. |
| coderag | **stdio MCP** (static binary) | continuous indexer, but MCP is stdio | Indexes `${CODE_ROOT}` main read-only. |

### The transport finding (updates the roadmap)

Roadmap framed all three memory services as "always-on containers." Verified reality:
**only hindsight is a network service.** swarmvault and coderag are **stdio MCP servers** —
they're *spawned per agent invocation* over stdin/stdout, not connected to over a port.

Consequence for M0:
- hindsight runs as a compose service with a healthcheck (real endpoint to probe).
- swarmvault + coderag: M0 builds their **images** and verifies each starts and answers MCP
  introspection via `docker run --rm -i`. The agent-server (M1) will launch them per-run with the
  code/vault dirs mounted, via the existing `MCP_REMOTE_CONFIG_DIR` hook. They are NOT `depends_on`
  services that must be "up."
- coderag "continuous indexing": the indexer watches a mounted tree, but agents reach the *graph*
  through a stdio MCP spawn. M0 proves the indexer builds a graph over `${CODE_ROOT}` main and the
  stdio server answers a query against it. Whether the indexer runs as a persistent sidecar writing
  to a shared graph volume, or is re-run per query, is an M0 spike outcome (see risks).

This does not change the milestone's value — it changes what "the container is up" means for two of
five services, and it's cheaper to learn now than in M1.

## Data / control flow (M0 scope only)

```
docker compose up
  ├─ postgres (ours)  ──── volume: pgdata ────────────► host
  ├─ redis            ──── volume: redisdata ─────────► host
  └─ hindsight + hindsight-db  ── volume: hs_pgdata ──► host   (HTTP :8888)

images built, verified via `docker run --rm -i` (not up):
  ├─ swarmvault   (vault dir mounted at M1)
  └─ coderag      (${CODE_ROOT}:ro mounted; graph volume ──► host)
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
- **swarmvault/coderag as build-and-verify, not up.** Forced by stdio transport; see finding above.

## Observability / rollout

- No telemetry in M0 (nothing runs yet). A probe script (`scripts/m0-verify.sh`) runs the six exit
  checks and prints pass/fail.
- Rollout: merge to main behind nothing — files are inert until M1 wires them. Reversible: delete
  the files.

## Risks

1. **coderag persistent-indexer vs per-query spawn is unresolved** (stdio finding). M0 spike
   determines it; if it must be a persistent sidecar writing a shared graph volume, that's a small
   addition, not a redesign.
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
