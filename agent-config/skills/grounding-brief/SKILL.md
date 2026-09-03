---
name: grounding-brief
description: "Universal context-gathering skill for AI agent workflows. Use for grounding brief, gather context, step zero, linked docs, tickets, code search, logs, traces, metrics, prior runs, memory MCP, gbrain, code RAG, repo-index, agent-fleet journal, PR context, investigation evidence, roadmap inputs, ML experiment setup. Produces a standard brief with sources, assumptions, open questions, risks, and confidence."
---

# Grounding Brief

Create standard context before any workflow commits to direction. Pull relevant facts from user input, linked artifacts, long-term memory, code RAG, logs, git history, PR metadata, prior workflow runs, and agent-fleet records.

Use this as universal first pass for dev workflow, ML experiments, PR review, debug/investigation, project ideation, roadmapping, backprop, and any high-stakes agent task.

## Core Principles

**Evidence before synthesis.** Separate observed facts from interpretation, assumptions, and guesses.

**Reuse before build.** Before proposing a broker, proxy, service, datastore, queue, credential path, or platform abstraction, inventory plausible existing capabilities. Missing local code is not proof that capability is absent.

**Use richest available context source.** Prefer long-term memory MCP for prior decisions and code RAG for codebase semantics; fall back to files, git, logs, and direct user-provided artifacts.

**Preserve source links.** Every important claim needs a path, URL, command, log pointer, ticket, transcript, or explicit user statement.

**Bound context collection.** Stop when enough evidence supports next workflow stage, or when additional searching is low-yield.

**Record uncertainty.** Missing context becomes open questions, not invented facts.

## Inputs

- goal or question.
- known artifact links: docs, tickets, PRs, commits, dashboards, logs, notebooks, datasets.
- target repo and branch if code is involved.
- desired time budget and depth.
- optional run id for `run-telemetry`.

## Tool Awareness

Use available tools in this order when relevant:

- long-term memory MCP / `gbrain` for prior plans, PRDs, DDs, incidents, people/company context, decisions, workflow retros.
- code RAG / repo-index / `coderag` for semantic code paths, ownership, dependencies, similar changes, historical failures.
- agent-fleet records: `$AGENT_FLEET_HOME`, `$AGENT_FLEET_JOURNAL`, `$AGENT_CHAT_ROOT/rooms`.
- git and GitHub CLI for branch, diff, commit history, PR comments, review state, CI status.
- logs, traces, metrics, dashboards, experiment tracking, notebooks, warehouse tables where supplied.
- local read/search/explore tools when MCP/RAG sources are absent.

Do not persist sensitive content to memory or third-party systems unless the operator explicitly approves the capture.

## Execution

**Scope the brief.** State the workflow, decision to support, repositories/artifacts in scope, and out-of-scope areas.

**Inventory reuse.** Before proposing new infrastructure or platform abstraction, search supplied artifacts, available MCP/tool and platform catalogs, known SDKs/service clients, and one bounded repository search. Record sources checked and each plausible capability's owner, consumers, access contract, deployment boundary, and evidence-based result: `reuse`, `cannot-satisfy`, or `clarify-first`. Record `no-candidate` only as inventory status when no plausible candidate exists. Stop when remaining sources are unavailable or further search is low-yield.

**Clarify unknown access.** A plausible capability with unknown owner, access contract, or deployment boundary returns `clarify-first`, never `ready` or `build`. Ask for one smallest missing fact, name the owner, and state the resume condition; do not plan new infrastructure first.

**Collect sources.** Query memory, code RAG, linked docs, tickets, code, logs, PR data, prior run dirs, and agent-fleet journals. Capture exact source pointers.

**Normalize findings.** Convert mixed inputs into concise facts, decisions, constraints, risks, and unresolved questions.

**Detect conflicts.** Call out stale docs, contradictory requirements, branch drift, mismatched metric definitions, divergent reviewer opinions, and unowned assumptions.

**Gate readiness.** Decide whether downstream work can proceed, needs clarification, or needs council review before spend.

**Emit telemetry.** Use `run-telemetry` to record context-source count, latency, missing-source count, confidence, and escalations.

## Output Contract

```markdown
# Grounding Brief

## Objective
- workflow:
- decision supported:
- scope:
- non-goals:

## Source Inventory
- source:
  type:
  pointer:
  freshness:
  relevance:

## Facts
- fact:
  evidence:
  confidence:

## Capability Reuse Inventory
- search boundary:
- sources checked:
- candidate:
  kind:
  owner/consumers:
  access contract/deployment boundary:
  result: reuse | cannot-satisfy | clarify-first
  evidence:
- inventory status: complete | clarify-first | no-candidate
- clarification needed:
  owner:
  requested fact:
  resume condition:

## Prior Decisions And Context
- decision/context:
  source:
  implication:

## Constraints
- constraint:
  source:
  impact:

## Risks And Unknowns
- risk/unknown:
  why it matters:
  owner or next check:

## Proceed Gate
- status: ready | clarify-first | council-first | blocked
- reason:
- next workflow step:
- new infrastructure proposal allowed: yes | no
```

## Success Criteria

- key claims have source pointers.
- infrastructure proposals include a completed capability reuse inventory.
- plausible capabilities with unknown access return `clarify-first`.
- downstream agent can proceed without re-discovering intent.
- assumptions are marked, not hidden.
- stale/conflicting sources are visible.
- `run-telemetry` event emitted.

## Common Failure Modes

**Context hoarding.** Brief becomes a dump. Fix by summarizing only facts that affect next decisions.

**Semantic search overtrust.** Code RAG result sounds plausible but lacks file evidence. Fix by opening cited files or requiring source pointers.

**Memory drift.** Long-term memory contains old decisions. Fix by checking dates, superseding docs, and current git state.

**Unbounded lookup.** Agent keeps searching. Fix by time budget and readiness gate.

## Self-Improvement

When repeated missing-context patterns appear, propose new required source checks, RAG indexes, memory tags, or brief fields. Feed aggregated patterns into `backprop`; do not silently expand this skill during an active workflow.
