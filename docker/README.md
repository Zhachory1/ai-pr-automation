# M0 — Agent fleet substrate

One `docker-compose.yml` (repo root) + this dir. Stands up the services the serial agent-server
(M1) depends on. **Inert until M1 wires them** — merging M0 changes no behavior.

## Bring it up

```bash
cp .env.example .env      # then edit: CODE_ROOT (host-absolute), passwords, provider
docker compose up -d      # brings up: db-requests, redis, hindsight (+ hindsight-db)
scripts/m0-verify.sh      # runs the six exit checks
```

## What runs as a service vs. what does not

Verified 2026-09-02 by reading each upstream repo. **Transport matters:**

| Service | Transport | In compose? |
|---|---|---|
| db-requests (our Postgres) | TCP 5432 | yes, `up` |
| redis | TCP 6379 | yes, `up` |
| hindsight (+ hindsight-db) | HTTP :8888 / UI :9999 | yes, `up` |
| swarmvault | **stdio MCP** (`swarmvault mcp`) | **image only** — spawned per agent run in M1 |
| coderag (codebase-memory-mcp) | **stdio MCP** (static binary) | **image only** — see below |

swarmvault and coderag are stdio MCP servers (spawned over stdin/stdout per client), not network
daemons. M0 builds and verifies their images; M1 launches them per run via the agent-server's
`MCP_REMOTE_CONFIG_DIR` hook, with the vault / code dirs mounted.

## Building the stdio MCP images

```bash
# swarmvault — from its own Dockerfile (docker run --rm -i swarmvault mcp)
git clone https://github.com/swarmclawai/swarmvault /tmp/swarmvault
docker build -t agent-fleet/swarmvault /tmp/swarmvault

# coderag — codebase-memory-mcp static binary. Run its MCP server directly;
# do NOT run its `install` (it mutates agent client config files — we don't want that).
# Package the release binary into a thin image; mount ${CODE_ROOT}:ro at run time.
```

coderag indexes **main only**, read-only, continuously; agents track their own worktree diffs.
Worktrees live in `${CODE_ROOT}/_roktcode-worktrees/<repo>/<branch>`. Note: these sit *under*
`${CODE_ROOT}`, so the `:ro` mount still *sees* them — read-only prevents coderag from *writing*,
but excluding worktrees (and dirty main) from indexing is the **indexer's** job (open item #4),
not the mount's. Do not assume the mount alone keeps the graph pristine.

## Open items (resolve at implementation — do not commit a secret regardless)

1. **hindsight keyless provider.** Its compose marks `HINDSIGHT_API_LLM_API_KEY` required, but the
   README says `claude-code`/`codex` need no key. Confirm which `HINDSIGHT_API_LLM_PROVIDER` value
   selects keyless and whether the key var can be empty. `.env.example` leaves the key blank.
2. **hindsight retain/recall REST paths.** `scripts/m0-verify.sh` check [3] uses
   `/v1/banks/{bank}/retain|recall` as a best guess; the upstream README documents the SDK, not raw
   REST. Verify against hindsight's API-reference and adjust the probe if paths differ. (The SDK or
   the per-bank MCP endpoint `/mcp/{bank}/` are alternatives.)
3. **coderag persistent-indexer vs per-query spawn.** If continuous indexing needs a persistent
   process, add coderag as a sidecar service writing a shared graph volume; otherwise it's spawned
   per query. M0 spike decides.
4. **dirty-main guard.** coderag must skip indexing a repo whose main worktree is dirty. Implement in
   the coderag run wrapper; `scripts/m0-coderag-probe.sh` (to add during the spike) tests it.

## Schema

`docker/initdb/01-schema.sql` loads once on first Postgres boot. `requests` (queue+record) and
`pending_decisions` (the daily human-review batch for decision-shaped memory writes). Validated
against postgres:16: dedupe index blocks two active rows for the same `(kind, dedupe_key)` and
allows re-enqueue after `done`.

**hindsight data + PG major version:** `hindsight_pgdata` is mounted at the fixed pg18 PGDATA path
(`/var/lib/postgresql/18/docker`) and the db image is pinned to `pgvector/pgvector:pg18`. This is
deliberate: the mount path is version-specific, and older tags (e.g. `pg16`) use a *different*
PGDATA (`/var/lib/postgresql/data`), so changing the tag without migrating would mount the volume
at the wrong path and silently re-init an empty cluster — data loss, no error. To move majors, do a
`pg_upgrade` or dump/restore and update both the pinned tag and the mount path together.
