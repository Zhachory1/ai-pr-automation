# Handoff: Move Agent Fleet Into Hermes Profiles

<!-- Handoff Format: v1 -->
<!-- Created: 2026-09-17T20:58:26Z -->
<!-- Project: /Users/zhach/code/ai-pr-automation -->

## Prompt

Refactor every model-driven fleet role into an explicit Hermes profile while preserving Compose containers only as trust-tier boundaries and keeping deterministic controllers responsible for queues, validation, and external effects. Activate the already-merged Hermes maintenance worker first, then migrate `swe-implement`, `memory-curator`, and `pr-safety` in small PRs.

## Instructions

- Treat a Hermes profile as the agent definition: model, SOUL/instructions, toolsets, skills, limits, and session behavior. Treat a Compose service as the security boundary: credentials, mounts, network, state, and publisher authority.
- Do not combine write-capable and read-only roles merely to reduce container count. Hermes profiles are not sandboxes; profiles inside one container share that container's credentials, mounts, network, and process authority.
- Current `doc-write` and `pr-review` use the shared zero-tool Hermes gateway but embed role instructions per request. Backfill them into explicit `doc-write` and `pr-review` profiles without changing controller-owned exact-byte publication or GitHub review publishing.
- PR #144 is merged, but running `agent-server-maintain` containers still use `agent-fleet/agent-server:latest` and `mewritecode-runner.sh`. Rebuild and recreate them from `Dockerfile.hermes-maintain`; verify exact branch/head push gates and the three-round lineage cap before a live run.
- The current maintain design uses request-local `HERMES_HOME`, so its sessions do not appear in the shared dashboard. Profile work must decide how to expose profile/session visibility without sharing credentials or writable mounts across trust tiers.
- Dashboard is healthy at `http://127.0.0.1:9119`; fleet status is healthy at `http://127.0.0.1:8080`. `doc-write` and `pr-review` are active on Hermes. PR-safety uses the new incident-only human-review path; the obsolete controller was removed.
- Next role order: activate/profile `pr-maintain`, then `swe-implement`, `memory-curator`, and finally `pr-safety`. Keep fresh-clone, branch push, draft-PR, shared-memory writer, immutable snapshot, and incident-only escalation controls in their existing controllers.
- Keep implementation practical. Do not reintroduce broad councils, staged pilots, raw trace frameworks, or extra state machines unless a real failure demands them. Use focused tests and one PR per role.
- Every repository change must be pushed through a PR. Opening PRs is approved. Never merge or push the default branch; merge remains human-owned.
- The previously exposed OpenAI API key was rotated. Never print credentials or rendered Compose environments containing secret values. Use synthetic environment overrides in tests.

## Relevant Files

1. `docker-compose.yml`
2. `bin/agent-server`
3. `bin/hermes-maintain-runner.sh`
4. `Dockerfile.hermes-maintain`
5. `bin/hermes-pr-review-request`
6. `bin/hermes-run`
7. `bin/doc-writer-server`
8. `agent-config/hermes/doc-config.yaml`
9. `scripts/hermes-doc-gate.py`
10. `docs/hermes-migration-roadmap.md`

## Current Runtime State

- `agent-fleet-hermes-doc-1`: healthy; shared zero-tool model gateway for documents and reviews.
- `fleet-doc-writer-server`: running with `DOC_WRITER_RUNTIME=hermes`.
- `fleet-agent-server-review`: running with `AGENT_SERVER_REVIEW_RUNTIME=hermes`.
- `agent-fleet-agent-server-maintain-{1,2,3}`: still old `agent-fleet/agent-server:latest`; PR #144 code is merged but not activated.
- `agent-fleet-hermes-dashboard-1`: healthy; dedicated authenticated dashboard container sharing Hermes document state.
- `agent-fleet-agent-server-pr-safety-1`: healthy; only incident candidates enter human review.
- `fleet-status`: running and published at localhost port 8080.
- No running obsolete fleet services remain; old Redis was stopped and old PR-safety controller removed.

## Next Steps

1. Fetch `origin/main` and create a fresh activation/profile branch.
2. Rebuild `agent-fleet/hermes-maintain:latest` from merged `Dockerfile.hermes-maintain` and recreate all maintain replicas.
3. Run one controlled maintain request or inspect the next natural request; verify Hermes invocation, exact PR branch push, resolved threads, valid result JSON, and no fourth round.
4. Confirm pinned Hermes profile invocation mechanism for both Runs API and local CLI. Determine whether API requests can select a named profile or require profile-scoped gateway/state.
5. Add committed profile definitions for `doc-write`, `pr-review`, and `pr-maintain`; update controllers/runners to select names instead of embedding the full agent identity in each request.
6. Decide dashboard/profile visibility across separate trust-tier state roots. Prefer explicit dashboard registration or read-only aggregation over shared credential-bearing state.
7. Migrate `swe-implement`, preserving fresh clone, exact target branch, server-owned push/draft PR, and no default-branch writes.
8. Migrate `memory-curator`, preserving its sole shared-memory writer authority and retention filters.
9. Migrate `pr-safety` last, preserving immutable snapshots, read-only provider context, local handoffs, and incident-only human escalation.
10. Remove Me Write runtime dependencies only after each Hermes profile is merged, activated, and observed working.

## Known Limits

- Maintain Hermes prompt has no Buildkite MCP. It escalates Buildkite failures rather than guessing from check names.
- Maintain still uses the accepted existing write-token trust model; controller shims enforce expected branch/head and lease checks but are not a replacement for a future fully brokered publisher.
- Combined gate PR #143 is merged. Current local document generation was refreshed during review activation; recompute after profile/topology changes.
- PR #141 dashboard and PR #142 review are merged. PR #144 maintain migration is merged but not deployed.

## Git Context

Branch: `docs/hermes-profiles-handoff`
Uncommitted: 1 file
Recent commits:

- `b47dcf4 Merge pull request #144 from Zhachory1/feat/hermes-pr-maintain`
- `32d8ee2 Merge pull request #143 from Zhachory1/chore/activate-hermes-review`
- `7db5b54 feat(hermes): migrate PR maintenance runtime`

## Working Directory

`/Users/zhach/code/ai-pr-automation`
