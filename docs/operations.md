# Local fleet operations

The personal `~/.hermes` gateway and Signal receiver run under the current macOS user as user LaunchAgents. Compose runs Hindsight, Coderag, nginx, and (once enabled) direct PR review and maintenance crons. Do not use `sudo` or the retired `hermes-agent` account to operate this fleet. [Rebuild instructions](getting-started.md#recreate-the-personal-fleet-on-another-mac) install the pinned Hermes patch and prepare user LaunchAgents without loading them. Before loading Signal, check that neither a Docker Signal receiver nor another host receiver is running; never run two receivers with the same identity. The operator must explicitly load each service after checking its private config.

On an existing fleet, first verify the old controller and effect-producing containers/host jobs are stopped; `support-up` does not stop orphaned services. Preserve their volumes and unresolved requests. From the repo root with `.env` configured, or with `COMPOSE_ENV_FILES` pointing to the operator env file:

```bash
scripts/fleet.sh support-up  # support services only; safe while discovery token is missing
scripts/fleet.sh status
scripts/fleet.sh logs
```

The Compose nginx proxy serves <http://localhost/> (fleet index), <http://hermes.localhost/health> (personal Hermes API; root redirects to dashboard), <https://fleet.localhost:8080/> (local Hermes Kanban dashboard; old `dashboard.localhost` redirects there), <http://memory.localhost/> (Hindsight UI), <http://memory-api.localhost/health> (Hindsight API), and <http://code.localhost/> (Coderag). The retired `/signal/` route returns 410. Signal state lives under `~/.local/share/signal-cli`; the unused Docker `signal_state` volume is retained, but the Docker service starts only if explicitly targeted or the `legacy-signal` profile is enabled and must not run beside the user receiver on port 18080. HTTP port 80 is loopback-only. The dashboard is passwordless over HTTPS `:8080`; its SPA session token is not proof of a human approval because agents can also write Kanban comments and status. No Gmail access or sending is enabled. No Signal message is sent by `support-up`.

## Start or pause PR discovery

Place the read-only GitHub discovery token at `GITHUB_DISCOVERY_TOKEN_FILE` and separate 64-hex host-ingress keys at `PR_REVIEW_INGRESS_KEY_FILE` and `PR_MAINTAIN_INGRESS_KEY_FILE` (see `.env.example`). Keep each file current-user-owned, mode `0600`, under an owner-only directory. Start the personal Hermes gateway and the user LaunchAgent for `scripts/hermes-kanban-ingress.py` **before** enabling cron discovery; stop legacy host PR producers to prevent duplicate admission. Keep values out of logs and chat. The host ingress re-checks the operator authority file; `fleet.sh` mirrors that file into Docker for discovery.

```bash
scripts/fleet.sh up  # starts both cron producers; this can trigger model and GitHub effects
scripts/fleet.sh pause  # stop both cron producers; leave support services and Hermes running
scripts/fleet.sh down  # stops Compose only; personal Hermes remains running
```

The cron image pins GitHub CLI 2.102.0 because maintenance discovery needs `gh pr checks --json`; Debian's older CLI lacks that flag. The two cron producers send current PR identities and actionable maintenance-feedback digests to the host ingress at `127.0.0.1:8767` (reachable from Compose as `host.docker.internal:8767`). The host CLI creates a blocked Kanban card, verifies its contract, then unblocks it. The fresh-install ingress LaunchAgent logs to `~/.hermes/logs/com.example.ai-pr-automation-kanban-ingress.{out,err}.log`; older plists can use `~/Library/Logs/ai-pr-automation/kanban-ingress.*.log`. Read the installed plist for the active path and keep this checkout available. Same feedback replays the same maintenance operation; a distinct snapshot waits until prior cards for that PR are done or archived, and a fourth distinct snapshot is capped. A deferred submission is skipped for this scan and reconsidered on the next scan without creating a card. A ready task is **not** proof a GitHub effect landed; inspect the task, host journal, and PR before resolving uncertain work. If producer stderr reports `unresolved create outcome` or `prior maintenance request incomplete`, pause the producers. Use the **prior** `pr-maintain-...` ID after `for` when present; otherwise use the operation ID before the error. Keep its `<ingress --work>/<operation-id>/request.json` and any existing `create-intent.json` intact; an intent without `task-id.json` means creation may have succeeded without a recorded binding. A request without an intent has no proven create, but still requires operator reconciliation before admitting a newer feedback round. Read the board and operation ID, and verify the matching task and GitHub state before any operator decision. Do not delete the intent, hand-write a binding, requeue the request, or restore a live board to force another create. Replays fail closed until the uncertainty is reconciled; `tests/test-hermes-pr-kanban-enqueue.py` checks this. Escalate if no exact matching task can be established. `scripts/fleet.sh up` and bare Compose `up` are not read-only health checks. `scripts/m0-verify.sh` writes a synthetic Hindsight fact, so do not use it for read-only validation.

## Preserved pre-cutover state

The former request database is archived offline, not an active Compose service. On this host the private, restore-checked archive is `~/.local/share/ai-pr-automation/archives/request-db-20261005.tar.gz` (SHA-256 `e8bf3f0af48870d349e6aa0eef606262a8cda51e102496b5fa753b95f821c01d`). It contains 13 historical `reconcile` requests; these were **not** migrated, retried, or marked complete. Inspect their external effects before any disposition. Do not run the old controller or restore its producers alongside direct cron.

`docker compose down -v` deletes named volumes; never use it for routine shutdown. Keep Hindsight's `hindsight_pgdata` mounted at the pg18 path `/var/lib/postgresql/18/docker`. The old bridge, document publication, memory writes, and PR-safety handoff paths are parked, not silently replaced by this cutover.
