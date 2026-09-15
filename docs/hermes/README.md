# Hermes Migration

Status: M0 merged. M2a non-routing foundation at machine-gate validation. M2b live activation and M1 blocked.

## Artifacts

- [Roadmap](../hermes-migration-roadmap.md)
- [Grounding brief](grounding-brief.md)
- [PRD](PRD-m0-m2.md)
- [Design](DD-m0-m2.md)
- [Council decision](council-m0-m2.md)
- [Parity matrix](parity-matrix.md)
- [M0 plan](plan-m0-evidence-scaffold.md)
- [M2 grounding](grounding-m2-doc-runtime.md)
- [M2 PRD](PRD-m2-doc-runtime.md)
- [M2 design](DD-m2-doc-runtime.md)
- [M2 council](council-m2-doc-runtime.md)
- [M2a plan](plan-m2a-doc-foundation.md)

## M0 Pull Requests

| Work | PR | State |
| --- | --- | --- |
| Intent, parity, and plan | [#117](https://github.com/Zhachory1/ai-pr-automation/pull/117) | merged |
| Pinned disabled Compose service | [#118](https://github.com/Zhachory1/ai-pr-automation/pull/118) | merged |
| Baseline metrics | [#119](https://github.com/Zhachory1/ai-pr-automation/pull/119) | merged |
| Isolated state-volume round trip | [#120](https://github.com/Zhachory1/ai-pr-automation/pull/120) | merged |

M0 focused validation passed after merge. No M0 PR activates Hermes.

## M2a Pull Requests

| Work | PR | State |
| --- | --- | --- |
| Design and plan | [#121](https://github.com/Zhachory1/ai-pr-automation/pull/121) | merged |
| Runtime/filesystem assumptions | [#122](https://github.com/Zhachory1/ai-pr-automation/pull/122) | merged |
| Durable run/publication state | [#123](https://github.com/Zhachory1/ai-pr-automation/pull/123) | merged |
| Atomic publication helper | [#124](https://github.com/Zhachory1/ai-pr-automation/pull/124) | merged |
| Exact publication approval | [#125](https://github.com/Zhachory1/ai-pr-automation/pull/125) | merged |
| Bounded Runs adapter | [#126](https://github.com/Zhachory1/ai-pr-automation/pull/126) | merged |
| Immutable prompt renderer | [#127](https://github.com/Zhachory1/ai-pr-automation/pull/127) | merged |
| Runtime and egress conformance | [#128](https://github.com/Zhachory1/ai-pr-automation/pull/128) | merged |

No M2a PR routes a doc request through Hermes or makes a paid provider call.

## Static Compose Check

Static validation only:

```bash
scripts/compose.sh --profile hermes-m0 config --quiet
```

This renders opt-in service shape. Do not run `up` with real provider credentials yet.

M2a adds reviewed zero-tool config and a dedicated OpenAI-only egress proxy. Validate without a
real provider key:

```bash
bash tests/test-hermes-compose-contract.sh
bash tests/test-hermes-doc-egress.sh
bash tests/test-hermes-doc-spikes.sh
bash tests/test-hermes-doc-quarantine.sh
bash tests/test-hermes-doc-gate.sh
```

Generate the owner-only M2a evidence report after focused checks pass:

```bash
scripts/hermes-doc-gate.py run
```

The report proves the non-routing foundation only. It cannot approve a real provider key, paid call,
or Hermes-routed document request.

## Baseline

Load request-DB settings, then collect near current time.

macOS:

```bash
set -a; . ./.env; set +a
export PGPASSWORD="$REQUESTS_DB_PASSWORD"
end="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
start="$(date -u -v-14d '+%Y-%m-%dT%H:%M:%SZ')"
scripts/hermes-baseline.py --start "$start" --end "$end"
```

Linux:

```bash
set -a; . ./.env; set +a
export PGPASSWORD="$REQUESTS_DB_PASSWORD"
end="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
start="$(date -u -d '14 days ago' '+%Y-%m-%dT%H:%M:%SZ')"
scripts/hermes-baseline.py --start "$start" --end "$end"
```

Collector uses current queue/pending state, so `--end` must be within five minutes of collection. Duplicate-effect and missed-eligible audits remain `unavailable` until later target inventory exists.

## Validation

After M0 PRs merge:

```bash
bash tests/test-hermes-compose-contract.sh
bash tests/test-compose-producers.sh
python3 tests/test-hermes-baseline.py
bash tests/test-hermes-state-roundtrip.sh
```

## M0 Limits

M0 proves:

- exact Hermes image pin;
- profile-gated Compose shape;
- unchanged default service set;
- read-only baseline metrics;
- isolated state-volume archive integrity.

M0 does not prove:

- safe Hermes model execution;
- no-tools or memory-disabled profile;
- provider-only egress;
- production backup or clean-host restore;
- route cutover or 15-minute rollback;
- run-attempt ownership;
- document-effect recovery;
- scheduler slot accounting or route fencing.

Execute M2a non-routing plan next. M2b paid shadow/live pilot needs separate plan-to-launch and human approval. M1 follows M2 evidence.
