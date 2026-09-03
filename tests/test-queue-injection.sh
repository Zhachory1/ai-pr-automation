#!/usr/bin/env bash
# COUNCIL-required invariant test: request data must never be interpreted as SQL, plus the
# claim/reclaim/posted_ref state machine. Runs against a throwaway Postgres.
# Targets rows by explicit id (no claim-order assumptions).
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

CID="m1-queue-test-$$"
PORT=55432
cleanup() { docker rm -f "$CID" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker run --rm -d --name "$CID" -e POSTGRES_PASSWORD=t -e POSTGRES_DB=fleet -p "$PORT:5432" postgres:16 >/dev/null
for _ in $(seq 1 30); do docker exec "$CID" pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
docker cp docker/initdb/01-schema.sql "$CID:/tmp/01.sql"
docker cp docker/initdb/02-agent-server.sql "$CID:/tmp/02.sql"
docker exec "$CID" psql -U postgres -d fleet -q -v ON_ERROR_STOP=1 -f /tmp/01.sql >/dev/null
docker exec "$CID" psql -U postgres -d fleet -q -v ON_ERROR_STOP=1 -f /tmp/02.sql >/dev/null
docker exec "$CID" psql -U postgres -d fleet -q -c "CREATE TABLE sentinel(x int);" >/dev/null

export REQUESTS_DB_USER=postgres REQUESTS_DB_NAME=fleet REQUESTS_DB_HOST=localhost REQUESTS_DB_PORT="$PORT"
export PGPASSWORD=t
# shellcheck source=../lib/queue.sh
. lib/queue.sh
set +e   # queue.sh sets -e; this harness deliberately checks nonzero returns.

fail=0
check(){ if eval "$2"; then echo "  PASS: $1"; else echo "  FAIL: $1"; fail=1; fi; }
q(){ docker exec "$CID" psql -U postgres -d fleet -tAc "$1"; }

echo "[1] injection payload does not execute as SQL"
EVIL="x'); DROP TABLE requests; DROP TABLE sentinel; --"
queue_enqueue "pr-review" "{\"evil\":\"$EVIL\"}" "$EVIL"
check "requests table survives" "q \"SELECT to_regclass('requests') IS NOT NULL;\" | grep -qx t"
check "sentinel table survives (injection inert)" "q \"SELECT to_regclass('sentinel') IS NOT NULL;\" | grep -qx t"

echo "[2] injection string round-trips literally as data"
GOT="$(q "SELECT dedupe_key FROM requests ORDER BY id LIMIT 1;")"
check "dedupe_key stored verbatim" "[[ \"\$GOT\" == \"\$EVIL\" ]]"

echo "[3] claim -> side_effect -> done sets posted_ref"
CLAIM="$(queue_claim_one pr-review run3 nonce3)"
check "claim returned a row" "[[ -n \"\$CLAIM\" ]]"
ID="$(jq -r '.id' <<<"$CLAIM")"
queue_mark_side_effect "$ID" >/dev/null
queue_mark_done "$ID" "head=deadbeef" >/dev/null
check "row done with posted_ref" "q \"SELECT status||'/'||coalesce(posted_ref,'') FROM requests WHERE id=$ID;\" | grep -qx 'done/head=deadbeef'"

echo "[4] reclaim: not-posted running -> requeued (target rows by explicit id)"
queue_enqueue "pr-review" '{}' "clean-1" >/dev/null
ID_CLEAN="$(q "SELECT id FROM requests WHERE dedupe_key='clean-1';")"
q "UPDATE requests SET status='running', started_at=now() WHERE id=$ID_CLEAN;" >/dev/null   # simulate a claimed, un-posted row
queue_reclaim_stale pr-review >/dev/null
check "clean running row requeued" "q \"SELECT status FROM requests WHERE id=$ID_CLEAN;\" | grep -qx queued"

echo "[4b] reclaim: posted running (posted_ref set) -> done, never re-run"
queue_enqueue "pr-review" '{}' "posted-1" >/dev/null
ID_POSTED="$(q "SELECT id FROM requests WHERE dedupe_key='posted-1';")"
q "UPDATE requests SET status='running', started_at=now(), side_effect_at=now(), posted_ref='head=abc' WHERE id=$ID_POSTED;" >/dev/null
queue_reclaim_stale pr-review >/dev/null
check "posted running row -> done" "q \"SELECT status FROM requests WHERE id=$ID_POSTED;\" | grep -qx done"

echo "[5] already-posted detection via posted_ref"
check "true for posted dedupe_key (EVIL was posted in [3])" "queue_already_posted pr-review \"\$EVIL\""
check "false for unposted (clean-1)" "! queue_already_posted pr-review clean-1"

echo "[6] pending_decisions insert (human-batch path)"
pending_decision_insert "$ID" "pr-review" '{"decision":"x"}' '{"run_id":"r","written_by":"agent-server"}' >/dev/null
check "pending_decisions row present + pending" "q \"SELECT state FROM pending_decisions WHERE request_id=$ID;\" | grep -qx pending"

echo
[[ $fail -eq 0 ]] && echo "ALL PASS" || { echo "FAILURES"; exit 1; }
