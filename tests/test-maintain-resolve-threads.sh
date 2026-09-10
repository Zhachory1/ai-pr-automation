#!/usr/bin/env bash
# Verifies the maintain prompt makes thread resolution MANDATORY and requires a final reconciliation
# sweep, so addressed comments are consistently closed out (not resolved on some, skipped on others).
# Renders build_maintain_prompt in isolation (no DB/containers) and asserts the rules.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1

fail=0; want() { if grep -Fq -e "$1" <<<"$PROMPT"; then :; else echo "FAIL: prompt missing: $1" >&2; fail=1; fi; }
wantnot() { if grep -Fq -e "$1" <<<"$PROMPT"; then echo "FAIL: prompt should NOT contain: $1" >&2; fail=1; fi; }

tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
awk '/^build_maintain_prompt\(\) \{/{p=1} p{print} p&&/^\}/{exit}' bin/agent-server > "$tmp"
[[ -s "$tmp" ]] || { echo "FAIL: could not extract build_maintain_prompt" >&2; exit 1; }
# shellcheck disable=SC1090
. "$tmp"
GITHUB_LOGIN=reviewer-bot
PROMPT="$(build_maintain_prompt pr-maintain ROKT/example 42 https://x/42 'A title' "$(printf '%040d' 42)" nonce123 /work/root)"

# resolution is mandatory + uniform, not "MAY"
want "Resolving addressed threads is MANDATORY, not optional"
want "do not resolve some and leave others"
want "Never leave"
want "a thread whose fix you pushed + replied to sitting unresolved"

# final reconciliation sweep exists
want "FINAL RECONCILIATION before you finish"
want "re-fetch the PR's review threads"
want "retry resolveReviewThread once for any that is still false"
want "report it in result.json.summary as an"

# the only unresolved threads are the escalated ones
want "The ONLY threads left unresolved are ones you did NOT fully address"

# the old permissive phrasing is gone
wantnot "you MAY auto-reply to and resolve ACTIONABLE"

# the skill echoes the same guarantee
skill="agent-config/skills/pr-review-handler/SKILL.md"
grep -Fq 'Final reconciliation' "$skill" || { echo "FAIL: skill missing final reconciliation sweep" >&2; fail=1; }
grep -Fq 'never resolve some handled threads and leave others' "$skill" || { echo "FAIL: skill missing uniform-resolution constraint" >&2; fail=1; }

(( fail == 0 )) && echo "PASS: maintain enforces mandatory + reconciled thread resolution"
(( fail == 0 ))
