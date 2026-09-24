# Council: Direct-Kanban PR Safety

- status: pass with changes for inert implementation;
- production launch: blocked pending plan gates and human approval;
- formal room: `council-pr-safety-direct-kanban`;
- rounds: 2;
- personas: software architect, reliability sentinel, product PM, red team;
- artifact: `PRD-pr-safety-direct-kanban.md`, `DD-pr-safety-direct-kanban.md`, pinned CLI observation;
- delta checker after required revisions: pass.

## Verdict

Proceed through the reversible technical plan. Do not activate direct production routing or delete the current bridge/controller path until fault, evaluation, policy, human-flow, canary, bake, rollback, and deletion gates pass.

## Required Changes Applied

1. Replaced timestamp-only mode cutoff with paginated per-author discovery cursors, 24-hour overlap, admission-before-cursor advance, and final old-engine discovery pass.
2. Split immutable finalization intent from closure receipt. Closure receipt is written last after exact five task archives verify; board removal is retention GC only.
3. Kept synthesis blocked until four one-run specialist handoffs, graph identity, and remaining deadline revalidate.
4. Defined Hermes `max-retries=1` as first-failure block with zero retries; exact one-run validation remains mandatory.
5. Assigned one source of truth per predicate: operation journal for admission/commit evidence, Kanban for tasks/claims/attempts.
6. Added pinned CLI observation and made executable retained output fixtures/preflight a blocking implementation task.
7. Added typed human disposition, Fleet critical status index/link, age SLO, fail-closed GC, disk admission floor, and typed quarantine resolution.
8. Replaced impossible confidence rule with empirical point gates plus achievable one-sided exact confidence guardrails and minimum independent cases.
9. Added matched bridge-vs-direct recovery benchmark; cleanup requires at most three commands and at least 50% median command/time reduction.
10. Reserved direct board prefixes and accepted shared-home risk only under existing single-OS trust-tier assumption.

## Named Dissents

- **Red team:** operation journal plus Kanban still creates cross-store recovery work. Simplification claim is unproven until cleanup deletes more lifecycle source than direct mode adds and fault recovery improves.
- **Software architect:** board-prefix ownership is convention, not credential isolation. Collision refusal and forbidden-mutation preflight are mandatory.
- **Product PM:** Fleet critical banner is not push notification. Named reviewer must sign off seeded discoverability/disposition journey before live canary.
- **Reliability:** quarantined effect-free specialists may finish. Admission fencing, rejected output, blocked synthesis, runtime limits, backpressure, and worker-free quarantine resolution are mandatory.

## Strongest Counterargument

Keep Postgres admission/human queue and remove only bridge transport. That retains proven transactions with less migration. Direct Kanban wins only if measured implementation evidence proves no lost/duplicate admission, lower recovery burden, acceptable human workflow, and net lifecycle deletion.

## Launch Blockers

- executable installed-runtime CLI conformance;
- complete crash/mode-switch fault matrix;
- independent 30 severe + 60 ordinary/clean evaluation and locked confidence gates;
- accepted provider policy and cost ceiling;
- named reviewer seeded incident-flow signoff;
- one approved live canary;
- 20-operation operational bake, restart, and rollback drill;
- explicit cleanup approval after rollback window.
