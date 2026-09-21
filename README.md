# AI PR Automation

Fair, bounded automation for GitHub pull-request review and maintenance — as a **host-native
autonomous agent fleet**.

## Architecture

AI execution runs host-native. A single pinned [Hermes](https://github.com/NousResearch/hermes)
runtime executes every role under a dedicated, non-admin `hermes-agent` macOS account. Docker Compose
renders only the **support substrate**; it no longer runs any AI worker.

```
producers ──enqueue──▶  Postgres `requests`  ──claim──▶  host-native Hermes  ──▶  branch + draft PR
(host cron)             (dedupe + leased claims)         (profile per queue kind)   (human merges)
```

- **Postgres queue** keeps dedupe, leased claims, retry caps, the 3-round maintenance cap, exact-byte
  document approval, and reconcile state. Enrollment and the security-definer queue functions are the
  sole runtime repository authority.
- **Host-native Hermes** claims a request, renders the task as untrusted data into an immutable
  profile prompt, and does the work in an ephemeral worktree: clone, branch, edit, test, commit, push
  over a per-repository SSH deploy key, and open a **draft** PR. One immutable profile per queue kind.
- **Fleet Controller** (`status`) is the operator UI at `https://127.0.0.1:8080`: runs, queue, human-review
  queue, and exact-byte document approval. GitHub remains the PR merge UI.
- The `hermes-agent` account is the security boundary. It holds only provider OAuth, a per-repository
  deploy key, a read-only GitHub API token, approved MCP credentials, and a restricted database role.
  It cannot read the human GitHub token, SSH, Keychain, or Fleet Controller secrets.

Server-side GitHub branch protection keeps merge, protected-branch push, unsafe workflow execution,
deployment, and administration out of the agent's reach. Merge stays a human operating decision.

Full design and rollout: **[`docs/hermes/README.md`](docs/hermes/README.md)**. Support substrate:
**[`docker/README.md`](docker/README.md)**.

## Requirements

- macOS host with a dedicated non-admin `hermes-agent` account
- Docker (support substrate only)
- [GitHub CLI](https://cli.github.com/) authenticated as the operator
- `jq`, `psql` client
- A pinned Hermes install for the service account (`scripts/hermes-native.sh install`)

## Quick start

Bring up the support substrate, then install and enroll the host-native runtime.

```bash
cp .env.example .env    # fill CODE_ROOT, DB/Fleet Controller secrets, Hindsight provider, vault path
scripts/compose.sh up -d --build   # validates vault path, then builds and starts SUPPORT services
scripts/m0-verify.sh               # substrate checks (Postgres, Hindsight, swarmvault, coderag)
```

Generate owner-only Fleet Controller secret files outside `CODE_ROOT`, set their paths in `.env`,
then rebuild `status`:

```bash
install -d -m 700 "$HOME/.config/ai-pr-automation"
umask 077
openssl rand -base64 24 > "$HOME/.config/ai-pr-automation/fleet-controller-password"
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
subjectAltName=DNS:localhost,IP:127.0.0.1
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
Never mount or configure a CA key in Compose. Set all five `FLEET_CONTROLLER_*_FILE` paths from
`.env.example` before `scripts/compose.sh up`.

Open https://127.0.0.1:8080 and sign in. Session lifetime defaults to 12 hours. Fleet Controller
keeps localhost, Host, Origin, and CSRF checks in addition to login.

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

This rollback restores anonymous HTTP Fleet Controller. Stop the host-native runtime before using it.

## Modes

| Kind | GitHub scope | Behavior |
| --- | --- | --- |
| `pr-review` | Open PRs assigned to the operator | Review only; posts `APPROVE` for validated clean verdicts, otherwise `COMMENT` or `REQUEST_CHANGES` |
| `pr-maintain` | Open PRs authored by the operator | Handle review feedback and CI with one bounded fix pass, at most 3 rounds per PR lineage |
| `swe-implement` | Enrolled repository | Implement a bounded task on a fresh branch and open a draft PR |
| `doc-write` | Fleet Controller | Draft a PRD/DD; exact bytes require human approval before filing |
| `pr-safety-review` | Merged PRs | Read-only safety analysis; only incident candidates surface |

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
  `https://127.0.0.1:8080`; **Reviewed** / **Dismiss** update local state only, never GitHub

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
# host-native queue runner: kind→profile map and typed settle
bash tests/test-hermes-queue-runner.sh
# collapsed queue API + YAML authority allowlist
bash tests/test-hermes-queue-authority.sh
bash tests/test-hermes-authority.sh
# pr-safety producer dedupe/snapshot identity and incident-only queue routing
bash tests/test-hermes-pr-safety-producer.sh
bash tests/test-hermes-pr-safety-runner.sh
# Fleet Controller auth and session controls
python3 tests/test-status-server.py
```

The queue tests use a throwaway `postgres:16` container. They do not contact GitHub or a provider.

## Operational notes

- GitHub search is capped at 1,000 results. Reaching the cap fails loudly; narrow repository scope.
- A `reconcile` row means an effect boundary was crossed with an unknown outcome. Verify GitHub state
  before marking it `done` or returning it to `queued`; automatic retries stay blocked for that head.
- The Postgres-loss watchdog stops the runtime before it can make new claims against a missing queue.
- Logs, prompts, and worktrees can contain private code or review text. Keep them on encrypted local
  storage and choose retention appropriate for your environment.

## License

MIT
