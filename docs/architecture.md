# Architecture

The supported PR workflow has no request database or custom queue controller. Review is stopped behind the opt-in `pr-review-mvp` profile while the [GitHub/file-handoff replacement](DD-pr-review-github-files.md) is implemented; only maintenance runs on normal fleet startup:

```text
Assigned PR review discovery (Compose cron) → local Hermes dashboard API → pr-review board → PR profiles → GitHub
Authored PR maintenance discovery (Compose cron) → signed host ingress → pr-maintain board → PR profiles → GitHub
```

`bin/hermes-pr-producer` discovers assigned review PRs across accessible repositories using a read-only GitHub token; the review cron creates exact-head cards through the local dashboard API. The pending review producer skips heads already reviewed by this account and uses Hermes' non-archived task key instead of a JSON request cache. Maintenance discovery remains authority-scoped and sends eligible feedback to `scripts/hermes-kanban-ingress.py`; the host ingress verifies its signature and authority before creating a Kanban task. The `pr-review-v1` and `pr-maintain-v1` profiles run under the current user's Hermes gateway. GitHub is authoritative for PR heads, feedback, and effects; Kanban is authoritative for task admission and status. Review reads back an exact matching card after an uncertain create; maintenance uses the local ingress journal to prevent uncertain creation from being blindly replayed. See [operations](operations.md) before resolving an ambiguous submission.

`docker-compose.yml` defines the opt-in review producer, the active maintenance producer, and support services (Hindsight, Coderag, nginx); it does not run a request Postgres instance, controller, or status server. Hindsight has its **own** Postgres-backed memory store; it does not hold PR requests. Profiles and prompts are not security boundaries: scope the discovery token, dashboard session, host GitHub credentials, maintenance ingress key, filesystem access, and repository permissions independently. No agent merges PRs; merge remains a human action in GitHub.

The former request DB was archived offline, not migrated to Kanban or GitHub. Its unresolved requests are historical, not runnable work. See [operations](operations.md#preserved-pre-cutover-state) for the archive and reconciliation warning. Older designs under `docs/hermes/` describe a retired controller, not deployment instructions.
