#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/work"
cat > "$tmp/bin/psql" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
args="$*"; input="$(cat || true)"
if [[ "$args" == *hermes_claim_request* ]]; then
  jq -cn '{id:7,kind:"swe-implement",payload:{repo:"owner/repo",source:"prompt",prompt:"change"},dedupe_key:"swe:test"}'
elif [[ "$args" == *hermes_renew_request* ]]; then
  echo t
elif [[ "$input" == *hermes_settle_swe_request* ]]; then
  printf '%s\n' "$input" > "$TEST_STATE/settle.sql"
  echo t
else
  exit 2
fi
SH
cat > "$tmp/bin/hermes" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$@" > "$TEST_STATE/hermes.args"
query=""; while (($#)); do [[ "$1" != --query-file ]] || { query="$2"; shift; }; shift; done
result="$(grep -Eo '/[^ ]+/result.json' "$query" | head -1)"
nonce="$(grep -Eo 'nonce must be [0-9a-f]{32}' "$query" | awk '{print $4}')"
jq -cn --arg nonce "$nonce" '{detail:"created",nonce:$nonce,posted_ref:"https://github.com/owner/repo/pull/9",status:"done"}' > "$result"
SH
chmod +x "$tmp/bin/psql" "$tmp/bin/hermes"
PATH="$tmp/bin:$PATH" TEST_STATE="$tmp" HERMES_BIN="$tmp/bin/hermes" HERMES_WORK_ROOT="$tmp/work" \
  HERMES_QUEUE_LEASE_SECONDS=120 REQUESTS_DB_USER=hermes_runtime PGPASSWORD=fake \
  bin/hermes-queue-runner swe-implement | grep -q 'request=7 status=done'
grep -Fxq -- '-p' "$tmp/hermes.args"
grep -Fxq 'swe-implement-v1' "$tmp/hermes.args"
grep -q 'hermes_settle_swe_request' "$tmp/settle.sql"
if PATH="$tmp/bin:$PATH" HERMES_BIN="$tmp/bin/hermes" HERMES_WORK_ROOT="$tmp/work" \
  REQUESTS_DB_USER=hermes_runtime PGPASSWORD=fake bin/hermes-queue-runner unknown >/dev/null 2>&1; then
  echo 'FAIL: unknown kind accepted' >&2; exit 1
fi
echo 'PASS: shared Hermes queue runner maps SWE and settles typed result'
