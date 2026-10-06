# AI PR Automation

Local PR review and maintenance automation. Docker Compose runs two scheduled discovery producers, Hindsight, Coderag, Signal, and an HTTPS proxy. The current macOS user's `~/.hermes` gateway runs the installed `pr-review-v1` and `pr-maintain-v1` profiles. Both cron producers and the coding-agent skill submit through the same profile-scoped Hermes Runs API; there is no active fleet-owned queue or controller.

The old request database, Kanban bridge, Fleet Controller, and their state are **preserved but not started**. Document publication, memory writes, and PR-safety delivery are not part of this cutover. GitHub remains the PR merge UI; no agent merges PRs.

## Start safely

On the operator's macOS account, install and configure Hermes in `~/.hermes` and its user-level gateway service. Install both PR profiles from `agent-config/hermes/profiles/` with separate profile API keys. Do not run the deprecated `scripts/hermes-native.sh install` or create/use a `hermes-agent` account for this runtime. See [getting started](docs/getting-started.md) for prerequisites.

Copy `.env.example` to `.env`, replace sample paths, configure a narrow repository authority file, and keep secrets outside this repository. The API keys and read-only GitHub discovery token are owner-only files under the paths in `.env.example`; do not paste their values into chat or logs.

```bash
scripts/fleet.sh support-up  # Hindsight, Coderag, Signal, nginx; no PR discovery
scripts/fleet.sh status
```

Open <https://localhost:8080>. `hermes.localhost` routes to the authenticated Runs API (health at <https://hermes.localhost:8080/health>), **not** a dashboard. `memory.localhost` routes to Hindsight, `code.localhost` to Coderag, and `/signal/` to Signal's local REST API. The proxy publishes only on `127.0.0.1:8080`.

Once both profile API key files and a **read-only** GitHub discovery token are present, `scripts/fleet.sh up` starts review and maintenance cron. **This can invoke paid models and make GitHub changes.** It is not a health probe. `scripts/fleet.sh pause` stops both crons; `scripts/fleet.sh down` stops Compose services, not the personal Hermes gateway. Neither command deletes volumes; never use `docker compose down -v`.

The historical `requests_pgdata` volume contains unresolved request state. Do not requeue or discard it during cutover. See [operations](docs/operations.md) for inspection and [container setup](docker/README.md) for persistent-volume details. Older controller and service-account design documents describe the previous runtime, not active startup commands.

## Optional Hermes week planner

A separate Hermes profile can plan one private Google Calendar through restricted calendar tools and a profile-local weekly agent cron. See [week planner setup](docs/hermes-week-planner.md). It is not enabled by default.

## Validation

Focused, fake-network tests cover the cron submission contract, profile MCP configuration, and Compose/nginx wiring. `scripts/m0-verify.sh` is **not** read-only: it writes a synthetic Hindsight fact. A real PR run, Signal message, or Hindsight recall requires separate authorization; static tests do not prove those effects.

## License

MIT
