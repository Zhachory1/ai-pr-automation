#!/usr/bin/env bash
# M2: producer enqueue + dedupe semantics against real Postgres.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
CID="m2-producer-test-$$"; PORT=55451
cleanup(){ docker rm -f "$CID" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker run --rm -d --name "$CID" -e POSTGRES_PASSWORD=t -e POSTGRES_DB=fleet -p "$PORT:5432" postgres:16 >/dev/null
for _ in $(seq 1 30); do docker exec "$CID" pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
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

echo "[3] NEW head -> new key -> fresh queued row (re-review on new commit)"
queue_enqueue pr-review '{"repo":"o/r","pr":"1"}' "o/r#1@def" >/dev/null
check "new-head row queued" "q \"SELECT status FROM requests WHERE dedupe_key='o/r#1@def';\" | grep -qx queued"

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

echo
[[ $fail -eq 0 ]] && echo "ALL PASS" || { echo "FAILURES"; exit 1; }
