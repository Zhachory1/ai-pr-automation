# Hermes API Control Plane

Status: Compose control plane active; host dispatcher and producer launchd jobs retired.

## Architecture

Compose owns deterministic scheduling and effects. Host Hermes owns profile execution.

```text
Compose producers -> Postgres requests/hermes_runs -> Compose hermes-controller
                                                   -> host.docker.internal:8642
                                                   -> /p/<profile>/v1/runs
```

Host launchd keeps:

- pinned Hermes gateway bound to `127.0.0.1:8642`;
- Hermes dashboard;
- installed but inactive PR safety Kanban bridge definition bound to `127.0.0.1:8766`.

Compose runs:

- `hermes-controller`;
- `pr-producer-review`;
- `pr-producer-maintain`;
- `pr-safety-producer`;
- `memory-curate-producer`;
- Postgres, Fleet Controller, and support services.

No controller mount exposes Hermes service home, provider OAuth, SSH keys, browser profile, Docker
socket, or GitHub write credentials. Controller receives profile API key bundle and deterministic
artifact paths. GitHub discovery producers receive only read-only token.

Approved design:

- [PRD](PRD-api-driven-control-plane.md)
- [design](DD-api-driven-control-plane.md)
- [delivery plan](plan-api-driven-control-plane.md)

## Queue and Runs ledger

`hermes_kind_routes` fixes route, generation, profile, auth/profile generations, and caps:

| Kind | Profile | Cap |
| --- | --- | ---: |
| `pr-maintain` | `pr-maintain-v1` | 3 |
| `pr-review` | `pr-review-v1` | 1 |
| `swe-implement` | `swe-implement-v1` | 1 |
| `doc-write` | `doc-write-v1` | 1 |
| `memory-curate` | `memory-curate-v1` | 1 |
| `pr-safety-review` | `pr-safety-v1` | 1 |

Claim and attempt reservation are one transaction. Each `hermes_runs` row stores immutable operation
identity, route/auth/profile generations, exact serialized request bytes and SHA-256, stable
idempotency key, submit count/deadline, Hermes run ID, terminal output digest, and reconcile evidence.
Every `submitting` attempt consumes kind capacity, including accepted, running, and stop-unconfirmed
attempts.

Controller behavior:

1. reserve attempt under API route/generation and fixed cap;
2. final nonce/route/generation CAS before POST;
3. POST exact stored bytes and stable key;
4. replay identical request after lost response, at most eight POSTs/five minutes and within 23 hours;
5. renew queue lease while polling;
6. stop same Hermes run on lease loss and reconcile if termination is uncertain;
7. digest terminal output, strict-parse per kind, run deterministic effect, and nonce-fence settlement.

Review, maintain, and SWE are direct-effect kinds. Missing/interrupted/malformed or otherwise uncertain
results enter `reconcile`; matching operation key remains blocked until human disposition. They never
start a second run automatically.

## Deterministic kind handling

- `doc-write`: controller reuses atomic stage/publication helper. Model returns questions or document
  bytes only. Exact staged bytes and digest bind human approval; publication-only requests skip model.
- `pr-safety-review`: controller validates snapshot head/base/diff and pinned policy before submit,
  writes immutable handoff after strict output, and inserts human queue row only for incident candidate.
- `memory-curate`: model proposes candidates from bounded source bytes. Controller applies secret,
  shape, convention, team dedupe, stricter org, and watermark gates before writes.
- `pr-review`, `pr-maintain`, `swe-implement`: profile performs GitHub effect; controller validates
  exact-head marker, pushed SHA, or repository-bound draft PR URL respectively.

## API keys and profiles

Pinned runtime contract is `agent-config/hermes/native.env`. `sync-support` enables profile
multiplexing, loopback Runs API, and stable distinct API keys for six profiles. Root/operator-owned
key bundle remains outside repository, default:

```text
/Users/Shared/ai-pr-automation-runtime/secrets/hermes-api-keys.json
```

Keys are copied into each installed profile `.env`; controller receives bundle as Compose secret.
Rotation requires pause, drain/reconcile, key replacement, conformance, generation increment, resume.
Do not rotate by editing bundle in place while attempts submit.

## Multi-agent workflow feasibility

Run the read-only Kanban PR Risk Council preflight as the service account:

```bash
sudo -u hermes-agent env HOME=/Users/hermes-agent HERMES_HOME=/Users/hermes-agent/.hermes \
  /Users/hermes-agent/.hermes/hermes-agent/venv/bin/python scripts/hermes-kanban-workflow-preflight.py \
  --hermes-home /Users/hermes-agent/.hermes \
  --install-dir /Users/hermes-agent/.hermes/hermes-agent \
  --contract agent-config/hermes/workflows/pr-risk-council-kanban.json
```

It validates six source profiles and descriptions, Sonnet 5/Haiku 4.5 identifiers, Kanban board,
graph, dispatcher, review, comment/handoff, per-task model override, and circuit-breaker contracts.
It creates one isolated temporary SQLite fixture to prove graph, comment, handoff, dependency promotion,
and review transitions. It makes no service-profile, service-board, model, GitHub, or network changes.

## Authority and producers

Repository authority YAML is scope-of-attention, not credential security. Compose review/maintain
producers discover open PRs, resolve exact heads, and enqueue deduped rows. Root-running safety producer
discovers merged PRs and writes immutable snapshots under the root-owned, `staff`-group-readable `0750`
snapshot root. Hermes bridge and gateway receive group read/traverse access only. Other shared runtime
directories remain service-owned, `staff`-group-writable `0770`. Memory producer enqueues
hourly-deduped schedule trigger. Producers make no model calls.

Set in `.env`:

- `HERMES_AUTHORITY_FILE`;
- `GITHUB_READ_TOKEN_FILE`;
- `PR_SAFETY_MERGED_PR_AUTHORS`, policy digest, and shared snapshot path;
- document stage/inbox paths;
- memory source/state paths.

## Operations

```bash
sudo scripts/hermes-native.sh install       # pinned runtime, profiles, API keys, gateway/dashboard plists
scripts/fleet.sh up                         # host gateway/dashboard + Compose controller/producers
scripts/fleet.sh status
scripts/fleet.sh logs
scripts/fleet.sh down
```

`sync-support` also unloads and removes old dispatcher/producer LaunchDaemons and installed queue
runner binaries. It installs the root-owned safety bridge, v2 workflow support, read-only reconcile and
preflight commands, creates its state directories, and renders its launchd plist. Bridge HMAC key defaults
to `/Users/Shared/ai-pr-automation-runtime/hermes-bridge-secrets/key.json`: parent is root-owned,
`staff`-group-readable/traversable `0750`, and key is service-user-owned `0600`. This keeps bridge key
outside operator-owned `secrets/`, whose API configuration path is `0700`. It does not bootstrap or
start bridge. It refuses support-byte replacement while any nonarchived bridge
workflow exists and unloads a loaded bridge before replacement. If that scan fails, it reloads the old
bridge plist when the bridge was previously loaded. Source artifacts remain in repository only for
bounded rollback/audit during bake; normal lifecycle cannot start retired workers.

Install and `sync-support` create all five v2 council profiles only when all five are absent and gateway,
dashboard, and bridge are stopped. A partial set fails closed. If profiles are missing while gateway is
running, run `scripts/fleet.sh down`, rerun install or `sync-support`, then run `scripts/fleet.sh up`.
Existing complete sets are not replaced and must pass live preflight. Gateway launchd exports exact
snapshot/workflow roots and the 120-second Kanban busy timeout used by profile MCP interpolation.
Bridge preflight also probes a fresh database through pinned Hermes Python and fails unless linked
SQLite reports `journal_mode=delete` and `busy_timeout=120000`.

Bridge operations are separate from fleet lifecycle:

```bash
sudo scripts/hermes-native.sh bridge-start   # explicit activation; runs installed v2 preflight first
sudo scripts/hermes-native.sh bridge-status
sudo scripts/hermes-native.sh bridge-reconcile # read-only report under exact installed bridge environment
sudo scripts/hermes-native.sh bridge-stop
```

The API requires signed requests, exact `Host: hermes-council.localhost:8766`, no `Origin`, and exact
`application/json` for POST. Authentication headers are `X-Hermes-Auth-Generation`,
`X-Hermes-Timestamp`, `X-Hermes-Nonce`, `X-Hermes-Body-SHA256`, and `X-Hermes-Signature`.
Request HMAC-SHA256 input is newline-joined generation, timestamp, nonce, body SHA-256, method, and
raw path. Signed responses use the same fields plus status as the last line. Timestamp tolerance is 60
seconds; nonce retention is 120 seconds. Stop and archive bodies repeat exact persisted `operation_id`,
create-body SHA-256 as `request_body_digest`, and safety request `nonce`. `GET /healthz`, create,
status, stop, and archive responses are signed. Reconcile opens Kanban SQLite with `mode=ro` and is
report-only; it never invokes the pinned connector, repairs, migrates, or removes bridge/Kanban state.

Bridge design uses repository contents from the immutable snapshot root as whole-repository context
for this PR-safety use case. Council tools remain root-owned, non-writable, and confined to configured
snapshot/workflow roots. Model-provider policy authorization is not asserted here; PR6 owns that gate.
This design does not grant access outside those roots or activate bridge, controller, or fleet routes.
PR4 owns controller metrics; final cutover owns `ONCALL.md`, SLOs, alerts, and activation. Until then the unloaded bridge emits only bounded structured request records for local conformance.

## Restricted Kanban council profiles

After validation, stop Hermes and create six restricted workflow clones as the service user:

```bash
sudo scripts/hermes-native.sh down
sudo -u hermes-agent env HOME=/Users/hermes-agent HERMES_HOME=/Users/hermes-agent/.hermes \
  /Users/hermes-agent/.hermes/hermes-agent/venv/bin/python \
  scripts/configure-hermes-kanban-profiles.py \
  --hermes-home /Users/hermes-agent/.hermes --service-user hermes-agent \
  --contract agent-config/hermes/workflows/pr-risk-council-kanban.json --apply
sudo scripts/hermes-native.sh up
```

Original profiles remain unchanged. Clones use Sonnet 5/Haiku 4.5 with no fallback, credentials,
MCP, plugins, background review, memory, delegation, or regular-session tools. Dispatcher-owned
workers receive only task-scoped Kanban lifecycle tools. Roll back while stopped with the same command
using `--restore`.

Create the isolated canary task after restart:

```bash
sudo -u hermes-agent env HOME=/Users/hermes-agent HERMES_HOME=/Users/hermes-agent/.hermes \
  /Users/hermes-agent/.hermes/hermes-agent/venv/bin/python \
  scripts/hermes-kanban-council-canary.py setup \
  --hermes-home /Users/hermes-agent/.hermes \
  --install-dir /Users/hermes-agent/.hermes/hermes-agent
```

Use `status` and `cleanup` with the same `--hermes-home` and `--install-dir` flags to inspect
durable progress and archive the board after a successful `done`.

After the one-worker canary passes, create the sanitized five-task council graph:

```bash
sudo -u hermes-agent env HOME=/Users/hermes-agent HERMES_HOME=/Users/hermes-agent/.hermes \
  /Users/hermes-agent/.hermes/hermes-agent/venv/bin/python \
  scripts/hermes-kanban-risk-council.py setup \
  --hermes-home /Users/hermes-agent/.hermes \
  --install-dir /Users/hermes-agent/.hermes/hermes-agent
```

Four Haiku specialists run in parallel. A Haiku verifier remains dependency-gated until all four
structured handoffs complete. `status` verifies exact workflow/artifact identity, one run per task,
worker-authored comments, completion metadata, zero attachments, and exactly five tasks. `cleanup`
archives the board after terminal completion.

## Validation

```bash
python3 tests/test-hermes-api-foundation.py
python3 tests/test-hermes-controller.py
bash tests/test-hermes-control-plane.sh
bash tests/test-hermes-compose-wiring.sh
bash tests/test-schema-migrate-idempotent.sh
bash tests/test-hermes-doc-write-schema.sh
bash tests/test-hermes-pr-safety-runner.sh
bash tests/test-hermes-memory-curate.sh
```

Postgres tests use throwaway `postgres:16`; fake-Hermes tests make no provider or GitHub effects.
