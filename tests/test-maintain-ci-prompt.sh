#!/usr/bin/env bash
# Verifies the maintain prompt tells the bot to fix failing CI conservatively: fix only clearly
# code-caused/validatable classes, escalate the rest, never weaken a check, never retry CI. Renders
# build_maintain_prompt in isolation (no DB/containers) and asserts the rules are present.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1

fail=0; want() { if grep -Fq -e "$1" <<<"$PROMPT"; then :; else echo "FAIL: prompt missing: $1" >&2; fail=1; fi; }
wantnot() { if grep -Fq -e "$1" <<<"$PROMPT"; then echo "FAIL: prompt should NOT contain: $1" >&2; fail=1; fi; }

# Pull just build_maintain_prompt (+ its helper deps are none) out of bin/agent-server and render it.
tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
awk '/^build_maintain_prompt\(\) \{/{p=1} p{print} p&&/^\}/{exit}' bin/agent-server > "$tmp"
[[ -s "$tmp" ]] || { echo "FAIL: could not extract build_maintain_prompt" >&2; exit 1; }
# shellcheck disable=SC1090
. "$tmp"
GITHUB_LOGIN=reviewer-bot
PROMPT="$(build_maintain_prompt pr-maintain ROKT/example 42 https://x/42 'A title' "$(printf '%040d' 42)" nonce123 /work/root)"

# core: it now covers CI, not just comments
want "handle review comments AND failing CI"
want "Failing CI (CONSERVATIVE autonomy"
want "--json statusCheckRollup"   # rendered: gh pr view "42" -R "ROKT/example" --json statusCheckRollup
want "Buildkite MCP to fetch the failing job's log"

# conservative fix scope (A): only code-caused/validatable classes
want "lint / format / style violations, type-check errors, a compile/build break, or a unit test that"
want "Reproduce the failure locally"

# escalate everything else, incl. flaky/infra
want "ESCALATE (report as pending, do NOT touch code) for everything else"
want "flaky or infrastructure/runner failures"

# never game the check; never retry (flaky = escalate-only)
want "NEVER make a check pass by weakening it"
want "Do NOT re-trigger, retry, cancel, or rebuild any CI job"

# commit convention + escalation surfaced in result.json
want "'fix(ci): ...' for a"
want "ESCALATED a failing check"

# must NOT have opened the door to aggressive/e2e auto-fixing or check-weakening
wantnot "attempt any failing check"

(( fail == 0 )) && echo "PASS: maintain prompt includes conservative failing-CI handling"
(( fail == 0 ))
