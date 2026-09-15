#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

cid="$(docker run -d --rm -p 127.0.0.1::5432 -e POSTGRES_PASSWORD=t -e POSTGRES_DB=fleet postgres:16)"
cleanup() { docker rm -f "$cid" >/dev/null 2>&1 || true; }
trap cleanup EXIT
for _ in $(seq 1 30); do docker exec "$cid" pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
docker exec "$cid" pg_isready -U postgres >/dev/null

for file in 01-schema.sql 02-agent-server.sql 03-human-review-queue.sql 04-pending-decision-approval.sql 06-hermes-doc-foundation.sql; do
  docker cp "docker/initdb/$file" "$cid:/tmp/$file"
done
docker exec "$cid" psql -U postgres -d fleet -q -v ON_ERROR_STOP=1 \
  -f /tmp/01-schema.sql -f /tmp/02-agent-server.sql -f /tmp/03-human-review-queue.sql \
  -f /tmp/04-pending-decision-approval.sql -f /tmp/06-hermes-doc-foundation.sql >/dev/null
# Existing volumes replay every additive migration.
docker exec "$cid" psql -U postgres -d fleet -q -v ON_ERROR_STOP=1 \
  -f /tmp/02-agent-server.sql -f /tmp/03-human-review-queue.sql \
  -f /tmp/04-pending-decision-approval.sql -f /tmp/06-hermes-doc-foundation.sql >/dev/null

port="$(docker port "$cid" 5432/tcp | awk -F: '{print $NF}')"
export REQUESTS_DB_USER=postgres REQUESTS_DB_NAME=fleet REQUESTS_DB_HOST=127.0.0.1 REQUESTS_DB_PORT="$port" PGPASSWORD=t
# shellcheck source=../lib/queue.sh disable=SC1091
. lib/queue.sh

sql() { docker exec "$cid" psql -U postgres -d fleet -qAt -v ON_ERROR_STOP=1 -c "$1"; }
expect_fail() { if sql "$1" >/dev/null 2>&1; then echo "FAIL: accepted invalid row: $2" >&2; exit 1; fi; }
hex_a="$(printf 'a%.0s' {1..64})"
hex_b="$(printf 'b%.0s' {1..64})"
hex_c="$(printf 'c%.0s' {1..64})"

request1="$(sql "INSERT INTO requests(kind,payload,dedupe_key) VALUES('doc-write','{}','doc:1') RETURNING id;")"
request2="$(sql "INSERT INTO requests(kind,payload,dedupe_key) VALUES('doc-write','{}','doc:2') RETURNING id;")"
request3="$(sql "INSERT INTO requests(kind,payload,dedupe_key) VALUES('doc-write','{}','doc:3') RETURNING id;")"
request4="$(sql "INSERT INTO requests(kind,payload,dedupe_key) VALUES('doc-write','{}','doc:4') RETURNING id;")"

sql "INSERT INTO hermes_doc_runs(request_id,phase,request_digest,runtime_generation,state,replay_until)
     VALUES($request1,'draft','$hex_a','$hex_b','submitting',now()+interval '23 hours');" >/dev/null
expect_fail "INSERT INTO hermes_doc_runs(request_id,phase,request_digest,runtime_generation,state,replay_until)
             VALUES($request1,'council','$hex_a','$hex_b','submitting',now()+interval '23 hours');" "two open phases for one request"
expect_fail "INSERT INTO hermes_doc_runs(request_id,phase,request_digest,runtime_generation,state,replay_until,submit_count,output_digest)
             VALUES($request2,'draft','$hex_a','$hex_b','completed',now(),0,'$hex_c');" "completed run without run id"
expect_fail "INSERT INTO hermes_doc_runs(request_id,phase,request_digest,runtime_generation,state,replay_until,submit_count)
             VALUES($request2,'draft','$hex_a','$hex_b','submitting',now(),3);" "third submit reservation"
sql "INSERT INTO hermes_doc_runs(request_id,phase,request_digest,runtime_generation,state,replay_until,submit_count,hermes_run_id,output_digest,usage)
     VALUES($request2,'draft','$hex_a','$hex_b','completed',now(),1,'run-2','$hex_c','{\"input_tokens\":10}');" >/dev/null
expect_fail "INSERT INTO hermes_doc_runs(request_id,phase,request_digest,runtime_generation,state,replay_until)
             VALUES($request1,'draft','$hex_a','$hex_b','submitting',now());" "duplicate request phase"

sql "INSERT INTO doc_publications(request_id,state,staged_path,target_path,content_digest,document_generation)
     VALUES($request1,'awaiting_approval','requests/$request1/publish.md','seprd-2026-09-14-one.md','$hex_a','legacy:$hex_b');" >/dev/null
expect_fail "UPDATE doc_publications SET target_path='seprd-2026-09-14-changed.md' WHERE request_id=$request1;" "mutable publication binding"
expect_fail "INSERT INTO doc_publications(request_id,state,staged_path,target_path,content_digest,document_generation)
             VALUES($request2,'reconcile','requests/$request2/publish.md','seprd-2026-09-14-two.md','$hex_a','legacy:$hex_b');" "unapproved reconcile"
expect_fail "INSERT INTO doc_publications(request_id,state,staged_path,target_path,content_digest,document_generation)
             VALUES($request3,'awaiting_approval','../publish.md','seprd-2026-09-14-three.md','$hex_a','legacy:$hex_b');" "traversal stage path"
expect_fail "INSERT INTO doc_publications(request_id,state,staged_path,target_path,content_digest,document_generation)
             VALUES($request3,'awaiting_approval','requests/$request3/publish.md','../three.md','$hex_a','legacy:$hex_b');" "invalid target path"

sql "INSERT INTO doc_publications(request_id,state,staged_path,target_path,content_digest,document_generation,approved_at)
     VALUES($request3,'approved','requests/$request3/publish.md','dd-2026-09-14-three.md','$hex_a','hermes:$hex_b',now());" >/dev/null
expect_fail "UPDATE doc_publications SET content_digest='$hex_c' WHERE request_id=$request3;" "mutable approved digest"
expect_fail "UPDATE doc_publications SET state='published' WHERE request_id=$request3;" "published without timestamp"
sql "UPDATE doc_publications SET state='published',published_at=now() WHERE request_id=$request3;" >/dev/null
expect_fail "INSERT INTO doc_publications(request_id,state,staged_path,target_path,content_digest,document_generation)
             VALUES($request4,'awaiting_approval','requests/$request4/publish.md','dd-2026-09-14-three.md','$hex_a','legacy:$hex_b');" "duplicate target"

# Queue-owned Hermes phase transitions stay fenced by current request nonce.
sql "UPDATE requests SET status='running',run_nonce='owner1',lease_expires_at=now()+interval '5 minutes' WHERE id=$request1;" >/dev/null
[[ "$(hermes_doc_run_begin "$request1" draft "$hex_a" "$hex_b" 82800 owner1 | jq -r .state)" == submitting ]]
[[ -z "$(hermes_doc_run_begin "$request1" draft "$hex_a" "$hex_b" 82800 wrong)" ]]
[[ "$(hermes_doc_run_reserve_submit "$request1" draft owner1)" == 1 ]]
[[ "$(hermes_doc_run_reserve_submit "$request1" draft owner1)" == 2 ]]
[[ -z "$(hermes_doc_run_reserve_submit "$request1" draft owner1)" ]]
[[ "$(hermes_doc_run_finish "$request1" draft completed run-1 completed "$hex_c" '{"input_tokens":10}' '' owner1)" == 1 ]]

# Open-question insertion and request completion are one statement.
request5="$(sql "INSERT INTO requests(kind,payload,dedupe_key,status,run_nonce,lease_expires_at)
  VALUES('doc-write','{}','doc:5','running','owner5',now()+interval '5 minutes') RETURNING id;")"
[[ "$(pending_doc_review_finish "$request5" '{"kind":"doc-open-questions"}' '{"request_id":"5"}' 'awaiting answers' owner5)" == 1 ]]
[[ "$(sql "SELECT status FROM requests WHERE id=$request5;")" == "done" ]]
[[ "$(sql "SELECT count(*) FROM pending_maintenance_reviews WHERE request_id=$request5;")" == 1 ]]

# Exact publication approval requeues the same request, then preparation quarantines before effect.
request6="$(sql "INSERT INTO requests(kind,payload,dedupe_key,status,run_nonce,lease_expires_at)
  VALUES('doc-write','{}','doc:6','running','owner6',now()+interval '5 minutes') RETURNING id;")"
proposal='{"kind":"doc-publication-approval"}'
provenance='{"request_id":"6"}'
[[ "$(doc_publication_stage "$request6" 'dd-2026-09-14-six.md' "$hex_a" "legacy:$hex_b" "$proposal" "$provenance" owner6)" == 1 ]]
[[ "$(doc_publication_approve "$request6")" == "$request6" ]]
[[ "$(sql "SELECT status||'/'||(payload->>'publication_only')||'/'||split_part(dedupe_key,'@',1) FROM requests WHERE id=$request6;")" == "queued/true/doc-publish:$request6" ]]
sql "UPDATE requests SET status='running',run_nonce='owner6b',lease_expires_at=now()+interval '5 minutes' WHERE id=$request6;" >/dev/null
[[ -z "$(doc_publication_prepare "$request6" 'dd-2026-09-14-six.md' "$hex_a" "legacy:$hex_b" wrong)" ]]
[[ "$(doc_publication_prepare "$request6" 'dd-2026-09-14-six.md' "$hex_a" "legacy:$hex_b" owner6b)" == "$request6" ]]
[[ "$(sql "SELECT status||'/'||(side_effect_at IS NOT NULL)::text FROM requests WHERE id=$request6;")" == reconcile/true ]]
[[ "$(doc_publication_mark_published "$request6" 'dd-2026-09-14-six.md' "$hex_a" "legacy:$hex_b")" == "$request6" ]]
[[ "$(sql "SELECT r.status||'/'||p.state||'/'||r.posted_ref FROM requests r JOIN doc_publications p ON p.request_id=r.id WHERE r.id=$request6;")" == done/published/dd-2026-09-14-six.md ]]

request7="$(sql "INSERT INTO requests(kind,payload,dedupe_key,status,run_nonce,lease_expires_at)
  VALUES('doc-write','{}','doc:7','running','owner7',now()+interval '5 minutes') RETURNING id;")"
[[ "$(doc_publication_stage "$request7" 'seprd-2026-09-14-seven.md' "$hex_a" "legacy:$hex_b" "$proposal" "$provenance" owner7)" == 1 ]]
[[ "$(doc_publication_dismiss "$request7")" == "$request7" ]]
[[ "$(sql "SELECT p.state||'/'||h.state FROM doc_publications p JOIN pending_maintenance_reviews h ON h.request_id=p.request_id WHERE p.request_id=$request7;")" == dismissed/dismissed ]]

request10="$(sql "INSERT INTO requests(kind,payload,dedupe_key,status,run_nonce,lease_expires_at)
  VALUES('doc-write','{}','doc:10','running','owner10',now()+interval '5 minutes') RETURNING id;")"
[[ "$(doc_publication_stage "$request10" 'seprd-2026-09-14-ten.md' "$hex_a" "legacy:$hex_b" "$proposal" "$provenance" owner10)" == 1 ]]
sql "DELETE FROM pending_maintenance_reviews WHERE request_id=$request10;" >/dev/null
[[ -z "$(doc_publication_dismiss "$request10")" ]]
[[ "$(sql "SELECT state FROM doc_publications WHERE request_id=$request10;")" == awaiting_approval ]]

# Rollback quarantine never waits for provider state.
request8="$(sql "INSERT INTO requests(kind,payload,dedupe_key,status,run_nonce,lease_expires_at)
  VALUES('doc-write','{}','doc:8','running','owner8',now()+interval '5 minutes') RETURNING id;")"
run8="$(hermes_doc_run_begin "$request8" draft "$hex_a" "$hex_b" 82800 owner8)"
[[ -n "$run8" ]] || { sql "SELECT id,kind,status,run_nonce,lease_expires_at>now() FROM requests WHERE id=$request8;" >&2; exit 1; }
hermes_doc_quarantine >/dev/null
[[ "$(sql "SELECT r.status||'/'||h.state FROM requests r JOIN hermes_doc_runs h ON h.request_id=r.id WHERE r.id=$request8;")" == reconcile/reconcile ]]

# Legacy doc side-effect expiry reconciles before generic requeue.
request9="$(sql "INSERT INTO requests(kind,payload,dedupe_key,status,run_nonce,lease_expires_at,side_effect_at)
  VALUES('doc-write','{}','doc:9','running','owner9',now()-interval '1 second',now()) RETURNING id;")"
queue_reclaim_stale doc-write >/dev/null
[[ "$(sql "SELECT status FROM requests WHERE id=$request9;")" == reconcile ]]

echo "PASS: Hermes doc schema and queue transitions are idempotent, atomic, and nonce-fenced"
