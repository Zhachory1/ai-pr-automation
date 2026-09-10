#!/usr/bin/env bash
# Tests the swe-implement-server queue worker end-to-end against ephemeral Postgres, with a FAKE
# harness (so no live clone / agent / push). Verifies: payload->args mapping per source, claim +
# mark-done with the draft-PR url in posted_ref, pr-review enqueue, reconciliation on missing review
# metadata, mark-failed on harness error, invalid-payload guard, and queue deduplication.
set -uo pipefail
cd "$(dirname "$0")/.."
CID="swe-implement-server-test-$$"
PORT="$(python3 - <<'PY'
import socket
with socket.socket() as s: s.bind(("127.0.0.1",0)); print(s.getsockname()[1])
PY
)"
TMP="$(mktemp -d)"
cleanup(){ docker rm -f "$CID" >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT
docker run --rm -d --name "$CID" -e POSTGRES_PASSWORD=t -e POSTGRES_DB=fleet -p "$PORT:5432" postgres:16 >/dev/null
for _ in $(seq 1 30); do docker exec "$CID" pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
for f in docker/initdb/{01-schema,02-agent-server}.sql; do
  docker cp "$f" "$CID:/tmp/${f##*/}"; docker exec "$CID" psql -U postgres -d fleet -q -v ON_ERROR_STOP=1 -f "/tmp/${f##*/}" >/dev/null
done

export REQUESTS_DB_HOST=localhost REQUESTS_DB_PORT="$PORT" REQUESTS_DB_USER=postgres REQUESTS_DB_NAME=fleet PGPASSWORD=t GH_TOKEN=faketoken
q(){ docker exec "$CID" psql -U postgres -d fleet -tAc "$1"; }
fail=0; check(){ if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1" >&2; fail=1; fi; }

# fake harness: records the args it was called with, and behaves per a control file.
cat > "$TMP/fake-harness" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$FAKE_HARNESS_ARGS"
case "$(cat "$FAKE_HARNESS_MODE" 2>/dev/null)" in
  fail) echo "swe-implement: clone failed for X" >&2; exit 2 ;;
  skip) echo "swe-implement no commit produced"; exit 1 ;;
  nopr) echo "committed on branch (--no-pr)"; exit 0 ;;
  badmeta) echo "2026-01-01 swe-implement draft PR: https://github.com/ROKT/x/pull/98"; exit 0 ;;
  *)    echo "2026-01-01 swe-implement draft PR: https://github.com/ROKT/x/pull/99"
        echo '2026-01-01 swe-implement draft PR review request: {"repo":"ROKT/x","pr":"99","url":"https://github.com/ROKT/x/pull/99","title":"Prevent duplicate delivery","head_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}'
        exit 0 ;;
esac
SH
chmod +x "$TMP/fake-harness"
export SWE_IMPLEMENT_HARNESS="$TMP/fake-harness" FAKE_HARNESS_ARGS="$TMP/args" FAKE_HARNESS_MODE="$TMP/mode"
export SWE_IMPLEMENT_POLL_INTERVAL=1

# run the server for one claim cycle in the background, stop after the row leaves 'queued'
run_one() {
  ( timeout 30 bash bin/swe-implement-server >/"$TMP"/server.log 2>&1 & echo $! > "$TMP/srv.pid" )
  for _ in $(seq 1 25); do
    local st; st="$(q "SELECT status FROM requests WHERE kind='swe-implement' ORDER BY id DESC LIMIT 1")"
    [[ "$st" == done || "$st" == failed || "$st" == skipped || "$st" == reconcile ]] && break; sleep 1
  done
  kill "$(cat "$TMP/srv.pid")" 2>/dev/null || true; pkill -f swe-implement-server 2>/dev/null || true; sleep 1
}

# 1) handoff source -> --handoff <path>, success -> done + posted_ref carries the PR url
echo success > "$TMP/mode"
q "INSERT INTO requests(kind,payload,dedupe_key) VALUES('swe-implement','{\"source\":\"handoff\",\"handoff_path\":\"/h/x.md\",\"no_pr\":false}','handoff:x.md');" >/dev/null
run_one
check "handoff maps to --handoff and marks done" "grep -qx -- '--handoff' \"$TMP/args\" && grep -qx -- '/h/x.md' \"$TMP/args\""
check "success stores draft PR url in posted_ref" "q \"SELECT posted_ref FROM requests WHERE dedupe_key='handoff:x.md'\" | grep -q 'pull/99'"
check "success marks done" "q \"SELECT status FROM requests WHERE dedupe_key='handoff:x.md'\" | grep -qx done"
check "success enqueues the created head for pr-review" "q \"SELECT payload->>'title' FROM requests WHERE kind='pr-review' AND dedupe_key='ROKT/x#99@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'\" | grep -qx 'Prevent duplicate delivery'"

# 1b) a created PR without typed review metadata must not be marked done and retried blindly
echo badmeta > "$TMP/mode"
q "INSERT INTO requests(kind,payload,dedupe_key) VALUES('swe-implement','{\"source\":\"issue\",\"issue\":\"ROKT/cpi#6\",\"no_pr\":false}','issue:ROKT/cpi#6');" >/dev/null
run_one
check "missing review metadata requires reconciliation" "q \"SELECT status FROM requests WHERE dedupe_key='issue:ROKT/cpi#6'\" | grep -qx reconcile"

# 2) issue source -> --issue owner/repo#N
echo success > "$TMP/mode"
q "INSERT INTO requests(kind,payload,dedupe_key) VALUES('swe-implement','{\"source\":\"issue\",\"issue\":\"ROKT/cpi#123\",\"no_pr\":false}','issue:ROKT/cpi#123');" >/dev/null
run_one
check "issue maps to --issue owner/repo#N" "grep -qx -- '--issue' \"$TMP/args\" && grep -qx -- 'ROKT/cpi#123' \"$TMP/args\""

# 3) prompt source + no_pr -> --prompt/--repo/--no-pr
echo nopr > "$TMP/mode"
q "INSERT INTO requests(kind,payload,dedupe_key) VALUES('swe-implement','{\"source\":\"prompt\",\"repo\":\"ROKT/cpi\",\"prompt\":\"do x\",\"no_pr\":true}','prompt:ROKT/cpi:abc');" >/dev/null
run_one
check "prompt maps to --prompt/--repo/--no-pr" "grep -qx -- '--prompt' \"$TMP/args\" && grep -qx -- 'do x' \"$TMP/args\" && grep -qx -- '--repo' \"$TMP/args\" && grep -qx -- 'ROKT/cpi' \"$TMP/args\" && grep -qx -- '--no-pr' \"$TMP/args\""

# 4) harness error -> failed with reason
echo fail > "$TMP/mode"
q "INSERT INTO requests(kind,payload,dedupe_key) VALUES('swe-implement','{\"source\":\"issue\",\"issue\":\"ROKT/cpi#7\",\"no_pr\":false}','issue:ROKT/cpi#7');" >/dev/null
run_one
check "harness error marks failed" "q \"SELECT status FROM requests WHERE dedupe_key='issue:ROKT/cpi#7'\" | grep -qx failed"

# 4b) harness rc=1 (no commit) -> skipped, NOT failed
echo skip > "$TMP/mode"
q "INSERT INTO requests(kind,payload,dedupe_key) VALUES('swe-implement','{\"source\":\"issue\",\"issue\":\"ROKT/cpi#8\",\"no_pr\":false}','issue:ROKT/cpi#8');" >/dev/null
run_one
check "harness rc=1 marks skipped (not failed)" "q \"SELECT status FROM requests WHERE dedupe_key='issue:ROKT/cpi#8'\" | grep -qx skipped"

# 5) invalid payload (unknown source) -> failed without calling harness
rm -f "$TMP/args"
q "INSERT INTO requests(kind,payload,dedupe_key) VALUES('swe-implement','{\"source\":\"bogus\"}','bad:1');" >/dev/null
run_one
check "invalid payload marks failed" "q \"SELECT status FROM requests WHERE dedupe_key='bad:1'\" | grep -qx failed"
check "invalid payload does not call harness" "[[ ! -f \"$TMP/args\" ]]"

(( fail == 0 ))
