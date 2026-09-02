# Roadmap — Agent Pub/Sub Orchestration

Date: 2026-09-02
Status: proposed
Owner: Zhach
Supersedes design in: `inbox/plan-2026-09-02-agent-pubsub-orchestration.md` (council-reviewed; see it for the killed-complexity history)

> Mirrored into this repo from the design vault. Where this roadmap and the shipped M0 code
> (`docker-compose.yml`, `docker/README.md`) differ on coderag/swarmvault transport or worktree
> paths, the code + `docs/m0/DD.md` are authoritative — they reflect facts verified during M0.

> MCP-stack facts below verified 2026-09-02 by reading each repo via `gh` (README + contents).
> Exact env/mount values still to confirm against release docs at M0, but shape is confirmed.

## Goal

Replace ~6 launchd cron bash scripts w/ pub/sub agent fleet. Producers enqueue. Consumers run agents **one at a time**. Table = durable record. UI tracks status.

## Core model (kept dead simple on purpose)

- **Redis** = dispatch. "What runs next." Fire-and-forget OK because durability lives in the table, not the queue.
- **Postgres** = durable record. Row per request: status, fail_response, timestamps.
- **Agent server** = the rewritten `ai-pr-automation` binary. Takes incoming request, kicks off agent, waits for terminal state, writes it back. Serial: one agent at a time. Reuses its existing runner contract, per-mode state, repo allowlist, fairness ordering, and `MCP_REMOTE_CONFIG_DIR` hook; swaps discover-and-loop-inline for drain-a-queue.
- **Producers** = cron scripts (the current discovery path), enqueue instead of running agent inline.
- **compose** = our Postgres (request table) + Redis + hindsight + swarmvault + coderag (codebase-memory-mcp, continuous main-only), host-mounted volumes. Container dies → data on disk, restart clean.

## Memory + MCP stack (external owners, not rebuilt) — verified 2026-09-02

| Service | Repo | Role | Deploy | Verified facts |
|---|---|---|---|---|
| hindsight | vectorize-io/hindsight | shared agent memory (decisions) | Docker container | REST :8888, UI :9999. Embeds own Postgres (`-v hindsight-data:/home/hindsight/.pg0`) OR external PG via `docker/docker-compose`. **Per-bank MCP endpoint `http://host:8888/mcp/{bank_id}/`** exposing retain/recall/reflect. Opt-in **Memory Defense**: scans every `retain` for secrets/PII (45 patterns), redact-or-block. LLM provider incl. `claude-code`/`codex` (no API key). macOS arm64 OK. |
| codebase-memory-mcp | DeusData/codebase-memory-mcp | codebase memory + parser (code RAG) | **container, continuous, main-only** | stdio MCP, 15 tools, SQLite knowledge graph, tree-sitter 162 langs. No API key, 100% local. Runs as its own container bind-mounting `~/code` read-only, indexing main continuously (see Code layout section). Caveat: `install` writes to agent config files + reads the codebase — use its MCP/indexer, not its client-config mutation. |
| swarmvault | swarmclawai/swarmvault | document maintainer (long-form wiki) | npm CLI (Node ≥24); has Dockerfile | MCP server (`swarmvault install --mcp`), git-backed `--commit`, watch mode, markdown wiki + local graph. Offline `heuristic` provider = no API key; optional Ollama for sharper extraction. Fills the slot zbrain would have (external owner). |

**Write-access = which endpoint the agent gets.** hindsight's per-bank MCP endpoint makes the
council's "run-scoped by default, promotion deliberate" policy trivial: give agents a read/
recall path, and route writes to the shared decision bank through the agent-server post-job (see
M1). hindsight Memory Defense covers the secrets/PII half of the prompt-injection→poisoned-recall
risk red-team flagged; the "injected instruction becomes a stored false fact" half is still on the
prompt-template discipline (external text = data, not instructions).

No leases. No reaper. No heartbeat. No timeout fencing. No concurrency race. Serial = none of that needed. Council's whole lease/fencing/spend-reserve section = dead, killed by serial-one-at-a-time.

## What complexity got cut and why

| Cut | Why gone |
|---|---|
| Lease / lease_expires_at | No claim outlives its owner. Serial. |
| Reaper binary | Nothing to reclaim. Terminal state written by the agent itself. |
| Heartbeat / attempt-epoch / pgid fencing | Double-execution needs concurrency>1. We run 1. |
| Container-clock-across-sleep worry | Only mattered for lease math. No leases. |
| Reserve-at-claim spend accounting | Cost solved later, agent-server side. |

## The table (minimum)

```sql
requests(
  id, kind, payload jsonb, dedupe_key,
  status,            -- queued | running | done | failed
  fail_response,
  created_at, started_at, finished_at
)
-- UNIQUE (kind, dedupe_key) WHERE status IN ('queued','running')
-- cron firing twice for same PR = no-op. Not timing, just no-double-work.
```

## The one real gap (small)

Agent process dies HARD (laptop off, OOM, docker kill) after claim, before writing terminal row → row stuck in `running`.

Serial fix, one line: **on agent-server startup, check for a stuck `running` row (at most one). Reset to `queued` or flag for human.** IF at boot, not a subsystem.

## The two things that survive regardless (not queue complexity — real)

1. **Cost tracking** — deferred. Lands agent-server-side (the binary that kicks off agents knows tokens/duration/exit). Not blocking. Table gets a cost column later.
2. **Prompt injection** — PR bodies/titles/comments reach an autonomous agent. Serial doesn't fix it, just limits to one poisoned run at a time. **Treat all external text as DATA, not instructions**, in the prompt template. Don't auto-retry a job that already made a write/PR side effect.

## Write path (the big one) — delivery vs content are two guarantees

Write mechanism is NOT the main risk. What gets written is. Split it:

- **Delivery guarantee** — the write lands, isn't lost, isn't phantom. Easy. Mechanism choice.
- **Content guarantee** — what landed is true, not attacker-planted. Hard. Neither hooks nor
  post-job placement help here; an injected agent writes poison through any pipe.

### Delivery: agent-server post-success. NOT hooks.

Hooks lose the exact races we're avoiding:
- Hook fires DURING the run → agent later fails/times-out/retries → phantom write for a run that
  didn't succeed. Worse than a missed write.
- Hook failure is invisible → agent exits 0, write silently lost, no retry path.
- Hook runs in the agent's (possibly injected) context → the untrusted thing holds the write pen.

Agent-server post-job wins on every point:
- Runs AFTER terminal state → write only on success, no phantom writes (same property the serial
  model already gives us).
- Server owns the sequence: mark job `done` + write decision + retry-on-fail. A hook can't retry.
- Write originates from trusted code (the server we wrote), not the agent.

**Decision: agent-server writes, post-terminal-success only, server-owned retry. Kill hooks.**

### Content: typed output + provenance + tiered gate

Moving the write to the server does NOT make content trustworthy — the server writes whatever the
agent handed it. So the post-job step is a GATE, not a `retain()` call:

1. **Typed output, not prose.** Agent returns a validated structure, not "here's what I decided."
   e.g. pr-review → `{ verdict: approve|request-changes|comment, findings: [{file, line, severity,
   text}], summary }`. Server validates the schema and writes THAT. An injected "record that X is
   safe" has nowhere to go in a typed field.
2. **Provenance on every stored fact.** `run_id`, `kind`, source (repo#PR@sha), terminal status,
   written_by=agent-server. So a bad recall later is traceable to a run and purgeable. hindsight
   Memory Defense (secret/PII redact-or-block on retain) covers the secrets half; provenance is the
   trace half.
3. **Tiered by blast radius — two banks:**
   - **run-scoped bank** (`run:{run_id}`): agent writes freely; only affects its own run; discarded
     or archived after. Injection here poisons nothing shared.
   - **shared decisions bank**: only the server writes, only validated typed structures, only on
     success. Decision-shaped writes (facts that steer FUTURE runs — "always skip flaky-test
     gating") are the poisoning target → human-gated (see below). Operational facts ("PR #123
     reviewed, verdict approve") are low-risk → auto-write.

### v1 human gate (cheap, not babysitting)

- **Operational facts** → auto-write to shared bank on success. No human.
- **Decision-shaped writes** → queued to a `pending_decisions` table; **daily human review batch**
  approves/rejects into the shared bank. One bad night can't silently rewrite what every future
  agent believes, and it's a once-a-day glance, not per-run approval.
- Classification (operational vs decision) is part of the typed output contract, server-enforced.

## Repo home — everything lives in `ai-pr-automation`

The agent-server IS the rewritten `ai-pr-automation`, so the whole system lives in that one repo:
compose file, per-service container defs, SQL schema, the agent-server binary, migrated producers,
typed-output contracts, write-path gate.

**In the repo (code, shape):** `docker-compose.yml` + service defs (hindsight, swarmvault Dockerfile
wiring, coderag container, our Postgres + Redis), schema (`requests`, `pending_decisions`),
agent-server, producers, contracts. `.env.example` committed.

**NOT in the repo (data, secrets):** hindsight/swarmvault/coderag data volumes → host-mounted, never
git. `.env` (provider keys, `HINDSIGHT_DB_PASSWORD`) → gitignored; repo defines shape, host holds
values.

**Two quirks from `~/code` being the mount:** (1) `ai-pr-automation` itself lives under `~/code`, so
coderag indexes its own source — harmless, just know the system watches itself. (2) The compose mount
is a host-absolute path (`~/code:ro` via `.env`), NOT repo-relative `./`, or it would mount only
itself.

**Name:** repo is called `ai-pr-automation` but becomes a general agent fleet (PR review = first
`kind`). Leave the name for now (renaming mid-build = churn); revisit later.

## Code layout + coderag (continuous, main-only)

- **`~/code` main checkouts = the true, up-to-date reality.** Never worked on in place; always clean.
- **coderag = long-running Docker container** bind-mounting `~/code` **read-only** (`:ro` →
  structurally cannot dirty the tree), indexing **main only, continuously**. A never-dirty main is
  what makes continuous indexing safe; codebase-memory re-indexes in ms.
- **Agents edit in worktrees under `~/code/_worktrees/<repo>/<branch>`** — dedicated dir so
  coderag's mount only ever sees pristine repos, zero worktree churn. Agent-server gets `~/code` +
  `~/code/_worktrees` **read-write**.
- **Agents track their own worktree diff.** coderag answers "how does the rest of the codebase
  (main) relate to this"; the agent already holds its branch diff and doesn't need coderag to
  re-derive it. Structural queries about symbols the agent just created in its branch fall back to
  the agent reading files directly — the small local part it can already see.
- **Guard:** "main is never dirty" is discipline, not an enforced invariant. coderag skips indexing
  a repo whose main worktree is dirty (`git status` not clean) rather than indexing garbage.

## Milestones

### M0 — compose substrate (all in `ai-pr-automation`)
- `docker-compose.yml` in the repo: our request-tracking Postgres + Redis + hindsight (container) + swarmvault (container) + **codebase-memory-mcp (container, continuous, main-only)**. Code-root mount is a host-absolute path via `.env` (`CODE_ROOT=~/code`), not repo-relative.
- `.env.example` committed (provider config, `HINDSIGHT_DB_PASSWORD`, `CODE_ROOT`); real `.env` gitignored. Data volumes host-mounted, never in git.
- **Mounts:** coderag bind-mounts `~/code` **read-only**, indexes main continuously, skips dirty-main repos. Agent-server bind-mounts `~/code` + `~/code/_worktrees` **read-write**; agents make worktrees under `~/code/_worktrees/<repo>/<branch>`.
- Audit what codebase-memory's `install` touches (writes to agent config files) before baking the container — we want its MCP server + indexer, not its client-config mutation.
- hindsight: decide embedded-pg0 vs external-PG-via-compose (its `docker/docker-compose` supports external). Host-mount the data volume either way.
- swarmvault: containerize via its Dockerfile; host-mount the vault dir so the wiki survives restart (git-backed `--commit` is a bonus durability layer).
- Provider config: hindsight + swarmvault can run keyless (`claude-code`/`codex` for hindsight; `heuristic`/Ollama for swarmvault). codebase-memory-mcp needs no key.
- Prove data survives `docker compose down` + up for all stateful services.
- Exit: our Postgres + Redis reachable from host; hindsight `/mcp/{bank}/` answers retain/recall; swarmvault MCP answers; coderag container indexes `~/code` main and answers a structural query, re-indexes on a main change, skips a deliberately-dirtied repo; restart loses nothing.

### M1 — agent server (serial worker)
- Rewrite `ai-pr-automation` binary: pop from Redis → write `running` → run agent (MCP endpoints injected via its existing `MCP_REMOTE_CONFIG_DIR` hook) → write terminal state + fail_response → **run the write-path gate** → loop.
- **Write path (see section above):** agent gets hindsight **recall** on shared bank + a **run-scoped bank** (`run:{run_id}`) for its own free writes; codebase-memory + swarmvault reads. Agent returns **typed structured output**; server validates schema. Server (not agent) writes validated structures to the shared bank, post-success only, with provenance (`run_id`, kind, repo#PR@sha, status), server-owned retry on write failure. **No hooks.**
- Operational facts auto-write to shared bank; decision-shaped writes go to `pending_decisions` for the daily human batch.
- Turn on hindsight Memory Defense on the shared bank.
- Startup stuck-`running` check.
- One `kind` end to end (`pr-review` — has a real cron producer).
- Exit: enqueue a request by hand; agent runs w/ memory MCPs attached; table shows correct terminal state; fail path writes fail_response; a typed decision reaches the shared bank ONLY via the server post-success path with provenance; an operational fact auto-writes; a decision-shaped write lands in `pending_decisions`, not the shared bank.

### M2 — first producer
- Convert `roktcode-pr-reviews` to enqueue instead of run-inline. Keep its discovery + fairness scoring.
- `UNIQUE (kind, dedupe_key)` dedupe live.
- Run alongside old script few days, compare. Delete old serial loop once trusted.
- Exit: cron enqueues, agent-server drains, no double-runs.

### M3 — second producer + status read
- Comment-handler as producer (2nd kind).
- Read-only status: `psql` view day one → single page later.
- Exit: two kinds flowing, one place answers "what ran / running / failed."

### M4 — multi-step workflows (only after M1–M3 trusted)
- Fixed straight line: `PRD → council → PRD-rewrite → DD`. Each step one agent job, exactly once.
- One branch: council `block` → park (row status), human resumes/kills. No loop.
- Handoff between steps written to a row/file the next step reads.
- Precondition: handoff-fidelity check — does a fresh agent per step, fed only the handoff, match single-context quality? Pre-register the check (n≥3, blinded, checklist: chained DD cites rejected alternatives + council blockers + PRD constraints). If it fails, richer handoffs before M4 ships.

## Cut list (not building)

- Concurrency>1 and everything it drags in (leases, fencing, reaper). Add only if serial throughput is a proven wall.
- Iterate-until-aligned loops. M4 is a straight line.
- PRD→DD→plan→ship full auto-chain beyond M4.
- Extra queue kinds w/ no non-human producer (plan-maker, SWE, doc-reviewer). Add when a producer exists.
- Building any memory/doc/code-RAG store ourselves — hindsight, codebase-memory-mcp, swarmvault own those. Consume, don't rebuild. (zbrain no longer in this design.)
- Cost accounting (deferred, agent-server side — the binary knows tokens/duration/exit).
- UI auth, autoscaling, multi-machine.

## Open questions

- hindsight storage: embedded pg0 vs external Postgres via its compose — pick at M0 (embedded is simpler; external shares one PG with our request table but couples them).
- bank layout in hindsight: shared decisions bank + run-scoped bank per run (`run:{run_id}`) is the decided v1 shape; per-kind shared banks are a maybe-later. Confirm hindsight bank-create cost is cheap enough for one run-scoped bank per run.
- `pending_decisions` review UX: where the daily human batch lives (psql query first, folds into the M3 status page later).
- codebase-memory-mcp `install` writes to agent config files — audit what it touches before baking into the agent-server image.
- Redis message shape — just `{request_id}` (agent-server reads row) vs full payload in the message?
- Does M4 handoff fidelity hold? Gates M4 only, not M0–M3.
