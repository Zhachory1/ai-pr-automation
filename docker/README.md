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

Verified 2026-09-02 by reading each upstream repo. **All memory services are persistent + shared
across agents** — the transport differs but none is spawned-fresh-per-agent-with-private-state:

| Service | Shared how | In compose? |
|---|---|---|
| db-requests (our Postgres) | TCP 5432 | yes, `up` |
| redis | TCP 6379 | yes, `up` |
| hindsight (+ hindsight-db) | network service, HTTP :8888 / UI :9999 | yes, `up` |
| coderag (codebase-memory-mcp) | **shared coordination daemon** + per-agent thin stdio frontend | yes, `up` (daemon) |
| swarmvault | **shared vault volume + `watch` daemon**; per-agent stdio MCP is read+trigger only | yes, `up` (watcher) |

### coderag — native shared daemon (verified)

CBM ships a **per-account coordination daemon** that owns the shared knowledge graph, background
watchers, continuous indexing, and the UI (:9749). The first session starts it; each agent runs a
**thin stdio MCP frontend** that registers a session against the shared daemon (clean JSON-RPC on
stdout; the daemon owns long-lived state). All CBM processes MUST share one canonical cache root
(`CBM_CACHE_DIR`) and the exact same build — a different root is rejected while any process is
active. So "persistent + shared" is coderag's native design, not a bridge we build. Requirement:
every agent mounts the same `CBM_CACHE_DIR` volume + `${CODE_ROOT}:ro`.

### swarmvault — shared vault + doc-drop model (verified)

swarmvault's MCP is stdio, but sharing does NOT go through the MCP as a content-write path. Model:

- One **shared vault dir** on a host volume; a persistent `swarmvault watch` container ingests it.
- Agents **write doc files directly into the vault dir** (filesystem — distinct files, no
  contention), then call the MCP only to **signal ingest / query**. The MCP is read + trigger,
  never a content-write RPC.
- Content-trust gate still applies to the SHARED vault: a malicious PR could make an agent drop an
  injected doc and trigger ingest → poisoned recall. Run-scoped scratch docs = free; promotion into
  the shared vault = server-gated + provenance-tagged (same discipline as hindsight; enforced in M1
  write-path).

## Building the images

```bash
# swarmvault — from its own Dockerfile. Runs as: (a) a persistent `swarmvault watch` container
# over the shared vault volume, and (b) per-agent `docker run --rm -i ... swarmvault mcp` for
# read+trigger, both mounting the same vault dir.
git clone https://github.com/swarmclawai/swarmvault /tmp/swarmvault
docker build -t agent-fleet/swarmvault /tmp/swarmvault

# coderag — codebase-memory-mcp. Run the coordination daemon in a container with a shared
# CBM_CACHE_DIR volume + ${CODE_ROOT}:ro; agents attach thin stdio frontends against it.
# Do NOT run its `install` (mutates agent client config files); invoke the binary directly.
```

coderag indexes **main only**, read-only, continuously; agents track their own worktree diffs.
Worktrees live in `${CODE_ROOT}/_roktcode-worktrees/<repo>/<branch>`. Note: these sit *under*
`${CODE_ROOT}`, so the `:ro` mount still *sees* them — read-only prevents coderag from *writing*,
but excluding worktrees (and dirty main) from indexing is the daemon's watcher config
(`auto_watch`/`watcher_enabled`, see open item #4), not the mount's.

## Open items (resolve at implementation — do not commit a secret regardless)

1. **hindsight needs a keyed LLM provider.** hindsight runs an LLM to extract facts on every
   `retain`, so `HINDSIGHT_API_LLM_API_KEY` must be set (verified: e2e worked with
   `HINDSIGHT_API_LLM_PROVIDER=openai` + key). Keyless subscription providers (`claude-code`)
   do NOT work headless in a container — there is no logged-in session inside the container
   (`Not logged in · Please run /login`). Do not use them for an unattended server; use a keyed
   provider.
2. **hindsight retain/recall REST paths.** `scripts/m0-verify.sh` check [3] uses
   `/v1/banks/{bank}/retain|recall` as a best guess; the upstream README documents the SDK, not raw
   REST. Verify against hindsight's API-reference and adjust the probe if paths differ. (The SDK or
   the per-bank MCP endpoint `/mcp/{bank}/` are alternatives.)
3. **coderag daemon in a container.** CBM's coordination daemon auto-starts from the first session
   and shares one `CBM_CACHE_DIR`. Verify it runs cleanly as a long-lived container (not just
   host-native) and that thin stdio frontends from other containers register against it over the
   shared cache volume. If cross-container session coordination needs the daemon reachable beyond a
   shared volume, resolve at implementation.
4. **coderag watcher scope.** Exclude `_roktcode-worktrees` and dirty main from indexing via
   `auto_watch`/`watcher_enabled` config, not the `:ro` mount. Confirm the config keys.

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

## Producers (M2)

`bin/pr-producer <review|maintain>` is enqueue-only: it discovers matching PRs (assigned for
`review`, authored for `maintain`), applies the repo allowlist + stale-age cutoff, resolves each
PR's head sha, and inserts one `requests` row per PR (`dedupe_key = repo#num@headsha`). It never
runs the agent — the serial `bin/agent-server` drains the queue.

Dedupe is two-layered (see `lib/queue.sh`):
- the partial unique index blocks a second **active** (queued|running) row for the same key;
- `queue_enqueue` also skips a key already queued/running/**done**, so re-runs don't create churn rows;
  a **failed** head IS re-enqueued (a transient error should retry; permanent poison-PR protection is a future `max_attempts` concern, not a dead-letter here).
- a PR whose head advanced gets a **new** dedupe_key → a fresh row (re-review on the new commit).

launchd templates: `launchd/com.example.agent-fleet-producer-{reviews,maintenance}.plist.template`.
Run these **alongside** the legacy `bin/pr-automation` cron during the M2 trust period; retire the
old inline loop only once the queue path is trusted (roadmap M2 exit). Keep the queue DB password
out of the plist — source `.env` from a wrapper or use the keychain.
