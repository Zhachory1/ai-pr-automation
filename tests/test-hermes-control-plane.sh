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

echo "PASS: Hermes API ledger exact replay, fixed caps, route/operation fences, lease nonce, and doc migration"
