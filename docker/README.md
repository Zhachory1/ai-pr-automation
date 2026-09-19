# Agent fleet support substrate

One `docker-compose.yml` (repo root) + this dir. Compose renders **support services only**: the
Postgres request queue, Hindsight memory, coderag, swarmvault, and the Fleet Controller UI. AI
execution runs host-native under the `hermes-agent` account (see
[`../docs/hermes/README.md`](../docs/hermes/README.md)); no AI worker runs in Compose.

## Bring it up

```bash
cp .env.example .env      # then edit: CODE_ROOT, passwords, Hindsight provider, vault path
scripts/compose.sh up -d --build  # validates vault path, then builds and starts support services
scripts/m0-verify.sh      # substrate checks (Postgres, Hindsight, swarmvault, coderag)
```

Schema upgrades for an existing database volume run through the `schema-migrate` service, which
reapplies additive `docker/initdb/0[2-9]-*.sql` migrations. `01-schema.sql` is the immutable
fresh-install baseline.

## Queue and reconciliation

The host-native runtime claims requests with a renewable Postgres lease. Different PR lineages run in
parallel; a partial unique index prevents two claimants working the same head. After an abrupt stop,
untouched work becomes claimable when the lease expires. Work that crossed a side-effect boundary
enters `reconcile` instead of replaying a possible push or reply. `reconcile` rows appear in the Fleet
Controller Recent list. Verify GitHub state before manually marking one `done` or returning it to
`queued`; automatic retries stay blocked for that head. Use one audited transaction after inspecting
the request ID and remote PR:

```sql
-- Effects landed: UPDATE requests SET status='done', posted_ref='manually-reconciled',
--   finished_at=now(), run_id=NULL, run_nonce=NULL, side_effect_at=NULL,
--   lease_expires_at=NULL, fail_response=NULL WHERE id=123 AND status='reconcile';
-- No effects landed: UPDATE requests SET status='queued', started_at=NULL, finished_at=NULL,
--   run_id=NULL, run_nonce=NULL, side_effect_at=NULL, lease_expires_at=NULL, fail_response=NULL
--   WHERE id=123 AND status='reconcile';
```

Open `https://127.0.0.1:8080` for the Fleet Controller. Blocked `pr-maintain` findings appear in its
human-review queue with an **Open PR** link, agent summary, findings, and local **Reviewed** /
**Dismiss** controls. These controls do not write to GitHub.

## What runs as a service

**All memory services are persistent + shared across agents** — the transport differs but none is
spawned fresh per agent with private state:

| Service | Shared how | In compose? |
|---|---|---|
| db-requests (our Postgres) | TCP 5432 | yes, `up` |
| hindsight (+ hindsight-db) | network service, HTTP :8888 / UI :9999 | yes, `up` |
| coderag (codebase-memory-mcp) | **shared coordination daemon** + per-agent thin stdio frontend | yes, `up` (daemon) |
| swarmvault | **shared vault volume + `watch` daemon**; internal HTTP MCP bridge | yes, `up` |

### coderag — native shared daemon

CBM ships a **per-account coordination daemon** that owns the shared knowledge graph, background
watchers, continuous indexing, and the UI (:9749). Each agent runs a **thin stdio MCP frontend** that
registers a session against the shared daemon. All CBM processes MUST share one canonical cache root
(`CBM_CACHE_DIR`) and the exact same build — a different root is rejected while any process is active.
The coderag container bridges its stdio frontend to `http://coderag:9750/mcp` on the Compose network
and runs bridge and daemon together so the bridge cannot create a private graph. Its index is confined
to the read-only `/code` mount.

### swarmvault — shared vault + doc-drop model

swarmvault's MCP is stdio, but sharing does not go through the MCP as a content-write path:

- One **Finder-visible shared vault dir** outside `CODE_ROOT`; a persistent watcher ingests it.
- `swarmvault-mcp` exposes Streamable HTTP at `http://swarmvault-mcp:9760/mcp`, mounting the vault
  read-only, so MCP calls cannot write or promote content.
- The watcher is the only fleet component with vault write access.
- Content-trust still applies: run-scoped scratch docs are free; promotion into the shared vault is
  server-gated and provenance-tagged.

## Building the optional services

```bash
scripts/compose.sh build coderag swarmvault-watch swarmvault-mcp
scripts/compose.sh up -d coderag swarmvault-watch swarmvault-mcp
```

Both images build from pinned upstream commits. The coderag entrypoint rejects codebase-memory-mcp
lifecycle commands that could mutate agent configuration.

## hindsight provider

hindsight runs an LLM to extract facts on every `retain`, so `HINDSIGHT_API_LLM_API_KEY` must be set
with a keyed provider (`HINDSIGHT_API_LLM_PROVIDER=openai` verified end to end). Keyless subscription
providers (`claude-code`) do NOT work headless in a container — there is no logged-in session inside
it. Agents recall from the bank-scoped `http://hindsight:8888/mcp/fleet-shared/` endpoint; the shared
bank is locked to a read-only MCP tool set by `hindsight-bank-init` so agents cannot self-retain junk.

**hindsight data + PG major version:** `hindsight_pgdata` is mounted at the fixed pg18 PGDATA path
(`/var/lib/postgresql/18/docker`) and the db image is pinned to `pgvector/pgvector:pg18`. The mount
path is version-specific; older tags (e.g. `pg16`) use a different PGDATA
(`/var/lib/postgresql/data`), so changing the tag without migrating would mount the volume at the
wrong path and silently re-init an empty cluster — data loss, no error. To move majors, do a
`pg_upgrade` or dump/restore and update both the pinned tag and the mount path together.

## Schema

`docker/initdb/01-schema.sql` loads once on first Postgres boot. `requests` stores queue records;
`pending_decisions` remains for compatibility with older records. Validated against postgres:16: the
dedupe index blocks two active rows for the same `(kind, dedupe_key)`; the running-lineage index
blocks concurrent work on different heads of the same PR; expired attempts are nonce-fenced and
reclaimed. `07-hermes-autonomy.sql` and `08-hermes-swe-pilot.sql` add the enrollment table and the
security-definer queue functions that are the runtime's sole repository authority.
