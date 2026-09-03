---
name: buildkite-mcp
description: "Use Buildkite MCP server tools when available. Preferred interface for deterministic build monitoring, builds, pipelines, logs, artifacts, annotations, clusters, and test engine queries. Falls back to `bk` CLI or REST API when MCP tools are not wired in."
---

# Buildkite MCP Server Tools

The Buildkite MCP server exposes native tool calls. Prefer these over `bk` CLI or raw REST API — they return structured data optimised for AI consumption and handle auth, pagination, and log parsing automatically.

Use upstream names in prose (`get_build`, `tail_logs`). Client tool names may be prefixed, such as opencode `mcp-buildkite_get_build` or Claude-style `buildkite_get_build`.

Tool definitions (params, types, descriptions) are already in the tool list. This reference covers **when to use which tool** and **sharp edges**.

## Detection

MCP tools are available when Buildkite functions appear in the tool list. In opencode this usually looks like `mcp-buildkite_*`; in other clients it may be `buildkite_*` or unprefixed. If absent, fall back to `references/cli.md` (preferred) or `references/api.md`.

---

## Workflows

### Investigate a failed build

```
get_build(org, pipeline, build_number, job_state="failed,broken")
→ for each failed job: tail_logs(org, pipeline, build_number, job_id)
→ if needed: search_logs(..., pattern="error|failed|exception", limit=20)
→ check annotations: list_annotations(org, pipeline, build_number)
```

### Monitor a build to terminal state

Use a bounded deterministic `get_build` loop for waiting.

```
loop until terminal or wall-clock cap:
  get_build(org_slug, pipeline_slug, build_number,
            detail_level="detailed",
            include_agent=false)
  inspect build state, job_summary, and jobs[id,name,state]
  sleep by cadence
```

Poll at a steady low-cost cadence, then back off for long quiet builds:

- Normal active builds: 30-45s
- Long or quiet builds beyond about 15 minutes: 60s
- Always use a bounded wall-clock cap from the caller or local policy

Keep each poll cheap:

- Use `detail_level="detailed"`; it includes lightweight job state plus `job_summary` without the cost of `full`.
- Keep `include_agent=false` unless diagnosing queue or agent placement.
- Do not fetch logs while jobs are still running unless there is an apparent stall or the user asked for live diagnosis.
- Do not call `detail_level="full"` in the loop; reserve it for one-off deep inspection.

For apparent stalls, track the last observed build state, `job_summary`, and running job IDs. If nothing changes for about 10 minutes or three consecutive long-cadence polls, fetch a small tail from the currently running or longest-running job and report the latest visible activity. Do not keep tailing every poll; repeat only after another similar quiet interval or a job state change. This gives users progress evidence without turning waiting into log streaming.

Treat `passed` as success. Treat `failed`, `canceled`, `skipped`, and `not_run` as terminal unless the caller's policy names an exception. Treat `scheduled`, `running`, `blocked`, and unknown in-progress states as non-terminal until a policy says otherwise. For allowlisted block-step types, named block steps, or rollback policies, verify the accepted job state with a final `get_build` snapshot before unblocking or reporting success.

On terminal failure: filtered `get_build` (`job_state="failed,broken,timed_out,canceled"`) → `tail_logs` (`tail=100-200`) → targeted `search_logs` (`limit=10-20`, small context) → bounded `read_logs` only if still needed → annotations and Test Engine tools when relevant.

### Find recent pipeline failures

```
buildkite_list_builds(org, pipeline_slug=pipeline, state="failed", per_page=5)
→ buildkite_get_build(..., job_state="failed,broken")
```

### Trigger a build

```
buildkite_create_build(org, pipeline, commit="HEAD", branch="<discovered-default-or-target-branch>", message="...")
→ monitor with get_build polling (detail_level="detailed")
```

### Unblock a blocked build

```
buildkite_get_build(org, pipeline, build_number)  — find blocked job_id
→ buildkite_unblock_job(org, pipeline, build_number, job_id)
```

### Inspect test failures

```
buildkite_get_build_test_engine_runs(org, pipeline, build_number)
→ buildkite_get_failed_executions(org, test_suite, run_id, include_failure_expanded=true)
→ buildkite_get_test(org, test_suite, test_id)  — additional metadata
```

### Pipeline CRUD

```
buildkite_list_pipelines(org)  — find by name or repo
buildkite_get_pipeline(org, pipeline_slug)  — inspect config / existence check
buildkite_create_pipeline(org, name, repository_url, cluster_id, configuration)
buildkite_update_pipeline(org, pipeline_slug, repository_url, configuration)
```

**Spec execution gotcha**: generated pipeline specs from `buildkite-pipelines` may include fields the MCP tools don't accept (e.g. `teams`, `provider_settings`, `skip_queued_branch_builds_filter`, `pipeline_template_uuid`). When executing a spec:

- Use MCP for **existence checks** (`buildkite_get_pipeline`, `buildkite_list_pipelines`) and **basic create/update** with supported fields.
- Use `bk api` (see `references/cli.md`) for the **full spec payload** when it contains unsupported fields.
- If neither MCP nor `bk api` is available, guide the user through **manual setup** — see `references/buildkite-standard-pipeline-creation.md` for the manual fallback steps.

---

## Log Investigation Escalation

Always start cheap and escalate:

- **`buildkite_tail_logs`** — start here; most failures are in the last lines
- **`buildkite_search_logs`** — targeted regex; use `limit=10-20` and patterns like `error|failed|exception`
- **`buildkite_read_logs`** — paginated full read; always pass `limit` to control token cost

During active monitoring, avoid log reads unless diagnosing a stall. Logs are for failure explanation; `get_build` is for waiting.

---

## CLI and Script Fallbacks

MCP `get_build` polling is the deterministic default because it gives structured state with controlled token use.

Use `bk build watch` only when MCP tools are unavailable, the user explicitly wants live terminal streaming, or debugging CLI behavior. If you spawn it in a PTY, prefer exit notifications and pattern-filtered reads; never repeatedly read the full buffer.

Use `scripts/monitor-build.sh` only when MCP is unavailable or a shell-only per-job state table is explicitly requested. Do not duplicate its logic with ad hoc unbounded polling; if using MCP, follow the bounded `get_build` loop above.

---

## Sharp Edges

- **`org_slug`**: defaults to `rokt`. Use `buildkite_user_token_organization` to discover if unsure.
- **`detail_level`**: start with `summary` (cheapest); escalate to `detailed` or `full` only when needed.
- **Build monitoring**: use `get_build(detail_level="detailed")` polling and fetch logs only on failure or suspected stall.
- **Mutations**: always confirm with the user before `create_build`, `create_pipeline`, `update_pipeline`, or `unblock_job`.
- **Retry mutations**: prefer targeted `retry_job` for flaky/single-job retries; use `rebuild_build` only when a full rebuild is intentionally required.
- **Dynamic discovery removed**: do not rely on removed `search_tools` or `list_toolsets` MCP tools.
- **Tool drift**: trust the client tool list over stale examples; docs and clients may use different prefixes.
- **No agent management**: use `references/cli.md` or `references/api.md` for agent list/stop/pause.
- **No local validation**: `bk pipeline validate` has no MCP equivalent; use CLI.
- **`buildkite_create_pipeline` requires `cluster_id`**: cannot create unclustered pipelines via MCP. For unclustered pipeline creation, use `bk` CLI (`bk api --method POST /pipelines`) or REST API directly. MCP is still preferred for existence checks (`buildkite_get_pipeline`, `buildkite_list_pipelines`) and for updating or creating clustered pipelines.

## Discovering More Tools

The full MCP tool catalogue is in the tool list; prefixes vary by client. For Buildkite MCP server docs: https://buildkite.com/docs/apis/mcp-server/tools
