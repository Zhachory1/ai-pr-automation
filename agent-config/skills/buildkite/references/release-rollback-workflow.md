---
name: release-rollback-workflow
description: >-
  Find rollback candidates on a release pipeline, classify branches as
  deployment locations, present options to the user, and unblock the fast
  rollback step. Use for rollback, revert release, fast rollback, find builds
  to rollback, undo deployment.
---

# Release Rollback Workflow

Use this reference when the user wants to roll back a service to a previous
release build. Release pipelines use branch-per-deployment-location: each
branch name corresponds to a DeployKit deployment location, and the `release`
branch is the base that triggers per-location builds.

## Branch → Deployment Location Mapping

Release pipeline branches map 1:1 to deployment locations:

```
Branch name              Deployment location     Type
─────────────────────    ─────────────────────   ──────────
release                  (base branch)           Triggers all locations
release-eng              (realm base branch)     Triggers eng realm locations
release-data             (realm base branch)     Triggers data realm locations
eng+stage+us-west-2     eng+stage+us-west-2     Stage
eng+prod+us-west-2      eng+prod+us-west-2      Production
data+stage+us-west-2    data+stage+us-west-2    Stage (data realm)
```

Base branches:

- `release` — single-realm pipelines or the top-level trigger for multi-realm
- `release-<realm>` — multi-realm pipelines group locations by realm; each
  `release-<realm>` branch triggers builds for that realm's location branches

When classifying branches:

- Branches matching `release` or `release-*` are base/trigger branches
- Branches with exactly three or four non-empty `+` components are
  location-shaped rollback branches in
  `<realm>+<env>+<region>[+<cluster-suffix>]` format
- Other branches are non-location and not rollback candidates

> **Source of truth:** `deploy-kit/references/deployment-location-resolution.md`
> owns the format and convention-based kube normalization. This workflow
> consumes that for branch classification—do not duplicate realm mappings here.

## Workflow

**1. Resolve target environment**

**Always discover branches first — never guess realm names.** List recent
builds to find what branches actually exist on this pipeline:

```
buildkite_list_builds(org, pipeline_slug, per_page=30)
```

Extract unique branch names from the results. Filter out `release` and
`release-*` (trigger branches), then retain only exact three- or four-component
location branches. Report the remainder as non-location and non-actionable. The
valid branches are still not independent proof of current service fan-out;
successful DeployKit inspection owns that proof.

**The rollback block step lives on location branches, not on `release`.**
A `release` branch build orchestrates the full pipeline (stage → prod), but
the "Fast rollback to this commit!" block step is on the per-location branch
build (e.g. `eng+prod+us-west-2`). To rollback in prod, you must find and
unblock the step on the prod location branch.

Then route based on user intent:

- **Full branch specified** (e.g. "rollback eng+prod+us-west-2"): confirm it
  exists in the discovered branches, then skip to step 2
- **Environment only** (e.g. "rollback in prod", "show me prod builds"):
  filter discovered branches where the middle segment matches the requested
  env. Examples: user says "prod" → match `eng+prod+us-west-2`,
  `data+prod+us-west-2`, etc.
  - One match → use it directly
  - Multiple matches (different realms or regions) → show builds for EACH
    matching branch as separate summaries, grouped by branch. Let the user
    pick which location+build to rollback.
  - Zero matches → report what branches exist and ask the user
- **No environment specified** (e.g. "find builds to rollback"): show two
  categories:
  - `release` / `release-*` builds as an **overview** (what commits went
    through the pipeline — useful context but not directly actionable for
    rollback)
  - Location branches grouped by environment (stage vs prod) — these are
    the actionable rollback targets

**2. List recent builds on the location branch(es)**

Once the target is resolved, list builds for each matching location branch:

```
buildkite_list_builds(org, pipeline_slug, branch="eng+prod+us-west-2", per_page=10)
```

If multiple branches match (e.g. user said "prod" and pipeline has
`eng+prod+us-west-2` AND `eng+prod+us-east-1`), query each and present
as separate grouped summaries.

CLI equivalent:

```bash
bk build list -p <pipeline-slug> --branch "eng+prod+us-west-2" --limit 10
```

API equivalent:

```bash
bk api "pipelines/<slug>/builds?branch=eng%2Bprod%2Bus-west-2&per_page=10"
```

Note: `+` must be URL-encoded as `%2B` in API query params.

**3. Present candidates and ask user to pick**

For each location branch, show recent builds:

- Show: build number, commit SHA (short), message, state, created time
- On location branches, `blocked` state is **normal and expected** — it means
  the rollback block step is available to unblock. These ARE the rollback
  candidates.
- `passed` on a location branch means the rollback was already triggered
  (already rolled back to that commit)

When showing `release` branch builds (overview mode only), label them as
context — "these commits went through the pipeline" — not as actionable
rollback targets. The rollback block step lives on location branches.

Ask the user which build number to roll back to.

**4. Find the rollback block step**

```
buildkite_get_build(org, pipeline_slug, build_number)
```

In the build's jobs, look for a blocked job matching the rollback pattern:

- `type: "manual"` (block step)
- `state: "blocked"`
- `name` containing "rollback" or "Fast rollback" (case-insensitive)

If the build has no rollback block step:

- It may have already been unblocked (check for `state: "unblocked"`)
- It may not have reached the rollback generation step yet (check if
  "Generating fast rollback steps" job passed)
- Report this to the user and suggest picking a different build

**5. Unblock (or link)**

Before attempting to unblock, ask the user:

```
Do you want me to unblock the rollback step directly, or would you prefer a
direct link to do it manually in the Buildkite UI?
```

**If user wants direct unblock:**

```
buildkite_unblock_job(org, pipeline_slug, build_number, job_id)
```

CLI equivalent:

```bash
bk job unblock <job-uuid> --data '{}'
```

API equivalent:

```bash
bk api --method PUT "pipelines/<slug>/builds/<number>/jobs/<uuid>/unblock" --data '{}'
```

If the unblock fails (403 / insufficient permissions), fall back to the link
approach below.

**If user wants manual unblock (or API unblock failed):**

Return the direct build URL:

```
https://buildkite.com/rokt/<pipeline-slug>/builds/<number>
```

Tell the user: "Open the link above and press 'Fast rollback to this commit!'
to trigger the rollback."

**6. Monitor rollback (optional)**

After unblocking, the rollback triggers a new build on the location branch.
Offer to monitor it:

```
buildkite_list_builds(org, pipeline_slug, branch="<location>", per_page=1)
→ get the triggered build
→ monitor with get_build polling until terminal
```

## Constraints

- Always get fresh user confirmation before unblocking any block step
- Never unblock a rollback to production without explicit user confirmation
  that includes the target environment
- If the build is on a prod location branch, add an extra warning:
  "This will roll back PRODUCTION in <region>. Confirm?"

## Handoff

- After rollback is triggered, hand off to `kubernetes` for rollout
  verification if the user wants confirmation that the rollback landed
- If the rollback build fails, route to `deploy-kit` (troubleshoot) for
  release failure diagnosis

## Output

When user asks for a specific environment (e.g. "prod builds"):

```
## Rollback Candidates — <pipeline-slug> (prod)

### eng+prod+us-west-2

| # | Build | Commit  | Message                          | State   | When        |
|---|-------|---------|----------------------------------|---------|-------------|
| 1 | #1323 | a34da7  | Add serviceTier-3 (#508)         | blocked | Mar 11      |
| 2 | #1302 | 79a487  | revert: restore quota multiplier | blocked | Dec 9       |

### eng+prod+us-east-1 (if exists)

| # | Build | Commit  | Message                          | State   | When        |
|---|-------|---------|----------------------------------|---------|-------------|
| 1 | #1280 | ...     | ...                              | blocked | ...         |

Which location and build do you want to roll back to?
```

When user asks without specifying environment:

```
## Rollback Candidates — <pipeline-slug>

### Overview (release branch — context only, not actionable)

| # | Build | Commit  | Message                          | State  | When   |
|---|-------|---------|----------------------------------|--------|--------|
| 1 | #1420 | 831a09  | fix: replace channel in monitors | passed | May 2  |
| 2 | #1412 | 257e8c  | fix: replace channel in monitors | passed | May 1  |

### eng+stage+us-west-2 (rollback available)

| # | Build | Commit  | Message                          | State   | When   |
|---|-------|---------|----------------------------------|---------|--------|
| 1 | #1421 | 831a09  | fix: replace channel in monitors | blocked | May 3  |

### eng+prod+us-west-2 (rollback available)

| # | Build | Commit  | Message                          | State   | When   |
|---|-------|---------|----------------------------------|---------|--------|
| 1 | #1323 | a34da7  | Add serviceTier-3 (#508)         | blocked | Mar 11 |

Which location and build do you want to roll back to?
```

After user selects:

```
## Rollback

Build: #1323 on eng+prod+us-west-2
Commit: a34da787
Action: Unblock "Fast rollback to this commit!" step

⚠️  This will roll back PRODUCTION in us-west-2. Confirm? (yes/no)
```
