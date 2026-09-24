# AI PR Automation

Fair, bounded automation for GitHub pull-request review and maintenance — as a **host-native
autonomous agent fleet**.

## Architecture

A single pinned [Hermes](https://github.com/NousResearch/hermes) runtime executes profiles under the
non-admin `hermes-agent` macOS account. Docker Compose owns producers, queue claims, leases, strict
result handling, and deterministic effects. Controller calls host Hermes through profile-scoped Runs
API keys; host launchd keeps gateway, dashboard, and the PR-safety Kanban recovery bridge.

```
Compose producers ──▶ Postgres queue/attempts ──▶ Compose controller ──Runs API/bridge──▶ host Hermes
```

- **Postgres queue** keeps dedupe, fixed per-kind caps, route generations, exact request bytes,
  stable idempotency keys, leased claims, exact-byte document approval, and reconcile state.
- **Compose controller** claims and renews queue work, replays lost submissions with identical bytes,
  polls/stops Hermes runs, strictly parses output, and nonce-fences settlement. PR safety defaults to
  current single run, which never creates a Kanban council. The signed bridge stays available only so
  persisted Kanban attempts can recover; opting into `kanban` also allows new council claims.
- **Host-native Hermes** owns profile/model/tool execution and host credentials. One immutable profile
  maps to each queue kind; direct-effect uncertainty enters human reconcile and never blind-retries.
- **Fleet Controller** (`status`) is the operator UI at `https://fleet.localhost:8080`: runs, queue, human-review
  queue, and exact-byte document approval. GitHub remains the PR merge UI.
- The `hermes-agent` account is the execution boundary. It holds provider OAuth, repository deploy
  keys, GitHub API access, and approved MCP credentials, but no queue database credential. Compose
  controller cannot read those host credentials or service home. The bridge has no Postgres, GitHub,
  or effect credential and is trusted only on this single-host deployment.

Server-side GitHub branch protection keeps merge, protected-branch push, unsafe workflow execution,
deployment, and administration out of the agent's reach. Merge stays a human operating decision.

Full design and rollout: **[`docs/hermes/README.md`](docs/hermes/README.md)**. Support substrate:
**[`docker/README.md`](docker/README.md)**.

## Requirements

- macOS host with a dedicated non-admin `hermes-agent` account
- Docker Desktop for Compose controller/producers and support services
- Read-only GitHub token for Compose producers
- `jq`, `psql` client for operator diagnostics
- A pinned Hermes install for the service account (`scripts/hermes-native.sh install`)

## Quick start

Configure `.env`, install the pinned host Hermes runtime, then start the fleet.

```bash
cp .env.example .env    # fill DB/UI secrets, API key bundle, producer token, and runtime paths
scripts/fleet.sh up                # Compose controller/producers + host gateway/dashboard/bridge
scripts/fleet.sh status
scripts/m0-verify.sh               # substrate checks (Postgres, Hindsight, swarmvault, coderag)
```

Generate owner-only Fleet Controller secret files outside `CODE_ROOT`, set their paths in `.env`,
then rebuild `status`:

```bash
install -d -m 700 "$HOME/.config/ai-pr-automation"
umask 077
openssl rand -hex 32 > "$HOME/.config/ai-pr-automation/fleet-controller-session-secret"
openssl genrsa -out "$HOME/.config/ai-pr-automation/fleet-controller-ca.key" 3072
openssl req -x509 -new -sha256 -days 3650 \
  -key "$HOME/.config/ai-pr-automation/fleet-controller-ca.key" \
  -out "$HOME/.config/ai-pr-automation/fleet-controller-ca.crt" \
  -subj '/CN=Fleet Controller Local CA' \
  -addext 'basicConstraints=critical,CA:TRUE,pathlen:0' \
  -addext 'keyUsage=critical,keyCertSign,cRLSign'
cert_tmp="$(mktemp -d "${TMPDIR:-/tmp}/fleet-controller.XXXXXX")"
chmod 700 "$cert_tmp"
trap 'rm -rf "$cert_tmp"' EXIT
openssl req -new -newkey rsa:3072 -nodes \
  -keyout "$HOME/.config/ai-pr-automation/fleet-controller.key" \
  -out "$cert_tmp/fleet-controller.csr" -subj '/CN=localhost'
cat > "$cert_tmp/fleet-controller.ext" <<'EOF'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:localhost,IP:127.0.0.1,DNS:fleet.localhost,DNS:hermes.localhost,DNS:memory.localhost,DNS:code.localhost
EOF
openssl x509 -req -sha256 -days 365 -in "$cert_tmp/fleet-controller.csr" \
  -CA "$HOME/.config/ai-pr-automation/fleet-controller-ca.crt" \
  -CAkey "$HOME/.config/ai-pr-automation/fleet-controller-ca.key" -CAcreateserial \
  -out "$HOME/.config/ai-pr-automation/fleet-controller.crt" \
  -extfile "$cert_tmp/fleet-controller.ext"
rm -rf "$cert_tmp"
trap - EXIT
chmod 600 "$HOME/.config/ai-pr-automation/fleet-controller.key"
rm -f "$HOME/.config/ai-pr-automation/fleet-controller-ca.key" \
  "$HOME/.config/ai-pr-automation/fleet-controller-ca.srl"
security add-trusted-cert -r trustRoot -k "$HOME/Library/Keychains/login.keychain-db" \
  "$HOME/.config/ai-pr-automation/fleet-controller-ca.crt"
git archive b7fe7ed Dockerfile.status bin/status-server \
  | docker build -f Dockerfile.status -t agent-fleet/status:pre-auth-b7fe7ed -
scripts/compose.sh up -d --build --force-recreate status
```

Trusting the CA certificate changes the human login Keychain and remains an explicit operator action.
The command destroys the CA signing key after issuing one leaf, so it cannot mint other trusted identities.
Never mount or configure a CA key in Compose. Set the session/TLS `FLEET_CONTROLLER_*_FILE` paths
from `.env.example` before `scripts/compose.sh up`.

Open https://localhost:8080 for the unified UI landing page. All UIs share port 8080 through nginx
hostname routing: `fleet.localhost` (Fleet Controller), `hermes.localhost` (Hermes dashboard),
`memory.localhost` (Hindsight), and `code.localhost` (Coderag). The proxy is loopback-only and
passwordless. Fleet Controller still enforces exact Host, Origin, and CSRF checks for writes.

Then install the host-native runtime under `hermes-agent` and grant a repository:

```bash
sudo scripts/hermes-native.sh install          # pinned Hermes for the service account
sudo scripts/hermes-native.sh sync-profiles     # install immutable profiles
# grant repos in the authority YAML (see agent-config/hermes/authority.example.yaml)
scripts/hermes-authority.py --check Zhachory1/ai-pr-automation
```

See [`docs/hermes/README.md`](docs/hermes/README.md) for the authority allowlist and per-role
activation.

### Fleet Controller rollback

Rollback does not depend on valid new TLS material. Use direct Compose only for this retained-image
recovery path:

```bash
docker compose stop ui-proxy
FLEET_CONTROLLER_PASSWORD_FILE=/dev/null \
FLEET_CONTROLLER_SESSION_SECRET_FILE=/dev/null \
FLEET_CONTROLLER_TLS_CA_CERT_FILE=/dev/null \
FLEET_CONTROLLER_TLS_CERT_FILE=/dev/null \
FLEET_CONTROLLER_TLS_KEY_FILE=/dev/null \
FLEET_CONTROLLER_ROLLBACK_VERSION=pre-auth-b7fe7ed \
  docker compose -f docker-compose.yml -f docker-compose.status-rollback.yml \
  up -d --no-deps --no-build --force-recreate status
test "$(curl -sS -o /tmp/fleet-controller-rollback.html -w '%{http_code}' \
  http://127.0.0.1:8080/)" = 200
grep -q 'agent-fleet' /tmp/fleet-controller-rollback.html
```

This rollback restores anonymous HTTP Fleet Controller. Stop the fleet before using it.

## Modes

| Kind | GitHub scope | Behavior |
| --- | --- | --- |
| `pr-review` | Open PRs assigned to the operator | Review only; posts `APPROVE` for validated clean verdicts, otherwise `COMMENT` or `REQUEST_CHANGES` |
| `pr-maintain` | Open PRs authored by the operator | Handle review feedback and CI with one bounded fix pass, at most 3 rounds per PR lineage |
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
- one initial feedback/CI snapshot, then at most one low-risk fix pass, at most 3 rounds per PR lineage
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

- Grant each repository in the authority YAML; producers only enqueue work for granted repos.
- The deploy key pushes feature branches only; branch protection blocks protected-branch and merge.
- The read-only API token cannot merge; merge is a human GitHub action.
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
