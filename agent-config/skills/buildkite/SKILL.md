---
name: buildkite
description: "Buildkite CI/CD via terminal, REST API, or terraform PR. Investigate failures, pull job logs, trigger/retry, watch live, download artifacts, manage agents, validate YAML, execute via terraform PR (preferred) or API. Triggers: buildkite, bk, build failure, job log, retry, rebuild, rollback."
group: integrations
---

# Buildkite

## Purpose

Operate Buildkite after the desired YAML or handoff object already exists.
This skill owns validation, existence checks, stable-template resolution for
release handoffs, and pipeline creation via terraform PR on ROKT/rokt-buildkite.
Direct API create/update is retained as a fallback.

## When to Use

Use this skill for build investigation, Buildkite log or artifact queries,
pipeline validation, CI handoff execution, release handoff execution, and any
request that needs `bk`, Buildkite MCP tools, or the Buildkite REST API.

## Not for

Do not use this skill for generating CI YAML or deriving release pipeline
payloads from service metadata; route those to `buildkite-pipelines` first.

## Transport Selection

For read-only operations: prefer Buildkite MCP tools when available, fall back to `bk` CLI, then REST API. See `references/mcp.md` for details.
For pipeline creation: prefer `references/terraform-pipeline-pr.md`.
Auth: MCP handles auth automatically; `bk configure add` for CLI; `BUILDKITE_API_TOKEN` as bearer for raw API.
**Exception — unclustered pipelines**: see `references/mcp.md` for the MCP limitation and fallback.

## Default Path

- Prefer MCP for read-only checks, deterministic build monitoring, and log inspection.
- For CI or release pipeline creation/update, prefer `references/terraform-pipeline-pr.md` (generates terraform + opens PR on ROKT/rokt-buildkite).
- Fallback to `references/buildkite-standard-pipeline-creation.md` (CI) or `references/buildkite-release-pipeline-creation.md` (release) only when the user explicitly requests direct API execution or terraform is unavailable.
- For stable-template resolution when needed by terraform or API paths, use `references/release-template-selection.md`.

## Execution

- **Classify** — decide whether the request is read-only investigation, CI handoff execution, or release handoff execution.
- **Inspect** — run existence checks, validation, or log queries with MCP first, then `bk`, then raw REST.
- **For pipeline creation/update** — prefer `references/terraform-pipeline-pr.md`: translate the spec into terraform, open a PR on ROKT/rokt-buildkite, watch CI checks, and guide through merge + apply.
- **Complete the payload** (API fallback only) — sync CI YAML when needed, or resolve the stable release template when a release handoff omits `pipeline_template_uuid`.
- **Confirm and mutate** — for terraform: watch PR to merge and confirm pipeline exists post-apply. For API fallback: show the intended action, ask before any mutation, then execute with the right transport.
- **Verify** — confirm the pipeline exists in Buildkite. When called by an orchestrator, enter the trigger→monitor→fix loop until the first build passes.
- **Report** — return the identifiers, final state, PR URL or API path used, and next steps.

## Common Operations

| Task                                                              | Reference                                                                                                                                                                                                                                                                                                |
| ----------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Investigate a failed build, get job logs                          | `references/mcp.md` or `references/cli.md` or `references/api.md`                                                                                                                                                                                                                                        |
| Trigger, retry, rebuild, cancel a build                           | `references/mcp.md` or `references/cli.md` or `references/api.md`                                                                                                                                                                                                                                        |
| Monitor builds to terminal state, filter builds by state/branch/duration | `references/mcp.md` or `references/cli.md`                                                                                                                                                                                                                         |
| **Monitor a long build with a shell-only per-job state table**     | `scripts/monitor-build.sh` fallback when MCP is unavailable or a terminal table is explicitly requested                                                                                                                                                                                                    |
| Download or list artifacts                                        | `references/mcp.md` or `references/cli.md` or `references/api.md`                                                                                                                                                                                                                                        |
| Manage agents (list, stop, pause)                                 | `references/cli.md` or `references/api.md` (no MCP agent-management tools)                                                                                                                                                                                                                               |
| Validate pipeline YAML locally                                    | `references/cli.md` (`bk pipeline validate`) — local-only, no MCP equivalent                                                                                                                                                                                                                             |
| Execute create/update for a generated CI or release pipeline spec | first use `buildkite-pipelines`; then use `references/terraform-pipeline-pr.md` (preferred) or `references/buildkite-standard-pipeline-creation.md` / `references/buildkite-release-pipeline-creation.md` (API fallback); for unclustered pipelines use the companion fallback guidance |
| Query logs (search, tail, read)                                   | `references/mcp.md`, `references/cli.md`, or `references/api.md`                                                                                                                                                                                                              |
| Inspect test failures                                             | `references/mcp.md`                                                                                                                                                                                                          |
| Unblock a blocked build                                           | `references/mcp.md` or `references/cli.md`                                                                                                                                                                                                                                     |
| Find rollback candidates and trigger fast rollback                | `references/release-rollback-workflow.md`                                                                                                                                                                                                                                        |
| Call any REST endpoint without a CLI/MCP tool                     | `bk api` (see `references/cli.md` Escape Hatch)                                                                                                                                                                                                                                                          |

## Companion Files

- **`references/terraform-pipeline-pr.md`** -- **Preferred for pipeline creation.** Translates a generated CI or release pipeline spec into terraform module invocations and opens a PR on ROKT/rokt-buildkite. Covers field mapping from spec to module variables, file naming conventions, branch/PR workflow, post-merge lifecycle, and the trigger→monitor→fix verification loop.

- **`references/mcp.md`** -- **Preferred for read-only operations.** Use when Buildkite MCP tools are in the tool list. Structured native tool calls for builds, pipelines, logs, artifacts, annotations, clusters, and test engine. Handles auth, pagination, log parsing, and efficient deterministic build monitoring.

- **`references/cli.md`** -- Use when MCP tools are not available but `bk` is on disk. Commands, flags, and workflows for builds, jobs, pipelines, agents, artifacts, clusters, and secrets. Includes `bk api` escape hatch for REST operations without a dedicated subcommand.

- **`references/api.md`** -- Use when neither MCP tools nor `bk` are available. REST API endpoints, curl patterns, query parameters, and debugging workflows via `https://api.buildkite.com/v2`.

- **Pipeline-spec generation** -- Owned by `buildkite-pipelines`. Generated CI and release specs should execute through `references/terraform-pipeline-pr.md` by default. Use `references/buildkite-standard-pipeline-creation.md`, `references/buildkite-release-pipeline-creation.md`, and `references/release-template-selection.md` for direct-API fallback, existence checks, or manual copy guidance.

- **`references/buildkite-standard-pipeline-creation.md`** -- Use when handed a generated CI pipeline spec. Covers existence checks, syncing or verifying the referenced YAML file, and create/update for YAML-backed pipelines.

- **`references/buildkite-release-pipeline-creation.md`** -- Use when handed a generated release-pipeline handoff from `buildkite-pipelines`. Covers stable-template lookup when `pipeline_template_uuid` is absent, existence checks, and create/update for template-backed release pipelines.

- **`references/release-template-selection.md`** -- Use when a release-pipeline handoff omits `pipeline_template_uuid`. Lists org templates, filters names containing `Release Pipeline [STABLE]`, and requires exactly one match.

- **`scripts/monitor-build.sh`** -- Fallback when MCP tools are unavailable or a shell-only per-job status table is explicitly requested.

- **`references/release-rollback-workflow.md`** -- Find rollback candidates on a release pipeline, classify branches as deployment locations, present options to the user, and unblock the fast rollback block step (or link for manual unblock).

## Constraints

- Always confirm with the user before mutations (cancel, retry, rebuild, create pipeline).
- When monitoring terraform-apply builds on ROKT/rokt-buildkite: if the plan shows destroys and the apply step is blocked, do NOT unblock. Rebase the source branch on the latest default branch to confirm the destroy is intentional. Reach out to [RGI Public](https://chat.google.com/app/chat/AAAA1Nqg14I) if unclear.
- When waiting, use the efficient MCP monitoring pattern in `references/mcp.md`: poll structured build state and fetch logs only on failure or suspected stall.
- `bk api` is the CLI escape hatch for REST operations without a dedicated subcommand.
- **`bk api` paths**: Never include `organizations/<org>/` — it's auto-prepended.
  Wrong: `bk api "organizations/rokt/pipelines/..."` → Right: `bk api "pipelines/..."`

## Success Criteria

- The correct execution path is chosen: terraform PR (preferred) vs direct API (fallback).
- For terraform PRs: the PR is opened, CI checks are monitored, and the user is guided through review/merge/apply.
- Any missing stable release template UUID is resolved from exactly one
  `Release Pipeline [STABLE]` template.
- The user sees the intended mutation and is asked before any Buildkite API mutation (API fallback path).
- Post-apply: the pipeline is confirmed to exist in Buildkite.

## Test

Prompt: `Create or update the payments-api release pipeline using the stable template.`

Pass when the skill routes to `references/terraform-pipeline-pr.md`, generates
a `deploykit-pipeline-module` invocation whose `release_pipeline_template_id`
points at the currently active stable template local, opens a PR on
ROKT/rokt-buildkite, and monitors CI checks.

If the skill routes directly to `bk api` without offering the terraform path
first, rewrite and retest.

## Output Format

- For read-only investigation, return a concise status/result summary with the
  key build, job, or artifact identifiers.
- For validation, return pass/fail plus the concrete command or error that
  proves the outcome.
- For generated pipeline execution, return the existence-check result, the planned
  mutation, the YAML sync plan when relevant, and any user confirmation required
  before API execution.

## Self-Improvement

After each session, append discoveries here:

```
[YYYY-MM-DD] operation - discovery - context
```

[2026-03-22] standard pipeline execution - generated CI pipeline specs from `buildkite-pipelines` should stay YAML-backed via minimal `configuration` bootstrap steps that call `buildkite-agent pipeline upload <path>`; reserve template-backed execution for release pipelines.
[2026-03-12] trigger step env interpolation - `$${VAR}` in a static trigger step `env:` block becomes the literal `${VAR}` in the child build (Buildkite server-side never expands it from the parent env). Use a command step that reads from `buildkite-agent meta-data get` and dynamically uploads the trigger via `pipeline upload` heredoc with shell-expanded `$VAR` instead.
[2026-03-12] monitor-build.sh - added to scripts/ as the preferred way to watch a build to terminal state with per-job icons; use instead of inline polling loops.
[2026-04-27] MCP build monitoring - prefer bounded structured build-state polling with log tools only on failure or stall diagnosis.
