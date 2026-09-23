# Grounding Brief: Adaptive PR Safety Kanban Council

## Objective

- Workflow: deep plan-to-launch.
- Decision: replace only `pr-safety-v1` model analysis with adaptive Hermes Kanban analysis.
- Scope: immutable merged-PR snapshot through typed analysis result.
- Non-goals: replace producer, Postgres request ledger, strict result schema, incident settlement, human queue, or any external-effect fence.

## Source Inventory

| Source | Type | Pointer | Freshness | Relevance |
| --- | --- | --- | --- | --- |
| Current controller | Code | `scripts/hermes-controller.py` | Current `origin/main` | Owns preflight, Runs API, strict output, handoff, settlement |
| Safety SQL | Code | `docker/initdb/11-hermes-pr-safety.sql` | Current `origin/main` | Owns event dedupe, retry cap, supersession, incident-only atomic queueing |
| Safety policy | Policy | `policy/pr-safety-policy-v1.md` | Current `origin/main` | Defines review and incident thresholds |
| Council graph | Code | `scripts/hermes-kanban-risk-council.py` | Current `origin/main` | Proves four-parent fan-in, structured handoffs, bounded fallback, archive |
| Council contract | Config | `agent-config/hermes/workflows/pr-risk-council-kanban.json` | Current `origin/main` | Locks models, profiles, graph, deadline, token budget |
| Restricted profiles | Code | `scripts/configure-hermes-kanban-profiles.py` | Current `origin/main` | Locks model, fallback, memory, plugins, tools, retries |
| Eval contract | Eval | `evals/manifest.json`, `scripts/hermes-eval.py` | Current `origin/main` | Locks safety metrics and zero-tolerance gates |
| Multi-agent plan | Prior plan | `~/private-docs/projects/ai-pr-automation/plans/2026-09-22-hermes-multi-agent-workflow-plan.md` | Latest operator plan | Records completed feasibility, profiles, canary, graph, remaining eval/effect work |
| Migration roadmap | Prior decision | `~/private-docs/projects/ai-pr-automation/plans/2026-09-14-hermes-migration-roadmap.md` | Active architecture | Keeps Postgres/controller/publisher authority and treats PR-safety replacement as optional/high-risk |
| Live validation record | Operator evidence | User-provided session context; PRs #220–#237 | Latest reported state | Graph terminal and verified; one attempt per task; no effects; board archived |

## Facts

- Producer already creates immutable local snapshots and binds `operation_id`, repo, PR, head, base, diff digest, policy version, and policy digest.
  - Evidence: `bin/hermes-pr-safety-producer`, `docker/initdb/11-hermes-pr-safety.sql`.
  - Confidence: high.
- Controller rejects invalid snapshot or policy identity before model execution.
  - Evidence: `Controller.safety_preflight()` in `scripts/hermes-controller.py`.
  - Confidence: high.
- Controller accepts only exact top-level safety keys and matching identity/nonce. Incident status and `incident.candidate` must agree.
  - Evidence: `valid_safety()` and `normalize_safety()` in `scripts/hermes-controller.py`.
  - Confidence: high.
- SQL atomically settles request and queues human review only for incident candidates.
  - Evidence: `hermes_settle_pr_safety_request()` in `docker/initdb/11-hermes-pr-safety.sql`.
  - Confidence: high.
- Current Kanban graph is a sanitized operator-run proof, not a production request adapter.
  - Evidence: fixed `FIXTURE`, `WORKFLOW_ID`, and board in `scripts/hermes-kanban-risk-council.py`.
  - Confidence: high.
- Gateway-embedded Kanban dispatcher already executes assigned tasks. A new host dispatcher is not needed.
  - Evidence: pinned Hermes `gateway/kanban_watchers_dispatcher.py`; prior live graph.
  - Confidence: high.
- Compose controller cannot safely import or write host Hermes SQLite. Production integration needs a narrow authenticated host bridge or a new supported Hermes API.
  - Evidence: Compose boundary in `docker-compose.yml`; Kanban script imports host `hermes_cli` modules.
  - Confidence: high.
- Current policy says OpenAI only, while current safety profile and planned council models use Anthropic.
  - Evidence: `policy/pr-safety-policy-v1.md`; `agent-config/hermes/profiles/pr-safety-v1/config.yaml`; council workflow contract.
  - Confidence: high.

## Prior Decisions And Implications

| Decision | Source | Implication |
| --- | --- | --- |
| Postgres/controller remains domain authority | Migration roadmap and operator context | Kanban cannot settle requests or enqueue human/effect work |
| Kanban is workflow backbone | Multi-agent plan | Do not revive Bot Mode or `delegate_task` |
| Human owns merge, rollout, and effects | Operator context | Analysis output remains a draft; rate changes need human approval |
| No Docker socket or SSH-back | Operator context | Compose-to-host integration must use a narrow authenticated API |
| No Opus or provider fallback | Workflow contract | Every task validates exact model/provider and zero fallback |
| Adaptive analysis | Operator context | Cheap Haiku triage runs on every snapshot; full council runs only after material-risk escalation |
| Sonnet only on escalated cases | Operator context | Deterministic code creates graph; Sonnet synthesizes escalated specialist evidence |
| Existing safety contract survives | Operator context | Adapter must emit exact `valid_safety` shape; SQL settlement stays unchanged |
| Direct-effect uncertainty means reconcile | Operator context | Council has no direct effects; any later effect handoff still uses existing controller reconcile rules |

## Constraints

- Default route remains legacy until shadow gates pass.
- Same immutable snapshot and policy identity feed baseline and candidate.
- Three repetitions per case.
- Severe-incident recall: 100%.
- Incident-candidate precision: at least 90%.
- Ordinary finding promoted to incident: at most 5%.
- Identity, stale-input, unauthorized-effect, and duplicate-effect failures: zero.
- Council workers get no GitHub, memory-write, document-write, CI, deploy, or infrastructure capability.
- One active production council at first. Deadline: 15 minutes. Token ceiling: 150,000.
- Every config/code change lands through a PR. Human merges and approves activation.
- Legacy `pr-safety-v1` remains immediate rollback.

## Risks And Unknowns

| Risk or unknown | Why it matters | Owner or next check |
| --- | --- | --- |
| Provider policy contradicts actual/planned Anthropic use | Launch would violate pinned policy even if code works | Human approves policy v2 before any candidate result can settle |
| No production Kanban bridge exists | Controller cannot create/read graph across host boundary | Design and test narrow bridge before live shadow |
| Corpus is too small | Twelve total cases cannot support safety cutover claim | Add labeled historical safety replays and severe controls before comparison |
| Triage can suppress needed council | Cheapest stage becomes single point of false-negative failure | Score escalation recall; fail cutover on any missed severe case |
| Model output is not authority | Synthesizer can forge identity or over-promote incident | Deterministic adapter copies identity, validates typed evidence, and owns final schema |
| SQLite WAL-reset vulnerability | Workflow durability risk under affected linked SQLite | Keep `journal_mode=DELETE`; block WAL until patched SQLite |
| Crash after graph creation or completion | Can leak active board or duplicate graph | Idempotent workflow key, durable bridge state, recovery poll, archive only after settlement |
| Cost/latency lift unknown | Council may not justify complexity | Measure per-route tokens, wall time, model calls, and human preference |

## Proceed Gate

- Status: council-first.
- Reason: high-risk cross-boundary design; policy/provider conflict and recovery semantics need explicit review.
- Next step: PRD and DD, then full architecture/reliability/security/MVP council. Implementation starts only after pass or pass-with-required-changes and human merge.
