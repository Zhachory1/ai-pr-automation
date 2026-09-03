---
name: buildkite-standard-pipeline-creation
description: >-
  Execute a generated standard CI pipeline specification from the
  buildkite-pipelines skill. Covers existence checks, YAML file sync or
  verification, and API-backed create or update for YAML-backed Buildkite pipelines.
---

# Buildkite Standard Pipeline Execution

> **Note**: This is the API fallback path. Prefer `references/terraform-pipeline-pr.md`
> for pipeline creation — it generates terraform and opens a PR on ROKT/rokt-buildkite.
> Use this reference only when the user explicitly requests direct API execution or
> terraform is unavailable.

CI pipeline specification generation is owned by `buildkite-pipelines`. This
reference covers the execution path once that handoff object already exists.

## Canonical input

Expect the handoff object from
`buildkite-pipelines/references/buildkite-ci-pipeline-creation.md`:

```json
{
  "pipeline_slug": "service-main",
  "pipeline_payload": {
    "name": "service-main",
    "configuration": "steps:\n  - command: \"buildkite-agent pipeline upload .buildkite/pipeline.yaml\""
  },
  "yaml_sync": {
    "path": ".buildkite/pipeline.yaml",
    "source": "generated",
    "contents": "steps:\n  - command: make test"
  }
}
```

## Execution contract

- verify whether `pipeline_slug` already exists before mutation
- show or summarize `pipeline_payload` and `yaml_sync` if the user wants review
- ask before calling any Buildkite API
- for `yaml_sync.source: "generated"`, materialize or update `yaml_sync.path` with `yaml_sync.contents` before the API mutation
- for `yaml_sync.source: "provided"`, require `yaml_sync.path` to be repository-relative and already present in the repo checkout; if not, require `yaml_sync.contents` and materialize the file before mutation
- create or update the pipeline with the bootstrap `configuration` that uploads `yaml_sync.path`

## Failure fallback

- if existence checks show the slug already belongs to a different pipeline than the caller expected, stop before mutation and report the existing slug plus the inspection command used
- if create or update fails, return the attempted `pipeline_slug`, whether `yaml_sync.path` was materialized, the exact API or CLI error, and the next action to retry through `buildkite`
- when the user needs to fix naming collisions manually, return enough detail to decide whether to rename the service or update the existing pipeline instead of guessing

## CLI and API guidance

Prefer MCP tools for existence checks and basic operations; use `bk api` for
the full spec payload when it contains fields MCP doesn't accept.

**Existence checks** (prefer MCP when available):

- `buildkite_get_pipeline(org, pipeline_slug)` — returns pipeline details or error if not found
- `buildkite_list_pipelines(org, name=pipeline_name)` — search by name
- Fallback: `bk api /pipelines/<pipeline_slug>`

**Create / Update** (full payload with `teams`, `provider_settings`, etc.):

- `bk api --method POST /pipelines --data '<json>'` for create
- `bk api --method PATCH /pipelines/<pipeline_slug> --data '<json>'` for update
- MCP `buildkite_create_pipeline` / `buildkite_update_pipeline` work for the
  subset of fields they support but cannot pass `teams`,
  `provider_settings`, `skip_queued_branch_builds_filter`, or
  `pipeline_template_uuid`

**Manual fallback** — when neither MCP nor `bk api` is available, guide the
user through the Buildkite web UI:

- Navigate to **Organization → Pipelines → New Pipeline**
- Set **Name** to `pipeline_slug` value, **Repository** to `pipeline_payload.repository`
- Set **Default branch** to `pipeline_payload.default_branch`
- Paste the bootstrap `configuration` YAML into the **Steps** editor
- Under **Settings → Pipeline**, add each tag from `pipeline_payload.tags`
- Under **Settings → Teams**, grant the required team access (`manage_build_and_read`)
- Under **GitHub / Provider Settings**, set trigger mode per `provider_settings`
- Commit `yaml_sync.path` to the repo with `yaml_sync.contents` before the first build

Notes:

- `configuration` for standard CI pipelines should stay a minimal bootstrap that
  uploads the referenced YAML path, not the full generated CI YAML embedded as a
  JSON string
- `bk pipeline create <name>` alone is insufficient when the spec includes
  `configuration`, `provider_settings`, or queue-skipping fields

## Distinction from release pipelines

- CI pipelines are YAML-backed and use `configuration` to upload the generated or provided YAML path
- release pipelines are template-backed and stay owned by
  `buildkite/references/buildkite-release-pipeline-creation.md`
- do not try to apply CI YAML upload behavior to release pipeline specs
