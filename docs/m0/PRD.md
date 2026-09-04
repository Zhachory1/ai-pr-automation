# PRD — M0 Compose Substrate

Run: m0-compose-20260902 · Mode: default · Repo: ai-pr-automation · Branch: feat/m0-compose-substrate
Parent roadmap: ../roadmap.md

## What

Stand up the containerized substrate the agent fleet runs on: our request-tracking Postgres,
Redis, hindsight (agent memory), swarmvault (doc maintainer), and coderag (codebase-memory-mcp).
All defined by one `docker-compose.yml` in `ai-pr-automation`,
with the SQL schema (`requests`, `pending_decisions`) for later milestones.

## Why

M1 (the serial agent-server) cannot be built until the services it depends on exist, come up
reliably, and survive restart with data intact. M0 de-risks the substrate in isolation so M1 is
pure application logic against known-good endpoints.

## Users

Single operator (Zhach), local macOS laptop. No external users.

## Scope

- `docker-compose.yml`: Postgres + Redis + hindsight + swarmvault + coderag.
- SQL schema for `requests` + `pending_decisions` (defined now; consumed in M1+).
- `.env.example` (provider config, `HINDSIGHT_DB_PASSWORD`, `CODE_ROOT`); real `.env` gitignored.
- Host-absolute read-only code-root mount for coderag; `${CODE_ROOT}` + worktrees rw for agent server.
- A `README`/make target to bring the stack up and a probe script for the exit checks.

## Non-goals

- No agent-server logic (M1). No producers (M2). No status UI (M3). No workflows (M4).
- No queue-draining, no write-path gate code — schema only, no consumers yet.
- No secrets committed. No data volumes in git.
- Not solving cost tracking, prompt-injection gate, or handoff fidelity (later milestones).

## Success criteria (locked)

Primary outcome: `docker compose up` brings all five services healthy, and `docker compose down`
+ up loses no data.

Exit checks (each must pass):
1. Our Postgres + Redis reachable from host (`psql`, `redis-cli`).
2. `requests` + `pending_decisions` tables exist with the agreed columns/constraints.
3. hindsight `/mcp/{bank}/` answers a retain then recall round-trip.
4. swarmvault MCP answers a probe.
5. coderag starts and serves its local UI for a selected repo below `/code`.
6. Full `down` + `up`: request rows, hindsight memory, swarmvault vault, coderag graph all survive.

Guardrails:
- No provider key or password committed (`.env` gitignored, `.env.example` only).
- coderag mount is read-only — cannot dirty code.
- Compose mount uses host-absolute `${CODE_ROOT}`, never repo-relative `./` (would mount only
  ai-pr-automation itself).

## Launch readiness

Merge to main. Build and start local coderag and swarmvault sidecars. "Launch" = six exit checks
pass on operator laptop and services stay up.
