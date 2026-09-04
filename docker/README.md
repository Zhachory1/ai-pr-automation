# M0 — Agent fleet substrate

One `docker-compose.yml` (repo root) + this dir. Stands up the services the serial agent-server
(M1) depends on. **Inert until M1 wires them** — merging M0 changes no behavior.

## Bring it up

```bash
cp .env.example .env      # then edit: CODE_ROOT (host-absolute), passwords, provider
scripts/compose.sh up -d --build  # validates vault path, then builds and starts all services
scripts/m0-verify.sh      # runs the six exit checks
```

Open `http://localhost:8080` for agent status. Blocked `pr-maintain` findings appear in its
human-review queue with an **Open PR** link, agent summary, findings, and local **Reviewed** /
**Dismiss** controls. These controls do not write to GitHub.

Pending decisions show their stored proposal, findings, and provenance before action. **Approve**
synchronously retains that exact stored data to `fleet-shared` as
`pending-decision-<id>`, then marks the local row approved. **Reject** only updates the local row.
A retain failure remains `publishing`; use **Retry** to replace the same Hindsight document safely.
For rollback, delete that Hindsight document by its deterministic ID, then investigate before
changing database history.

## What runs as a service vs. what does not

Verified 2026-09-02 by reading each upstream repo. **All memory services are persistent + shared
across agents** — the transport differs but none is spawned-fresh-per-agent-with-private-state:

| Service | Shared how | In compose? |
|---|---|---|
| db-requests (our Postgres) | TCP 5432 | yes, `up` |
| redis | TCP 6379 | yes, `up` |
| hindsight (+ hindsight-db) | network service, HTTP :8888 / UI :9999 | yes, `up` |
| coderag (codebase-memory-mcp) | **shared coordination daemon** + per-agent thin stdio frontend | yes, `up` (daemon) |
| swarmvault | **shared vault volume + `watch` daemon**; internal HTTP MCP bridge | yes, `up` |

### coderag — native shared daemon (verified)

CBM ships a **per-account coordination daemon** that owns the shared knowledge graph, background
watchers, continuous indexing, and the UI (:9749). The first session starts it; each agent runs a
**thin stdio MCP frontend** that registers a session against the shared daemon (clean JSON-RPC on
stdout; the daemon owns long-lived state). All CBM processes MUST share one canonical cache root
(`CBM_CACHE_DIR`) and the exact same build — a different root is rejected while any process is
active. So "persistent + shared" is coderag's native design. The Coderag container bridges its
stdio frontend to `http://coderag:9750/mcp` on the Compose network. It runs bridge and daemon together
so the bridge cannot create a private graph. Agent configuration lands in a later PR.

### swarmvault — shared vault + doc-drop model (verified)

swarmvault's MCP is stdio, but sharing does NOT go through the MCP as a content-write path. Model:

- One **Finder-visible shared vault dir** outside `CODE_ROOT`; persistent watcher ingests it.
- `swarmvault-mcp` exposes Streamable HTTP at `http://swarmvault-mcp:9760/mcp` on the Compose
  network. It mounts the vault read-only, so agent MCP calls cannot write or promote content.
- Agents use the MCP for read/query. The watcher is the only fleet component with vault write access.
- Content-trust gate still applies to the SHARED vault: a malicious PR could make an agent drop an
  injected doc and trigger ingest → poisoned recall. Run-scoped scratch docs = free; promotion into
  the shared vault = server-gated + provenance-tagged (same discipline as hindsight; enforced in M1
  write-path).

## Building the optional services

```bash
scripts/compose.sh build coderag swarmvault-watch swarmvault-mcp
scripts/compose.sh up -d coderag swarmvault-watch swarmvault-mcp
```

Both images build from pinned upstream commits. `coderag` starts codebase-memory-mcp's permanent
daemon and exposes its UI only at `http://localhost:9749`; its index is confined to the read-only
`/code` mount. `swarmvault-watch` initializes an empty mounted vault once, then
watches its inbox. The coderag entrypoint rejects codebase-memory-mcp lifecycle commands that could
mutate agent configuration.

coderag can only index paths selected beneath its read-only `/code` mount. This setup does not
configure automatic indexing or watcher scope; agents still own their worktree diffs.

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
3. **coderag agent wiring.** Agents use the internal HTTP bridge at `http://coderag:9750/mcp`.
   They must not mount Coderag cache or binary files.

## Schema

`docker/initdb/01-schema.sql` loads once on first Postgres boot. `requests` (queue+record) and
`pending_decisions` (the daily human-review batch for decision-shaped memory writes). The
`schema-migrate` service reapplies additive schema changes for existing database volumes, including
`pending_maintenance_reviews`, the local queue for maintenance findings needing human judgment, and
pending-decision `publishing` recovery fields.
Validated against postgres:16: dedupe index blocks two active rows for the same `(kind, dedupe_key)`
and allows re-enqueue after `done`.

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

launchd templates: `launchd/com.example.agent-fleet-producer-{reviews,maintenance}.plist.template`
(the producers) and `com.example.agent-fleet-agent-server.plist.template` (the drain worker). The
legacy `bin/pr-automation` inline loop has been retired (M2 exit) — the producers + agent-server
fully replace it. Keep the queue DB password out of the plist — source `.env` via
`scripts/agent-server-launch.sh` (the agent-server wrapper) or use the keychain.
