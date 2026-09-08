#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
R="bin/pr-safety-json-repair.py"
fail=0
check() { if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1" >&2; fail=1; fi; }

# valid input is left byte-for-byte unchanged, exit 0
printf '{"operation_id":"op-y","status":"clear"}' > "$TMP/ok.json"
before="$(cat "$TMP/ok.json")"
python3 "$R" "$TMP/ok.json"; rc=$?
check "valid JSON untouched, exit 0" "[[ $rc -eq 0 && \"\$(cat "$TMP/ok.json")\" == '$before' ]]"

# duplicated key token (real luna failure) is repaired, exit 0
printf '{"operation_id":"operation_id":"op-x","status":"changes_requested","findings":[]}' > "$TMP/dup.json"
python3 "$R" "$TMP/dup.json"; rc=$?
check "duplicate-key token repaired" "[[ $rc -eq 0 ]] && python3 -c 'import json,sys; d=json.load(open(\"$TMP/dup.json\")); sys.exit(0 if d[\"operation_id\"]==\"op-x\" and d[\"status\"]==\"changes_requested\" else 1)'"

# prose + code fence + trailing junk around the object is stripped, exit 0
printf 'Here:\n```json\n{"operation_id":"op-z","status":"clear"}\n```\ndone' > "$TMP/wrap.json"
python3 "$R" "$TMP/wrap.json"; rc=$?
check "prose/fence/trailing junk stripped" "[[ $rc -eq 0 ]] && python3 -c 'import json,sys; d=json.load(open(\"$TMP/wrap.json\")); sys.exit(0 if d[\"operation_id\"]==\"op-z\" else 1)'"

# genuinely truncated input is unrepairable: left unchanged, exit 1
printf '{"operation_id":"op-w","status":"changes_requested","findings":[{"claim":"cut off' > "$TMP/trunc.json"
cp "$TMP/trunc.json" "$TMP/trunc.before"
rc=0; python3 "$R" "$TMP/trunc.json" || rc=$?
check "truncated left unchanged, exit 1" "[[ $rc -eq 1 ]] && cmp -s \"$TMP/trunc.json\" \"$TMP/trunc.before\""

(( fail == 0 ))
