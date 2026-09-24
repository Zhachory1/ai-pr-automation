#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
container="hermes-control-plane-$$"
cleanup(){ docker rm -f "$container" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker run --rm -d --name "$container" -e POSTGRES_PASSWORD=t -e POSTGRES_DB=fleet postgres:16 >/dev/null
for _ in $(seq 1 30); do docker exec "$container" pg_isready -U postgres -d fleet >/dev/null 2>&1 && break; sleep 1; done
docker cp docker/initdb "$container:/migrations" >/dev/null
docker exec "$container" sh -ec 'for f in /migrations/0[1-9]-*.sql /migrations/1[0-9]-*.sql; do psql -U postgres -d fleet -v ON_ERROR_STOP=1 -qf "$f"; done'
q(){ docker exec "$container" psql -U postgres -d fleet -qAt -v ON_ERROR_STOP=1 "$@"; }
fail(){ echo "FAIL: $*" >&2; exit 1; }

[[ "$(q -c "SELECT to_regclass('hermes_doc_runs') IS NULL")" == t ]] || fail "specialized doc ledger remains"
[[ "$(q -c "SELECT string_agg(kind||'='||max_concurrent,',' ORDER BY kind) FROM hermes_kind_routes")" == \
  "doc-write=1,memory-curate=1,pr-maintain=3,pr-review=1,pr-safety-review=1,swe-implement=1" ]] || fail "fixed caps"
gen="$(printf 'a%.0s' {1..64})"
[[ "$(q -c "SELECT hermes_configure_api_route('pr-maintain','pr-maintain-v1',1,'$gen')")" == t ]] || fail "route configure"

for i in 1 2 3 4; do
  q -c "INSERT INTO requests(kind,payload,dedupe_key) VALUES('pr-maintain','{\"repo\":\"o/r\",\"number\":$i}'::jsonb,'o/r#$i@$(printf 'b%.0s' {1..40})')" >/dev/null
done
[[ -z "$(q -c "SELECT hermes_claim_request('pr-maintain','native-run','99999999999999999999999999999999',120)")" ]] || fail "native claim crossed API route"
claim(){ local id="$1" nonce="$2"; q -c "SELECT hermes_claim_request('pr-maintain','api',$id,'exact prompt $id','pr-maintain-v1',1,'$gen','$nonce',120)"; }
a1="$(claim 1 11111111111111111111111111111111)"; [[ -n "$a1" ]] || fail "first claim"
a2="$(claim 2 22222222222222222222222222222222)"; [[ -n "$a2" ]] || fail "second claim"
a3="$(claim 3 33333333333333333333333333333333)"; [[ -n "$a3" ]] || fail "third claim"
[[ -z "$(claim 4 44444444444444444444444444444444)" ]] || fail "maintain cap exceeded"
[[ "$(q -c "SELECT request_digest=encode(digest(request_bytes,'sha256'),'hex') FROM hermes_runs WHERE request_id=1")" == t ]] || fail "exact request digest"

first="$(q -c "SELECT hermes_api_begin_submit(1,1,'11111111111111111111111111111111',1)")"
second="$(q -c "SELECT hermes_api_begin_submit(1,1,'11111111111111111111111111111111',1)")"
[[ "$(jq -r .idempotency_key <<<"$first")" == "$(jq -r .idempotency_key <<<"$second")" ]] || fail "idempotency key changed"
[[ "$(jq -r .request_b64 <<<"$first")" == "$(jq -r .request_b64 <<<"$second")" ]] || fail "request bytes changed on replay"
[[ "$(q -c "SELECT replay_until=first_submit_at+interval '23 hours' FROM hermes_runs WHERE request_id=1")" == t ]] || fail "replay boundary"
[[ "$(q -c "SELECT hermes_switch_route('pr-maintain','api',1,'native')")" == f ]] || fail "route switched with active attempts"

q -c "UPDATE requests SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=1" >/dev/null
[[ "$(q -c "SELECT hermes_renew_request(1,'11111111111111111111111111111111',120)")" == f ]] || fail "expired lease renewed through normal path"
[[ "$(q -c "SELECT hermes_finish_api_attempt(1,1,'11111111111111111111111111111111',1,'done','stale','','completed')")" == f ]] || fail "stale nonce settled"
[[ "$(q -c "SELECT hermes_api_recover_lease(1,1,'11111111111111111111111111111111',120)")" == t ]] || fail "same attempt did not recover"
[[ "$(q -c "SELECT hermes_finish_api_attempt(1,1,'11111111111111111111111111111111',1,'reconcile','uncertain','','reconcile')")" == t ]] || fail "reconcile settle"

[[ "$(q -c "SELECT hermes_configure_api_route('pr-review','pr-review-v1',1,'$gen')")" == t ]] || fail "review route configure"
head="$(printf 'c%.0s' {1..40})"
q -c "INSERT INTO requests(kind,payload,dedupe_key) VALUES('pr-review','{\"repo\":\"o/r\",\"number\":9}'::jsonb,'o/r#9@$head')" >/dev/null
review="$(q -c "SELECT hermes_claim_request('pr-review','api',5,'review prompt','pr-review-v1',1,'$gen','55555555555555555555555555555555',120)")"
[[ -n "$review" ]] || fail "review claim"
[[ "$(q -c "SELECT hermes_finish_api_attempt(5,1,'55555555555555555555555555555555',1,'reconcile','uncertain','','reconcile')")" == t ]] || fail "review reconcile"
q -c "INSERT INTO requests(kind,payload,dedupe_key) VALUES('pr-review','{\"repo\":\"o/r\",\"number\":9}'::jsonb,'o/r#9@$head')" >/dev/null
[[ -z "$(q -c "SELECT hermes_claim_request('pr-review','api',6,'review prompt','pr-review-v1',1,'$gen','66666666666666666666666666666666',120)")" ]] || fail "unresolved operation admitted"
[[ "$(q -c "SELECT hermes_reconcile_api_attempt(5,1,'not-done','operator','verified no review posted')")" == t ]] || fail "human disposition"
[[ "$(q -c "SELECT status FROM requests WHERE id=5")" == failed ]] || fail "human disposition request state"
review_retry="$(q -c "SELECT hermes_claim_request('pr-review','api',6,'review prompt','pr-review-v1',1,'$gen','66666666666666666666666666666666',120)")"
[[ -n "$review_retry" ]] || fail "resolved operation remained blocked"
q -c "SELECT hermes_finish_api_attempt(6,1,'66666666666666666666666666666666',1,'failed','test','','failed')" >/dev/null
q -c "SELECT hermes_finish_api_attempt(2,1,'22222222222222222222222222222222',1,'failed','test','','failed')" >/dev/null
q -c "SELECT hermes_finish_api_attempt(3,1,'33333333333333333333333333333333',1,'failed','test','','failed')" >/dev/null

[[ "$(q -c "SELECT hermes_configure_api_route('pr-safety-review','pr-safety-v1',1,'$gen')")" == t ]] || fail "safety route configure"
safety_nonce=77777777777777777777777777777777
operation=op-kanban
workflow="pr-risk-council-$(printf %s "$operation:$safety_nonce" | shasum -a 256 | awk '{print substr($1,1,32)}')"
safety_payload='{"operation_id":"op-kanban","repo":"o/r","pr":7,"head_sha":"h","base_sha":"b","diff_hash":"d","policy_version":"v1","policy_digest":"p","snapshot_path":"/snap","policy_path":"/policy"}'
safety_body="$(jq -cS --arg nonce "$safety_nonce" '. + {nonce:$nonce}' <<<"$safety_payload")"
safety_hex="$(printf %s "$safety_body" | xxd -p | tr -d '\n')"
safety_digest="$(printf %s "$safety_body" | shasum -a 256 | awk '{print $1}')"
q -c "INSERT INTO requests(kind,payload,dedupe_key) VALUES('pr-safety-review','$safety_payload'::jsonb,'safety-one')" >/dev/null
kanban_claim(){ q -c "SELECT hermes_claim_kanban_safety_request(7,'$safety_nonce',120,$1,1,'$gen','$2',decode('$safety_hex','hex'),'$3')"; }
[[ -z "$(kanban_claim 2 "$workflow" "$safety_digest")" ]] || fail "Kanban claim ignored route generation"
! kanban_claim 1 "$workflow" "$(printf '0%.0s' {1..64})" >/dev/null 2>&1 || fail "Kanban claim accepted digest mismatch"
! kanban_claim 1 pr-risk-council-00000000000000000000000000000000 "$safety_digest" >/dev/null 2>&1 || fail "Kanban claim accepted workflow mismatch"
kanban="$(kanban_claim 1 "$workflow" "$safety_digest")"; [[ -n "$kanban" ]] || fail "Kanban safety claim"
[[ "$(jq -r .run_id <<<"$kanban")" == "kanban:$workflow" ]] || fail "Kanban marker"
[[ "$(q -c "SELECT profile='pr-safety-v1' AND profile_generation='$gen' AND request_bytes=decode('$safety_hex','hex') AND request_digest='$safety_digest' FROM hermes_runs WHERE request_id=7")" == t ]] || fail "Kanban exact bytes or generation"
[[ "$(q -c "SELECT hermes_pr_safety_operation_active('$operation')")" == t ]] || fail "running Kanban snapshot not active"
retry_nonce=88888888888888888888888888888888
retry_workflow="pr-risk-council-$(printf %s "$operation:$retry_nonce" | shasum -a 256 | awk '{print substr($1,1,32)}')"
retry_body="$(jq -cS --arg nonce "$retry_nonce" '. + {nonce:$nonce}' <<<"$safety_payload")"
retry_hex="$(printf %s "$retry_body" | xxd -p | tr -d '\n')"
retry_digest="$(printf %s "$retry_body" | shasum -a 256 | awk '{print $1}')"
q -c "UPDATE requests SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=7" >/dev/null
[[ "$(q -c "SELECT (x->>'lease_expired')::boolean AND NOT (x->>'stop_requested')::boolean AND NOT (x->>'stop_confirmed')::boolean FROM hermes_api_open_attempts() AS x WHERE (x->>'request_id')::bigint=7")" == t ]] || fail "expired Kanban recovery flags"
[[ "$(q -c "SELECT hermes_api_recover_lease(7,1,'$safety_nonce',120)")" == f ]] || fail "expired Kanban lease recovered before stop intent"
[[ "$(q -c "SELECT hermes_api_prepare_kanban_stop(7,1,'$safety_nonce')")" == t ]] || fail "Kanban stop intent prepare"
[[ "$(q -c "SELECT (x->>'stop_requested')::boolean AND NOT (x->>'stop_confirmed')::boolean FROM hermes_api_open_attempts() AS x WHERE (x->>'request_id')::bigint=7")" == t ]] || fail "Kanban stop intent not exposed"
[[ "$(q -c "SELECT hermes_api_recover_lease(7,1,'$safety_nonce',120)")" == f ]] || fail "stop-pending Kanban lease recovered"
[[ "$(q -c "SELECT hermes_api_confirm_kanban_stop(7,1,'$safety_nonce')")" == t ]] || fail "Kanban stop confirmation"
[[ "$(q -c "SELECT (x->>'stop_confirmed')::boolean FROM hermes_api_open_attempts() AS x WHERE (x->>'request_id')::bigint=7")" == t ]] || fail "Kanban stop confirmation not exposed"
[[ "$(q -c "SELECT hermes_api_recover_lease(7,1,'$safety_nonce',120)")" == t ]] || fail "stop-confirmed Kanban lease not recovered"
[[ "$(q -c "SELECT hermes_settle_pr_safety_request(7,'$safety_nonce','failed','test',false,NULL,NULL)")" == t ]] || fail "Kanban failure settlement"
[[ "$(q -c "SELECT hermes_pr_safety_operation_active('$operation')")" == t ]] || fail "settled Kanban snapshot released before archive completion"
[[ "$(q -c "SELECT x->>'request_status' FROM hermes_api_open_attempts() AS x WHERE (x->>'request_id')::bigint=7")" == failed ]] || fail "settled Kanban attempt not recoverable"
[[ "$(q -c "SELECT hermes_complete_effect_attempt(7,1,'$safety_nonce','failed','test')")" == t ]] || fail "Kanban attempt completion"
[[ "$(q -c "SELECT hermes_pr_safety_operation_active('$operation')")" == f ]] || fail "completed Kanban snapshot remained active"
q -c "INSERT INTO requests(kind,payload,dedupe_key) VALUES('pr-safety-review','$safety_payload'::jsonb,'safety-two')" >/dev/null
retry="$(q -c "SELECT hermes_claim_kanban_safety_request(8,'$retry_nonce',120,1,1,'$gen','$retry_workflow',decode('$retry_hex','hex'),'$retry_digest')")"
[[ -n "$retry" && "$(jq -r .run_id <<<"$retry")" == "kanban:$retry_workflow" ]] || fail "failed Kanban operation did not requeue with new workflow"
[[ "$workflow" != "$retry_workflow" && "$(q -c "SELECT count(DISTINCT run_id)=2 FROM hermes_runs WHERE request_id IN (7,8)")" == t ]] || fail "retry workflow collided with prior run id"
[[ "$(q -c "SELECT hermes_settle_pr_safety_request(8,'$retry_nonce','failed','test',false,NULL,NULL)")" == t ]] || fail "retry settlement"
[[ "$(q -c "SELECT hermes_complete_effect_attempt(8,1,'$retry_nonce','failed','test')")" == t ]] || fail "retry completion"

terminal_nonce=99999999999999999999999999999999
terminal_workflow="pr-risk-council-$(printf %s "$operation:$terminal_nonce" | shasum -a 256 | awk '{print substr($1,1,32)}')"
terminal_body="$(jq -cS --arg nonce "$terminal_nonce" '. + {nonce:$nonce}' <<<"$safety_payload")"
terminal_hex="$(printf %s "$terminal_body" | xxd -p | tr -d '\n')"
terminal_digest="$(printf %s "$terminal_body" | shasum -a 256 | awk '{print $1}')"
terminal_output="$(printf '{\"verdict\":\"clear\",\"padding\":\"%080d\"}' 0)"
terminal_output_hex="$(printf %s "$terminal_output" | xxd -p | tr -d '\n')"
q -c "INSERT INTO requests(kind,payload,dedupe_key) VALUES('pr-safety-review','$safety_payload'::jsonb,'safety-terminal')" >/dev/null
terminal_claim="$(q -c "SELECT hermes_claim_kanban_safety_request(9,'$terminal_nonce',120,1,1,'$gen','$terminal_workflow',decode('$terminal_hex','hex'),'$terminal_digest')")"
[[ -n "$terminal_claim" ]] || fail "terminal recovery claim"
[[ "$(q -c "SELECT hermes_api_record_terminal(9,1,'$terminal_nonce','completed',decode('$terminal_output_hex','hex'))")" == t ]] || fail "terminal record"
q -c "UPDATE requests SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=9" >/dev/null
[[ "$(q -c "SELECT x->>'terminal_status'='completed' AND x->>'output_digest'=encode(digest(convert_to('$terminal_output','UTF8'),'sha256'),'hex') AND x->>'output_b64'=replace(encode(convert_to('$terminal_output','UTF8'),'base64'),E'\\n','') FROM hermes_api_open_attempts() AS x WHERE (x->>'request_id')::bigint=9")" == t ]] || fail "immutable terminal evidence not exposed"
[[ "$(q -c "SELECT hermes_api_recover_lease(9,1,'$terminal_nonce',120)")" == t ]] || fail "expired terminal-recorded Kanban lease not recovered"
[[ "$(q -c "SELECT hermes_api_record_terminal(9,1,'$terminal_nonce','completed',decode('$terminal_output_hex','hex'))")" == t ]] || fail "same terminal record was not idempotent"
! q -c "SELECT hermes_api_record_terminal(9,1,'$terminal_nonce','failed',convert_to('different','UTF8'))" >/dev/null 2>&1 || fail "completed terminal was overwritten as failed"
[[ "$(q -c "SELECT terminal_status='completed' AND output_bytes=decode('$terminal_output_hex','hex') FROM hermes_runs WHERE request_id=9")" == t ]] || fail "completed terminal changed after overwrite attempt"
[[ "$(q -c "SELECT hermes_settle_pr_safety_request(9,'$terminal_nonce','done','clear',false,NULL,NULL)")" == t ]] || fail "terminal recovery settlement"
[[ "$(q -c "SELECT hermes_complete_effect_attempt(9,1,'$terminal_nonce','completed',NULL)")" == t ]] || fail "terminal recovery completion"

[[ "$(q -c "SELECT hermes_switch_route('pr-maintain','api',1,'native')")" == t ]] || fail "drained route did not switch"
[[ "$(q -c "SELECT generation FROM hermes_kind_routes WHERE kind='pr-maintain'")" == 2 ]] || fail "route generation did not advance"

docker exec "$container" createdb -U postgres legacy
docker exec "$container" sh -ec 'for f in /migrations/0[1-9]-*.sql /migrations/1[0-2]-*.sql; do psql -U postgres -d legacy -v ON_ERROR_STOP=1 -qf "$f"; done'
docker exec -i "$container" psql -U postgres -d legacy -v ON_ERROR_STOP=1 -qAt <<'SQL'
INSERT INTO requests(kind,payload,dedupe_key,status) VALUES('doc-write','{"doc_type":"dd","title":"legacy"}'::jsonb,'legacy-doc','done');
INSERT INTO hermes_doc_runs(request_id,phase,request_digest,runtime_generation,state,submit_count,
  hermes_run_id,raw_status,replay_until,output_digest)
VALUES(1,'draft',repeat('b',64),repeat('a',64),'completed',1,'legacy-run','completed',
  clock_timestamp()+interval '1 hour',repeat('c',64));
SQL
docker exec "$container" psql -U postgres -d legacy -v ON_ERROR_STOP=1 -qf /migrations/13-hermes-api-control-plane.sql
[[ "$(docker exec "$container" psql -U postgres -d legacy -qAt -c "SELECT to_regclass('hermes_doc_runs') IS NULL AND state='completed' AND run_id='legacy-run' AND reconcile_reason LIKE 'legacy output digest:%' FROM hermes_runs")" == t ]] || fail "legacy doc evidence migration"

echo "PASS: Hermes API ledger exact replay, Kanban atomic claim/recovery, fixed caps, route/operation fences, lease nonce, and doc migration"
