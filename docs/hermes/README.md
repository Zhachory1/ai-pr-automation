# Hermes API Control Plane

Status: Compose control plane active; host PR-safety producer is installed and runs only when `PR_SAFETY_QUEUE_ENGINE=kanban`; other host producer jobs remain retired.

## Architecture

Compose owns deterministic scheduling and effects. Host Hermes owns profile execution.

```text
Compose producers -> Postgres requests/hermes_runs -> Compose hermes-controller
                                                   -> host.docker.internal:8642 (Runs API)
                                                   -> host.docker.internal:8766 (Kanban recovery bridge)
```

Host launchd keeps:

- pinned Hermes gateway bound to `127.0.0.1:8642`;
- Hermes dashboard;
- PR safety Kanban bridge bound to `127.0.0.1:8766`, always available for persisted-attempt recovery;
- PR-safety launchd timer, inert unless queue engine is explicitly `kanban`.

Compose runs:

- `hermes-controller`;
- `pr-producer-review`;
- `pr-producer-maintain`;
- `pr-safety-producer` in `postgres` mode; it idles when queue engine is `kanban`;
- `memory-curate-producer`;
- Postgres, Fleet Controller, and support services.

No controller mount exposes Hermes service home, provider OAuth, SSH keys, browser profile, Docker
socket, or GitHub write credentials. Controller receives profile API key bundle, dedicated bridge
HMAC key, and deterministic artifact paths. GitHub discovery producers receive only read-only token.
The bridge has no Postgres, GitHub, or effect credentials. It is trusted only within this single-host
Docker Desktop-to-loopback deployment.

Approved current design:

- [PRD](PRD-api-driven-control-plane.md)
- [design](DD-api-driven-control-plane.md)
- [delivery plan](plan-api-driven-control-plane.md)

Approved next PR-safety design (implementation only; current bridge/Postgres path remains active until separate launch approval):

- [grounding](grounding-pr-safety-direct-kanban.md)
- [PRD](PRD-pr-safety-direct-kanban.md)
- [design](DD-pr-safety-direct-kanban.md)
- [council](council-pr-safety-direct-kanban.md)
- [delivery plan](plan-pr-safety-direct-kanban.md)
- [pinned CLI evidence](evidence-pr-safety-direct-kanban-cli.json)

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
idempotency key, run/workflow marker, terminal output digest, and reconcile evidence.
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

For `PR_SAFETY_ANALYSIS_ENGINE=kanban`, claim atomically stores `kanban:<workflow_id>` with exact
canonical bridge bytes. Recovery follows that persisted marker, not current environment. Controller
polls signed status while renewing lease, maps verified synthesis, settles once, confirms archived
tombstone, then closes attempt. Deadline or lease loss requires signed stop confirmation first.
Default `single` keeps existing Runs API path and request bytes and never calls the bridge for a new
safety claim. A persisted `kanban:<workflow_id>` marker still recovers through the always-running bridge
when current configuration is `single`; recovery does not create another model run or council.

Review, maintain, and SWE are direct-effect kinds. Missing/interrupted/malformed or otherwise uncertain
results enter `reconcile`; matching operation key remains blocked until human disposition. They never
start a second run automatically.

## Deterministic kind handling

- `doc-write`: controller reuses atomic stage/publication helper. Model returns questions or document
  bytes only. Exact staged bytes and digest bind human approval; publication-only requests skip model.
- `pr-safety-review`: controller validates snapshot head/base/diff and pinned policy before claim.
  `single` uses current profile run; opt-in `kanban` uses fixed council bridge. Both write same immutable
  handoff and insert human queue row only for incident candidate. No automatic cross-engine fallback.
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
producers discover open PRs, resolve exact heads, and enqueue deduped rows. PR-safety queue engine defaults
to `postgres`; when explicitly set to `kanban`, Compose safety discovery idles and the host launchd producer
creates the fixed five-task graph through the pinned local Hermes CLI. The snapshot root stays root-owned
`0750`; direct enqueue uses its service-owned `direct-kanban/` child, which legacy Postgres GC never scans.
Each published snapshot is recursively changed to read/execute-only. Other shared runtime
directories remain service-owned, `staff`-group-writable `0770`. Memory producer enqueues
hourly-deduped schedule trigger. Producers make no model calls.

Set in `.env`:

- `HERMES_AUTHORITY_FILE`;
- `GITHUB_READ_TOKEN_FILE`;
- `PR_SAFETY_MERGED_PR_AUTHORS`, policy digest, shared snapshot path, and
  `PR_SAFETY_ANALYSIS_ENGINE=single|kanban`;
- `HERMES_KANBAN_BRIDGE_KEY_FILE` for host bridge authentication;
- `HERMES_KANBAN_BRIDGE_CONTROLLER_KEY_FILE` for Docker Desktop's controller-only key copy;
- document stage/inbox paths;
- memory source/state paths.

## Direct CLI enqueue (opt-in)

`PR_SAFETY_QUEUE_ENGINE=kanban scripts/fleet.sh up` selects the host producer; default `postgres` keeps
Compose discovery. Pass the mode on every `fleet.sh up` (not only in `.env`). Fleet stops both safety
producers before synchronizing mode, authors, and allowed orgs, then starts only the selected discovery path.
Install merged support files with `sudo scripts/hermes-native.sh sync-support` before activation.

The host producer calls `hermes-pr-safety-kanban-enqueue.py` using Hermes's Python. It validates the snapshot,
reuses the existing restricted profiles/prompts, and places every new PR-safety graph on the persistent
`pr-safety-council` board. Each PR uses its workflow ID as the Hermes tenant and idempotency namespace;
repository and PR number prefix its five card titles. The producer creates all cards blocked, verifies that
operation's complete tenant-filtered graph, then releases synthesis to dependency-gated `todo` and the four
specialists to `ready`. Repeated polls adopt the same cards, including archived cards; worker/human blocks
are never automatically reopened. Existing per-operation boards and pre-migration workflow inputs remain
untouched and are never copied, retried, archived, or reopened by shared-board enqueue. This enqueue-only
slice deliberately adds no automated cleanup, Postgres settlement, final handoff publication, or human
incident queue. Results remain on their original or shared Kanban board. No new bridge call, fork, journal,
controller change, or automatic migration is part of this slice.

Before switching engines, stop discovery and drain existing safety requests. Discovery history is not
migrated between engines; select a bounded, approved pilot to avoid re-reviewing old merges. Provider policy,
quality/cost evaluation, and human activation approval remain gates for production council traffic.

## Operations

```bash
sudo scripts/hermes-native.sh install       # pinned runtime, profiles, API keys, gateway/dashboard plists
scripts/fleet.sh up                         # host gateway/dashboard/bridge + Compose controller/producers
scripts/fleet.sh status
scripts/fleet.sh logs
scripts/fleet.sh down
```

For a pinned Hermes version change, stop both control planes before replacing runtime bytes:

```bash
scripts/fleet.sh down
sudo scripts/hermes-native.sh install
PR_SAFETY_ANALYSIS_ENGINE=single scripts/fleet.sh up
```

`sync-support` also unloads and removes old dispatcher/producer LaunchDaemons and installed queue
runner binaries. It installs the root-owned safety bridge, v2 workflow support, read-only reconcile and
preflight commands, creates its state directories, and renders its launchd plist. Bridge HMAC key defaults
to `/Users/Shared/ai-pr-automation-runtime/hermes-bridge-secrets/key.json`: parent is root-owned,
`staff`-group-readable/traversable `0750`, and key is service-user-owned `0600`. `fleet.sh up` copies
those exact bytes to operator-owned `secrets/hermes-kanban-bridge-key.json` with mode `0600`, because
Docker Desktop mounts host files as the operator and cannot mount the service-owned source. Compose
mounts only that controller copy. `sync-support` does not bootstrap or start bridge. Its state-file guard refuses support-byte replacement while any nonarchived bridge workflow
exists and unloads a loaded bridge before replacement. If that scan fails, it reloads the old bridge plist
when the bridge was previously loaded. Source artifacts remain in repository only for bounded
rollback/audit during bake; normal lifecycle cannot start retired workers.

Install and `sync-support` create all five v2 council profiles only when all five are absent and gateway,
dashboard, and bridge are stopped. A partial set fails closed. If profiles are missing while gateway is
running, run `scripts/fleet.sh down`, rerun install or `sync-support`, then run `scripts/fleet.sh up`.
Existing complete sets are not replaced and must pass live preflight. Support sync writes only the
snapshot root, workflow root, and pinned council-tools interpreter into each v2 profile's private `.env`,
preserving existing entries. Hermes strips root-profile settings when spawning a different profile;
these worker settings must therefore be profile-local. Preflight requires those local settings rather
than injecting them into a synthetic worker environment. Gateway launchd also exports the roots and
120-second Kanban busy timeout.
Bridge preflight also probes a fresh database through pinned Hermes Python and fails unless linked
SQLite reports `journal_mode=delete` and `busy_timeout=120000`.

`fleet.sh up` always exports the real dedicated bridge key. Native `up` reuses an installed version that
passes preflight, or runs guarded `sync-support` when installation is needed, then starts gateway and
dashboard. Fleet refreshes the operator-owned controller copy, explicitly starts bridge, then starts
Compose, then the opt-in host safety producer. `fleet.sh down` stops that producer first,
then Compose, then native bridge, dashboard, and gateway. A same-version `down`/`up`
restart is supported, including recovery of a persisted Kanban marker under `single`.

The state-file guard protects support/profile replacement, but it does not determine engine behavior or
query Postgres. Before pulling an upgrade, the operator must drain every open Kanban attempt in Postgres;
do not pull or run `install`, `sync-support`, or `sync-profiles` while one exists. After drain, all bridge
workflow markers must be archived before support sync. `PR_SAFETY_ANALYSIS_ENGINE=single` still blocks
new Kanban claims even though bridge and key remain available for recovery.

Direct bridge commands remain available for diagnostics:

```bash
sudo scripts/hermes-native.sh bridge-start
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
status, stop, and archive responses are signed. Transport is plaintext only across the trusted single-host
Docker Desktop-to-loopback channel; HMAC provides integrity/authentication, not confidentiality. Controller
already has the same read-only snapshot paths. Multi-host or untrusted local networking requires TLS before
activation. Reconcile opens Kanban SQLite with `mode=ro` and is report-only; it never invokes the pinned
connector, repairs, migrates, or removes bridge/Kanban state.

Bridge design uses repository contents from the immutable snapshot root as whole-repository context
for this PR-safety use case. Council tools remain root-owned, non-writable, and confined to configured
snapshot/workflow roots. Model-provider policy authorization is not asserted here; PR6 owns that gate.
This design grants no access outside those roots. Merge does not change `single` default or activate
Kanban analysis. Final cutover owns policy approval, `ONCALL.md`, SLOs, alerts, and engine activation.

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
python3 tests/test-hermes-kanban-safety-bridge.py
bash tests/test-hermes-control-plane.sh
bash tests/test-hermes-compose-wiring.sh
bash tests/test-hermes-native-foundation.sh
bash tests/test-schema-migrate-idempotent.sh
bash tests/test-hermes-doc-write-schema.sh
bash tests/test-hermes-pr-safety-runner.sh
bash tests/test-hermes-memory-curate.sh
```

Postgres tests use throwaway `postgres:16`; fake-Hermes tests make no provider or GitHub effects.
