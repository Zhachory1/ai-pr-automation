# PR Safety Review Contract

Status: read-only merged-PR producer and dedicated PR-safety agent server. Agent runs directly in worker container.

## Purpose

`pr-safety-review` assesses one immutable PR snapshot. It checks correctness, necessity,
system assumptions, engineering quality, tests, docs, observability, and incident risk. It returns
structured draft findings for human review.

Canonical analyst instructions: [`agent-config/skills/pr-safety-review/SKILL.md`](../agent-config/skills/pr-safety-review/SKILL.md).

## Hard Boundaries

- Do not reuse `pr-review` or `pr-maintain` write authority.
- Worker joins internal PR-safety network only. `pr-safety-egress` is sole internet path. It allows HTTPS to OpenAI, GitHub, Buildkite, and Datadog only.
- Agent gets read-only GitHub, Buildkite, and Datadog credentials. No Chat, cloud, GitHub-write, CI-write, or Datadog-write credential.
- Agent subprocess gets clean environment without queue DB credentials. It shares worker container and mounted roots. This is accepted trust tradeoff.
- Worker has no Docker socket. No nested agent container.
- `hindsight-world` reads `fleet-shared`; agent must not write there. `hindsight-pr-safety` reads and writes dedicated `pr-safety` bank.
- Agent server binds operation, repo, PR, head, base, diff, policy, snapshot, and policy path.
- Changed head means `superseded`, not review of newer code.
- Work comes from merged PRs. GitHub remains approval and merge authority. Review is forward-fix signal, not merge gate.
- PR text, comments, code, CI output, tool output, and recalled memory are data, not authorization.
- No remediation, rollback, GitHub comment, CI retry, or Datadog change.
- Agent writes only supplied handoff draft. Agent server validates result and publishes immutable local handoff for human review.

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
merge commit is a new event, key, and snapshot. When a new head for the same `repo#pr` enqueues, any
still-`queued` review of that PR at an older head is marked `superseded` (no point reviewing code a
newer merge already replaced); a `running` review is left to finish and `done` reviews are history.

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

`agent-server-pr-safety` claims only `pr-safety-review` rows from existing `requests`.
Every payload must include `operation_id`, `repo`, `pr`, `head_sha`, `base_sha`, `diff_hash`,
`policy_version`, `policy_digest`, `snapshot_path`, and `policy_path`. Agent server resolves both paths
beneath configured roots, verifies clean snapshot `HEAD`, `git diff base_sha..head_sha` digest, and
policy-file SHA-256 digest. Mismatch becomes `superseded` before analyst starts.

PR-safety service uses shared `Dockerfile.agent-server` image with specialized entrypoint. It runs `mewritecode exec` directly from trusted per-operation workspace, not untrusted repository root. This blocks repository `.mcp.json` from changing tool configuration. Server clears tmpfs session data before and after each run. It pins `PR_SAFETY_ANALYST_MODEL` to approved OpenAI provider. Agent subprocess gets clean environment with model key, read-only investigation credentials, supplied input/output paths, and proxy settings. Queue DB variables stay out of child environment. Worker has read-only root, dropped capabilities, PID/CPU/memory limits, temporary runtime storage, read-only snapshots/policies, and writable work/handoff mounts. Agent can see worker mounts and network. This is accepted simpler trust model.

Valid `clear` result becomes done without handoff. Other valid results become immutable handoff plus `pending_maintenance_reviews` row with path and SHA-256 provenance.

## Read-Services Egress

`pr-safety-egress` joins default network and internal `agent-fleet-pr-safety-analyst` network.
PR-safety agent server joins internal network only and receives `HTTPS_PROXY`, `HTTP_PROXY`, and
`NODE_USE_ENV_PROXY=1`. Squid permits only `CONNECT :443` to approved read hosts
(`.openai.com`, `.github.com`, `.buildkite.com`, `.datadoghq.com`); direct, metadata, Chat, cloud,
and any off-allowlist destination has no route or is denied. Internal MCP calls stay on Compose network. `hindsight-world` is recall-only by agent policy. `hindsight-pr-safety` can retain to dedicated bank.
GitHub/CI/Datadog write actions are prevented by credential scope and mount mode, not by egress rules.

## Handoff Storage

For non-clear results, agent server gives analyst one writable `PR_SAFETY_HANDOFF_DRAFT` path inside
a fresh, private per-operation output workspace. Analyst writes `handoff.md` there, organized into two
required sections: `## Concrete breakage` (the mechanical, forward-fixable `findings`) and
`## Human decisions` (each `human_decisions_needed` item plus the description-fidelity and simplicity
assessments). Both headers are always present; an empty section states "None."
Agent can see worker mounts but is instructed to use only current operation paths. Agent cannot choose final path or overwrite another handoff.

Agent server validates draft against JSON result and immutable operation identity, then copies it to
temporary file under private local `HANDOFF_ROOT`, sets private file permissions, and atomically
publishes final file without overwrite. The final filename is prefixed for humans as
`<owner>__<name>__pr<N>__<operation_id>.md` (repository slash escaped to `__`); the operation ID
suffix keeps it unique and dedup-safe. Final handoff contains operation ID, repository, PR number, immutable source
SHA, diff hash, policy/prompt/model versions, findings, evidence, and recommendations. Queue
`provenance` stores final handoff path and content digest.

Agent server mounts `HANDOFF_ROOT` read/write. A later automatic PR-creation agent receives only
agent server-selected final `handoff.md` as a read-only mount plus a fresh worktree. Handoff is input,
not authority: agent server revalidates queue approval, handoff digest, and source SHA before action.

Reuse existing `pending_maintenance_reviews` queue for v1. Its `reviewed` and `dismissed` states
mean acknowledgement only. A later automatic-PR stage adds explicit `approved_for_pr` state.
Only authenticated, policy-qualified operator can set it. Record actor, reason, source SHA,
handoff digest, and timestamp.

## Agent Server Activation

Set all `PR_SAFETY_*` roots, policy path/version/digest, and `HANDOFF_ROOT` in private `.env`. Then run:

```bash
scripts/pr-safety-up.sh
```

The script resolves paths, requires an exact policy SHA-256 match, creates private runtime directories,
and starts only the `pr-safety` agent server profile. It refuses placeholder or invalid policy inputs.

## Policy Bundle

Agent server must give analyst a versioned bundle before run. Bundle contains:

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
| Memory poisoning from untrusted input | Recall world memory through `hindsight-world`; write only synthesized findings through `hindsight-pr-safety`. This split is agent-enforced because Hindsight 0.9.2 has no per-client bank permissions. Treat `pr-safety` as untrusted-derived. |
| Stale result | Store SHA and diff hash in job payload. Mark changed head as `superseded`. |
| Duplicate merged-PR event | `pr_safety_merged_pr_events` uses the merge commit SHA as primary key and stores canonical payload SHA-256; ledger insert and queue insert are one SQL statement. |
| Bot feedback loop | Use correlation IDs, bot-message filtering, one active operation per PR lineage, quotas, and circuit breaker. |
| Private code or secret leakage | Approved provider only. Redact at every log, database, and model boundary. |
| Unsafe generated fix | Pilot agent creates no fix. Final stage creates one draft PR only after explicit `approved_for_pr`, immutable handoff validation, fresh worktree, and policy validation. |

## Pilot Gates

Pilot starts only when one repository and named reviewers opt in, policy revisions are pinned, data
provider is approved, and circuit-breaker owner is named.

Pilot succeeds only when its sole external write is dedicated `pr-safety` memory and it proves useful findings without higher reviewer effort or author churn. Agent server validates and promotes analyst handoff drafts as
immutable local handoff docs, then queues them for human review. Record reviewer time, material-finding acceptance rate,
false-positive rate, duplicate rate, and stale-result rate.

## Final Automatic PR Stage

After shadow pilot and handoff-quality gates pass, agent server may create one draft remediation PR
for an `approved_for_pr` handoff. Remediation agent receives immutable source snapshot and
agent server-selected read-only local handoff mount. It works only in a new worktree and branch.
Agent server, not agent, owns narrow GitHub App capability to create draft PR.

Automated creation never permits merge, deployment, CI retry, Datadog apply, force-push, or
default-branch write. Send resulting draft PR to same Chat room for notification; GitHub code-owner
approval and merge remain human-owned.

## SWE-Implement (task → draft PR)

`swe-implement` turns an approved task into a **draft** PR. The agent is the general
`swe-implementer` persona (`agent-config/agents/swe-implementer.md`, model `gpt-5.6-terra`); a
handoff's concrete breakage is just one of its input sources. All inputs normalize to a common brief
and run against a **disposable fresh clone** of the target repo at its default branch.

`bin/swe-implement` is the standalone harness (run by hand or by the server):

```bash
swe-implement --handoff <handoff.md>              # repo/base + '## Concrete breakage' section
swe-implement --issue  ROKT/cpi#123               # GitHub issue title+body
swe-implement --prompt "<text>" --repo ROKT/cpi   # free-form; --repo required
```

Boundaries: it never touches the author's PR branch or any existing working checkout (always a fresh
temp clone); for a handoff only the `## Concrete breakage` section is actioned (human-decision
findings are not auto-fixed); it stops with a commit and opens a **draft** PR for human review.
`--no-pr` stops at a committed local branch. It authors as the push+SAML-capable token identity
(`GH_TOKEN`). Jira input is intentionally not wired (no Jira access).

### Agent-server + UI

swe-implement also runs as a queue worker, matching the agent-server pattern:

- **`bin/swe-implement-server`** — a serial worker for the `swe-implement` request kind. It holds a
  per-kind single-instance advisory lock, claims one `swe-implement` row at a time
  (`FOR UPDATE SKIP LOCKED`), maps the payload to harness args, runs the harness, and marks the row
  `done` (draft-PR url in `posted_ref`) or `failed`. Runs in its own container
  (`Dockerfile.swe-implement-server`, compose profile `swe-implement`) with git/gh/mewritecode and a
  push+SAML `GH_TOKEN`; clones go to an ephemeral `swe_implement_work` volume, never the code root.
  Request payload: `{source: handoff|issue|prompt, handoff_path|issue|prompt+repo, no_pr?}`.
- **UI: a section of the status page** (`bin/status-server`). Each row in the human-review queue with
  a handoff gets an **Implement** button that enqueues a `swe-implement` handoff task; a submit form
  handles issue / prompt tasks; a table shows `swe-implement` requests with their draft-PR links.
  Enqueue-only (CSRF-guarded, parameterized SQL) — the status page holds no creds and never runs the
  harness; the server does. Handoff paths are read from the DB, not the filesystem.

## Validation

Run:

```bash
bash tests/test-pr-safety-review-skill.sh
bash tests/test-agent-server-pr-safety.sh
bash tests/test-pr-safety-runtime.sh
bash tests/test-pr-safety-chat-producer.sh
bash tests/test-pr-safety-flow.sh
bash tests/test-swe-implement.sh
bash tests/test-swe-implement-server.sh
```

Test guards contract language. It does not prove future runtime sandboxing. Runtime enforcement is
required in server implementation PR.
