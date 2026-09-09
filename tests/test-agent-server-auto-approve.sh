#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

[[ "$(grep -Fc 'Default to no Hindsight retain call.' bin/agent-server)" == 2 ]]
[[ "$(grep -Fc 'another agent could act differently on a later task' bin/agent-server)" == 2 ]]
[[ "$(grep -Fc 'Never retain review completion, verdict, run status' bin/agent-server)" == 2 ]]

CID="agent-server-auto-approve-test-$$"
TMP="$(mktemp -d)"
PORT="$(python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)"
worker=""
cleanup() {
  [[ -z "$worker" ]] || kill -TERM "$worker" >/dev/null 2>&1 || true
  [[ -z "$worker" ]] || wait "$worker" >/dev/null 2>&1 || true
  docker rm -f "$CID" >/dev/null 2>&1 || true
  rm -rf "$TMP"
}
trap cleanup EXIT

docker run --rm -d --name "$CID" -e POSTGRES_PASSWORD=t -e POSTGRES_DB=fleet -p "$PORT:5432" postgres:16 >/dev/null
for _ in $(seq 1 30); do docker exec "$CID" pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
for f in docker/initdb/{01-schema,02-agent-server,03-human-review-queue}.sql; do
  docker cp "$f" "$CID:/tmp/${f##*/}"
  docker exec "$CID" psql -U postgres -d fleet -q -v ON_ERROR_STOP=1 -f "/tmp/${f##*/}" >/dev/null
done

mkdir -p "$TMP/bin" "$TMP/work"
cat > "$TMP/bin/gh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
head_for() { printf '%040d' "$1"; }
if [[ "$1 $2" == "api user" ]]; then
  echo reviewer-bot
elif [[ "$1 $2" == "pr view" ]]; then
  pr="$3"; head="$(head_for "$pr")"; author=human-author
  [[ "$pr" == 9 ]] && author=reviewer-bot
  if [[ "$*" == *"--jq .headRefOid"* ]]; then echo "$head"; else printf '{"headRefOid":"%s","author":{"login":"%s"}}\n' "$head" "$author"; fi
elif [[ "$1 $2" == "api --paginate" ]]; then
  endpoint="$3"; pr="${endpoint%/reviews}"; pr="${pr##*/}"
  [[ "$pr" == 10 ]] && exit 1
  [[ -f "$TEST_STATE/review-$pr" ]] || exit 0
  head="$(head_for "$pr")"; state="$(cat "$TEST_STATE/review-$pr")"
  if [[ "$*" == *'state != "DISMISSED"'* ]]; then
    printf 'reviewer-bot\t<!-- ai-pr-automation head=%s -->\n' "$head"
  elif [[ "$state" == APPROVED ]]; then
    printf 'reviewer-bot\t%s\n' "$head"
  fi
elif [[ "$1 $2 $3" == "api --method POST" ]]; then
  endpoint="$4"; pr="${endpoint%/reviews}"; pr="${pr##*/}"
  cat > "$TEST_STATE/payload-$pr.json"
  count=0; [[ ! -f "$TEST_STATE/posts-$pr" ]] || count="$(cat "$TEST_STATE/posts-$pr")"
  echo $((count + 1)) > "$TEST_STATE/posts-$pr"
  echo APPROVED > "$TEST_STATE/review-$pr"
  exit 1 # ambiguous client failure after GitHub accepted the review
else
  echo "unexpected gh call: $*" >&2
  exit 2
fi
SH
cat > "$TMP/bin/runner" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
grep -Fq 'Default to no Hindsight retain call.' "$1"
grep -Fq 'another agent could act differently on a later task' "$1"
grep -Fq 'the conclusion stays valid after this PR' "$1"
grep -Fq 'non-obvious, and is not cheap to recover from code or docs' "$1"
grep -Fq 'Never retain review completion, verdict, run status, clean result, test result, PR URL, commit SHA' "$1"
grep -Fq 'Provenance alone is not useful' "$1"
! grep -Fq 'memory.decisions' "$1"
printf '%s\n' "$PR_NUMBER" >> "$TEST_STATE/runner-prs"
if gh pr review "$PR_NUMBER" -R "$PR_REPO" --approve 2> "$TEST_STATE/shim-$PR_NUMBER"; then exit 4; fi
grep -Fq 'agent-server owns APPROVE reviews' "$TEST_STATE/shim-$PR_NUMBER"
if gh pr review "$PR_NUMBER" -R "$PR_REPO" -a 2> "$TEST_STATE/shim-short-$PR_NUMBER"; then exit 4; fi
grep -Fq 'agent-server owns APPROVE reviews' "$TEST_STATE/shim-short-$PR_NUMBER"
printf '{"event":"APPROVE"}\n' > "$PR_WORK_ROOT/agent-approval.json"
if gh api --method POST "repos/$PR_REPO/pulls/$PR_NUMBER/reviews" --input "$PR_WORK_ROOT/agent-approval.json" 2> "$TEST_STATE/shim-api-$PR_NUMBER"; then exit 4; fi
grep -Fq 'agent-server owns APPROVE reviews' "$TEST_STATE/shim-api-$PR_NUMBER"
if [[ "$PR_NUMBER" == 7 ]]; then
  jq -cn --arg nonce "$AGENT_RUN_NONCE" '{nonce:$nonce,verdict:"approve",findings:[],summary:"clean",ready_for_human_review:true,memory:{decisions:[{scope:"fleet",rule:"legacy proposal",rationale:"must be ignored"}]}}' > "$AGENT_RESULT_FILE"
elif [[ "$PR_NUMBER" == 8 ]]; then
  jq -cn --arg nonce "$AGENT_RUN_NONCE" '{nonce:$nonce,verdict:"comment",findings:[],summary:"clean",ready_for_human_review:true}' > "$AGENT_RESULT_FILE"
elif [[ "$PR_NUMBER" == 11 ]]; then
  jq -cn --arg nonce "$AGENT_RUN_NONCE" '{nonce:$nonce,verdict:"approve",findings:[{severity:"minor",blocks_merge:"false"}],summary:"malformed",ready_for_human_review:true}' > "$AGENT_RESULT_FILE"
elif [[ "$PR_NUMBER" == 12 ]]; then
  echo CHANGES_REQUESTED > "$TEST_STATE/review-12"
  jq -cn --arg nonce "$AGENT_RUN_NONCE" '{nonce:$nonce,verdict:"approve",findings:[],summary:"conflict",ready_for_human_review:true}' > "$AGENT_RESULT_FILE"
else
  jq -cn --arg nonce "$AGENT_RUN_NONCE" '{nonce:$nonce,verdict:"approve",findings:[],summary:"clean",ready_for_human_review:true}' > "$AGENT_RESULT_FILE"
fi
SH
cat > "$TMP/bin/curl" <<'SH'
#!/bin/sh
exit 0
SH
chmod +x "$TMP/bin/gh" "$TMP/bin/runner" "$TMP/bin/curl"

q() { docker exec "$CID" psql -U postgres -d fleet -tAc "$1"; }
for pr in 7 8 9 10 11 12; do
  head="$(printf '%040d' "$pr")"
  q "INSERT INTO requests(kind,payload,dedupe_key) VALUES ('pr-review','{\"repo\":\"owner/repo\",\"pr\":\"$pr\",\"url\":\"https://github.com/owner/repo/pull/$pr\",\"title\":\"test\"}','owner/repo#$pr@$head');" >/dev/null
done

export REQUESTS_DB_USER=postgres REQUESTS_DB_NAME=fleet REQUESTS_DB_HOST=localhost REQUESTS_DB_PORT="$PORT" PGPASSWORD=t
export AGENT_SERVER_KIND=pr-review AGENT_SERVER_RUNNER="$TMP/bin/runner" AGENT_SERVER_WORK_ROOT="$TMP/work"
export AGENT_SERVER_POLL_INTERVAL=1 AGENT_SERVER_LEASE_SECONDS=30 AGENT_SERVER_LEASE_HEARTBEAT=5 AGENT_SERVER_LEASE_DB_TIMEOUT=3
export AGENT_SERVER_LOOP_HEARTBEAT_MAX=60
export TEST_STATE="$TMP" PATH="$TMP/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
bin/agent-server > "$TMP/server.log" 2>&1 & worker=$!
for _ in $(seq 1 60); do
  if ! kill -0 "$worker" 2>/dev/null; then cat "$TMP/server.log" >&2; exit 1; fi
  [[ "$(q "SELECT count(*) FROM requests WHERE status IN ('queued','running');")" == 0 ]] && break
  sleep 1
done
kill -TERM "$worker" >/dev/null 2>&1 || true
wait "$worker" || true
worker=""

[[ "$(q "SELECT status||'/'||posted_ref FROM requests WHERE dedupe_key LIKE 'owner/repo#7@%';")" == "done/head=$(printf '%040d' 7)" ]]
[[ "$(jq -r '.event' "$TMP/payload-7.json")" == APPROVE ]]
[[ "$(jq -r '.commit_id' "$TMP/payload-7.json")" == "$(printf '%040d' 7)" ]]
grep -Fq "<!-- ai-pr-automation head=$(printf '%040d' 7) -->" "$TMP/payload-7.json"
[[ "$(cat "$TMP/posts-7")" == 1 ]]
[[ "$(q "SELECT count(*) FROM pending_maintenance_reviews WHERE request_id=(SELECT id FROM requests WHERE dedupe_key LIKE 'owner/repo#7@%');")" == 1 ]]
[[ "$(q "SELECT count(*) FROM pending_decisions;")" == 0 ]]
[[ "$(q "SELECT status FROM requests WHERE dedupe_key LIKE 'owner/repo#8@%';")" == failed ]]
[[ "$(q "SELECT status FROM requests WHERE dedupe_key LIKE 'owner/repo#9@%';")" == failed ]]
[[ "$(q "SELECT status FROM requests WHERE dedupe_key LIKE 'owner/repo#10@%';")" == failed ]]
[[ "$(q "SELECT status FROM requests WHERE dedupe_key LIKE 'owner/repo#11@%';")" == failed ]]
[[ "$(q "SELECT status FROM requests WHERE dedupe_key LIKE 'owner/repo#12@%';")" == failed ]]
[[ ! -e "$TMP/posts-8" && ! -e "$TMP/posts-9" && ! -e "$TMP/posts-10" && ! -e "$TMP/posts-11" && ! -e "$TMP/posts-12" ]]
[[ "$(sort -n "$TMP/runner-prs" | tr '\n' ' ')" == "7 8 9 11 12 " ]]

echo "PASS: agent-server validates and idempotently posts approval signals"
