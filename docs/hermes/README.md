# Hermes API Control Plane

Status: Compose control plane active; host dispatcher and producer launchd jobs retired.

## Architecture

Compose owns deterministic scheduling and effects. Host Hermes owns profile execution.

```text
Compose producers -> Postgres requests/hermes_runs -> Compose hermes-controller
                                                   -> host.docker.internal:8642
                                                   -> /p/<profile>/v1/runs
```

Host launchd keeps only:

- pinned Hermes gateway bound to `127.0.0.1:8642`;
- Hermes dashboard.

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

## Authority and producers

Repository authority YAML is scope-of-attention, not credential security. Compose review/maintain
producers discover open PRs, resolve exact heads, and enqueue deduped rows. Safety producer discovers
merged PRs and writes immutable snapshots. Memory producer enqueues hourly-deduped schedule trigger.
Producers make no model calls.

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
runner binaries. Source artifacts remain in repository only for bounded rollback/audit during bake;
normal lifecycle cannot start them.

## Read-only PR Risk Council trial

Hermes already carries general `orchestrator`, `reviewer`, `security-engineer`,
`site-reliability-engineer`, `technical-architect`, and `verifier` profiles. Configure those existing
profiles as Bot Mode participants without changing fleet effect profiles:

```bash
sudo scripts/hermes-native.sh down
sudo scripts/hermes-native.sh sync-workflows
sudo scripts/hermes-native.sh up
# rollback: down, restore-workflows, up
```

The orchestrator uses `claude-sonnet-4-6`; five specialists use
`claude-haiku-4-5-20251001`. The command snapshots existing profile YAML, then changes model/provider,
sets `agent.bot_mode_protocol`, adds Bot Mode identity metadata, and sets the API-server toolset to
`no_mcp`; hosted group turns therefore receive only Hermes' verified text-only `bot_room` capability.
It refuses to modify loaded gateways, unsafe profile files, missing profiles, or an existing un-restored
backup. Apply failure restores every original file. Group creation and live delivery remain a separate
explicit feasibility step.

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
