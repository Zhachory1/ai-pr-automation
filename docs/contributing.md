# Contributing

Use [getting started](getting-started.md) for the supported personal Hermes deployment. There is no request database or custom queue controller in the PR runtime; older controller designs are historical.

| Change | Source | Focused checks |
| --- | --- | --- |
| PR discovery and scope | [`../bin/hermes-pr-producer`](../bin/hermes-pr-producer), [`../scripts/hermes-authority.py`](../scripts/hermes-authority.py) | `tests/test-hermes-pr-producer.sh`, `tests/test-hermes-authority.sh` |
| Signed Kanban admission | [`../scripts/hermes-kanban-ingress.py`](../scripts/hermes-kanban-ingress.py), [`../scripts/hermes-pr-kanban-enqueue.py`](../scripts/hermes-pr-kanban-enqueue.py) | `tests/test-hermes-kanban-ingress.py`, `tests/test-hermes-pr-kanban-enqueue.py` |
| Runtime wiring and no request DB | [`../docker-compose.yml`](../docker-compose.yml), [`../scripts/fleet.sh`](../scripts/fleet.sh), [`../Dockerfile.hermes-pr-review-cron`](../Dockerfile.hermes-pr-review-cron) | `tests/test-hermes-compose-wiring.sh`, `tests/test-request-db-retired.py` |
| GitHub effects | [`../scripts/hermes_pr_review_github.py`](../scripts/hermes_pr_review_github.py), Hermes profiles | `tests/test-hermes-pr-review-github.py`, `tests/test-hermes-maintain-cron-wiring.sh` |
| Optional PR safety, memory | [`../bin/hermes-pr-safety-producer`](../bin/hermes-pr-safety-producer), [`../bin/hermes-memory-curate`](../bin/hermes-memory-curate) | `tests/test-hermes-pr-safety-producer.sh`, `tests/test-hermes-memory-curate.sh` |

Run focused checks for the changed area. Fake-provider tests are not authorization to start `scripts/fleet.sh up` or submit a real GitHub PR. Some tests create local temporary files; inspect a test before using a live environment. For docs-only changes, verify links/commands against the shipped files and run `git diff --check`.
