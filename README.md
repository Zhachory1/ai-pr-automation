# AI PR Automation

Fair, bounded automation for GitHub pull-request review and maintenance.

The project handles scheduling, queue fairness, locking, time budgets, GitHub discovery, and prompt generation. You provide a small executable wrapper for your preferred coding agent.

## Modes

| Mode | GitHub query | Intended behavior |
| --- | --- | --- |
| `review` | Open PRs assigned to `@me` | Review only; submit `APPROVE`, `REQUEST_CHANGES`, or `COMMENT` from verdict |
| `maintain` | Open PRs authored by `@me` | Handle review feedback and CI with one bounded fix pass |

```bash
bin/pr-automation review
bin/pr-automation maintain
```

## Requirements

- Bash
- [GitHub CLI](https://cli.github.com/) authenticated with `gh auth login`
- `jq`
- GNU `timeout` (`timeout` on Linux, `gtimeout` from Homebrew coreutils on macOS)
- An executable agent runner

## Quick start

Clone and verify prerequisites:

```bash
gh auth status
command -v timeout || command -v gtimeout
git clone https://github.com/Zhachory1/ai-pr-automation.git
cd ai-pr-automation
```

Create a safe print-only runner for setup validation:

```bash
mkdir -p "$HOME/.local/bin"
cat > "$HOME/.local/bin/pr-automation-runner" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cat "$1"
EOF
chmod +x "$HOME/.local/bin/pr-automation-runner"
```

Run a scoped dry run:

```bash
PR_AUTOMATION_RUNNER="$HOME/.local/bin/pr-automation-runner" \
PR_AUTOMATION_REPOSITORIES='owner/repo' \
PR_AUTOMATION_DRY_RUN=true \
bin/pr-automation review
```

Then run one targeted print-only attempt and inspect logs:

```bash
PR_AUTOMATION_RUNNER="$HOME/.local/bin/pr-automation-runner" \
PR_AUTOMATION_REPOSITORIES='owner/repo' \
PR_AUTOMATION_ONLY='owner/repo#123' \
bin/pr-automation review

find "$HOME/.local/state/ai-pr-automation/logs" -name latest.log -print
```

Replace the print-only runner with your coding agent wrapper only after reviewing repository scope, provider data handling, GitHub permissions, and the generated prompts.

## Agent runner contract

Set `PR_AUTOMATION_REVIEW_RUNNER` and `PR_AUTOMATION_MAINTENANCE_RUNNER` to separate executable wrappers with mode-specific credentials. `PR_AUTOMATION_RUNNER` is an explicit shared fallback. The scheduler calls the selected runner with one argument: the generated prompt-file path.

```text
/path/to/your-runner /path/to/prompt.md
```

The runner also receives:

```text
PR_AUTOMATION_MODE
PR_REPO
PR_NUMBER
PR_URL
PR_TITLE
PR_UPDATED_AT
PR_HEAD_SHA
PR_WORK_ROOT
```

The scheduler creates a fresh `PR_WORK_ROOT` for every attempt. The runner owns repository setup inside it: clone/fetch, checkout `PR_HEAD_SHA`, validate cleanliness, and run all tools from that isolated directory. The scheduler does not clone repositories. Successful workspaces are removed by default; failed workspaces age out with retention.

Runner stdout/stderr flows into the scheduler log. Exit `0` for success and nonzero for failure. On timeout the runner process group receives `TERM`, then `KILL` after 10 seconds. The runner should `exec` its agent so signals propagate cleanly.

Minimal wrapper shape:

```bash
#!/usr/bin/env bash
set -euo pipefail
prompt_file="$1"
exec your-agent-cli --non-interactive "$(<"$prompt_file")"
```

Replace the final command with your agent's supported non-interactive interface. Keep credentials in that agent's normal credential store; do not add them to this repository.

## Queue ordering and stale cutoffs

Each run discovers the complete matching PR list once, then processes that snapshot sequentially.

Review mode defaults to hybrid activity plus aging fairness:

```bash
PR_AUTOMATION_ORDER=hybrid
```

The score combines latest activity with twice the time since last attempt, so fresh review requests lead initially while overdue work eventually moves ahead and cannot starve. Set `updated` for strict newest-first or `least-attempted` for pure round-robin backlog processing.

Maintenance mode defaults to least-recently-attempted. Previously attempted PRs use `last_attempt_at`; never-attempted PRs use creation time. Success, failure, and timeout all advance the attempt timestamp. If you add wrapper-side branch refresh in your own runner, advance the timestamp there too and skip the agent for that PR until the next run, so a refreshed branch does not consume its slot twice.

Stale filtering is opt-in in the public base:

```bash
PR_AUTOMATION_MAX_UPDATED_AGE_DAYS=0
PR_AUTOMATION_MAX_PR_AGE_DAYS=0
```

Set updated age when inactive PRs should be parked. Created-at filtering can additionally exclude old PRs regardless of fresh activity, so enable it deliberately. `PR_AUTOMATION_ONLY` bypasses age gates.

State lives at:

```text
~/.local/state/ai-pr-automation/review/attempts.tsv
~/.local/state/ai-pr-automation/maintain/attempts.tsv
```

## Bounds and safety

Defaults:

```text
12 minutes per PR
55-minute hard watchdog and queue budget with a 20-second teardown reserve
60 seconds per GitHub CLI call
one process per mode via lock directory
```

Override:

```bash
PR_AUTOMATION_PER_PR_TIMEOUT=8m \
PR_AUTOMATION_RUN_BUDGET_SECONDS=3000 \
bin/pr-automation maintain
```

Review mode prompts enforce:

- no source edits, pushes, or merges
- `approve` / `approve-with-nits` → `APPROVE`
- `request-changes` / `block` → `REQUEST_CHANGES`
- `needs-info` or self-review restrictions → `COMMENT`
- one visible result per head SHA using `<!-- ai-pr-automation head=<full-head-sha> -->`
- exact body-file preview before posting

The scheduler can pre-skip already-reviewed heads from the configured GitHub account before launching the model:

```bash
PR_AUTOMATION_SKIP_REVIEWED_HEADS=true
```

Set it to `false` to force same-head re-review.

Maintenance mode prompts enforce:

- no merge, deploy, release, force-push, history rewrite, or default-branch push by the agent
- one initial feedback/CI snapshot
- at most one low-risk fix pass
- changed files must finish committed+pushed, reverted to a clean diff, or explicitly blocked by permission/head/conflict
- fixed review comments receive commit/validation replies and are resolved when possible
- focused validation
- at most one unrelated CI-flake retry

The public base intentionally does not rebase or force-push branches. Branch refresh is a destructive, repository-specific policy and belongs in an explicitly authorized maintenance runner, not the generic scheduler.

If you add branch refresh to your own maintenance runner, be aware that it couples the two modes through the PR head SHA. Review mode dedupes on `<!-- ai-pr-automation head=<full-head-sha> -->`, so anything that rewrites a branch head between review runs invalidates that marker and the next run posts again. A wrapper that rebases whenever a branch is merely behind its base will therefore generate one duplicate review per cycle on any repository whose base branch moves faster than the review cadence.

Two mitigations, both recommended:

- Refresh only when the forge itself reports the branch as blocked on staleness or conflict, not when a local commit count says the branch is behind. Most "behind" branches are perfectly mergeable, and rebasing them is pure churn that also re-triggers other PR bots.
- Prefer a merge over a rebase where your repository policy allows it. A merge needs no force-push, so existing review comments keep their line anchors, and it reconciles only the two branch endpoints rather than replaying each commit against intermediate trees.

If you delegate conflict resolution to the agent, do not implement "prefer the base branch" as `-X theirs` or `checkout --theirs`. On a file with mixed conflict hunks those silently discard the other side's changes. Resolve hunk by hunk, require lint and focused tests to pass before committing the merge, and treat a parked conflict as an acceptable outcome.

These are prompt guardrails, not a security sandbox. Pull-request metadata, diffs, comments, files, and tool output are explicitly labeled untrusted, but the runner must still provide real containment.

Required safety posture:

- Configure an organization or repository allowlist. The scheduler refuses to run without one unless `PR_AUTOMATION_ALLOW_ALL_REPOS=true` is explicitly set.
- Use a read/review-scoped token for review mode and a narrowly scoped contents token for maintenance mode.
- Restrict filesystem and network access where the agent supports sandboxing.
- Confirm whether private repository content may be sent to the selected AI provider. Do not run private PRs through a provider that is not approved for that data.
- Never give the review-mode runner deployment, merge, or broad write credentials.

Suggested fine-grained GitHub token permissions:

| Permission | Review mode | Maintenance mode |
| --- | --- | --- |
| Repository access | Selected repositories only | Selected repositories only |
| Metadata | Read | Read |
| Contents | Read | Read and write |
| Pull requests | Read and write (approval/comment) | Read and write (reply/update branch) |
| Commit statuses | Read | Read |
| Actions | Read | Read; write only if CI retry is required |
| Deployments, administration, workflows | None | None |

Use separate tokens or GitHub App installations for the two runners. Put tokens in the runner's credential mechanism or launch environment, not in prompts, logs, or this repository.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `PR_AUTOMATION_REVIEW_RUNNER` | `pr-automation-review-runner` | Review-mode wrapper/profile |
| `PR_AUTOMATION_MAINTENANCE_RUNNER` | `pr-automation-maintenance-runner` | Maintenance-mode wrapper/profile |
| `PR_AUTOMATION_RUNNER` | empty | Explicit shared fallback for both modes |
| `PR_AUTOMATION_REPOSITORIES` | empty | Comma-separated exact `owner/repo` allowlist |
| `PR_AUTOMATION_ORGS` | empty | Comma-separated exact organization allowlist |
| `PR_AUTOMATION_ALLOW_ALL_REPOS` | `false` | Explicitly disable repository scoping |
| `PR_AUTOMATION_GITHUB_ACCOUNT` | empty | Pin `gh` token selection to a named authenticated account |
| `PR_AUTOMATION_ORDER` | review: `hybrid`; maintain: `least-attempted` | `hybrid`, `updated`, or `least-attempted` ordering |
| `PR_AUTOMATION_MAX_UPDATED_AGE_DAYS` | `0` | Maximum days since PR activity; disabled by default |
| `PR_AUTOMATION_MAX_PR_AGE_DAYS` | `0` | Maximum PR age in days; disabled by default |
| `PR_AUTOMATION_SKIP_REVIEWED_HEADS` | `true` | Pre-skip review heads carrying the exact scheduler marker |
| `PR_AUTOMATION_PER_PR_TIMEOUT` | `12m` | Per-PR GNU timeout |
| `PR_AUTOMATION_RUN_BUDGET_SECONDS` | `3300` | Hard watchdog and queue budget |
| `PR_AUTOMATION_GH_TIMEOUT_SECONDS` | `60` | GitHub CLI call timeout |
| `PR_AUTOMATION_ONLY` | empty | Target one `owner/repo#number` directly |
| `PR_AUTOMATION_DRY_RUN` | `false` | List processing order without invoking runner |
| `PR_AUTOMATION_STATE_ROOT` | `~/.local/state/ai-pr-automation` | Locks, attempts, prompts |
| `PR_AUTOMATION_LOG_ROOT` | state root `/logs` | Run logs |
| `PR_AUTOMATION_WORK_ROOT` | `~/.local/share/ai-pr-automation/work` | Per-attempt runner work areas |
| `PR_AUTOMATION_TIMEOUT_BIN` | auto-detected | Explicit GNU timeout path |
| `PR_AUTOMATION_INPUT_FILE` | empty | TSV fixture/input override for tests; scope checks still apply |
| `PR_AUTOMATION_RETENTION_DAYS` | `14` | Failed workspace and timestamped-log retention |
| `PR_AUTOMATION_KEEP_SUCCESS_WORKSPACES` | `false` | Preserve successful workspaces when true |
| `PR_AUTOMATION_MCP_AUTH_DIR` | empty | Opt-in remote MCP OAuth-cache directory |

Targeted run:

```bash
PR_AUTOMATION_REPOSITORIES='owner/repo' \
PR_AUTOMATION_ONLY='owner/repo#123' \
bin/pr-automation maintain
```

Dry run:

```bash
PR_AUTOMATION_DRY_RUN=true bin/pr-automation review
```

## MCP OAuth reuse

If your agent uses `mcp-remote`, OAuth tokens normally live under version-specific directories in `~/.mcp-auth`.

Reuse is opt-in:

```bash
PR_AUTOMATION_MCP_AUTH_DIR="$HOME/.mcp-auth" bin/pr-automation review
```

The scheduler forwards that value as `MCP_REMOTE_CONFIG_DIR`. Pin your `mcp-remote` dependency/version. Unpinned upgrades can select a new version-scoped token directory and trigger another browser login.

Tool-specific CLI tokens can be separate from MCP OAuth. For example, a CI CLI may fail authentication while the corresponding MCP integration still works. Prefer the authenticated integration intended for unattended use.

## launchd setup

Templates are in [`launchd/`](launchd/). Replace:

- `__INSTALL_DIR__` with this repository's absolute path
- `__HOME__` with your home directory
- `__RUNNER__` with your agent runner's absolute path
- `__REPOSITORIES__` with a comma-separated exact repository allowlist
- `__LABEL_PREFIX__` with a reverse-DNS prefix you own, such as `com.yourname`
- `__GITHUB_ACCOUNT__` with the exact account shown by `gh auth status`

Copy them to `~/Library/LaunchAgents/`, then load:

```bash
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.example.ai-pr-reviews.plist
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.example.ai-pr-maintenance.plist
```

Inspect:

```bash
launchctl print "gui/$(id -u)/com.yourname.ai-pr-reviews"
launchctl print "gui/$(id -u)/com.yourname.ai-pr-maintenance"
```

Templates use fixed wall-clock hourly starts: maintenance at minute 5 and review at minute 35. This avoids `StartInterval` behavior where a long run can delay the next start by an additional hour. They set `RunAtLoad=false` to avoid surprise work during reloads and launchd `Umask=63` (`077`) so launchd-created logs remain user-private. Per-mode locks skip overlaps safely.

### systemd user setup

Templates are in [`systemd/`](systemd/). Replace `__INSTALL_DIR__`, `__HOME__`, `__RUNNER__`, and `__REPOSITORIES__`, then install:

```bash
mkdir -p "$HOME/.config/systemd/user"
cp systemd/ai-pr-reviews.service.template "$HOME/.config/systemd/user/ai-pr-reviews.service"
cp systemd/ai-pr-maintenance.service.template "$HOME/.config/systemd/user/ai-pr-maintenance.service"
cp systemd/ai-pr-reviews.timer systemd/ai-pr-maintenance.timer "$HOME/.config/systemd/user/"
systemctl --user daemon-reload
systemctl --user enable --now ai-pr-reviews.timer ai-pr-maintenance.timer
```

Inspect:

```bash
systemctl --user status ai-pr-reviews.timer ai-pr-maintenance.timer
journalctl --user -u ai-pr-reviews.service -u ai-pr-maintenance.service
```

The repository files are templates; copy them to names without `.template` after replacing placeholders. Timers use fixed hourly wall-clock starts matching launchd: maintenance at `:05`, review at `:35`.

## Testing

```bash
bash -n bin/pr-automation
bash tests/test-scheduler.sh
```

The test uses fixture PR data and fake runner/timeout executables. It does not contact GitHub or an AI provider.

## Exit status and operational notes

Exit status:

- `0`: successful run, no matching work, dry run, or another valid run already holds the lock
- `1`: one or more runner attempts failed or timed out
- `2`: invalid configuration or missing dependency
- `3`: GitHub discovery failed or hit the 1,000-result search cap
- `124`/`137`: hard watchdog or process-group timeout

Additional notes:

- GitHub search is capped at 1,000 results. Reaching the cap fails loudly; narrow repository scope.
- A targeted run uses `gh pr view` directly and is not limited by search ordering.
- Machines with multiple authenticated GitHub accounts should set `PR_AUTOMATION_GITHUB_ACCOUNT`. The scheduler resolves `GH_TOKEN` through `gh auth token -u`, preventing a later interactive `gh auth switch` from silently changing which account `@me` means.
- Do not edit a Bash script in place while it is running; Bash can read later lines after startup. Validate a temporary file and atomically rename it over the installed path.
- A run takes one PR-list snapshot. PRs created after that snapshot become eligible next run.
- Per-runner GNU timeout intentionally runs without `--foreground`, giving timeout a process group it can terminate. `TERM` is followed by `KILL` after 10 seconds so descendants cannot leak output or writes into the next PR slot. The whole-run watchdog traps termination and explicitly kills/reaps any active runner process group before releasing the lock.
- Locks include PID, mode, start time, and an ownership token to reject PID-reuse false positives, close startup races, and prevent one process from removing another's lock. A matching live process is never reclaimed solely because it is old.
- `health.env` records `running`, `success`, or `failed`, timestamps, attempts, and failures. Alert when it remains `running` beyond the configured budget or has repeated `failed` results.
- Timestamped logs and failed workspaces default to 14-day retention. Successful workspaces are removed unless `PR_AUTOMATION_KEEP_SUCCESS_WORKSPACES=true`.
- Logs, prompts, and failed workspaces can contain private code or review text. Directories are created under `umask 077`; keep them on encrypted local storage and choose retention appropriate for your environment.

## License

MIT
