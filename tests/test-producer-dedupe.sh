#!/usr/bin/env bash
# M2: producer enqueue + dedupe semantics against real Postgres.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
CID="m2-producer-test-$$"; PORT="$(python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)"
CAP_A="/tmp/maint-cap-a-$$"; CAP_B="/tmp/maint-cap-b-$$"
cleanup(){ docker rm -f "$CID" >/dev/null 2>&1 || true; rm -f "$CAP_A" "$CAP_B"; }
trap cleanup EXIT
docker run --rm -d --name "$CID" -e POSTGRES_PASSWORD=t -e POSTGRES_DB=fleet -p "$PORT:5432" postgres:16 >/dev/null
for _ in $(seq 1 30); do docker exec "$CID" pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
docker exec "$CID" pg_isready -U postgres >/dev/null
docker cp docker/initdb/01-schema.sql "$CID:/tmp/01.sql"; docker cp docker/initdb/02-agent-server.sql "$CID:/tmp/02.sql"
docker exec "$CID" psql -U postgres -d fleet -q -f /tmp/01.sql >/dev/null
docker exec "$CID" psql -U postgres -d fleet -q -f /tmp/02.sql >/dev/null
export REQUESTS_DB_USER=postgres REQUESTS_DB_NAME=fleet REQUESTS_DB_HOST=localhost REQUESTS_DB_PORT="$PORT" PGPASSWORD=t
# shellcheck source=../lib/queue.sh
. lib/queue.sh; set +e
fail=0; check(){ if eval "$2"; then echo "  PASS: $1"; else echo "  FAIL: $1"; fail=1; fi; }
q(){ docker exec "$CID" psql -U postgres -d fleet -tAc "$1"; }

echo "[1] enqueue same key twice while queued -> exactly one row (active dedupe)"
queue_enqueue pr-review '{"repo":"o/r","pr":"1"}' "o/r#1@abc" >/dev/null
queue_enqueue pr-review '{"repo":"o/r","pr":"1"}' "o/r#1@abc" >/dev/null
check "one active row" "[[ \"\$(q \"SELECT count(*) FROM requests WHERE dedupe_key='o/r#1@abc';\")\" == 1 ]]"

echo "[2] after done, re-enqueue SAME head -> no new row (NOT EXISTS guard; no churn)"
q "UPDATE requests SET status='done' WHERE dedupe_key='o/r#1@abc';" >/dev/null
out="$(queue_enqueue pr-review '{"repo":"o/r","pr":"1"}' "o/r#1@abc")"
check "done head: no insert (empty RETURNING)" "[[ -z \"\$out\" ]]"
check "still one row (no churn re-enqueue)" "[[ \"\$(q \"SELECT count(*) FROM requests WHERE dedupe_key='o/r#1@abc';\")\" == 1 ]]"

echo "[2b] a FAILED head IS allowed to re-enqueue (transient error should retry)"
q "INSERT INTO requests(kind,payload,dedupe_key,status) VALUES ('pr-review','{}','o/r#9@fail','failed');" >/dev/null
out="$(queue_enqueue pr-review '{"repo":"o/r","pr":"9"}' "o/r#9@fail")"
check "failed head: inserted (RETURNING 1)" "[[ \"\$out\" == 1 ]]"
check "now a queued row exists alongside the failed one" "q \"SELECT count(*) FROM requests WHERE dedupe_key='o/r#9@fail' AND status='queued';\" | grep -qx 1"

echo "[2c] retry cap: a head that has FAILED >= 3 times is NOT re-enqueued (poison PR)"
q "INSERT INTO requests(kind,payload,dedupe_key,status) VALUES ('pr-review','{}','o/r#99@poison','failed'),('pr-review','{}','o/r#99@poison','failed'),('pr-review','{}','o/r#99@poison','failed');" >/dev/null
out="$(queue_enqueue pr-review '{"repo":"o/r","pr":"99"}' "o/r#99@poison")"
check "capped head: NOT inserted (empty return)" "[[ -z \"\$out\" ]]"
check "no queued row created for capped head" "q \"SELECT count(*) FROM requests WHERE dedupe_key='o/r#99@poison' AND status='queued';\" | grep -qx 0"
echo "[2d] cap is configurable: PR_PRODUCER_MAX_ATTEMPTS=0 disables it"
out="$(PR_PRODUCER_MAX_ATTEMPTS=0 queue_enqueue pr-review '{"repo":"o/r","pr":"99"}' "o/r#99@poison")"
check "cap disabled (=0): capped head re-enqueues" "[[ \"\$out\" == 1 ]]"

echo "[3] NEW head -> new key -> fresh queued row (re-review on new commit)"
queue_enqueue pr-review '{"repo":"o/r","pr":"1"}' "o/r#1@def" >/dev/null
check "new-head row queued" "q \"SELECT status FROM requests WHERE dedupe_key='o/r#1@def';\" | grep -qx queued"

echo "[3b] NEW head supersedes an older QUEUED row of same lineage (no duplicate stacking)"
queue_enqueue pr-review '{"repo":"o/r","pr":"5"}' "o/r#5@old" >/dev/null
queue_enqueue pr-review '{"repo":"o/r","pr":"5"}' "o/r#5@new" >/dev/null
check "old head o/r#5@old now superseded" "q \"SELECT status FROM requests WHERE dedupe_key='o/r#5@old';\" | grep -qx superseded"
check "only the new head is still queued for o/r#5" "[[ \"\$(q \"SELECT count(*) FROM requests WHERE kind='pr-review' AND split_part(dedupe_key,'@',1)='o/r#5' AND status='queued';\")\" == 1 ]]"

echo "[3c] a RUNNING older head is NOT superseded by a new head (mid-analysis)"
q "INSERT INTO requests(kind,payload,dedupe_key,status,run_nonce,lease_expires_at) VALUES ('pr-review','{}','o/r#7@run','running','run',clock_timestamp()+interval '60 seconds');" >/dev/null
queue_enqueue pr-review '{"repo":"o/r","pr":"7"}' "o/r#7@new" >/dev/null
check "running head stays running" "q \"SELECT status FROM requests WHERE dedupe_key='o/r#7@run';\" | grep -qx running"
check "new head queued alongside it" "q \"SELECT status FROM requests WHERE dedupe_key='o/r#7@new';\" | grep -qx queued"

echo "[4] different kind, same repo/pr coexists (maintain vs review)"
queue_enqueue pr-maintain '{"repo":"o/r","pr":"1"}' "o/r#1@def" >/dev/null
check "review + maintain rows both present for same key" "[[ \"\$(q \"SELECT count(*) FROM requests WHERE dedupe_key='o/r#1@def';\")\" == 2 ]]"

echo "[5] kind-scoped claim: a pr-review worker never claims a pr-maintain row"
# only pr-maintain rows are queued at a fresh key; a review claim must return nothing for them
q "UPDATE requests SET status='done' WHERE kind='pr-review';" >/dev/null  # clear review queue
claim_r="$(queue_claim_one pr-review rr nr)"
check "pr-review claim returns empty when only pr-maintain is queued" "[[ -z \"\$claim_r\" ]]"
claim_m="$(queue_claim_one pr-maintain rm nm)"
check "pr-maintain claim returns the pr-maintain row" "[[ \"\$(jq -r '.kind' <<<\"\$claim_m\")\" == pr-maintain ]]"

echo "[6] pr-maintain runs cap at three per PR lineage"
q "UPDATE requests SET status='done',lease_expires_at=NULL WHERE kind='pr-maintain' AND split_part(dedupe_key,'@',1)='o/r#1';" >/dev/null
queue_enqueue pr-maintain '{"repo":"o/r","pr":"1"}' "o/r#1@second" >/dev/null
q "UPDATE requests SET status='done' WHERE dedupe_key='o/r#1@second';" >/dev/null
queue_enqueue pr-maintain '{"repo":"o/r","pr":"1"}' "o/r#1@third-a" > "$CAP_A" & cap_a=$!
queue_enqueue pr-maintain '{"repo":"o/r","pr":"1"}' "o/r#1@third-b" > "$CAP_B" & cap_b=$!
wait "$cap_a"; wait "$cap_b"
check "concurrent threshold admits exactly one third run" "[[ \"\$(cat '$CAP_A' '$CAP_B' | grep -c '^1$')\" == 1 ]]"
q "UPDATE requests SET status='done' WHERE kind='pr-maintain' AND split_part(dedupe_key,'@',1)='o/r#1';" >/dev/null
out="$(queue_enqueue pr-maintain '{"repo":"o/r","pr":"1"}' "o/r#1@fourth")"
rm -f "$CAP_A" "$CAP_B"
check "fourth maintenance run is suppressed" "[[ -z \"\$out\" ]]"
check "lineage has exactly three non-superseded runs" "q \"SELECT count(*) FROM requests WHERE kind='pr-maintain' AND split_part(dedupe_key,'@',1)='o/r#1' AND status<>'superseded';\" | grep -qx 3"

echo
[[ $fail -eq 0 ]] && echo "ALL PASS" || { echo "FAILURES"; exit 1; }
