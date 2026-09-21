# Hermes Migration

Status: host-native autonomous migration approved; Fleet Controller auth merged; native foundation in progress.

## Artifacts

- [Host-native autonomous design](DD-host-native-agent-engine.md)
- [Host-native implementation plan](plan-host-native-autonomous-hermes.md)
- [Authority allowlist and two-tier memory design](DD-authority-and-memory.md)
- [Roadmap](../hermes-migration-roadmap.md)
- [Grounding brief](grounding-brief.md)
- [PRD](PRD-m0-m2.md)
- [Design](DD-m0-m2.md)
- [Council decision](council-m0-m2.md)
- [Parity matrix](parity-matrix.md)
- [M0 plan](plan-m0-evidence-scaffold.md)
- [M2 grounding](grounding-m2-doc-runtime.md)
- [M2 PRD](PRD-m2-doc-runtime.md)
- [M2 design](DD-m2-doc-runtime.md)
- [M2 council](council-m2-doc-runtime.md)
- [M2a plan](plan-m2a-doc-foundation.md)
- [M2b shadow/routing plan](plan-m2b-shadow-routing.md)
- [OAuth PRD](PRD-oauth-login.md)
- [OAuth design](DD-oauth-login.md)
- [OAuth plan](plan-oauth-login.md)

## Native Foundation

Pinned contract: `agent-config/hermes/native.env`. Foundation installs one headless gateway and
`smoke-v1` profile under dedicated `hermes-agent` account. It does not create account, configure
provider credentials, load LaunchDaemon, claim queue work, or make provider calls.

After human creates `hermes-agent`, install without starting:

```bash
sudo scripts/hermes-native.sh install
sudo scripts/hermes-native.sh preflight
```

Operator-local Hermes may exist for CLI testing. It is not fleet runtime. Start dedicated gateway
only after account, provider, API key, and autonomy gates are approved:

```bash
sudo scripts/hermes-native.sh start
sudo scripts/hermes-native.sh stop
scripts/hermes-native.sh status
scripts/hermes-native.sh logs
```

## Repo Authority

Authority is scope-of-attention, not security. The enforcement boundary is the `hermes-agent` OS
account, the repo-scoped deploy key, the read-only API token, and GitHub branch protection — those
enforce which repos and what actions server-side. The agent does not pre-verify them; if it hits a
protected-branch, merge, or permission wall it stops and reconciles.

The operator lists granted repositories in a plain YAML allowlist outside `CODE_ROOT` and git (see
`agent-config/hermes/authority.example.yaml`), pointed to by `HERMES_AUTHORITY_FILE`:

```bash
scripts/hermes-authority.py --check Zhachory1/ai-pr-automation   # exit 0 granted, 3 denied
scripts/hermes-authority.py                                       # list granted repos
```

Producers consult the allowlist before enqueuing repo-scoped work. The queue functions no longer take
a proof or check enrollment; there is no freshness gate. Remove a repo from the YAML to stop the
fleet spending effort on it. Local roles (`doc-write`, `memory-curate`) are not repo-scoped and need
no grant.

This replaces the earlier enrollment/proof/10-minute-freshness model, which re-proved a server-side
wall that already enforces itself. See [DD-authority-and-memory.md](DD-authority-and-memory.md).

## M0 Pull Requests

| Work | PR | State |
| --- | --- | --- |
| Intent, parity, and plan | [#117](https://github.com/Zhachory1/ai-pr-automation/pull/117) | merged |
| Pinned disabled Compose service | [#118](https://github.com/Zhachory1/ai-pr-automation/pull/118) | merged |
| Baseline metrics | [#119](https://github.com/Zhachory1/ai-pr-automation/pull/119) | merged |
| Isolated state-volume round trip | [#120](https://github.com/Zhachory1/ai-pr-automation/pull/120) | merged |

M0 focused validation passed after merge. No M0 PR activates Hermes.

## M2a Pull Requests

| Work | PR | State |
| --- | --- | --- |
| Design and plan | [#121](https://github.com/Zhachory1/ai-pr-automation/pull/121) | merged |
| Runtime/filesystem assumptions | [#122](https://github.com/Zhachory1/ai-pr-automation/pull/122) | merged |
| Durable run/publication state | [#123](https://github.com/Zhachory1/ai-pr-automation/pull/123) | merged |
| Atomic publication helper | [#124](https://github.com/Zhachory1/ai-pr-automation/pull/124) | merged |
| Exact publication approval | [#125](https://github.com/Zhachory1/ai-pr-automation/pull/125) | merged |
| Bounded Runs adapter | [#126](https://github.com/Zhachory1/ai-pr-automation/pull/126) | merged |
| Immutable prompt renderer | [#127](https://github.com/Zhachory1/ai-pr-automation/pull/127) | merged |
| Runtime and egress conformance | [#128](https://github.com/Zhachory1/ai-pr-automation/pull/128) | merged |

No M2a PR routes a doc request through Hermes or makes a paid provider call.

## Account OAuth

Provider credentials live in the `hermes-agent` account's `~/.hermes/.env`, never in repo `.env`.
Log the service account into a provider before activating any paid role:

```bash
sudo -u hermes-agent env HOME=/Users/hermes-agent HERMES_HOME=/Users/hermes-agent/.hermes \
  /Users/hermes-agent/.local/bin/hermes auth add anthropic --type oauth --no-browser
sudo -u hermes-agent env HOME=/Users/hermes-agent HERMES_HOME=/Users/hermes-agent/.hermes \
  /Users/hermes-agent/.local/bin/hermes auth status anthropic
```

OpenAI Codex uses the same flow with `openai-codex`. If a token is exposed, revoke it at the provider
before local logout. Each immutable profile pins its own `model.provider` / `model.default`.

## Host-Native Runtime

One pinned Hermes runs every role under `hermes-agent`. Install and manage the gateway with:

```bash
sudo scripts/hermes-native.sh install         # pinned Hermes for the service account
sudo scripts/hermes-native.sh sync-profiles    # install immutable profiles
sudo scripts/hermes-native.sh start            # load LaunchDaemon (after gates approved)
sudo scripts/hermes-native.sh stop             # maintenance mode; stops the gateway
scripts/hermes-native.sh status
scripts/hermes-native.sh logs
```

`bin/hermes-queue-runner <kind>` claims one request, renders it as untrusted task data into the
matching immutable profile, does the work in an ephemeral worktree, and settles a typed result. The
kind→profile map is fixed. `bin/hermes-postgres-watchdog` stops the gateway before it can claim
against a missing queue.

Mapped kinds: `swe-implement` → `swe-implement-v1` (typed SWE settle, draft-PR URL); `pr-review` →
`pr-review-v1` (generic settle, exact-head marker); `pr-maintain` → `pr-maintain-v1` (generic settle,
pushed head). `pr-review-v1` resolves the head, refuses to approve an incomplete or superseded diff,
and posts one review per head. `pr-maintain-v1` works the exact claim head, makes one bounded fix pass,
pushes with force-with-lease, and resolves addressed threads; the three-round cap and stale-head
supersede are enforced server-side in `hermes_enqueue_request`. Branch protection keeps merge
human-owned.

## Dispatcher

Queue execution is driven by a long-running dispatcher, not interval timers. `bin/hermes-dispatcher`
runs under launchd as `hermes-agent` and continuously drains the queue: each pass, for every kind
with a free slot and unclaimed depth (`hermes_queue_depth`), it spawns one executor in the background
and tracks its PID to enforce a per-kind concurrency cap. Work starts within a couple of seconds of
enqueue; there are no per-role timers to tune.

Per-kind caps (env-overridable): `pr-maintain=3`, `pr-review=1`, `swe-implement=1`, `memory-curate=1`.
A crashed executor's row is reclaimed on lease expiry; a crashed dispatcher is restarted by launchd
and in-flight rows are never lost (claim/settle is transactional and nonce-fenced). SIGTERM drains
in-flight executors before exit; the maintenance file pauses new claims. The dispatcher replaces the
consumer cron jobs entirely — do not run both.

```bash
sudo scripts/hermes-native.sh dispatcher-start   # load the dispatcher daemon
sudo scripts/hermes-native.sh dispatcher-stop    # SIGTERM, drain, unload
```

## Per-Role Activation

Each role activates behind the same exclusive cutover:

1. Confirm the queue has no in-flight row for the kind.
2. Grant the target repository in the authority YAML (repo roles only).
3. Enqueue one bounded task through the security-definer queue function.
4. Run `bin/hermes-queue-runner <kind>` once and verify a single claimant, the typed result, and the
   expected draft PR or review.
5. Leave the executor paused until standing activation is approved.

The boundary is enforced server-side: if the agent attempts a protected-branch push, merge, or other
denied action, GitHub rejects it and the row reconciles. Nothing is pre-proven client-side.

## Validation

```bash
bash tests/test-hermes-queue-runner.sh
bash tests/test-hermes-queue-authority.sh
bash tests/test-hermes-authority.sh
bash tests/test-hermes-native-foundation.sh
bash tests/test-hermes-state-roundtrip.sh
python3 tests/test-status-server.py
```

## Boundaries

The runtime proves:

- exact Hermes image and native commit pin;
- immutable profile per queue kind;
- YAML allowlist as operator scope-of-attention (not a security gate);
- least-privilege security-definer queue API;
- server-side denial of protected push, merge, unsafe workflow execution, deployment, administration.

The runtime does not grant:

- human GitHub token, SSH, Keychain, or Fleet Controller secret access;
- protected-branch or default-branch push;
- API merge, deployment, release, or administration;
- exact-byte document publication without human approval.
