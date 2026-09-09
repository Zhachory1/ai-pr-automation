# Fleet memory: read-only agents + a single curator

## Who writes shared memory

The shared Hindsight bank `fleet-shared` is **read-only over MCP** for the review, maintain, and
swe-implement agents (enforced by `hindsight-bank-init`, issue #75). They can `recall` but cannot
`retain` — this stopped them writing review-completion noise ("PR X reviewed with verdict Y") that
prompt rules alone never prevented.

The **only writer** is the `memory-curator` (`bin/memory-curator`), a scheduled job that writes over
the Hindsight **REST** `POST /memories` path, which the MCP read-only lock does not gate.

## What the curator does

On a schedule (launchd, ~6h), it:
1. Gathers a bounded slice of new source material since its last-run watermark: agent-fleet
   transcripts (`~/.mewrite/agent/tasks/*`, excluding its own runs), Rokt code, and `~/private-docs`.
2. Runs the `memory-curator` persona, which proposes a strict JSON list of durable, agent-useful
   memories (decisions, root causes, conventions, patterns, strategy/people/decision context) and
   drops run status, verdicts, one-off findings, and secrets.
3. A deterministic wrapper is the actual gate: it applies a reject-shape filter (completion/verdict/
   provenance-only, too-short/long, secret-looking), requires **≥2 independent sources** for anything
   phrased as a durable convention, dedups against `recall`, and writes keepers.

Autonomous does **not** mean the model's output is trusted verbatim — the wrapper filters it.

## Scope and known risks

- **One bank, everything.** Engineering, strategy, people, and decision context all land in
  `fleet-shared`, which every fleet agent reads. This is intentional (agents should know the general
  state of things) — treat every curator memory as company-visible. Credentials/secrets are filtered
  out; sensitive *prose* is in scope by choice.
- Each write is tagged `curator` + `curator/v1` with `metadata.written_at`, so the set is auditable
  and purgeable.

## Operating it

- **Bounds** (env): `MEMORY_CURATOR_MAX_MEMORIES` (default 15/run), `MEMORY_CURATOR_MAX_SOURCE_DOCS`
  (40), `MEMORY_CURATOR_TIMEOUT` (1200s), `MEMORY_CURATOR_TTL_DAYS` (90), `MEMORY_CURATOR_MODEL`
  (default `openai/gpt-5.6-sol`).
- **TTL sweep**: each run evicts `curator`-tagged memories older than `TTL_DAYS` (only rows it can
  positively date via `written_at`; never evicts blind).
- **Kill switch**: unload the launchd job (`launchctl unload …memory-curator.plist`) and purge by tag:
  list `GET /memories/list?tags=curator`, then `DELETE /memories?document_id=<id>` per row.
