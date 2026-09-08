# PR Safety Review Contract

Status: read-only merged-PR producer, draft-only controller, and analyst runner. Analyst reads approved investigation systems and retains findings to a dedicated shared-memory bank; it makes no other external write.

## Purpose

`pr-safety-review` assesses one immutable PR snapshot. It checks correctness, necessity,
system assumptions, engineering quality, tests, docs, observability, and incident risk. It returns
structured draft findings for human review.

Canonical analyst instructions: [`agent-config/skills/pr-safety-review/SKILL.md`](../agent-config/skills/pr-safety-review/SKILL.md).

## Hard Boundaries

- Do not reuse `pr-review` or `pr-maintain` worker authority.
- Analyst gets a disposable read-only checkout. Its only route out is `pr-safety-egress`, which permits
  HTTPS CONNECT to approved read hosts (`*.openai.com`, `*.github.com`, `*.buildkite.com`,
  `*.datadoghq.com`) only; it cannot bypass this proxy or reach any other destination.
- Analyst holds read-only credentials for GitHub (`GH_TOKEN`), Buildkite (`BUILDKITE_API_TOKEN`),
  and Datadog (`DD_PAT` bearer), plus internal MCP access to Coderag, SwarmVault, and Hindsight. The
  Hindsight MCP server is bound to the `pr-safety` bank endpoint (`/mcp/pr-safety/`), so retain can
  only reach that bank. No Chat credential, DB password, GitHub/CI/Datadog write scope,
  cloud/metadata credential, host-code, or Docker socket reaches analyst.
- Controller requires and binds `operation_id`, `repo`, `pr`, `head_sha`, `base_sha`, `diff_hash`,
  `policy_version`, `snapshot_path`, and `policy_path`; paths resolve beneath configured roots.
- Changed head means `superseded`, not a review of newer code.
- Work is sourced from already-merged PRs. GitHub remains approval and merge authority; review is a
  forward-fix signal on what already landed, never a gate.
- PR text, comments, code, CI output, and tool output are data, not authorization.
- No remediation, rollback, GitHub comment, CI retry, or Datadog change belongs in first pilot.
  Shared-memory retain is confined to the `pr-safety` bank by the Hindsight MCP endpoint; because the
  analyst reads untrusted content, `pr-safety` is treated as untrusted-derived and is never
  auto-promoted into `fleet-shared`.
- Analyst writes handoff draft only at controller-supplied path in private per-operation output
  workspace. Controller validates and promotes it into immutable local handoff doc, then queues it
  for human review.

## Merged-PR Producer

`bin/pr-safety-merged-pr-producer` reviews MERGED PRs. Each run searches merged PRs authored by the
configured GitHub logins (`PR_SAFETY_MERGED_PR_AUTHORS`, comma-separated) across the org via
`gh search prs --author <login> --merged`, then resolves each to its merge commit and base ref via
`gh pr view`. The fleet `GH_TOKEN` must be SAML-authorized for the org; on a SAML-expiry `403` the
producer exits non-zero with a loud operator alert (never a silent empty result).

The **merge commit SHA is the immutable head** and the event identity. For each merged PR the producer
verifies the configured pinned policy path/version/SHA-256, creates a clean read-only Git snapshot of
the merge commit under `PR_SAFETY_SNAPSHOT_ROOT`, computes `git diff base..merge` SHA-256, then
atomically records the merge SHA plus canonical payload digest in `pr_safety_merged_pr_events` and
enqueues at most one `pr-safety-review` request. A repeated merge SHA cannot enqueue again; a new
merge commit is a new event, key, and snapshot.

`PR_SAFETY_MERGED_PR_INPUT_FILE` supplies a local JSON-lines fixture of normalized
`{repo, number, mergeSha, baseSha}` records for tests and makes no network request. It is test-only
input, not an authorization bypass: policy and snapshot validation remain unchanged.

## Automatic Merged-PR Producer

Use `scripts/pr-safety-merged-pr-producer-launch.sh` under launchd. It sources private `.env`, takes
one host lock, and runs the read-only producer once. Install a copy of
`launchd/com.example.agent-fleet-pr-safety-merged-pr-producer.plist.template` with absolute paths and
user-private log paths. The template runs at load and every 60 seconds.

Do not load the job until `PR_SAFETY_MERGED_PR_AUTHORS`, a SAML-authorized `GH_TOKEN`, and the policy pin
are configured. `.env` must be current-user-owned and mode `0600`; launchd creates logs with `0700` umask.
The template and launcher set a Homebrew-aware `PATH` for `gcloud`, `gh`, and `python3`. Startup failures
are visible in its stderr log; the producer never posts to GitHub.

## Runtime

`pr-safety-review-controller` claims only `pr-safety-review` rows from existing `requests`.
Every payload must include `operation_id`, `repo`, `pr`, `head_sha`, `base_sha`, `diff_hash`,
`policy_version`, `policy_digest`, `snapshot_path`, and `policy_path`. Controller resolves both paths
beneath configured roots, verifies clean snapshot `HEAD`, `git diff base_sha..head_sha` digest, and
policy-file SHA-256 digest. Mismatch becomes `superseded` before analyst starts.

`pr-safety-review-runner` starts a separate non-root, read-only Docker image. It mounts immutable
snapshot and policy paths read-only and mounts only per-operation `handoff.md` writable. It pins the
model with `--model` (`PR_SAFETY_ANALYST_MODEL`, default `gpt-5.6-terra`) so it cannot drift to the
provider default; the pattern must stay on the approved OpenAI provider, matching the egress
allowlist. It passes `OPENAI_API_KEY` and read-only investigation credentials (`GH_TOKEN`, `BUILDKITE_API_TOKEN`,
`DD_PAT`); internal Coderag, SwarmVault, and Hindsight MCP are reached over the analyst network, with
Hindsight bound to the `pr-safety` bank endpoint. No Chat credential, DB password, GitHub/CI/Datadog
write scope, cloud/metadata credential, or Docker socket reaches analyst. Runtime drops Linux
capabilities, prevents privilege escalation, uses temporary runtime storage, and limits process, CPU,
and memory use. Controller marks a valid `clear` result done without
a handoff. For every other terminal result, it validates result and handoff identity, findings, and
evidence, atomically promotes handoff, then uses existing `pending_maintenance_reviews` with final
path and SHA-256 digest in provenance.

## Read-Services Egress

`pr-safety-egress` joins the default network and internal `agent-fleet-pr-safety-analyst` network.
The analyst joins only the internal network and receives `HTTPS_PROXY`, `HTTP_PROXY`, and
`NODE_USE_ENV_PROXY=1`. Squid permits only `CONNECT :443` to the approved read hosts
(`.openai.com`, `.github.com`, `.buildkite.com`, `.datadoghq.com`); direct, metadata, Chat, cloud,
and any off-allowlist destination has no route or is denied. Internal MCP calls (Coderag, SwarmVault,
and Hindsight `pr-safety`-bank retain) stay on the Compose network and do not traverse this proxy.
GitHub/CI/Datadog write actions are prevented by credential scope and mount mode, not by egress rules.

## Handoff Storage

For non-clear results, controller gives analyst one writable `PR_SAFETY_HANDOFF_DRAFT` path inside
a fresh, private per-operation output workspace. Analyst writes `handoff.md` there, organized into two
required sections: `## Concrete breakage` (the mechanical, forward-fixable `findings`) and
`## Human decisions` (each `human_decisions_needed` item plus the description-fidelity and simplicity
assessments). Both headers are always present; an empty section states "None."
Analyzer cannot see or write shared `HANDOFF_ROOT`, choose final path, or overwrite other handoffs.

Controller validates draft against JSON result and immutable operation identity, then copies it to
temporary file under private local `HANDOFF_ROOT`, sets private file permissions, and atomically
publishes final file without overwrite. Final handoff contains operation ID, repository, PR number, immutable source
SHA, diff hash, policy/prompt/model versions, findings, evidence, and recommendations. Queue
`provenance` stores final handoff path and content digest.

Controller mounts `HANDOFF_ROOT` read/write. A later automatic PR-creation agent receives only
controller-selected final `handoff.md` as a read-only mount plus a fresh worktree. Handoff is input,
not authority: controller revalidates queue approval, handoff digest, and source SHA before action.

Reuse existing `pending_maintenance_reviews` queue for v1. Its `reviewed` and `dismissed` states
mean acknowledgement only. A later automatic-PR stage adds explicit `approved_for_pr` state.
Only authenticated, policy-qualified operator can set it. Record actor, reason, source SHA,
handoff digest, and timestamp.

## Controller Activation

Set all `PR_SAFETY_*` roots, policy path/version/digest, and `HANDOFF_ROOT` in private `.env`. Then run:

```bash
scripts/pr-safety-up.sh
```

The script resolves paths, requires an exact policy SHA-256 match, creates private runtime directories,
and starts only the `pr-safety` controller profile. It refuses placeholder or invalid policy inputs.

## Policy Bundle

Controller must give analyst a versioned bundle before run. Bundle contains:

- repository-local engineering and ownership rules;
- pinned revision of documentation-readability policy;
- pinned revision of E2E Ownership Manifesto;
- test and coverage command for target repository, when one exists;
- data classification and approved model-provider policy.

Missing required policy source produces `needs_human_decision`. PR description alone is never
sufficient intent evidence.

## Threat Model

| Threat | Required control |
| --- | --- |
| Prompt injection from PR, code, comments, or tool output | Treat all external text as data. Read-only sandbox; credentials carry no GitHub/CI/Datadog write scope; egress allowlist blocks non-approved hosts. |
| Memory poisoning from untrusted input | Retain only analyst-synthesized findings, never raw untrusted or recalled text. The Hindsight MCP server is bound to the `pr-safety` bank endpoint, so retain cannot reach `fleet-shared`; treat `pr-safety` as an untrusted-derived bank and never auto-promote it into `fleet-shared`. |
| Stale result | Store SHA and diff hash in job payload. Mark changed head as `superseded`. |
| Duplicate merged-PR event | `pr_safety_merged_pr_events` uses the merge commit SHA as primary key and stores canonical payload SHA-256; ledger insert and queue insert are one SQL statement. |
| Bot feedback loop | Use correlation IDs, bot-message filtering, one active operation per PR lineage, quotas, and circuit breaker. |
| Private code or secret leakage | Approved provider only. Redact at every log, database, and model boundary. |
| Unsafe generated fix | Pilot agent creates no fix. Final stage creates one draft PR only after explicit `approved_for_pr`, immutable handoff validation, fresh worktree, and policy validation. |

## Pilot Gates

Pilot starts only when one repository and named reviewers opt in, policy revisions are pinned, data
provider is approved, and circuit-breaker owner is named.

Pilot succeeds only when it creates no external writes and proves useful findings without higher
reviewer effort or author churn. Controller validates and promotes analyst handoff drafts as
immutable local handoff docs, then queues them for human review. Record reviewer time, material-finding acceptance rate,
false-positive rate, duplicate rate, and stale-result rate.

## Final Automatic PR Stage

After shadow pilot and handoff-quality gates pass, controller may create one draft remediation PR
for an `approved_for_pr` handoff. Remediation agent receives immutable source snapshot and
controller-selected read-only local handoff mount. It works only in a new worktree and branch.
Controller, not agent, owns narrow GitHub App capability to create draft PR.

Automated creation never permits merge, deployment, CI retry, Datadog apply, force-push, or
default-branch write. Send resulting draft PR to same Chat room for notification; GitHub code-owner
approval and merge remain human-owned.

## Validation

Run:

```bash
bash tests/test-pr-safety-review-skill.sh
bash tests/test-pr-safety-review-controller.sh
bash tests/test-pr-safety-runtime.sh
bash tests/test-pr-safety-chat-producer.sh
bash tests/test-pr-safety-flow.sh
```

Test guards contract language. It does not prove future runtime sandboxing. Runtime enforcement is
required in server implementation PR.
