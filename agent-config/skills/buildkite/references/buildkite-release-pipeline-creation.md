---
name: buildkite-release-pipeline-creation
description: >-
  Create or update a template-backed Buildkite release pipeline from a
  buildkite-pipelines handoff. Resolves the stable release template when the
  handoff omits `pipeline_template_uuid`, checks for existing pipelines, and
  executes the API mutation.
---

# Buildkite Release Pipeline Execution

> **Note**: This is the API fallback path. Prefer `references/terraform-pipeline-pr.md`
> for pipeline creation — it generates terraform and opens a PR on ROKT/rokt-buildkite.
> Use this reference only when the user explicitly requests direct API execution or
> terraform is unavailable.

Release pipeline handoff generation is owned by `buildkite-pipelines`. This
reference covers the execution path once that handoff already exists.

## Canonical input

Expect the handoff object from
`buildkite-pipelines/references/buildkite-release-pipeline-creation.md`:

It provides optional `buildkite_org_slug` plus `pipeline_payload` with the
derived release pipeline name, repository, default branch, tags, team access,
provider settings, and optional `pipeline_template_uuid`.

`pipeline_payload.pipeline_template_uuid` is optional in the handoff. When it is
missing, this skill resolves it during execution by following
`references/release-template-selection.md`.

## Execution contract

- verify whether the target release pipeline already exists before mutation
- if `pipeline_payload.pipeline_template_uuid` is missing, resolve it via
  `references/release-template-selection.md`
- show or summarize the final payload if the user wants review before mutation
- ask before calling any Buildkite API
- create or update the release pipeline with the completed payload

## Stable template resolution

- prefer a caller-provided `pipeline_template_uuid` when present
- otherwise use `buildkite_org_slug` from the handoff if present; if absent, ask
  for the Buildkite org slug
- resolve the template UUID with `references/release-template-selection.md`
- include the matched stable template name in the response summary so the user
  can verify which slot was selected

## Failure fallback

- if existence checks show the pipeline already belongs to a different release
  pipeline than the caller expected, stop before mutation and report the
  existing pipeline plus the inspection command or result
- if stable template resolution returns zero or multiple matches, stop and
  return the matching names and UUIDs instead of guessing
- if create or update fails, return the attempted payload name, whether a
  template UUID was caller-provided or auto-resolved, the exact error, and the
  next `buildkite` retry path

## CLI and API guidance

Prefer MCP tools for existence checks. Use `bk api` or raw REST for release
pipeline mutations because release specs often include fields MCP does not
accept, such as `teams`, `provider_settings`,
`skip_queued_branch_builds_filter`, and `pipeline_template_uuid`.

**Existence checks**:

- `buildkite_get_pipeline(org, pipeline_slug)` — returns pipeline details or
  error if not found
- `buildkite_list_pipelines(org, name=pipeline_name)` — search by name
- fallback: `bk api /pipelines/<pipeline_slug>`

**Create / Update**:

- `bk api --method POST /pipelines --data '<json>'` for create
- `bk api --method PATCH /pipelines/<pipeline_slug> --data '<json>'` for update
- raw REST fallback:
  `POST /v2/organizations/{org.slug}/pipelines` or
  `PATCH /v2/organizations/{org.slug}/pipelines/{slug}`

**Manual fallback** — when neither MCP nor `bk api` is available:

- navigate to **Organization → Pipelines → New Pipeline**
- set the pipeline name, repository, and default branch from `pipeline_payload`
- assign the stable release template UUID that was resolved earlier
- add tags, team access, and `trigger_mode: none`

## Distinction from CI pipeline execution

- release pipelines are template-backed and do not upload CI YAML
- CI YAML-backed execution stays in
  `references/buildkite-standard-pipeline-creation.md`
