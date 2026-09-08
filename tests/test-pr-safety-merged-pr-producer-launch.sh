#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir "$TMP/bin"
cat > "$TMP/bin/gh" <<'SH'
#!/bin/sh
exit 0
SH
cat > "$TMP/producer" <<'SH'
#!/bin/sh
printf 'run\n' >> "$PR_SAFETY_TEST_RUNS"
sleep 1
SH
chmod +x "$TMP/bin/gh" "$TMP/producer"
cat > "$TMP/.env" <<EOF
REQUESTS_DB_PASSWORD=test
GH_TOKEN=ghtok
PR_SAFETY_MERGED_PR_AUTHORS=roktfleet,brucerokt
PR_SAFETY_POLICY_PATH=/tmp/policy.md
PR_SAFETY_POLICY_VERSION=v1
PR_SAFETY_POLICY_DIGEST=$(printf a%.0s {1..64})
EOF
chmod 600 "$TMP/.env"
fail=0
run() {
  env -i HOME="$HOME" PATH="/usr/bin:/bin:/usr/sbin:/sbin" \
  PR_SAFETY_MERGED_PR_PATH="$TMP/bin:/opt/homebrew/bin:/usr/bin:/bin" \
  PR_SAFETY_MERGED_PR_ENV_FILE="$TMP/.env" PR_SAFETY_MERGED_PR_PRODUCER_BIN="$TMP/producer" \
  PR_SAFETY_MERGED_PR_LOCK_DIR="$TMP/lock" PR_SAFETY_TEST_RUNS="$TMP/runs" \
  /bin/bash scripts/pr-safety-merged-pr-producer-launch.sh
}

# concurrent runs: the inherited flock lets only one proceed
run & first=$!
sleep 0.1
run
wait "$first"
if [[ "$(wc -l < "$TMP/runs")" -eq 1 ]]; then echo "PASS: single-flight lock"; else echo "FAIL: single-flight lock" >&2; fail=1; fi

# group-readable env is rejected
chmod 644 "$TMP/.env"
if run >/dev/null 2>&1; then echo "FAIL: group-readable env accepted" >&2; fail=1; else echo "PASS: group-readable env rejected"; fi
chmod 600 "$TMP/.env"

# missing required var is rejected
sed -i.bak '/PR_SAFETY_MERGED_PR_AUTHORS/d' "$TMP/.env"; rm -f "$TMP/.env.bak"
if run >/dev/null 2>&1; then echo "FAIL: missing authors accepted" >&2; fail=1; else echo "PASS: missing authors rejected"; fi

(( fail == 0 ))
