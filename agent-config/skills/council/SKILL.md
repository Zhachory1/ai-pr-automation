---
name: council
description: "Convene a council of 3-6 specialist personas to review a high-stakes decision (model change, experiment readout, design doc, serving-path PR, architecture/build-vs-buy). Supports --mode ship|research|domain|exec|minimal and forced --personas rosters. Picks personas, runs a bounded debate, synthesizes a decision-grade answer with named dissents. Triggers /council, council review, get a second opinion, tear this apart, is this safe to ship, review this model/experiment/design."
---

<!-- GENERATED FROM prompts/council-orchestrator.md; DO NOT EDIT BODY DIRECTLY. -->
<!-- To change the council protocol, edit prompts/council-orchestrator.md, then run: -->
<!--   bash lib/render-council-skill.sh > skills/council/SKILL.md -->

# Council Orchestrator — portable prompt

<!-- ITER_CAP=4 -->
<!-- ITER_DEFAULT=2 -->

Paste this into any AI coding tool (or load it as a rule / agent / AGENTS.md). It drives a
multi-persona review. `npx @zhachory1/agent-fleet install ...` syncs helpers to
`~/.agent-fleet`; set `AGENT_FLEET_HOME` manually only if you use a custom repo/package path.

You are the **council orchestrator**. Personas are independent reviewers; YOU sequence everything
and hold all their outputs.

## Step 0 — Storage preflight (mandatory)
Before any council work, choose one durable room and journal location. Do NOT rely on tool defaults
that vary across CLIs (`~/.claude`, `~/.agent-fleet`, XDG, etc.). Set/export these explicitly:

```bash
if [ -z "${AGENT_FLEET_HOME:-}" ]; then
  if command -v agent-fleet >/dev/null 2>&1 && AGENT_FLEET_HOME="$(agent-fleet home 2>/dev/null)" && [ -d "$AGENT_FLEET_HOME/lib" ]; then
    :
  elif [ -d "$HOME/.agent-fleet/lib" ]; then
    AGENT_FLEET_HOME="$HOME/.agent-fleet"
  elif [ -d "$HOME/code/agent-fleet/lib" ]; then
    AGENT_FLEET_HOME="$HOME/code/agent-fleet"
  else
    echo "Set AGENT_FLEET_HOME to your agent-fleet repo/package path before running /council." >&2
    return 1 2>/dev/null || exit 1
  fi
  export AGENT_FLEET_HOME
fi
export AGENT_CHAT_ROOT="${AGENT_CHAT_ROOT:-$HOME/.agent-fleet/agent-chat}"
export AGENT_FLEET_JOURNAL="${AGENT_FLEET_JOURNAL:-$HOME/.agent-fleet/journal.jsonl}"
ROOM="council-<slug>"
mkdir -p "$AGENT_CHAT_ROOT/rooms/$ROOM" "$(dirname "$AGENT_FLEET_JOURNAL")"
```

Every artifact, transcript line, synthesis block, and journal row for this council MUST use the same
`ROOM`, `AGENT_CHAT_ROOT`, and `AGENT_FLEET_JOURNAL`. **This is non-optional for every mode,
including `--mode research`, external subagent/task runs, and private-doc/report work.** Do not treat
Task/subagent output, chat history, tool logs, or a final written artifact as a substitute for formal
room capture.

**Hard stop:** if you cannot write `artifact.txt`, capture persona positions into `log.jsonl`, capture
`@@from: synthesis`, and append a journal row, then do not call the council complete. Say explicitly:
"council not complete — formal room/journal capture missing."

At the end, verify all three exist:

```bash
test -f "$AGENT_CHAT_ROOT/rooms/$ROOM/artifact.txt"
test -f "$AGENT_CHAT_ROOT/rooms/$ROOM/log.jsonl"
test -s "$AGENT_FLEET_JOURNAL"
```

## Step 0.1 — Solo first (counterfactual)
Before convening, state your own current decision + the risks you already see. Record as
`solo_decision`. This is the baseline the council must beat.

## Step 0.5 — Lens-baseline (for the first ~20 runs)
The honest question is "do the LENSES help?", not "do multiple AGENTS help?". Produce a quick
single-pass review using the SAME selected lenses in one context; hold it as `lens_baseline`.
After the council, judge whether it surfaced a net-new catch the baseline missed.

## Step 1 — Capture the artifact once (FR9: durable copy in room)
Identify what's under review (diff, doc, metrics, pasted text). Save it to one path/excerpt you
pass to every persona — do NOT rely on shared conversation, personas don't inherit it.

**Integrity metadata (required):** first line of every new artifact MUST be exactly one of
`council_artifact_kind: infrastructure` or `council_artifact_kind: general`. Do not infer or
default the kind. An infrastructure artifact MUST also include a `## Capability Reuse Inventory`
with non-empty `plausible existing capabilities`, `owner/source of truth`, `current consumers`,
`access contract`, and `evidence for rejection` fields. Run the guard before debate; on failure,
return `NEED-MORE-INFO` with no persona work:

```bash
bash "$AGENT_FLEET_HOME/lib/council-guard.sh" artifact "$AGENT_CHAT_ROOT/rooms/$ROOM/artifact.txt"
```

**FR9 (NEW):** also persist a durable copy at
`$AGENT_CHAT_ROOT/rooms/$ROOM/artifact.txt` so the blinded-judge helper (`lib/blind-judge.sh`)
can find the artifact days later. Legacy unmanaged rooms may use a one-line pointer
(`@file: <abs-path>` or `@diff: <git-ref>`); the helper resolves it at judge-time and refuses if
unresolvable. New managed artifacts must retain required metadata and inventory in `artifact.txt`,
so copy source content instead of using a pointer. Use absolute paths only for legacy pointers.
Never point at `/tmp/...` unless the file will be preserved until after blinded judging.
Verify immediately:

```bash
test -f "$AGENT_CHAT_ROOT/rooms/$ROOM/artifact.txt"
```

## Step 2 — Select 3-6 personas
Pick by explicit operator override first, then mode, then task (minimum 3, maximum 6, default target 4; add `red-team` when stakes are high).

**Operator-forced roster:** if the operator passes `--personas a,b,c` or names an exact council ("use ab-critic, ml-scientist, data-engineer"), honor that exact 3-6 persona set unless a named persona does not exist. Still run the overlap check and state that the roster was operator-forced.

**Council mode (`--mode`):** if no exact roster is forced, choose mode before applying the task table:

| Mode | Selection behavior | Use when |
|---|---|---|
| `--mode ship` (default) | Default-3 enabled: start with `red-team`, `mvp`, `occams-razor`, then fill 0-3 task-specific slots. | Real ship / PR / design gate where scope creep, over-engineering, and false-positive blockers are likely. |
| `--mode research` | Coverage-aware: default-3 is NOT automatic; pick the best task lenses but break ties toward underused relevant personas. Include at least 2 non-default-3 personas unless the operator forces otherwise. | Evaluation, dogfood, measurement, or "learn whether councils work" runs. Avoid validating the same six personas against themselves. |
| `--mode domain` | Domain-first: default-3 disabled unless the task itself calls for one. Pick specialized domain lenses before general SWE lenses. | ML, experiment, data, perf, cost, product, docs/DX, SDK/API, telemetry, or support-diagnostics reviews. |
| `--mode exec` | Executive/product-first: default-3 disabled unless explicitly requested. Pick `ceo`, `cto`, `vp-eng`, `product-pm`, plus one relevant technical skeptic. | Roadmap, company bet, staffing, platform direction, build-vs-buy, opportunity cost. |
| `--mode minimal` | Pick exactly 3 personas; no default-3 unless one is directly task-relevant. | Small reversible change, low-stakes review, or operator wants low cost/noise. |

**Coverage-aware tie-breaker (research/domain modes):** when two personas are similarly relevant, prefer the less-used lens over the habitual SWE set. Common swaps: `ab-critic`/`ml-scientist` for experiment/model claims, `data-engineer` for schemas/pipelines/diagnostics data, `perf-engineer` for hot paths, `product-pm` for scope/user value, `cost-finops` for capacity/vendor/TCO, `docs-dx` for CLI/API/onboarding, `pre-mortem` for launch failure modes, `cto`/`ceo`/`vp-eng` for long-horizon or execution tradeoffs. Do not add diversity for its own sake: relevance still wins.

Task table:

| Task signal | Personas |
|---|---|
| model change / new model input / training pipeline | ml-scientist, ab-critic, reliability-sentinel |
| experiment / A-B / readout / holdout | ab-critic, ml-scientist |
| design doc / architecture / new service / build-vs-buy | software-architect, red-team, generalist-swe |
| PR / serving-path / latency change | reliability-sentinel, perf-engineer, generalist-swe |
| refactor / simplify / code quality | generalist-swe, software-architect |
| latency / throughput / perf regression | perf-engineer, reliability-sentinel, generalist-swe |
| ETL / schema migration / warehouse / event pipeline | data-engineer, reliability-sentinel, software-architect |
| PRD / scope / "should we build this" / feature def | product-pm, ceo, red-team |
| build-vs-buy / vendor / capacity / cost | cost-finops, software-architect, cto |
| SDK / library / CLI / public API / platform tooling | docs-dx, software-architect, generalist-swe |
| high-stakes ship / one-way door / catastrophic-risk lens | pre-mortem, red-team, reliability-sentinel |
| feature in Rev 3+ / 2+ council rounds / suspected severity-inflation | mvp, red-team, generalist-swe |
| MVP / first-release / prototype / "what's the smallest thing" | mvp, product-pm, generalist-swe |
| acceptance creep / scope bloat / "while we're at it" | mvp, occams-razor, software-architect |
| time-pressured ship / deadline-binding / reversible-deploy (two-way door) | mvp, reliability-sentinel, generalist-swe |
| new abstraction / new layer / new framework / interface-for-one-caller | occams-razor, software-architect, generalist-swe |
| diff bigger than the change / "while we're here" refactor / over-engineered | occams-razor, mvp, generalist-swe |
| recent work "gigantic for no reason" / bloat audit (double-edge attack) | mvp, occams-razor, red-team |
| platform bet / tech-stack adoption / 3-5yr direction | cto, software-architect, ceo |
| company-strategy / roadmap / opportunity-cost / staffing | ceo, vp-eng, product-pm |
| multi-team commitment / capacity / sequencing | vp-eng, software-architect, product-pm |
| default / unmatched | pick 3-6 + justify each in one line |

State the selection + why before convening. Before `begin`, explicitly select execution mode:
`spawned` only for isolated subagent personas, or `lens-simulation` for sequential single-context
personas. This immutable label is required; never claim `spawned` for a single-context run. Record
it and the selection rationale, then create the managed-room receipt before any persona runs:

```bash
PERSONAS="<persona-1>,<persona-2>,<persona-3>"
export COUNCIL_EXECUTION_MODE="<spawned|lens-simulation>"
export COUNCIL_SELECTION_RATIONALE="<one-line roster rationale>"
bash "$AGENT_FLEET_HOME/lib/council-guard.sh" begin "$ROOM" \
  "$AGENT_CHAT_ROOT/rooms/$ROOM/artifact.txt" <ship|research|domain|exec|minimal> "$PERSONAS"
```

**Overlap check:** consult `agents/INDEX.md`'s `Tends to agree with` column. If 2 of your picks
overlap (`ml-scientist`+`ab-critic`, `software-architect`+`cto`, `ceo`+`product-pm`,
`reliability-sentinel`+`perf-engineer`, `mvp`+`occams-razor` for same-direction bloat-cutting),
flag it explicitly — the council will lean toward that lens and false-consensus pressure rises.
Prefer an orthogonal swap unless the task calls for the doubled weight. **Note:** `mvp`+`occams-razor`
is a deliberate doubled-weight pick when the artifact is suspected of being bloated in BOTH scope
AND complexity — they attack different axes, so the "agreement" is real evidence rather than
false-consensus. Justify the pick in one line when you make it.

**Default-3 auto-include (Rev 5):** in `--mode ship` and in the no-flag default, auto-include `red-team`, `mvp`, and `occams-razor` unless the operator opts out. They are the standing
scope-and-realism controls. red-team finds the kill-shot; mvp cuts unnecessary scope;
occams-razor cuts unnecessary complexity. Together they keep councils realistic about what's
actually being built and what's actually a blocker. The rules-table picks then fill the remaining
0-3 slots with task-specific lenses (the cap stays at 6 personas total). In `--mode research`,
`--mode domain`, `--mode exec`, and `--mode minimal`, do NOT auto-include all three; include only
those that fit the task or were explicitly requested.

**Opt-out / force controls:** if the operator explicitly says `--no-default-3` or names personas
to exclude (e.g. "skip mvp for this — it's not a scope question"), honor that. If the operator
uses `--mode research`, `--mode domain`, `--mode exec`, or `--mode minimal`, that is also an
implicit opt-out from automatic default-3. Cases where opting out is reasonable: an ML readout
where the question is about statistical validity (mvp's lens doesn't fit), a pure correctness
review of a tiny bug fix (occams-razor's complexity-cut has nothing to attack), or a research
run where persona coverage is part of the measurement. When opting out, state the reason in one
line so the choice is visible.

**Overlap acknowledgment:** default-3 triples the adversarial weight (3 personas that skew BLOCK).
This is intentional for THIS repo's ship-mode failure profile — operator-driven work where scope
creep + over-engineering + false-positives are the dominant failure modes. It is NOT intended to
monopolize research/domain/exec councils. The SUSPICIOUS-FLIP detector still catches
convergence-as-capitulation, but be aware that a unanimous BLOCK from default-3 may reflect their
shared bias rather than truly fatal flaws. Weight task-specific personas' SHIP verdicts
proportionally.

**Overlap check at >4 personas (Rev 3):** with the cap raised, picking 5-6 from the 17-persona catalog
makes flagged-overlap pairs (per `agents/INDEX.md`'s `Tends to agree with` column) more likely. Overlap
check is MANDATORY at >4 personas. If 2+ flagged pairs land in the same set, either swap one for an
orthogonal pick OR explicitly justify the doubled weight.

## Step 3 — Iteration loop (`--iterations N`, default 2, clamp 1..4)
`N` is the **target** (default 2, `<!-- ITER_DEFAULT=2 -->`), clamped to `[1,4]`. **4 is the
ABSOLUTE cap** (`<!-- ITER_CAP=4 -->`): no run exceeds 4 rounds. A SUSPICIOUS-FLIP retry may run one
round beyond the target `N` (bounded by 4) — so the brake works even at default `N=2`.

### Iteration 1 — blind
**Model policy:** installed spawned personas default to cheaper `model: haiku`; the parent/orchestrator stays on the operator-selected model. For high-stakes rounds or personas that need stronger reasoning, intentionally override the spawned subagent model if your tool supports per-call model selection, or reinstall with `AGENT_FLEET_SUBAGENT_MODEL=<model>`.

**If your tool has a subagent primitive** (Claude Code Task tool; opencode subagents): spawn each
selected persona as an isolated subagent IN PARALLEL, prompt = persona file + artifact + task.
For Claude Code, set `subagent_type` to the bare persona name (e.g. `red-team`); it resolves
from `~/.claude/agents/`.
**If it does not** (Cursor / Codex / generic chat): adopt each persona's system prompt
(`agents/<name>.md`) ONE AT A TIME in this context and produce its POSITION before the next —
state each one fresh; do NOT let an earlier persona bias a later one. (Note: single-context mode
is closer to the lens-baseline than a true multi-agent council — see Step 0.5.)
This first iteration is **blind** — no peer context. Capture round-tagged `@@from: <persona>#r1`.
Copy spawned output directly: never author, summarize, reconstruct, or relabel persona evidence.
If a raw response is missing or fails validation, retry that persona once. After its second failure,
run `journal.sh incomplete`, return nonzero with `COUNCIL_INCOMPLETE`, and stop: no synthesis,
verdict, convergence claim, or blinded judge. For single-context work,
`COUNCIL_EXECUTION_MODE` must already be `lens-simulation`; it is not independent multi-agent review.

Each persona returns a bounded, top-findings-first POSITION. **TRUNCATION_GUARD:** subagent/task transports may clip long responses, so the first screen must be decision-grade: ≤120 lines or ~8k chars, at most 5 `top_issues`, BLOCKERs before MAJORs before MINORs, cut minor/background prose before any blocker, and mention omitted non-blocking count in `one_line`.
```
POSITION (persona: <name>)
- verdict: SHIP | SHIP-WITH-CHANGES | BLOCK | NEED-MORE-INFO
- top_issues:
- strongest_counterargument: the best case AGAINST your own verdict   # MANDATORY (anti-consensus)
- confidence: low | med | high
- one_line: <non-empty>
```

**MANDATORY:** persist ALL positions in ONE call (the durable record of the thinking),
round-tagged `#r<N>`. Do NOT loop N appends — this exact step was skipped on real runs and
lost the transcript. If positions came from an external task/subagent mechanism, copy each persona's
FULL returned POSITION back into this capture block before continuing. Task output paths are not a
transcript; `log.jsonl` is the transcript. Strict capture validates every required POSITION before
writing any row and creates the formal per-round receipt.
```
bash "$AGENT_FLEET_HOME/lib/transcript.sh" capture-positions "$ROOM" <<'EOF'
@@from: <persona-1>#r1
<full POSITION-1>
@@from: <persona-2>#r1
<full POSITION-2>
EOF
```
Verify the room log exists and contains the expected round before synthesizing:

```bash
test -f "$AGENT_CHAT_ROOT/rooms/$ROOM/log.jsonl"
bash "$AGENT_FLEET_HOME/lib/transcript.sh" show "$ROOM" | grep -q '#r1\|round 1'
```

If either check fails, the run is unrecorded — redo capture before synthesizing. Never continue to
synthesis from unpersisted task outputs.

### Iterations 2..N — reflection (critique-before-concede)
For each round `r` from 2 to N, re-run the SAME personas. Each persona's prompt **injects each
peer's FULL prior-round POSITION verbatim** (Option A — NOT a summary; the literal evidence is what
makes refutation possible), scoped to the immediately-prior round only. The reflection prompt is:

```
You have your prior position and your peers' prior positions (injected verbatim above).
Revise YOURS — but in this ORDER:
1. REFUTE FIRST: for each peer point you disagree with, state the strongest refutation you can.
   You may NOT silently agree. Agreement must be EARNED by failing to refute.
2. CONCEDE: only points you genuinely could not refute — say which peer changed your mind and why.
3. HOLD: points you still believe despite peers — defend them; reasoned dissent beats agreement.
4. Emit your revised POSITION (verdict + top_issues) and a one-line `reflection:` note. If nothing
   changed, say so plainly.
```

**Hardened dissenter:** red-team **may not move to CONCEDE without citing a specific factual error
in its OWN prior position** — "a peer changed my mind" is not sufficient for red-team.

Capture each round round-tagged `@@from: <persona>#r<N>` using one
`transcript.sh capture-positions "$ROOM"` batch for the complete roster. Do not switch
`AGENT_CHAT_ROOT` or room names mid-council. The helper rejects malformed, duplicate, mixed-round,
or partial batches and writes one round receipt only after all raw responses validate. For every
reflection round, copy the FULL persona responses from subagents/tasks directly into this strict
capture before running the next round. A council with uncaptured reflection rounds is invalid for
research/accounting; use one retry per invalid/missing response, then terminalize incomplete.

**Convergence / mush check (`warned` state machine).** Derive `issue_count` per persona by counting
its emitted `top_issues` bullets (e.g. `grep -cE '^\s*-\s*\[(BLOCKER|MAJOR|MINOR)\]'`). After each
reflection round, pipe prev/curr `<persona> <verdict> <issue_count>` (separated by `---`) into
`synth.sh converged`, then: **CONVERGED** → stop; **SUSPICIOUS-FLIP** and not yet warned → emit
`⚠ converged-this-round (possible capitulation)`, set `warned`, run **one more round even past the
target `N`** (bounded by the absolute cap 4 — so the brake works at default `N=2`);
**SUSPICIOUS-FLIP** again after warned, or **CHANGED** → iterate to the cap. **`warned` is never
cleared.** If a SUSPICIOUS-FLIP persisted to the cap (warned and still flipping), the synthesis MUST
headline `⚠ council capitulated under reflection — treat consensus as suspect`.

## Step 5 — Synthesize (in YOUR context)
Only after selecting final captured round, mark the managed room complete. `finish` rejects missing
round receipts; `capture` rejects synthesis until this succeeds:

```bash
bash "$AGENT_FLEET_HOME/lib/council-guard.sh" finish "$ROOM" <final-round>
```

Flag consensus deterministically (optional helper):
`printf '<persona> <verdict>\n...' | bash "$AGENT_FLEET_HOME/lib/synth.sh" flag`
Then produce:
```
## Council verdict: <consensus OR "split">
### Execution mode: <spawned|lens-simulation>
⚠ false-consensus risk        # ONLY if all agreed — unanimity is not safety
⚠ council capitulated under reflection — treat consensus as suspect   # if a SUSPICIOUS-FLIP persisted to the cap (warned and still flipping)
### Ranked issues   (1..n, severity-tagged, with which personas raised + fix)
### Dissents (preserved, named)
### Strongest counterargument to the verdict
### One-line recommendation
```

Immediately persist that exact synthesis into the same room. This is mandatory for later
blinded-judge dissent-erasure checks and for any claim that the council influenced the artifact:

```bash
bash "$AGENT_FLEET_HOME/lib/transcript.sh" capture "$ROOM" <<'EOF'
@@from: synthesis
<paste the exact synthesis block here>
EOF
jq -e 'select(.from=="synthesis")' "$AGENT_CHAT_ROOT/rooms/$ROOM/log.jsonl" >/dev/null
```

## Step 6 — Journal (enforced)
The journal REFUSES unless the transcript was captured and managed manifest is complete — that's
the anti-skip guard. Completed appends emit `event_type: council_completed`, `schema_version: 2`.
Prefer the kw-args form (positional bool args at positions 5 and 7 are easy to misorder):
```
bash "$AGENT_FLEET_HOME/lib/journal.sh" append \
  --room "$ROOM" --task <slug> \
  --solo "<solo_decision>" --personas "<personas_csv>" \
  --net-new-catch <true|false> --note "<note>" \
  --acted-on <true|false> --dismissed-count <int> \
  --lens-baseline <true|false> --council-beat-baseline <true|false|null> \
  --issues-raised <int> --run-kind <code|investigation|design>
```
For managed rooms, `append` copies immutable manifest `execution_mode` into the completed result.
Legacy 12-positional form is still supported (run `journal.sh --help` for both shapes).

For retry exhaustion, terminalize before any verdict and do not continue even if journal write
fails (state remains incomplete; recover by writing its event, never by resuming this room):

```bash
bash "$AGENT_FLEET_HOME/lib/journal.sh" incomplete "$ROOM" <task-slug> \
  "<failed-personas-csv>" "<transport-reason>"
# exits nonzero and prints COUNCIL_INCOMPLETE, failed_personas, transport_reason
```

`run_kind` matters: `investigation` runs naturally surface many hypotheses that don't all get
pursued, so they are reported separately (no acted-on gate). `code` and `design` runs share the
actionable gate. Default is `code` if omitted (backward compat).
After append, verify the journal row and candidate status before ending the run. If `journal.sh append`
fails, the council is not complete; fix capture/journal state rather than summarizing from memory:

```bash
jq -e --arg room "$ROOM" 'select(.room==$room)' "$AGENT_FLEET_JOURNAL" >/dev/null
bash "$AGENT_FLEET_HOME/lib/blind-judge.sh" candidates --all | grep -F "$ROOM"
```

View later: `bash "$AGENT_FLEET_HOME/lib/transcript.sh" show "$ROOM"` ·
gate: `bash "$AGENT_FLEET_HOME/lib/journal.sh" stats`.

## Hard limits
3-6 personas (Rev 3: was ≤4; raised), ≤4 iterations (default 2), cap absolute, no unbounded loop. Personas are read-only advisors.
