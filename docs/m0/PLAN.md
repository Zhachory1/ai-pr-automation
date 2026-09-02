# Implementation Plan — M0 Compose Substrate

Run: m0-compose-20260902 · council_level: single-lens (stdio finding is a source-verified
simplification, not multi-domain/high-risk; reflected in DD risks). No blocker.

Ordered tasks. Each has acceptance + validation. Stop conditions noted.

## T1 — repo scaffolding + .env shape
- Add `docker/` dir, `.gitignore` for `.env` + `*.local`, `.env.example` (CODE_ROOT,
  HINDSIGHT_DB_PASSWORD, HINDSIGHT_API_LLM_API_KEY-or-keyless, ports).
- Accept: `.env.example` committed, `.env` gitignored, no secret in git.
- Validate: `git check-ignore .env` passes; `git grep -i` finds no key/password value.

## T2 — SQL init file
- `docker/initdb/01-schema.sql`: `requests` + `pending_decisions` per DD (real DDL:
  `BIGINT GENERATED ALWAYS AS IDENTITY`), partial unique index, checks.
- Accept: file present, valid SQL.
- Validate: `psql` runs it clean against a scratch PG (or compose boot loads it via
  `/docker-entrypoint-initdb.d/`).

## T3 — compose: our Postgres + Redis
- Services `db-requests` (postgres, initdb mount, named volume, port 5432) + `redis` (named
  volume, port 6379). Healthchecks.
- Accept: both come up healthy.
- Validate: `psql` lists both tables; `redis-cli ping` → PONG.

## T4 — compose: hindsight (adopt external-pg block)
- Reference hindsight's `external-pg` services (hindsight-app + hindsight-db + hs volume), our
  network, `${HINDSIGHT_*}` from `.env`. Add healthcheck on :8888.
- Accept: hindsight healthy; `/mcp/{bank}/` reachable.
- Validate: retain then recall round-trip via curl/SDK on a scratch bank.
- Stop condition: if keyless provider var is unclear (risk #2), pause + resolve before committing a
  working default; never commit a key.

## T5 — swarmvault + coderag images (build + verify, NOT up)
- Build swarmvault image from its Dockerfile; build/pull coderag; wire coderag to run its MCP server
  directly (bypass `install` config mutation, risk #3).
- coderag: mount `${CODE_ROOT}:ro`, produce a graph over main.
- Accept: `docker run --rm -i swarmvault mcp` answers MCP introspection; coderag stdio answers one
  structural query over ${CODE_ROOT} main.
- Validate: probe both via a minimal MCP handshake.
- Spike (risk #1): determine persistent-indexer vs per-query; record outcome in DD. If persistent,
  add coderag as a sidecar service writing a shared graph volume.

## T6 — dirty-main guard for coderag
- coderag indexing skips a repo whose main worktree is dirty (`git status` not clean).
- Accept: deliberately dirty a scratch repo under ${CODE_ROOT}; coderag skips it, indexes others.
- Validate: probe shows skipped repo absent/stale, clean repos present.

## T7 — verify script + README
- `scripts/m0-verify.sh` runs all six PRD exit checks, prints pass/fail per check.
- README section: `cp .env.example .env`, set values, `docker compose up`, `scripts/m0-verify.sh`.
- Accept: script exits 0 with all six green on the operator laptop.
- Validate: full `docker compose down` + `up`, re-run script — data survives (check 6).

## Rollout / stop
- Merge behind nothing (inert infra). Reversible = delete files.
- Hard stop → return to DD if: coderag cannot index main-only without a persistent daemon that
  dirties the tree, or hindsight cannot run without a committed secret. Either is a design mismatch,
  not a patch.
