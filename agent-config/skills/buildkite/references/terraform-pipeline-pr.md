---
name: buildkite-terraform-pipeline-pr
description: >-
  Preferred execution path for CI and release pipeline creation. Translates a
  generated pipeline spec from buildkite-pipelines into terraform module
  invocations and opens a PR on ROKT/rokt-buildkite. Covers field mapping,
  file naming, branch workflow, and module selection.
---

# Terraform Pipeline PR

Preferred path for creating or updating Buildkite pipelines. Instead of calling
the Buildkite API directly, generate terraform that uses the existing pipeline
modules in `ROKT/rokt-buildkite` and open a PR.

## When to use

- Any CI pipeline creation handoff from `buildkite-pipelines`
- Any release pipeline creation handoff from `buildkite-pipelines`
- Pipeline updates that change terraform-managed settings (name, repo, branch, tags, team)

## When NOT to use (fall back to direct API)

- User explicitly requests direct API execution
- The pipeline already exists only in Buildkite, and the user needs a temporary
  API-only mutation before a separate terraform-adoption change can land
- Emergency pipeline creation where PR review latency is unacceptable
- Read-only operations (build investigation, log queries, artifact downloads)

New pipeline creation still defaults to terraform in `ROKT/rokt-buildkite`.

## Prerequisites

- `gh` CLI authenticated with access to `ROKT/rokt-buildkite`
- The generated spec from `buildkite-pipelines` (CI or release handoff object)

## Repository layout

Target repo: `ROKT/rokt-buildkite`
Target directory: `infra/terraform-buildkite/`

| Pipeline type | Module | File placement |
|---|---|---|
| CI pipeline | `./ci-pipeline-module` | `infra/terraform-buildkite/<service>-pipeline.tf` (new file) |
| Release pipeline | `./deploykit-pipeline-module` | `infra/terraform-buildkite/dk-pipelines.tf` (append to existing) |

## Spec → Terraform mapping (CI)

Input: CI pipeline handoff from `buildkite-pipelines/references/buildkite-ci-pipeline-creation.md`

| Spec field | Terraform variable | Notes |
|---|---|---|
| `pipeline_payload.name` | `pipeline_name` | |
| repo name from `pipeline_payload.repository` | `github_repo` | Strip `git@github.com:ROKT/` prefix and `.git` suffix |
| `pipeline_payload.default_branch` | `default_branch` | |
| `tags[owner]` | `pipeline_owner` | |
| `tags[owner]` | `team_slug` | Same value unless caller specifies a different team |
| `tags[serviceName]` | `service_name` | Only set when different from `pipeline_name` |
| `tags[serviceGroup]` | `service_group` | Omit variable when null/absent |
| `yaml_sync.path` | `pipeline_file` | Only set when not `.buildkite/pipeline.yaml` |
| `provider_settings.trigger_mode` | `trigger_mode` | Only set when not `"code"` (the default) |
| `pipeline_payload.description` | `description` | Omit variable when null/absent |

### CI terraform output template

```hcl
# <service-name> CI pipeline
# <one-line description of what the pipeline does>
module "<module_name>" {
  source = "./ci-pipeline-module"

  pipeline_name  = "<pipeline_name>"
  github_repo    = "<github_repo>"
  default_branch = "<default_branch>"
  pipeline_owner = "<pipeline_owner>"
  team_slug      = "<team_slug>"
  # Include only non-default values below:
  # service_name   = "<service_name>"    # only if != pipeline_name
  # service_group  = "<service_group>"   # only if known
  # pipeline_file  = "<pipeline_file>"   # only if != .buildkite/pipeline.yaml
  # trigger_mode   = "none"              # only if != "code"
  # description    = "<description>"     # only if provided
}
```

Module name: use the service name with hyphens replaced by underscores.

## Spec → Terraform mapping (release)

Input: release pipeline handoff from `buildkite-pipelines/references/buildkite-release-pipeline-creation.md`

| Spec field | Terraform variable | Notes |
|---|---|---|
| `pipeline_payload.name` minus `-release` suffix | `pipeline_name` | |
| repo name from `pipeline_payload.repository` | `github_repo` | Strip prefix/suffix |
| `pipeline_payload.default_branch` | `default_branch` | |
| `tags[owner]` | `pipeline_owner` | |
| `tags[owner]` | `existing_team_slug` | Only set when different from default `rokt-global-infrastructure` |
| `tags[serviceGroup]` | `service_group` | Omit when null/absent |
| always | `release_pipeline_template_id` | Resolve the local whose template name currently contains `Release Pipeline [STABLE]`; never hardcode an A/B slot |

### Release terraform output template

```hcl
module "<module_name>" {
  source = "./deploykit-pipeline-module"

  default_branch               = "<default_branch>"
  github_repo                  = "<github_repo>"
  pipeline_name                = "<pipeline_name>"
  pipeline_owner               = "<pipeline_owner>"
  release_pipeline_template_id = local.<stable_template_local>
  # Include only non-default values below:
  # service_group      = "<service_group>"       # only if known
  # existing_team_slug = "<existing_team_slug>"  # only if != "rokt-global-infrastructure"
}
```

Module name: use the pipeline name with hyphens replaced by underscores.

Append to `dk-pipelines.tf` in the appropriate section (group by repo/team).

## PR workflow

1. **Clone or use existing checkout** of `ROKT/rokt-buildkite`
2. **Create branch**: `feat/add-<service-name>-pipeline` from the target repo's default branch (determine it from git; do not assume)
3. **Generate the `.tf` file** using the templates above
4. **Also commit the pipeline YAML** to the service repo if `yaml_sync.source` is `"generated"` — this is a separate PR on the service repo (see ordering below)
5. **Commit** with message: `feat(terraform-buildkite): add <service-name> CI/release pipeline`
6. **Push and open PR**:
   ```bash
gh pr create --repo ROKT/rokt-buildkite \
  --title "feat(terraform-buildkite): add <service-name> pipeline" \
  --body "$(cat <<'EOF'
## Summary
- Adds <CI|release|CI + release> pipeline for <service-name>
- Module: <ci-pipeline-module|deploykit-pipeline-module>
- Repo: ROKT/<github_repo>
- Owner: <pipeline_owner>

Generated by the buildkite skill from a buildkite-pipelines spec.
EOF
)"
    ```
7. **Watch the PR** — monitor CI checks on the PR (use `ci-watcher` as a subtask when available, otherwise poll with `gh pr checks`). When checks pass, report readiness for review.
8. **Report** the PR URL and review guidance to the user.

## Ordering: terraform PR vs service repo YAML

The terraform PR creates the Buildkite pipeline; the service repo PR provides
the `.buildkite/pipeline.yaml` that the pipeline uploads on first run.

- Both PRs can be opened in parallel
- Note the dependency: the pipeline must exist (terraform merged + applied)
  before the YAML will actually run
- If the YAML PR merges first, the push event has no pipeline to trigger — this
  is harmless but the first real build won't happen until the terraform is applied
- Recommend merging terraform first, then the service repo YAML

## Post-PR lifecycle

### Auto-apply on merge

`ROKT/rokt-buildkite` has CI that auto-applies terraform on merge to its default branch.
The pipeline runs per-workspace (e.g. `eng+prod+us-west-2`).

- **New pipelines / additive changes**: plan runs, shows `0 to destroy`, apply proceeds automatically
- **Changes to existing pipelines / destroys**: plan runs, shows `N to destroy`, apply is **blocked** behind a manual unblock step ("Unblock apply for rokt-buildkite: [eng+prod+us-west-2]")

**When the apply is blocked (destroys detected)**:

- **Do NOT unblock** unless the destroy is confirmed intentional
- **Rebase the PR on the latest default branch** before merging to ensure the plan reflects current state — a stale branch can show phantom destroys from resources that were already moved or renamed
- **If the destroy is expected** (e.g. removing a decommissioned pipeline): unblock to proceed
- **If unclear**: reach out to [RGI Public chat](https://chat.google.com/app/chat/AAAA1Nqg14I) for guidance before unblocking
- **Never auto-retry or auto-unblock** a blocked destroy step from an agent

### After terraform is applied

1. **Confirm the pipeline exists** in Buildkite (use MCP or `bk api /pipelines/<slug>`)
2. **Report success** — the pipeline is live

### Verification loop (for orchestrators)

Higher-level skills (`project-bootstrap`, `platform-scaffolder`, `platform-driver`)
can use this skill's verification path after the pipeline exists:

1. **Trigger a build** — push to the service repo or use `bk build create`
2. **Monitor the build** — use MCP build monitoring or `scripts/monitor-build.sh`
3. **If build fails** — diagnose with log inspection, fix the pipeline YAML or
   service code, push again, and re-monitor
4. **Repeat** until the build passes

This trigger→monitor→fix loop is available to any caller. The `buildkite` skill
can execute it directly when asked, or orchestrator skills can invoke it as part
of a larger workflow.

### Support channel

For blocked applies, review requests, or terraform issues with `ROKT/rokt-buildkite`:
- Google Chat: [RGI Public](https://chat.google.com/app/chat/AAAA1Nqg14I)

## Existence checks before PR

Before generating terraform, check whether the pipeline already exists:

- Search `infra/terraform-buildkite/` for existing `.tf` files referencing the same `pipeline_name` or `github_repo`
- If found, this is an **update** — modify the existing file instead of creating a new one
- If the pipeline exists in Buildkite but not in terraform, this is a **terraform adoption** — note this in the PR description

## Combining CI + release in one PR

When both CI and release pipeline specs are generated together:

- CI pipeline: new file `<service>-pipeline.tf`
- Release pipeline: append to `dk-pipelines.tf`
- Single branch and PR covering both

## Fallback

If `gh` is unavailable or the user cannot access `ROKT/rokt-buildkite`:

1. Show the generated terraform snippet for the user to copy
2. Provide the file path and instructions for manual PR creation
3. Offer direct API execution via `references/buildkite-standard-pipeline-creation.md` or `references/buildkite-release-pipeline-creation.md`
4. For support: [RGI Public chat](https://chat.google.com/app/chat/AAAA1Nqg14I)
