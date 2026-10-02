# Operator runbook (host-native fleet)

Use [getting started](getting-started.md) for first install. This runbook is for inspection and controlled recovery on **one macOS host**. It does not authorize paid calls, GitHub writes, Keychain changes, database updates, or deleting state. `scripts/fleet.sh up` and bare Compose `up` start live controller/producers; do not use either as a health probe.

## Status and first checks

From the repository root with the configured `.env`:

```bash
scripts/fleet.sh status
scripts/fleet.sh logs
scripts/compose.sh ps
sudo scripts/hermes-native.sh bridge-status
```

`status` shows Compose containers and host launchd services; `logs` includes controller/producer and host gateway logs, which can contain private PR text. Keep logs off public issue trackers. Fleet Controller is at <https://fleet.localhost:8080> (localhost-only proxy); the UI has a login/session and Host/Origin/CSRF checks on mutations. Hindsight API is loopback at `127.0.0.1:8888`. Request Postgres publishes `REQUESTS_DB_PORT` without an explicit loopback host bind; restrict it at the host/network boundary.

For a **read-only queue summary**, the database container already has its own credentials:

```bash
scripts/compose.sh exec -T db-requests sh -ec \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT kind,status,count(*) FROM requests GROUP BY kind,status ORDER BY kind,status;"'
scripts/compose.sh exec -T db-requests sh -ec \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT state,count(*) FROM hermes_runs GROUP BY state ORDER BY state;"'
```

A nonempty `queued` row can be claimed immediately if the controller is running; an expired lease is not proof that no external action happened. Never print raw `request_bytes`, payloads, tokens, or staged docs into a shared terminal/log. `scripts/m0-verify.sh` is **not** read-only: it writes a synthetic Hindsight fact to a new bank and may start/check support services. Its sixth durability check is manual, not a backup test.

## Pause, reconciliation, and shutdown

Prevent new Compose discovery before draining: stop the relevant `pr-producer-review`, `pr-producer-maintain`, `pr-safety-producer`, and `memory-curate-producer` services via `scripts/compose.sh stop <service>`. If a Kanban or cron producer was separately activated on the host, stop it with the matching `sudo scripts/hermes-native.sh review-producer-stop`, `maintain-producer-stop`, `producer-stop` (PR safety), or `memory-cron-stop`. **This does not cancel queued or already-running requests.** Check the queue and `hermes_runs` state, then inspect remote GitHub/document/memory effects before any manual disposition. The [Hermes runtime guide](hermes/README.md#operations) describes the profile/bridge rules.

A read-only query for unsettled runs:

```bash
scripts/compose.sh exec -T db-requests sh -ec \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT r.id,r.kind,r.status,h.attempt_no,h.state,h.run_id,h.reconcile_reason FROM requests r LEFT JOIN hermes_runs h ON h.request_id=r.id WHERE r.status IN ('\''running'\'','\''reconcile'\'') OR h.state IN ('\''submitting'\'','\''reconcile'\'') ORDER BY r.id,h.attempt_no;"'
```

A `reconcile` row means outcome is unknown, especially for direct GitHub effects. The controller blocks another attempt for the same unresolved operation. **Do not** requeue, mark done, re-run a producer to bypass the lock, or use the legacy SQL snippet in [`docker/README.md`](../docker/README.md#queue-and-reconciliation) for a current `hermes_runs` attempt without reviewing remote effects and the applicable reconciliation contract. The UI's human-review **Reviewed/Dismiss** controls update local review state only; they do not reply on GitHub.

`scripts/fleet.sh down` stops the host safety producer, Compose containers, then native bridge/dashboard/gateway. It does **not** delete named Docker volumes, but it can interrupt active work; drain first. Same-version restart may recover persisted attempts on their original route, not on a new profile. **Never use `docker compose down -v` for routine shutdown**: it deletes queue, Hindsight, and Coderag named volumes. Do not pull/install/sync support during an unresolved Kanban attempt; host bridge state files must be archived before replacement.

## Troubleshooting matrix

| Symptom | Inspect (no live activation) | Response boundary |
| --- | --- | --- |
| Compose fails before start | `scripts/compose.sh config --quiet`; `python3 scripts/validate-fleet-controller-secrets.py --env-file .env --repo .`; vault path | Set actual absolute paths; verify Fleet Controller TLS/session modes, cert chain/expiry, non-symlink files, and authority/token secret files. `scripts/compose.sh` runs TLS validation even for a targeted `up`. Avoid `config` without `--quiet`. |
| Gateway/dashboard absent | `scripts/fleet.sh status`; `sudo scripts/hermes-native.sh preflight`; `sudo scripts/hermes-native.sh logs` | Check account, pinned install, profile digest, host launchd status. Never run `install`/`sync-support` over open Kanban attempts or to bypass a failed preflight. |
| Bridge failed or old Kanban marker blocked | `sudo scripts/hermes-native.sh bridge-status`; `sudo scripts/hermes-native.sh bridge-reconcile` | Reconcile is a **read-only report**. Verify persistent marker and Postgres attempt on the original engine; do not delete bridge workflow files to clear an error. |
| Queue stays empty | Producer logs via `scripts/fleet.sh logs`; authority check with `scripts/hermes-authority.py --file <path> --check owner/repo` | Check token scope/SAML, producer queue-engine selection, exact GitHub identity/assigned/authored PRs, and PR-safety **authors plus authority OR org**. Empty queue does not prove the model works. |
| Requests stay queued | `scripts/fleet.sh status`; inspect requests and `hermes_runs` above | Confirm controller, gateway API conformance, route generation, and cap. If a run is `reconcile`, inspect effects rather than forcing another claim. |
| Hindsight retain fails | `scripts/compose.sh logs hindsight hindsight-db`; keyed provider settings | Check provider credentials and database health. Retain may incur cost; do not test by submitting private text. |
| Document publication blocked | Fleet Controller publication queue and controller logs | Verify exact staged bytes, approved digest/target, and inbox mount before trying the existing no-clobber recovery path. Never create an alternative target to hide an uncertain write. |

## Controller recovery

If `hermes-controller` is down, first inspect `scripts/compose.sh ps`, `scripts/compose.sh logs --tail=100 hermes-controller schema-migrate db-requests`, host gateway status (`scripts/fleet.sh status`), and the two read-only queue queries above. Stop discovery on **both** active Compose and host producer paths before changing anything. Do not treat a failed review row or an expired lease as proof that a GitHub review was not posted.

When Postgres/schema and the gateway are healthy and versions/routes/profile keys have **not** changed, an operator who authorizes resuming queued work **and** the same persisted `submitting` attempts may restart only the controller. It recovers existing attempt IDs/request bytes under the original generation; do not manually create replacement attempts. First inspect unresolved `reconcile` rows and decide their remote effects separately; restart does not resolve them:

```bash
scripts/compose.sh up -d --no-deps hermes-controller
```

This is **live activation**: it can immediately claim queued work, incur model costs, and cause GitHub/document/memory effects. It is not a read-only health check. If route/auth/profile generations changed, a bridge marker is unresolved, or the previous effect is uncertain, do not force a replacement attempt or reset database rows; inspect remote effects and use the [Hermes recovery contract](hermes/README.md#queue-and-runs-ledger). Review failures usually settle `failed`; uncertain maintenance and SWE effects enter `reconcile`. Do not use Fleet Controller **manual PR injection** as an automatic review retry: its manual timestamp dedupe key is not the reviewed head SHA marker, so even a posted review can settle locally as `failed`. This needs a separate code/test fix. Follow up with `scripts/fleet.sh status` and inspect queue/results before resuming producers.

## Queue-engine changes

`scripts/fleet.sh review-kanban-up`/`review-postgres-up`, `maintain-kanban-up`/`maintain-postgres-up`, and `memory-cron-up`/`memory-postgres-up` **recreate a live producer and/or start a host job**; they are not validation commands. The scripts stop the old producer for their mode and verify the replacement environment. Existing Postgres requests, persisted Kanban cards, and run IDs are not erased or migrated by a switch. The `requests`/`hermes_runs` queries above cover the default controller path; new direct Kanban review/maintenance work lives in host Hermes workflow/journal state, not new Postgres attempts. Direct PR-safety council intake currently has no automatic Postgres settlement, final handoff, or incident queue. Inspect those host workflows separately and review the target mode, inflight attempts, external-effect authority, and rollback before using a switch. PR-safety has separate `PR_SAFETY_QUEUE_ENGINE` (Postgres vs host Kanban producer) and `PR_SAFETY_ANALYSIS_ENGINE` (`single` default vs council); bridge availability by itself does not activate a new council. See [Hermes authority and direct enqueue](hermes/README.md#authority-and-producers) and [configuration](configuration.md#workflows-and-switches).

## TLS, upgrades, and backup limits

- `scripts/generate-ui-tls.sh` refuses to overwrite its destination and destroys the CA signing key after issuing the leaf. TLS preflight rejects a leaf expiring within 24 hours. For renewal, plan a **new** private directory/certificate, explicit CA trust decision, `.env` path update, and controlled restart after draining; no tested seamless renewal procedure is shipped. Do not remove existing trusted CA/files until the new UI works. The [README rollback](../README.md#fleet-controller-rollback) intentionally restores anonymous HTTP and is emergency-only, not a safe normal renewal.
- Before changing pinned Hermes/runtime/profile bytes, stop **both Compose and host** discovery. Inspect/drain or reconcile Postgres attempts, **host-direct review/maintenance cards and their journals** (which are not in `requests`/`hermes_runs`), and PR-safety bridge workflows. Confirm bridge markers are archived and no direct-effect card is still active or uncertain before install/profile sync. Postgres queries alone do not prove a full drain; the bridge state-file guard does not query Postgres or direct PR work. Follow the [documented upgrade order](hermes/README.md#operations); do not rotate API keys in place while runs submit.
- Persistent state spans `requests_pgdata`, `hindsight_pgdata` (pinned PG18 mount), `coderag_cache`, `/Users/hermes-agent/.hermes`, bridge workflow files, and the approved shared-runtime stage/snapshot/handoff/secret paths. Logs, prompts, and workspaces can contain private code. There is **no verified full-fleet backup/restore**; a Compose `down`/`up` persistence check is not one. Before machine replacement or major Postgres upgrades, arrange coordinated database backups plus relevant host state and test restoration in isolation. Never copy a live database volume as though that were a tested backup procedure.
