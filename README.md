# AI PR Automation

Local PR review and maintenance automation. Docker Compose runs two scheduled GitHub discovery producers, Hindsight, Coderag, and nginx. The producers submit eligible PRs to a localhost-only, authenticated host ingress that creates tasks through the current user's Hermes Kanban CLI. The `pr-review-v1` and `pr-maintain-v1` profiles run those tasks; there is no active fleet-owned queue or controller. Signal runs under the current user's LaunchAgent, not Docker.

The old request database is archived offline; the legacy PR-safety bridge and Fleet Controller are not started. Document publication, memory writes, and PR-safety delivery are not part of this cutover. GitHub remains the PR merge UI; no agent merges PRs.

## Start safely

On the operator's macOS account, install and configure Hermes in `~/.hermes` and its user-level gateway service. Install both PR profiles from `agent-config/hermes/profiles/`. Do not run the deprecated `scripts/hermes-native.sh install` or create/use a `hermes-agent` account for this runtime. See [getting started](docs/getting-started.md) for prerequisites.

Copy `.env.example` to `.env`, replace sample paths, configure a narrow repository authority file, and keep secrets outside this repository. The read-only GitHub discovery token and two per-kind Kanban ingress keys are owner-only files under the paths in `.env.example`; do not paste their values into chat or logs.

```bash
scripts/fleet.sh support-up  # Hindsight, Coderag, nginx; no PR discovery
scripts/fleet.sh status
```

Open <http://localhost/>. The local Hermes Kanban dashboard is at <https://fleet.localhost:8080/>; the old `dashboard.localhost` HTTP alias redirects there. `hermes.localhost/health` and Runs API routes still reach the gateway; `memory.localhost` routes to Hindsight and `code.localhost` to Coderag. The retired `/signal/` proxy path returns 410. The proxy publishes only on loopback ports 80 (HTTP redirects and APIs) and 8080 (HTTPS).

Once the host ingress is running and both ingress key files and a **read-only** GitHub discovery token are present, `scripts/fleet.sh up` starts review and maintenance cron. **This can invoke paid models and make GitHub changes.** It is not a health probe. `scripts/fleet.sh pause` stops both crons; `scripts/fleet.sh down` stops Compose services, not the personal Hermes gateway. Neither command deletes volumes; never use `docker compose down -v`.

Historical request state, including unresolved `reconcile` records, is in a private offline archive. Do not requeue those records without checking external effects. See [operations](docs/operations.md). Older controller and service-account design documents describe the previous runtime, not active startup commands.

## Optional Hermes week planner

A separate Hermes profile can plan one private Google Calendar through restricted calendar tools and a profile-local weekly agent cron. See [week planner setup](docs/hermes-week-planner.md). It is not enabled by default.

## Validation

Focused, fake-network tests cover the Kanban ingress, cron submission contract, profile MCP configuration, and Compose/nginx wiring. `scripts/m0-verify.sh` is **not** read-only: it writes a synthetic Hindsight fact. A real PR run, Signal message, or Hindsight recall requires separate authorization; static tests do not prove those effects.

## License

MIT
