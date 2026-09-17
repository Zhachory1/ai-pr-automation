# Plan: Hermes PR Maintain Cutover

- Owner: Zhach
- Status: approved
- Source: [`../hermes-migration-roadmap.md`](../hermes-migration-roadmap.md), M6

## Goal

Run `pr-maintain` agent execution with Hermes. Keep current queue, three-round cap, worktree creation, lease, branch/head checks, push gate, review replies, CI policy, and human escalation.

## Shape

- Dedicated worker image based on pinned Hermes.
- Existing `bin/agent-server` remains controller.
- Hermes CLI runs inside worker against controller-created worktree.
- Tool access limited to terminal and file.
- Existing GitHub/git shims remain authoritative.
- OpenAI key stays in worker environment; no shared Hermes document state.
- Default maintain runtime becomes Hermes. Legacy Me Write image remains rollback.

## Guardrails

- Maximum three maintenance rounds per PR lineage.
- All unresolved threads handled each round.
- One low-risk fix pass.
- No merge, deploy, release, force-push, history rewrite, or default-branch push.
- Push only expected PR head branch after lease/head validation.
- No CI retries or weakened checks.
- Ambiguous writes reconcile.

## Tasks

1. Add pinned Hermes maintain image.
2. Add Hermes one-shot runner with terminal/file tools and bounded turns/time.
3. Preserve nonce-bound `result.json` fallback.
4. Switch maintain Compose service to Hermes runner/image.
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

## Rollback

Restore `agent-server-maintain` to `Dockerfile.agent-server` plus `mewritecode-runner.sh`, rebuild, and recreate maintain workers. Queue and three-round history stay unchanged.
