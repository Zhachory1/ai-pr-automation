# DD: Kanban-Backed PR Safety Analysis

- Status: draft.
- Owner: fleet operator.
- PRD: `docs/hermes/PRD-pr-safety-kanban-council.md`.
- Next gate: implementation plan after approval.

## Decision

Replace controller’s single safety Runs API call with one fixed Kanban graph.

Keep everything before and after analysis unchanged.

```mermaid
flowchart LR
    Q["Existing request"] --> P["Existing safety_preflight"]
    P --> E{"Analysis engine"}
    E -->|single rollback| R["pr-safety-v1 Runs API"]
    E -->|kanban target| B["Kanban bridge"]
    B --> K["4 Haiku specialists"]
    K --> Y["Sonnet synthesis"]
    R --> V["Existing valid_safety"]
    Y --> A["Pure controller mapping"]
    A --> V
    V --> S["Existing handoff + settlement"]
```

## Scope

### Change

- Add production v2 council contract.
- Add read-only council analysis profile policy.
- Parameterize proven council script for one immutable safety request.
- Add narrow authenticated host bridge.
- Add controller `single|kanban` analysis branch.
- Add one private pure controller function mapping synthesis to current safety schema.
- Add comparison harness and tests.

### Do Not Change

- producer;
- request kind or payload;
- Postgres request schema;
- lease, dedupe, retry, or supersession;
- snapshot or policy identity;
- top-level safety result schema;
- handoff ownership;
- incident threshold;
- settlement SQL;
- human queue or UI;
- external-effect authority.

## Ownership

| Component | Responsibility |
| --- | --- |
| Producer | Create immutable merged-PR snapshot and enqueue same request |
| Postgres/controller | Own request, lease, nonce, identity, and settlement |
| Bridge | Create, inspect, and archive exact Kanban graph |
| Gateway dispatcher | Run assigned Kanban tasks |
| Specialists | Produce typed role findings |
| Synthesizer | Combine four handoffs into one proposed safety result |
| Human | Decide incident, remediation, merge, and activation |

Bridge never accesses Postgres or external-effect systems.

## Workflow Contract V2

Create `agent-config/hermes/workflows/pr-risk-council-kanban-v2.json`.

Keep current v1 sanitized canary contract and installed profiles unchanged. V2 uses versioned profiles because read-only snapshot tools change profile bytes:

- `council-reviewer-v2`;
- `council-security-v2`;
- `council-reliability-v2`;
- `council-architect-v2`;
- `council-orchestrator-v2`.

Configurator installs/restores v2 names separately. V1 profiles and rollback evidence remain intact.

V2 graph:

```mermaid
graph LR
    R["General review\nHaiku 4.5"] --> Y["Safety synthesis\nSonnet 5"]
    S["Security/privacy\nHaiku 4.5"] --> Y
    E["Reliability/data\nHaiku 4.5"] --> Y
    A["Architecture/contracts\nHaiku 4.5"] --> Y
```

Profiles:

| Role | Profile | Model |
| --- | --- | --- |
| General | `council-reviewer-v2` | `claude-haiku-4-5-20251001` |
| Security | `council-security-v2` | `claude-haiku-4-5-20251001` |
| Reliability/data | `council-reliability-v2` | `claude-haiku-4-5-20251001` |
| Architecture/contracts | `council-architect-v2` | `claude-haiku-4-5-20251001` |
| Synthesis | `council-orchestrator-v2` | `claude-sonnet-5` |

Rules:

- one attempt per task;
- zero retries;
- no fallback provider/model;
- four specialist tasks ready in parallel;
- synthesis blocked on all four;
- no attachments or child tasks;
- typed completion metadata required;
- no production comment fallback;
- 15-minute graph deadline;
- 150,000 aggregate input/output token ceiling;
- one active workflow initially.

`council-verifier` stays available for v1 canary only. V2 does not reinterpret it.

## Snapshot Access

Same use case requires repository context, not a truncated packet.

Bridge receives already-preflighted snapshot and policy paths. It verifies:

- path under configured roots;
- no symlink escape;
- HEAD equals request head;
- clean worktree;
- recomputed diff digest;
- policy path, version, and digest;
- snapshot is not writable through exposed worker tools.

Bridge writes mode-0440 workflow inputs:

```text
workflow-input/
  identity.json
  diff.patch
  policy.md
```

Task body contains these paths plus snapshot path. Specialists may:

- `read_file` workflow input and repository files;
- `search_files` inside snapshot;
- use own-task Kanban lifecycle tools.

They may not receive:

- terminal or process tools;
- write, patch, or delete tools;
- web/browser tools;
- GitHub, CI, deploy, incident, monitor, memory, document, MCP, plugin, delegation, or connection tools;
- orchestrator Kanban create/link/unblock tools.

Pinned-runtime preflight must prove exact effective schemas are only `read_file`, `search_files`, and worker-owned Kanban lifecycle tools. If current Hermes config cannot express that exact set, stop. Add a bounded snapshot-reader tool in a separate reviewed design; do not grant broad file or terminal access.

Profiles remain one OS trust tier, not sandboxes. This change does not claim tenant isolation.

## Specialist Handoff

Exact keys:

```json
{
  "workflow_id": "string",
  "artifact_digest": "64 hex",
  "role": "review|security|reliability|architecture",
  "verdict": "clear|findings|needs_human_decision|inconclusive",
  "claims": ["bounded claim"],
  "evidence": [
    {"path": "relative/file", "line": 1, "side": "new", "quote": "bounded changed-line text"}
  ],
  "confidence": "low|medium|high",
  "dissent": [
    {
      "source_role": "security",
      "claim": "bounded disagreement",
      "evidence": [],
      "disposition": "accepted|rejected|unresolved",
      "rationale": "bounded reason"
    }
  ],
  "residual_risk": [
    {"source_role": "security", "claim": "bounded risk", "evidence": [], "requires_human_decision": true}
  ]
}
```

Evidence cites file, changed line, side, and bounded quote. Incident claims require direct changed-line citation.

## Synthesis Handoff

Exact keys:

```json
{
  "workflow_id": "string",
  "artifact_digest": "64 hex",
  "verdict": "clear|changes_requested|needs_human_decision|incident_candidate|inconclusive",
  "intent": {},
  "findings": [],
  "coverage": {},
  "documentation": {},
  "observability": {},
  "incident": {
    "candidate": false,
    "changed_line_cause": false,
    "concrete_trigger": false,
    "severe_impact": false,
    "high_confidence_chain": false,
    "stop_rollback_or_page": false,
    "evidence": []
  },
  "human_decisions_needed": [],
  "dissent": [],
  "residual_risk": []
}
```

Bridge verifies:

- exact workflow/artifact identity;
- exact five tasks and four parent IDs;
- one completed attempt per task;
- expected profile and model;
- all four structured parent handoffs;
- no failed member, attachment, child, retry, or effect-capable tool;
- deadline and budget;
- every incident citation points to changed line.

Evidence, dissent, and residual-risk item schemas are exact as shown above. Strings, arrays, quotes, and item counts have fixed bounds.

Bridge deterministically unions specialist dissent and residual risk by source role plus canonical item digest. Sonnet proposes disposition/rationale but cannot remove an item. Missing disposition becomes `unresolved`.

## Pure Controller Mapping

One private pure function receives trusted request, controller nonce, and verified synthesis package. It is not a service, class hierarchy, or package.

Function writes trusted fields:

- nonce;
- operation ID;
- repo and PR;
- head and base SHA;
- diff hash;
- policy version and digest.

Verdict mapping:

| Synthesis | Safety status |
| --- | --- |
| `clear` | `clear` only if findings/gaps/human decisions/incident evidence are empty |
| `changes_requested` | `changes_requested` |
| `needs_human_decision` | `needs_human_decision` |
| `incident_candidate` | `incident_candidate` only when all five incident conditions are true |
| `inconclusive` | `needs_human_decision` |

Function projects council detail without new top-level keys:

- `findings[]` keeps role, claim, typed evidence, confidence, dissent, and residual risk;
- `coverage.council` keeps full dissent/residual-risk ledger plus workflow/artifact identity;
- unresolved dissent and human-required residual risk enter `human_decisions_needed`.

Output then passes unchanged `normalize_safety()` and `valid_safety()`.

`write_safety_handoff()` adds one `## Council context` JSON section from `coverage.council`. Existing identity, findings, and human-decision sections remain. `hermes_settle_pr_safety_request()` stays authoritative.

## Bridge

Repo-owned Python service. Runs as `hermes-agent`. Uses pinned Hermes Python environment. Gateway remains only dispatcher.

Endpoints:

```text
POST /v1/councils
GET /v1/councils/{workflow_id}
POST /v1/councils/{workflow_id}/stop
POST /v1/councils/{workflow_id}/archive
GET /healthz
```

Authentication:

- dedicated generation-bound HMAC key;
- loopback bind;
- exact Host and content type;
- reject Origin and CORS;
- timestamp and nonce replay window;
- sign request and response body digests;
- 1 MiB body limit;
- mode-0600 key/state files.

Create request binds:

- operation ID;
- repo/PR/head/base/diff identity;
- policy identity;
- snapshot and policy paths;
- workflow contract digest;
- profile generations;
- pinned Hermes runtime digest;
- absolute deadline.

Bridge derives workflow and task idempotency keys. Caller cannot choose them.

Responses:

- `201`: graph created;
- `200`: exact replay or status;
- `409`: same identity with different bytes, active archive, or replay nonce;
- `422`: path/profile/model/tool/runtime/graph preflight failure;
- `429`: another workflow active;
- `404`: unknown workflow;
- `410`: archived tombstone;
- `503`: Kanban store unavailable.

No bridge queue. Controller holds one safety request at a time.

Stop is authenticated and idempotent. It marks `stopping`, terminates active Kanban worker processes through pinned runtime APIs, terminalizes unfinished tasks, confirms no worker remains, then returns `stopped`. If confirmation fails, controller cannot recover lease or start replacement analysis.

## Durable State And Recovery

Bridge writes state before board mutation:

```json
{
  "schema_version": 1,
  "phase": "creating|active|stopping|stopped|terminal|archiving|archived",
  "workflow_id": "operation-derived",
  "operation_id": "string",
  "artifact_digest": "64 hex",
  "contract_digest": "64 hex",
  "profile_generations": {},
  "runtime_digest": "64 hex",
  "task_ids": {},
  "created_at": 0,
  "deadline_at": 0,
  "result_digest": null
}
```

Atomic write: temporary file, file fsync, rename, parent-directory fsync.

Recovery:

- `creating`: resume only missing idempotency-bound tasks.
- `active`: inspect same graph and continue polling.
- deadline or lease loss: enter `stopping`; stop and confirm all workers before terminal failure.
- `stopped|terminal`: return same terminal state and verified result digest when present.
- archive: persist `archiving`, remove exact board idempotently, then persist retained `archived` tombstone.
- restart in `archiving`: detect board presence; remove exact matching board if present; persist same tombstone.
- `archived`: return tombstone. Never delete tombstone during rollback window.
- state/board identity mismatch: fail closed and require report-only reconcile command.

Controller recovery uses no new table. Kanban claim uses a security-definer claim function that atomically claims request and writes `run_id='kanban:' || workflow_id` in same transaction. No claimed-but-unbound window exists. Recovery branches from persisted prefix, never current engine config. Engine changes affect new claims only.

Extend open-attempt query to include settled-but-not-closed Kanban attempts. Split safety postprocess:

1. validate and settle exact result;
2. archive exact workflow;
3. call `hermes_complete_effect_attempt` only after archive/tombstone confirmation.

Crash after settlement resumes archive from persisted Kanban run marker, then closes attempt. Settlement nonce/status fence prevents repeat. Controller keeps renewing same lease before settlement.

## Controller Change

```text
PR_SAFETY_ANALYSIS_ENGINE=single|kanban
```

### Single

Current code path unchanged:

- build safety prompt;
- submit `pr-safety-v1` Runs API request;
- poll;
- parse safety JSON;
- existing postprocess.

### Kanban

- claim request and persist `kanban:<workflow_id>` atomically;
- run existing `safety_preflight()`;
- call bridge create with stable identity;
- poll signed status while renewing Postgres lease;
- map verified terminal package;
- call new settlement-only helper containing current `postprocess_safety()` validation, handoff, and `hermes_settle_pr_safety_request()` logic but not `hermes_complete_effect_attempt`;
- archive exact workflow and confirm tombstone;
- call `hermes_complete_effect_attempt`.

Current `postprocess_safety()` wrapper keeps single-engine behavior by calling settlement-only helper and then immediately completing attempt.

On deadline or lease loss, controller calls bridge stop and requires confirmed worker termination before lease recovery or terminal failure. No automatic single-agent retry or hidden fallback.

Rollback changes engine to `single` for new claims. Persisted `kanban:` attempts continue Kanban recovery regardless of process config. Operator may instead stop and terminally settle them before restart.

## Policy

Current policy says OpenAI only. Current safety profile and target council use Anthropic.

Before real PR cutover, human-reviewed policy must approve:

- `claude-haiku-4-5-20251001` specialists;
- `claude-sonnet-5` synthesis;
- no provider/model fallback;
- provider retention terms;
- local Kanban artifact retention and purge.

Policy change is separate PR and human decision.

## Security

- Treat policy-adjacent repo files, code, comments, paths, and handoffs as untrusted data.
- Model never supplies trusted identity.
- Bridge checks installed profile/config/runtime digests before create and after completion.
- Worker toolset is exact read/search plus own-task Kanban lifecycle.
- Bridge has no Postgres, GitHub, memory, doc, CI, deploy, incident, or infrastructure access.
- Controller has no direct Kanban SQLite mount.
- Raw code stays in existing snapshot plus local Kanban/workflow state. No shared memory or logs.
- Purge workflow inputs/board after settlement and audit metadata export.
- Keep `journal_mode=DELETE` until linked SQLite is patched.

## Reliability And Operations

Signals:

- safety request queue depth/age;
- bridge availability/errors;
- active workflow age;
- task state/attempt/profile/model;
- workflow completion/failure/deadline;
- pure mapping rejection;
- token/cost by role;
- cleanup pending;
- SQLite busy time/disk/integrity.

Stop conditions:

- wrong profile/model/toolset/runtime generation;
- any fallback;
- duplicate task attempt;
- missing/invalid handoff;
- lost dissent;
- stale artifact;
- token ceiling exceeded or usage missing;
- workflow beyond 15 minutes;
- SQLite corruption.

Operator recovery remains explicit. No Postgres watchdog. Add bridge status/reconcile/archive commands before cutover.

## Tests

### Unit

- v2 contract and exact graph;
- profile effective tool schemas;
- specialist/synthesis metadata;
- dissent union;
- incident five-condition gate;
- adapter identity and verdict mapping;
- bridge HMAC, replay, body, path, idempotency, and error handling;
- recovery states and deadline;
- engine selection.

### Integration

- immutable snapshot to five-task terminal graph;
- four-parent fan-in;
- one attempt each;
- exact models and zero fallback;
- controller lease renewal while polling;
- controller restart during create/running/terminal/archive;
- same handoff and incident-only settlement as single path;
- bridge cannot access effects;
- legacy `single` route unchanged.

### Evaluation

Run both engines on same labeled snapshots, three repetitions. Score current safety metrics, latency, tokens, cost, variance, and blind human preference.

## Rollout

1. Merge design.
2. Add v2 contract and read-only profile preflight.
3. Add parameterized graph and pure controller mapping.
4. Add bridge and fault tests.
5. Add controller engine branch, default `single`.
6. Run baseline comparison.
7. Human approves policy and cutover evidence.
8. Set engine `kanban`; verify one live request.
9. Keep `single` as immediate rollback.

No merge alone activates Kanban analysis.

## Rejected Complexity

- Adaptive triage.
- Conditional specialists.
- New request kinds.
- New route/lineage database.
- Controller percentage rollout.
- New UI.
- Kanban effect handoff.
- Broad worker terminal/file access.
- Replacing current settlement schema.

## Design Stop Conditions

Return to design if:

- exact read-only worker toolset cannot be enforced;
- bridge needs Postgres or effect credentials;
- current safety schema cannot carry council findings;
- pinned Hermes cannot stop deadline-expired tasks safely;
- fixed graph fails quality or cost gate.
