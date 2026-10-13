# Configuration and credentials

Use [getting started](getting-started.md) for current setup and [`.env.example`](../.env.example) for Compose variables. The request-database and service-account installer settings described in older plans are retired.

| Boundary | Setting | Purpose |
| --- | --- | --- |
| PR discovery | `GITHUB_DISCOVERY_TOKEN_FILE` | Read-only GitHub token shared by both Compose cron producers. |
| Kanban ingress | `PR_REVIEW_INGRESS_KEY_FILE`, `PR_MAINTAIN_INGRESS_KEY_FILE` | Separate owner-only keys for signed submissions from Compose to the current user's host ingress. |
| Repository scope | `HERMES_AUTHORITY_SOURCE_FILE`, `HERMES_DOCKER_AUTHORITY_FILE` | Operator authority YAML and Docker-readable mirror; export shell overrides when using `scripts/fleet.sh`. Repo scope limits discovery, not GitHub permissions. |
| Hermes | Current user's `~/.hermes` profiles and credentials | Runs `pr-review-v1` and `pr-maintain-v1`; model and GitHub effect access stay on the host. |
| Support services | `CODE_ROOT`, `HINDSIGHT_DB_PASSWORD`, `HINDSIGHT_API_LLM_PROVIDER`, `HINDSIGHT_API_LLM_API_KEY` | Coderag reads code; Hindsight retains its separate Postgres-backed memory service. |
| UI | `FLEET_CONTROLLER_TLS_CERT_FILE`, `FLEET_CONTROLLER_TLS_KEY_FILE`, optional `STATUS_PORT` | Legacy variable names for the localhost nginx TLS proxy to Hermes Kanban. No Fleet Controller or request DB is behind it. |

Keep secrets outside Git; use owner-only files for discovery and ingress keys. `scripts/fleet.sh support-up` starts support services only; `scripts/fleet.sh up` starts both PR crons and can trigger paid model calls and GitHub effects. Do not start legacy producers or the retired controller alongside this fleet. See [operations](operations.md) for pause and reconciliation.
