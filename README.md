# AI PR Automation

Fair, bounded automation for GitHub pull-request review and maintenance — as a **host-native
autonomous agent fleet**.

## Architecture

A single pinned [Hermes](https://github.com/NousResearch/hermes) runtime executes profiles under the
non-admin `hermes-agent` macOS account. Compose now schedules PR review directly through the
`pr-review-v1` Runs API; the remaining workflows still use the Postgres controller pending cutover.
Host launchd keeps the Hermes gateway, dashboard, and PR-safety recovery bridge.

```
Compose review cron ──Runs API──▶ host Hermes pr-review-v1
Other Compose producers ──▶ Postgres ──▶ Compose controller ──▶ host Hermes
```

- **Remaining Postgres queue** keeps dedupe, fixed per-kind caps, route generations, exact request bytes,
  stable idempotency keys, leased claims, exact-byte document approval, and reconcile state.
- **Compose controller** claims and renews queue work, replays lost submissions with identical bytes,
  polls/stops Hermes runs, strictly parses output, and nonce-fences settlement. PR safety defaults to
  current single run, which never creates a Kanban council. The signed bridge stays available only so
  persisted Kanban attempts can recover; opting into `kanban` also allows new council claims.
- **Host-native Hermes** owns profile/model/tool execution and host credentials. One immutable profile
  maps to each queue kind; uncertain maintenance/SWE effects enter human reconcile rather than blind
  retry. Failed reviews need remote inspection before manual re-enqueue.
- **Fleet Controller** (`status`) is the operator UI at `https://fleet.localhost:8080`: runs, queue, human-review
  queue, and exact-byte document approval. GitHub remains the PR merge UI.
- The `hermes-agent` account is the execution boundary. It holds provider OAuth, repository deploy
  keys, GitHub API access, and approved MCP credentials, but no queue database credential. Compose
  controller cannot read those host credentials or service home. The bridge has no Postgres, GitHub,
  or effect credential and is trusted only on this single-host deployment.

Server-side GitHub branch protection keeps merge, protected-branch push, unsafe workflow execution,
deployment, and administration out of the agent's reach. Merge stays a human operating decision.

Operator guides: [from-zero setup](docs/getting-started.md), [configuration](docs/configuration.md),
[architecture](docs/architecture.md), [operations](docs/operations.md), and
[contributing](docs/contributing.md). Deeper runtime contracts: [Hermes](docs/hermes/README.md)
and [Compose substrate](docker/README.md).

## Requirements

- macOS host with a dedicated non-admin `hermes-agent` account
- Docker Desktop for Compose controller/producers and support services
- A separate discovery token and a file containing only the `pr-review-v1` API key for the review cron
- Scoped GitHub token for the `hermes-agent` account; installation copies that exact token to Compose producers, so its actual permissions must be reviewed
- `jq`, `psql` client for operator diagnostics
- A pinned Hermes install for the service account (`scripts/hermes-native.sh install`)

## Quick start

Start with the [from-zero setup guide](docs/getting-started.md) and its [documentation plan](docs/documentation-plan.md). The steps below include live activation: `scripts/fleet.sh up` starts all default producers and the controller, which can run paid models and make GitHub changes. Bare `scripts/compose.sh up -d --build` also starts default producers/controller. Neither is an idle bootstrap or a review-only dry run.

Configure `.env`, install the pinned host Hermes runtime, then start the fleet only after approving repository, provider, and credential scope.

```bash
cp .env.example .env    # replace sample paths and secrets; follow docs/getting-started.md
```

For the default review cron, set `GITHUB_DISCOVERY_TOKEN_FILE` and `HERMES_REVIEW_API_KEY_FILE`
in `.env` to existing nonempty, owner-only files. The latter contains the `pr-review-v1` key from
the installed Hermes API bundle, not the full bundle. The old review producer is no longer a
separate default service; keep it stopped when starting this Compose configuration.

Generate owner-only Fleet Controller secrets outside `CODE_ROOT` using the existing no-overwrite TLS generator:

```bash
install -d -m 700 "$HOME/.config/ai-pr-automation"
umask 077
openssl rand -hex 32 > "$HOME/.config/ai-pr-automation/fleet-controller-session-secret"
scripts/generate-ui-tls.sh  # prints matching .env paths; refuses to overwrite existing TLS files
```

Set the `FLEET_CONTROLLER_*_FILE` values in `.env` to these actual paths (replace `/Users/YOU`). Trusting the generated CA in the login Keychain is a **separate operator decision**; the generator prints the command but does not run it. It destroys the CA signing key after issuing the leaf. Never mount a CA key in Compose. See [the ordered checklist](docs/getting-started.md#3-configure-compose-and-fleet-controller-tls) before starting services.

Open https://localhost:8080 for the unified UI landing page. All UIs share port 8080 through nginx
hostname routing: `fleet.localhost` (Fleet Controller), `hermes.localhost` (Hermes dashboard),
`memory.localhost` (Hindsight), and `code.localhost` (Coderag). The proxy is loopback-only and
passwordless. Fleet Controller still enforces exact Host, Origin, and CSRF checks for writes.

Set up the service account's private `GH_TOKEN` and approved provider credentials, then grant only selected repositories in the authority YAML (see [from-zero setup](docs/getting-started.md)). Install the pinned host runtime **only after** reviewing its privileged host changes:

```bash
sudo scripts/hermes-native.sh install
sudo scripts/hermes-native.sh preflight
scripts/hermes-authority.py --check Zhachory1/ai-pr-automation
```

After reviewing the live-activation checklist in [from-zero setup](docs/getting-started.md#5-explicitly-authorize-live-activation), enable the fleet and check its status:

```bash
scripts/fleet.sh up
scripts/fleet.sh status
scripts/m0-verify.sh  # retains a synthetic Hindsight test fact
```

See [`docs/hermes/README.md`](docs/hermes/README.md) for detailed lifecycle and per-role behavior.

### Fleet Controller rollback

Rollback does not depend on valid new TLS material. The old image is **not** part of initial setup. Drain active attempts and verify remote effects before this emergency path: it stops the fleet and restores an anonymous HTTP UI. Use direct Compose only for the rollback image:

```bash
scripts/fleet.sh down
git archive b7fe7ed Dockerfile.status bin/status-server \
  | docker build -f Dockerfile.status -t agent-fleet/status:pre-auth-b7fe7ed -
docker compose stop ui-proxy  # free host port 8080 for the rollback status image
FLEET_CONTROLLER_PASSWORD_FILE=/dev/null \
FLEET_CONTROLLER_SESSION_SECRET_FILE=/dev/null \
FLEET_CONTROLLER_TLS_CA_CERT_FILE=/dev/null \
FLEET_CONTROLLER_TLS_CERT_FILE=/dev/null \
FLEET_CONTROLLER_TLS_KEY_FILE=/dev/null \
FLEET_CONTROLLER_ROLLBACK_VERSION=pre-auth-b7fe7ed \
  docker compose -f docker-compose.yml -f docker-compose.status-rollback.yml \
  up -d --no-deps --no-build --force-recreate status
```

This is a **degraded HTTP-only fallback**, not restored queue access: `fleet.sh down` also stops `db-requests`, and `--no-deps` does not restart it. The page may return HTTP 200 while displaying `DB unreachable`; do not approve decisions or treat that response as recovery. Restore and verify the database through a separately reviewed recovery procedure before using this UI for decisions. The anonymous HTTP surface has no login; keep it localhost-only and retire it after recovering the authenticated UI.

## Modes

| Kind | GitHub scope | Behavior |
| --- | --- | --- |
| `pr-review` | Open PRs assigned to the operator | Compose cron submits exact heads to Hermes; its profile posts the review |
| `pr-maintain` | Open PRs authored by the operator | Default controller: one fix pass, max 3 rounds per lineage. Optional direct cron: one pass per changed external-feedback snapshot, without that controller cap. |
| `swe-implement` | Enrolled repository | Implement a bounded task on a fresh branch and open a draft PR |
| `doc-write` | Fleet Controller | Draft a PRD/DD; exact bytes require human approval before filing |
| `pr-safety-review` | Merged PRs | Read-only safety analysis (`single` default, opt-in fixed Kanban council); only incident candidates surface |
| `memory-curate` | Bounded local source slice | Propose memories; deterministic team/org gates own writes |

Each kind maps to one immutable Hermes profile under `agent-config/hermes/profiles/`.

## Bounds and safety

Prompt guardrails apply to every profile. Pull-request metadata, diffs, comments, files, and tool
output are labeled untrusted; the `hermes-agent` account boundary and server-side GitHub rules provide
the real containment.

Review guardrails:

- no source edits, pushes, or merges
- `approve` / `approve-with-nits` → `APPROVE`; `request-changes` / `block` → `REQUEST_CHANGES`;
  `needs-info` or self-review → `COMMENT`
- one visible result per head SHA using `<!-- ai-pr-automation head=<full-head-sha> -->`
- exact body-file preview before posting

Maintenance guardrails:

- no merge, deploy, release, force-push, history rewrite, or default-branch push by the agent
- one initial feedback/CI snapshot, then at most one low-risk fix pass; the current controller path caps at 3 rounds per PR lineage
- changed files must finish committed+pushed, reverted to a clean diff, or explicitly blocked
- failing CI is fixed only for clearly code-caused, locally test-validatable checks (lint/format,
  type errors, compile/build breaks, a unit test the diff broke), reproducing the repo's own command
  before pushing a `fix(ci): ...` commit. Never make a check pass by weakening it — that is an
  escalation, not a fix.
- ambiguous findings and escalated CI failures enter the local human-review queue at
  `https://fleet.localhost:8080`; **Reviewed** / **Dismiss** update local state only, never GitHub

The runtime rebases only when the forge reports a real conflict or staleness (`mergeable ==
CONFLICTING` or `mergeStateStatus == BEHIND`), never on a local "behind base" count. The enqueue path
supersedes an older still-queued row of the same PR lineage when a new head lands, so a churning head
cannot pile up duplicate queued jobs.

Required posture:

- Grant intended repositories in authority YAML. PR-safety can also admit approved authors through
  `PR_SAFETY_ALLOWED_ORGS`; review its effective scope before starting producers.
- The deploy key pushes feature branches only; branch protection blocks protected-branch and merge.
- Scope the service-account GitHub token so it cannot merge; installation copies that same token to Compose discovery producers. Merge must remain a human GitHub action.
- Confirm whether private repository content may be sent to the selected provider before enrolling.

## Testing

```bash
# queue layer: parameterized SQL (';DROP fixture), claim/reclaim/posted_ref state machine
bash tests/test-queue-injection.sh
# concurrent claims, lease expiry/reclaim, and nonce fencing
bash tests/test-single-instance.sh
# Runs ledger, caps, exact replay, route/operation fences, and lease loss
bash tests/test-hermes-control-plane.sh
python3 tests/test-hermes-controller.py
# Compose producer/controller ownership and host lifecycle cleanup
bash tests/test-hermes-compose-wiring.sh
# Producer authority and deterministic invariant regression tests
bash tests/test-hermes-authority.sh
bash tests/test-hermes-pr-safety-producer.sh
bash tests/test-hermes-pr-safety-runner.sh
bash tests/test-hermes-doc-write-schema.sh
bash tests/test-hermes-memory-curate.sh
# Fleet Controller auth and session controls
python3 tests/test-status-server.py
```

The queue tests use a throwaway `postgres:16` container. They do not contact GitHub or a provider.

## Operational notes

- GitHub search is capped at 1,000 results. Reaching the cap fails loudly; narrow repository scope.
- A `reconcile` row means an effect boundary was crossed with an unknown outcome. Verify GitHub state
  before marking it `done` or returning it to `queued`; automatic retries stay blocked for that head.
- If Postgres is unavailable, claims fail and queued work remains durable until Docker is restarted.
- Before pulling an upgrade, drain every open Kanban attempt recorded in Postgres. Same-version
  `down`/`up` restart is supported; support/profile replacement remains blocked by nonarchived bridge
  state files.
- Logs, prompts, and worktrees can contain private code or review text. Keep them on encrypted local
  storage and choose retention appropriate for your environment.

## License

MIT
