#!/usr/bin/env bash
# COUNCIL-required invariant test: request data must never be interpreted as SQL.
# Feeds injection payloads through the queue layer against a throwaway Postgres and asserts
# the requests table survives and values round-trip literally.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1

CID="m1-queue-test-$$"
cleanup() { docker rm -f "$CID" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker run --rm -d --name "$CID" -e POSTGRES_PASSWORD=t -e POSTGRES_DB=fleet -p 55432:5432 postgres:16 >/dev/null
for _ in $(seq 1 30); do docker exec "$CID" pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
docker cp docker/initdb/01-schema.sql "$CID:/tmp/01.sql"
docker cp docker/initdb/02-agent-server.sql "$CID:/tmp/02.sql"
docker exec "$CID" psql -U postgres -d fleet -q -v ON_ERROR_STOP=1 -f /tmp/01.sql >/dev/null
docker exec "$CID" psql -U postgres -d fleet -q -v ON_ERROR_STOP=1 -f /tmp/02.sql >/dev/null

export REQUESTS_DB_USER=postgres REQUESTS_DB_NAME=fleet REQUESTS_DB_HOST=localhost REQUESTS_DB_PORT=55432
export PGPASSWORD=t
# shellcheck source=../lib/queue.sh
. lib/queue.sh

fail=0
check(){ if eval "$2"; then echo "  PASS: $1"; else echo "  FAIL: $1"; fail=1; fi; }

echo "[1] injection payload as dedupe_key + payload does not drop the table"
EVIL="x'); DROP TABLE requests; --"
queue_enqueue "pr-review" "{\"evil\":\"$EVIL\"}" "$EVIL" || true
check "requests table still exists" "docker exec $CID psql -U postgres -d fleet -tAc \"SELECT to_regclass('requests') IS NOT NULL;\" | grep -qx t"

echo "[2] the injection string round-trips literally as data"
GOT="$(docker exec "$CID" psql -U postgres -d fleet -tAc "SELECT dedupe_key FROM requests LIMIT 1;")"
check "dedupe_key stored verbatim" "[[ \"\$GOT\" == \"\$EVIL\" ]]"

echo "[3] claim -> side_effect -> done state machine"
run_id="test-$(date +%s)"; nonce="nonce-abc"
CLAIM="$(queue_claim_one "$run_id" "$nonce")"
check "claim returned a row" "[[ -n \"\$CLAIM\" ]]"
ID="$(cut -f1 <<<"$CLAIM")"
queue_mark_side_effect "$ID" >/dev/null
queue_mark_done "$ID" "head=deadbeef" >/dev/null
check "row is done with posted_ref" "docker exec $CID psql -U postgres -d fleet -tAc \"SELECT status||'/'||coalesce(posted_ref,'') FROM requests WHERE id=$ID;\" | grep -qx 'done/head=deadbeef'"

echo "[4] reclaim: running+side_effect -> failed (fail-closed); clean running -> queued"
queue_enqueue "pr-review" '{}' "clean-1" >/dev/null
queue_enqueue "pr-review" '{}' "sideeffect-1" >/dev/null
C1="$(queue_claim_one "$run_id" n1)"; ID1="$(cut -f1 <<<"$C1")"
C2="$(queue_claim_one "$run_id" n2)"; ID2="$(cut -f1 <<<"$C2")"
queue_mark_side_effect "$ID2" >/dev/null
queue_reclaim_stale >/dev/null
check "clean running row requeued" "docker exec $CID psql -U postgres -d fleet -tAc \"SELECT status FROM requests WHERE id=$ID1;\" | grep -qx queued"
check "side-effect running row fail-closed" "docker exec $CID psql -U postgres -d fleet -tAc \"SELECT status FROM requests WHERE id=$ID2;\" | grep -qx failed"

echo "[5] already-posted detection via posted_ref"
check "already_posted true for posted dedupe_key" "queue_already_posted pr-review \"\$EVIL\""
check "already_posted false for unposted" "! queue_already_posted pr-review clean-1"

echo "[6] pending_decisions insert (human-batch path)"
pending_decision_insert "$ID" "pr-review" '{"decision":"x"}' '{"run_id":"r","written_by":"agent-server"}' >/dev/null
check "pending_decisions row present + pending" "docker exec $CID psql -U postgres -d fleet -tAc \"SELECT state FROM pending_decisions WHERE request_id=$ID;\" | grep -qx pending"

echo
[[ $fail -eq 0 ]] && echo "ALL PASS" || { echo "FAILURES"; exit 1; }
