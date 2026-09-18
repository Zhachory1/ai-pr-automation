# Plan: Hermes PR Maintain Cutover

- Owner: Zhach
- Status: implemented; superseded as target architecture by [`DD-host-native-agent-engine.md`](DD-host-native-agent-engine.md)
- Source: [`../hermes-migration-roadmap.md`](../hermes-migration-roadmap.md), M6

## Goal

Run `pr-maintain` agent execution with Hermes. Keep current queue, three-round cap, worktree creation, lease, branch/head checks, push gate, review replies, CI policy, and human escalation.

## Shape

- Dedicated worker image based on pinned Hermes.
- Existing `bin/agent-server` remains controller.
- Hermes CLI runs inside worker against controller-created worktree.
- Tool access limited to terminal and file.
- Existing GitHub/git shims remain authoritative.
- Maintain prompt is self-contained for terminal/file tools; no MCP service dependency.
- OpenAI key stays in worker environment; no shared Hermes document state.
- Default maintain runtime becomes Hermes. Legacy Me Write image remains rollback.

## Guardrails

- Maximum three maintenance rounds per PR lineage.
- All unresolved threads handled each round.
- One low-risk fix pass.
- No merge, deploy, release, CI mutation, unpinned force-push, or default-branch push.
- Push only `HEAD:<captured-head-branch>` after the remote branch is confirmed at the claim SHA.
- History rewrite is allowed only with exact `--force-with-lease=<captured-head-branch>:<claim-sha>`.
- No CI retries or weakened checks.
- Ambiguous writes reconcile.

## Tasks

1. Add pinned Hermes maintain image.
2. Add Hermes one-shot runner with terminal/file tools and bounded turns/time.
3. Require a strict nonce-bound maintain result; missing or invalid output reconciles.
4. Switch maintain Compose service to Hermes runner/image without dead MCP dependencies.
5. Add fake CLI, prompt, image, push-gate, and legacy rollback tests.
6. Rebuild and activate after merge.

## Validation

```bash
bash tests/test-hermes-maintain-runner.sh
bash tests/test-maintain-ci-prompt.sh
bash tests/test-maintain-resolve-threads.sh
bash tests/test-producer-dedupe.sh
git diff --check
```

## Evidence

- fake Hermes runner, prompt forwarding, strict result schema, and failure reconciliation: pass;
- exact branch/refspec, remote-head, lease, and force-with-lease gates: pass;
- self-contained thread discovery, one-pass CI policy, and unavailable Buildkite escalation: pass;
- three-round concurrent cap: pass;
- pinned image build and non-root CLI/dependency smoke: pass;
- final code review: no behavioral blockers.

## Rollback

Restore `agent-server-maintain` to `Dockerfile.agent-server` plus `mewritecode-runner.sh`, rebuild, and recreate maintain workers. Queue and three-round history stay unchanged.
