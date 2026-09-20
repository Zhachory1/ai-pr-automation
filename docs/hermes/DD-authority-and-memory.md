# DD: GitHub Authority Auto-Reauth and Two-Tier Memory Curation

Status: proposed. Extends [DD-host-native-agent-engine.md](DD-host-native-agent-engine.md) and
[plan-host-native-autonomous-hermes.md](plan-host-native-autonomous-hermes.md). Covers two runtime
gaps found while activating the host-native fleet: per-task GitHub reauth friction, and the wrong
authority model for shared-memory curation.

## Problem

1. **GitHub reauth friction.** The autonomy gate requires an enrollment proof refreshed within ten
   minutes of every enqueue or claim (`hermes_repository_authorized`, from #150). Nothing refreshes
   it automatically, so the operator re-runs evidence generation by hand before every pilot. Repo
   roles (`pr-review`, `pr-maintain`) cannot run hands-off. The deeper issue: the gate re-proves
   branch protection client-side, duplicating the server-side wall that already enforces it.

2. **Wrong authority model for memory.** memory-curate was gated behind the same enrollment
   machinery via a `local/fleet` sentinel the operator enrolls by hand. But memory has no externally
   mutable authority to re-prove: "may this agent store a memory" does not change minute to minute.
   The ten-minute freshness gate on a six-hour job is a category error — the job can never satisfy
   it, and a human grant adds friction without safety. The real control for memory is **what gets
   written**, not **who authorized it**.

## Decisions

### GitHub authority: declarative YAML grant; branch protection is the wall

Corrected model (operator decision): **branch protection is the enforcement boundary. The agent does
not pre-verify it.** If the agent hits a protected-branch, merge, or permission denial, it stops and
reconciles. The server-side wall already stops the disallowed action, so re-proving it client-side
every cycle is theater — the same reasoning that removed the freshness gate from memory.

- The operator maintains an **authority YAML** outside `CODE_ROOT` and git
  (`/Users/Shared/zhach-ai-pr-automation/authority.yaml`). It is simply the operator's **grant of
  repo space**: "you may write to and use these repositories." It is permission intent, not a
  security proof, and carries no branch-protection or credential digest.
- The runtime authorizes a repo by presence in the YAML grant. No live GitHub re-query, no owner
  token in any background job, no ten-minute freshness digest for repo roles.
- **On a wall:** a protected-branch push, merge, or permission error is a terminal `reconcile` — the
  agent reads back state and stops; it never retries around the wall.
- **Revocation** = remove the repo from the YAML (agent loses the grant on next read) and/or the
  server-side controls that already deny the action. Instant hard stop remains available by disabling
  the role's cron job.
- **Boundary preserved:** `hermes-agent` still never holds the owner token; it simply is not needed,
  because protection enforces server-side rather than being re-checked client-side.

This supersedes the earlier "reauth producer that re-queries live branch protection" idea and the
#150 ten-minute enrollment-freshness gate for repo roles. Those are removed as redundant with the
server-side wall.

### Memory: no enrollment; deterministic gate is the control; two tiers

memory-curate stops using enrollment entirely. Authority to *run* comes from the operator resuming
its cron job (and the instant kill-switch is pausing it). Authority over *content* is the
deterministic filter in the runner — the model proposes, the runner disposes.

Both banks live in the **remote shared agent-memory MCP service** (per global AGENTS.md:
`memory-ads-success` and `memory-org` servers), not local Hindsight. The runner's write/recall path
moves from local Hindsight REST to MCP `retain`/`recall` against that service.

- **Tier 1 — default: `team-ads-success` (team bank).** Fully automatic. The model decides what is
  relevant to other agents; the base deterministic gate (secrets, tokens, PII, provenance/verdict
  noise, wrong shape, too short/long, near-duplicate) is the only guardrail. Blast radius is the
  team, which the base gate covers. This is the real memory store.
- **Tier 2 — `fleet-shared` (org-wide): automatic with a stricter filter.** No human approval per
  memory (operator decision, over human-gated promotion). Company-wide writes pass the base gate
  **plus** an additional tighter gate:
  - require **≥2 independent sources** (already true for conventions; extended to all fleet-shared
    writes);
  - higher minimum length / substance threshold;
  - **no colleague names** (reject any personal reference beyond generic professional role);
  - an **internal-topic blocklist** (strategy, revenue, incidents, customer identifiers,
    unreleased-product keywords).
  Anything that fails the stricter gate but passed the base gate stays in `team-ads-success` only.

`zhach-private-docs` is **not** a memory target. It is the operator's long-form file brain (docs,
plans, designs, reports) under `~/private-docs`, curated by the existing brain-capture workflow, not
by memory-curate.

## Why not the alternatives

- **Human-approved promotion to fleet-shared** (reuse doc-publication exact-bytes gate): rejected by
  the operator in favor of automatic-with-stricter-filter. Recorded as the accepted tradeoff: an
  unattended model judgment can reach the org-wide store, bounded by the stricter deterministic gate.
- **Keeping enrollment for memory**: rejected — it makes the human a bottleneck and re-proves an
  authority that does not change.
- **Uniform ten-minute freshness for local roles (cron rubber-stamps each tick)**: rejected as
  security theater — it looks like a live check but verifies nothing for a role with no external
  authority.

## Guardrails and boundaries (unchanged or strengthened)

- `hermes-agent` never holds the owner GitHub token; the reauth producer runs as the operator.
- The deterministic memory gate runs in the runner, outside the model; a bad proposal cannot leak a
  secret or a colleague's name into any bank.
- `fleet-shared` remains an auth-less org-wide store ("treat a retain like posting in a company-wide
  channel"); the stricter Tier-2 gate is the compensating control for making writes automatic.
- Instant kill-switches: pause the memory cron job; set enrollment `active=false` for GitHub roles;
  remove a repo from the authority YAML.

## Open questions / runtime dependencies

- Confirmed wired in `~/.roktcode/mcp.json`: `memory-ads-success` →
  `https://rokt-agent-memory.eng.roktinternal.com/mcp/team-ads-success/`; `memory-org` → the
  `Rokt Builders` bank (org-wide). Bank name is **`team-ads-success`**. Streamable HTTP endpoints.
  Must confirm the same servers are configured for the `hermes-agent` account (they are configured
  for the operator account today).
- `retain` is asynchronous and LLM-rewrites the input, so runner-side dedupe must query `recall`
  before proposing, and cannot assume a just-written memory is immediately recallable.
- Decide the internal-topic blocklist source (static list in-repo vs operator-maintained file).
- The org-wide bank is `Rokt Builders` (via `memory-org`), not literally named `fleet-shared`; map
  the Tier-2 target to the org bank the service exposes.

## Work items

1. `authority.yaml` schema (plain repo-grant list) + runtime reads it to authorize a repo; remove
   the repo-role enrollment/freshness digest path; map protected/permission errors to `reconcile`.
2. Drop the `local/fleet` sentinel requirement for memory-curate; remove enrollment from its path.
3. Rewrite the memory-curate write/recall path from local Hindsight REST to the remote memory MCP
   `retain`/`recall` (`team-ads-success` default; org bank for Tier 2).
4. Base gate (exists) + Tier-2 stricter gate for the org bank; route Tier-1 to `team-ads-success`.
5. Tests: repo-grant authorize/deny, wall-hit → reconcile, base vs stricter gate fixtures, tier
   routing, dedupe against recall.
6. Docs: update `docs/hermes/README.md` and `docs/memory-curation.md`.
