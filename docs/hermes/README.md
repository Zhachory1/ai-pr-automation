# Hermes Migration

Status: host-native autonomous migration approved; Fleet Controller auth merged; native foundation in progress.

## Artifacts

- [Host-native autonomous design](DD-host-native-agent-engine.md)
- [Host-native implementation plan](plan-host-native-autonomous-hermes.md)
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

## Autonomy Enrollment

Native Hermes can enqueue or claim repository work only through `hermes_worker` database functions.
Those functions require active repository proof checked within ten minutes. Existing Compose workers
keep their old queue API only for rollback.

Validate human-produced denial evidence, then enroll with existing fleet DB credentials:

```bash
scripts/hermes-repo-gate.py evidence.json
scripts/hermes-repo-enroll.py evidence.json
```

Evidence binds repository, credential, ruleset, workflow, environment policy, required denials, and
allowed actions. This foundation creates no credential, evidence, enrollment, or live GitHub probe.
PR 4 performs one `Zhachory1/ai-pr-automation` autonomy capability check before native credentials start.
The check proves both allowed work (feature push, draft PR, review) and enforced boundaries
(protected branch, unsafe workflow/deployment, administration). Merge remains normal human policy,
not a hard credential boundary. Workflow files may still be
edited in a feature branch; `unsafe_workflow_execution` means agent-push runs get no dangerous
secret, write token, deployment, or release authority.

`bin/hermes-queue-runner` and `swe-implement-v1` are installed paused. Live activation still requires
`hermes-agent`, enrollment for this repository, GitHub credentials, fresh authority evidence, and
explicit human approval. No Fleet Worker or Effect Gateway is introduced.

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

## Autonomy Capability Check

Before activating credentials, prove the boundary once against a live repository. Generate evidence,
validate denials, and enroll:

```bash
scripts/hermes-repo-gate.py evidence.json      # server-side denials hold
scripts/hermes-repo-enroll.py evidence.json    # record runtime repository authority
```

Evidence binds repository, credential fingerprint, ruleset, workflow, and environment-policy digests,
required denials (protected push, unsafe workflow execution, deployment, administration), and allowed
actions (unprotected push, draft PR, review). Enrollment proof must be refreshed within ten minutes of
an enqueue or claim; `scripts/hermes-authority-watch.py` refreshes it or invalidates enrollment when
an authority digest changes. Merge stays a human GitHub decision, not a hard credential boundary.

### Local (non-repo) roles

`doc-write` and `memory-curate` touch no GitHub repository, so the GitHub capability probes do not
apply. They authorize through a reserved `local/fleet` sentinel enrollment created by
`scripts/hermes-local-enroll.py` from a minimal authority digest. The queue functions enforce the
split both ways: a local kind must use the sentinel, and the sentinel cannot authorize a repo-scoped
kind. The same ten-minute freshness, security-definer API, and no-direct-DML rules apply.

## Per-Role Activation

Each role activates behind the same exclusive cutover:

1. Confirm the queue has no in-flight row for the kind.
2. Enroll the target repository with fresh evidence.
3. Enqueue one bounded task through the security-definer queue function.
4. Run `bin/hermes-queue-runner <kind>` once and verify a single claimant, the typed result, and the
   expected draft PR or review.
5. Leave the executor paused until standing activation is approved.

## Validation

```bash
bash tests/test-hermes-queue-runner.sh
bash tests/test-hermes-autonomy-gate.sh
bash tests/test-hermes-native-foundation.sh
bash tests/test-hermes-state-roundtrip.sh
python3 tests/test-status-server.py
```

## Boundaries

The runtime proves:

- exact Hermes image and native commit pin;
- immutable profile per queue kind;
- enrollment as sole runtime repository authority;
- least-privilege security-definer queue API;
- server-side denial of protected push, merge, unsafe workflow execution, deployment, administration.

The runtime does not grant:

- human GitHub token, SSH, Keychain, or Fleet Controller secret access;
- protected-branch or default-branch push;
- API merge, deployment, release, or administration;
- exact-byte document publication without human approval.
