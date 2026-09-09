#!/usr/bin/env bash
# Lease/concurrency contract: workers share one kind, different PRs run in parallel, one PR lineage
# never does, and an expired worker cannot mutate its replacement's attempt.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

CID="m1-lease-test-$$"
PORT=55440
cleanup(){ docker rm -f "$CID" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker run --rm -d --name "$CID" -e POSTGRES_PASSWORD=t -e POSTGRES_DB=fleet -p "$PORT:5432" postgres:16 >/dev/null
for _ in $(seq 1 30); do docker exec "$CID" pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
docker cp docker/initdb/01-schema.sql "$CID:/tmp/01.sql"
docker cp docker/initdb/02-agent-server.sql "$CID:/tmp/02.sql"
docker exec "$CID" psql -U postgres -d fleet -q -v ON_ERROR_STOP=1 -f /tmp/01.sql -f /tmp/02.sql >/dev/null

export REQUESTS_DB_USER=postgres REQUESTS_DB_NAME=fleet REQUESTS_DB_HOST=localhost REQUESTS_DB_PORT="$PORT" PGPASSWORD=t
# shellcheck source=../lib/queue.sh
. lib/queue.sh
set +e
fail=0
check(){ if eval "$2"; then echo "  PASS: $1"; else echo "  FAIL: $1"; fail=1; fi; }
q(){ docker exec "$CID" psql -U postgres -d fleet -tAc "$1"; }

queue_enqueue pr-maintain '{"repo":"o/a","pr":"1"}' 'o/a#1@old' >/dev/null
queue_enqueue pr-maintain '{"repo":"o/b","pr":"2"}' 'o/b#2@head' >/dev/null

claim_a="$(queue_claim_one pr-maintain run-a nonce-a 60)"
claim_b="$(queue_claim_one pr-maintain run-b nonce-b 60)"
id_a="$(jq -r .id <<<"$claim_a")"; id_b="$(jq -r .id <<<"$claim_b")"
key_a="$(jq -r .dedupe_key <<<"$claim_a")"; key_b="$(jq -r .dedupe_key <<<"$claim_b")"
running_count="$(q "SELECT count(*) FROM requests WHERE status='running';")"
check "two workers claim different PR lineages" "[[ \"$key_a\" != \"$key_b\" ]]"
check "both claims run concurrently" "[[ \"$running_count\" == 2 ]]"

# A new head may queue while the old head runs, but no worker may claim that lineage yet.
queue_enqueue pr-maintain '{"repo":"o/a","pr":"1"}' 'o/a#1@new' >/dev/null
claim_blocked="$(queue_claim_one pr-maintain run-c nonce-c 60)"
renewed="$(queue_renew_lease "$id_a" nonce-a 60)"
wrong_renew="$(queue_renew_lease "$id_a" wrong 60)"
wrong_side_effect="$(queue_mark_side_effect "$id_a" wrong)"
check "live lease blocks a second head of the same PR" "[[ -z \"$claim_blocked\" ]]"
check "correct nonce renews lease" "[[ \"$renewed\" == renewed ]]"
check "wrong nonce cannot renew lease" "[[ \"$wrong_renew\" == lost ]]"
check "wrong nonce cannot stamp side effects" "[[ -z \"$wrong_side_effect\" ]]"

# Simulate abrupt removal. Expired old head is superseded because its newer head is queued.
q "UPDATE requests SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=$id_a;" >/dev/null
queue_reclaim_stale pr-maintain >/dev/null
check "expired old head is superseded" "q \"SELECT status FROM requests WHERE id=$id_a;\" | grep -qx superseded"
claim_c="$(queue_claim_one pr-maintain run-c nonce-c 60)"; id_c="$(jq -r .id <<<"$claim_c")"
key_c="$(jq -r .dedupe_key <<<"$claim_c")"
stale_done="$(queue_mark_done "$id_c" stale nonce-a)"
fresh_done="$(queue_mark_done "$id_c" head=new nonce-c)"
check "replacement claims newer head" "[[ \"$key_c\" == o/a#1@new ]]"
check "expired nonce cannot finish replacement" "[[ -z \"$stale_done\" ]]"
check "replacement nonce finishes its attempt" "[[ \"$fresh_done\" == 1 ]]"

# With no newer head, an expired request returns to the shared queue.
q "UPDATE requests SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=$id_b;" >/dev/null
queue_reclaim_stale pr-maintain >/dev/null
check "orphaned request is requeued after lease expiry" "q \"SELECT status FROM requests WHERE id=$id_b;\" | grep -qx queued"
claim_d="$(queue_claim_one pr-maintain run-d nonce-d 60)"; id_d="$(jq -r .id <<<"$claim_d")"
stale_same_row="$(queue_mark_side_effect "$id_d" nonce-b)"
check "any surviving worker can claim orphaned work" "[[ \"$id_d\" == $id_b ]]"
check "old nonce cannot mutate same row after replacement" "[[ -z \"$stale_same_row\" ]]"
queue_mark_done "$id_d" head nonce-d >/dev/null

# Two simultaneous workers racing different queued heads still produce one active lineage.
q "INSERT INTO requests(kind,payload,dedupe_key) VALUES ('pr-maintain','{}','o/race#3@old'),('pr-maintain','{}','o/race#3@new');" >/dev/null
queue_claim_one pr-maintain race-a race-nonce-a 60 >/dev/null & pid_a=$!
queue_claim_one pr-maintain race-b race-nonce-b 60 >/dev/null & pid_b=$!
wait_a=0; wait_b=0
wait "$pid_a" || wait_a=$?; wait "$pid_b" || wait_b=$?
race_running="$(q "SELECT count(*) FROM requests WHERE status='running' AND split_part(dedupe_key,'@',1)='o/race#3';")"
race_superseded="$(q "SELECT count(*) FROM requests WHERE status='superseded' AND split_part(dedupe_key,'@',1)='o/race#3';")"
check "simultaneous claim SQL succeeds" "[[ \"$wait_a\" == 0 && \"$wait_b\" == 0 ]]"
check "simultaneous claims keep one running attempt per lineage" "[[ \"$race_running\" == 1 ]]"
check "simultaneous claims discard stale queued head" "[[ \"$race_superseded\" == 1 ]]"

# An abrupt loss after a possible external write is ambiguous and must never auto-replay.
queue_enqueue pr-maintain '{}' 'o/ambiguous#4@head' >/dev/null
claim_e="$(queue_claim_one pr-maintain run-e nonce-e 60)"; id_e="$(jq -r .id <<<"$claim_e")"
queue_mark_side_effect "$id_e" nonce-e >/dev/null
q "UPDATE requests SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=$id_e;" >/dev/null
queue_reclaim_stale pr-maintain >/dev/null
retry_e="$(queue_enqueue pr-maintain '{}' 'o/ambiguous#4@head')"
check "expired maintenance side effect requires reconciliation" "q \"SELECT status FROM requests WHERE id=$id_e;\" | grep -qx reconcile"
check "ambiguous head cannot auto-enqueue again" "[[ -z \"$retry_e\" ]]"

echo
[[ $fail -eq 0 ]] && echo "ALL PASS" || { echo "FAILURES"; exit 1; }
