#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir "$TMP/bin"
cat > "$TMP/bin/gcloud" <<'SH'
#!/bin/sh
[ "$1 $2 $3" = "auth application-default print-access-token" ] && printf 'token\n'
SH
cat > "$TMP/producer" <<'SH'
#!/bin/sh
printf 'run\n' >> "$PR_SAFETY_TEST_RUNS"
sleep 1
SH
chmod +x "$TMP/bin/gcloud" "$TMP/producer"
cat > "$TMP/.env" <<EOF
REQUESTS_DB_PASSWORD=test
GOOGLE_CHAT_SPACE=spaces/AAAA
PR_SAFETY_CHAT_BOT_SENDER=users/123456789
PR_SAFETY_POLICY_PATH=/tmp/policy.md
PR_SAFETY_POLICY_VERSION=v1
PR_SAFETY_POLICY_DIGEST=$(printf a%.0s {1..64})
EOF
chmod 600 "$TMP/.env"
run() {
  env -i HOME="$HOME" PATH="/usr/bin:/bin:/usr/sbin:/sbin" \
  PR_SAFETY_CHAT_PATH="$TMP/bin:/opt/homebrew/bin:/usr/bin:/bin" \
  PR_SAFETY_CHAT_ENV_FILE="$TMP/.env" PR_SAFETY_CHAT_PRODUCER_BIN="$TMP/producer" \
  PR_SAFETY_CHAT_LOCK_DIR="$TMP/lock" PR_SAFETY_TEST_RUNS="$TMP/runs" \
  /bin/bash scripts/pr-safety-chat-producer-launch.sh
}
run & first=$!
sleep 0.1
run
wait "$first"
[[ "$(wc -l < "$TMP/runs")" -eq 1 ]]
chmod 644 "$TMP/.env"
if run >/tmp/pr-safety-chat-launch-permissions.out 2>&1; then
  echo "group-readable env accepted" >&2
  exit 1
fi
grep -q 'must not be group/world-readable' /tmp/pr-safety-chat-launch-permissions.out
echo "PASS: PR safety chat producer launcher lock and env permissions"
