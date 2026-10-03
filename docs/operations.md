# Local fleet operations

The personal `~/.hermes` gateway runs under the current macOS user as a user LaunchAgent. Compose runs Hindsight, Coderag, Signal, nginx, and (once enabled) direct PR review and maintenance crons. Do not use `sudo` or the retired `hermes-agent` account to operate this fleet.

On an existing fleet, first verify the old controller and effect-producing containers/host jobs are stopped; `support-up` does not stop orphaned services. Preserve their volumes and unresolved requests. From the repo root with `.env` configured, or with `COMPOSE_ENV_FILES` pointing to the operator env file:

```bash
scripts/fleet.sh support-up  # support services only; safe while discovery token is missing
scripts/fleet.sh status
scripts/fleet.sh logs
```

`https://hermes.localhost:8080/health` reaches the personal Hermes Runs API through nginx; it is not a dashboard. Hindsight and Coderag are at `memory.localhost` and `code.localhost` on the same HTTPS port. Signal's REST API is under `https://localhost:8080/signal/`. The proxy binds only `127.0.0.1:8080`. No Signal message is sent by startup.

## Start or pause PR discovery

Place the read-only GitHub discovery token at `GITHUB_DISCOVERY_TOKEN_FILE` and the two profile-scoped Hermes keys at `HERMES_REVIEW_API_KEY_FILE` and `HERMES_MAINTAIN_API_KEY_FILE` (see `.env.example`). Each must be a nonempty regular file, owned by the current user, mode `0600`, with an owner-only parent directory. Keep values out of logs and chat. The operator authority file is copied into a Docker-readable mirror by `fleet.sh`; Compose refuses to use the broad repository example as a default.

```bash
scripts/fleet.sh up  # starts both cron producers; this can trigger model and GitHub effects
scripts/fleet.sh pause  # stop both cron producers; leave support services and Hermes running
scripts/fleet.sh down  # stops Compose only; personal Hermes remains running
```

The two cron producers use the same profile-scoped Runs API keys and idempotent submission contract as the coding-agent skill. A submitted run is **not** proof a GitHub effect landed; inspect its terminal result and the PR before resubmitting uncertain work. `scripts/fleet.sh up` and bare Compose `up` are not read-only health checks. `scripts/m0-verify.sh` writes a synthetic Hindsight fact, so do not use it for read-only validation.

## Preserved pre-cutover state

The former request database and controller are no longer default services. The `requests_pgdata` named volume and old host state remain intact; unresolved `reconcile` rows are **not** migrated, retried, or marked complete. Do not run the old controller, restore the old producers alongside direct cron, edit database rows to clear uncertainty, or remove Docker volumes to make the new runtime appear healthy. Inspect the corresponding external effects and decide historical disposition separately.

The stopped `fleet-db-requests` container can be used for read-only inspection only after an operator reviews its old port binding and explicitly starts it. While it is running, aggregate counts without printing request payloads:

```bash
docker exec fleet-db-requests psql -U fleet -d fleet -Atc \
  'SELECT status,count(*) FROM requests GROUP BY status ORDER BY status;'
```

`docker compose down -v` deletes named volumes; never use it for routine shutdown. Keep `hindsight_pgdata` mounted at the pg18 path `/var/lib/postgresql/18/docker`. The old bridge, document publication, memory writes, and PR-safety handoff paths are parked, not silently replaced by this cutover.
