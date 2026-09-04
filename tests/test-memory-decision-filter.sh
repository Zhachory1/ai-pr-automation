#!/usr/bin/env bash
# Validates the production gate used before pending_decisions inserts.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1

# shellcheck source=../lib/queue.sh
. lib/queue.sh

fail=0
check() {
  local name="$1"
  shift
  if "$@"; then
    echo "  PASS: $name"
  else
    echo "  FAIL: $name"
    fail=1
  fi
}

valid() { printf '%s' "$1" | valid_memory_decisions; }
invalid() { ! valid "$1"; }

valid_repository='[{"scope":"repository","rule":"Use named exports for shared modules.","rationale":"Makes imports consistent in this repository."}]'
valid_fleet='[{"scope":"fleet","rule":"Always pin action versions.","rationale":"Avoids unreviewed upstream changes."}]'

check "accepts structured repository rule" valid "$valid_repository"
check "accepts structured fleet rule" valid "$valid_fleet"
check "rejects empty decisions" invalid '[]'
check "rejects string-only decisions" invalid '["PR review completed"]'
check "rejects ordinary run summary" invalid '["review URL=https://github.com/acme/repo/pull/1 head=abc status=passed"]'
check "rejects ordinary finding object" invalid '[{"file":"src/x.py","line":10,"text":"rename this"}]'
check "rejects missing rationale" invalid '[{"scope":"repository","rule":"Use named exports."}]'
check "rejects empty rule" invalid '[{"scope":"repository","rule":"","rationale":"Consistency."}]'
check "rejects whitespace-only rule" invalid '[{"scope":"repository","rule":"   ","rationale":"Consistency."}]'
check "rejects extra fields" invalid '[{"scope":"repository","rule":"Use named exports.","rationale":"Consistency.","finding":"temporary"}]'
check "rejects invalid scope" invalid '[{"scope":"organization","rule":"Use named exports.","rationale":"Consistency."}]'
check "rejects non-array decisions" invalid '{"scope":"repository","rule":"Use named exports.","rationale":"Consistency."}'
check "rejects null decisions" invalid 'null'

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
AGENT_RESULT_FILE="$tmp/result.json" AGENT_RUN_NONCE=test-nonce tests/fake-runner.sh /dev/null >/dev/null
check "accepts fake runner's structured decision" valid "$(jq -c '.memory.decisions' "$tmp/result.json")"

[[ "$fail" -eq 0 ]] && echo "ALL PASS" || exit 1
