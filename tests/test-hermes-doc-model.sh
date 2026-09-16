#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

tmp="$(mktemp -d)"
cid="$(docker run -d --rm -p 127.0.0.1::5432 -e POSTGRES_PASSWORD=t -e POSTGRES_DB=fleet postgres:16)"
cleanup() { docker rm -f "$cid" >/dev/null 2>&1 || true; rm -rf "$tmp"; }
trap cleanup EXIT
for _ in $(seq 1 30); do docker exec "$cid" pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
for file in 01-schema.sql 02-agent-server.sql 03-human-review-queue.sql 04-pending-decision-approval.sql 06-hermes-doc-foundation.sql; do
  docker cp "docker/initdb/$file" "$cid:/tmp/$file"
done
docker exec "$cid" psql -U postgres -d fleet -q -v ON_ERROR_STOP=1 \
  -f /tmp/01-schema.sql -f /tmp/02-agent-server.sql -f /tmp/03-human-review-queue.sql \
  -f /tmp/04-pending-decision-approval.sql -f /tmp/06-hermes-doc-foundation.sql >/dev/null
port="$(docker port "$cid" 5432/tcp | awk -F: '{print $NF}')"
export REQUESTS_DB_USER=postgres REQUESTS_DB_NAME=fleet REQUESTS_DB_HOST=127.0.0.1 REQUESTS_DB_PORT="$port" PGPASSWORD=t
q() { docker exec "$cid" psql -U postgres -d fleet -qAt -v ON_ERROR_STOP=1 -c "$1"; }

stage="$tmp/stage"; mkdir -p "$stage"
generation="$(printf 'a%.0s' {1..64})"
cat > "$tmp/fake-hermes-run" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
run_id=""; run_id_file=""; run_id_ack_file=""; request_file=""; key=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --idempotency-key) key="$2"; shift 2 ;;
    --request-file) request_file="$2"; shift 2 ;;
    --run-id) run_id="$2"; shift 2 ;;
    --run-id-file) run_id_file="$2"; shift 2 ;;
    --run-id-ack-file) run_id_ack_file="$2"; shift 2 ;;
    *) shift 2 ;;
  esac
done
count=0; [[ -f "$CALL_COUNT" ]] && count="$(cat "$CALL_COUNT")"; count=$((count + 1)); printf '%s' "$count" > "$CALL_COUNT"
printf '%s\t%s\n' "$key" "$run_id" >> "$CALL_LOG"
id="${key#doc:}"; id="${id%%:*}"
case "$(cat "$MODE")" in
  unknown_once)
    if (( count == 1 )); then jq -cn '{status:"submit_unknown",raw_status:"transport_unknown",error:"unknown"}'; exit 4; fi ;;
  unknown_delete)
    rm -f "$request_file"
    jq -cn '{status:"submit_unknown",raw_status:"transport_unknown",error:"unknown"}'
    exit 4 ;;
  unknown) jq -cn '{status:"submit_unknown",raw_status:"transport_unknown",error:"unknown"}'; exit 4 ;;
  conflict) jq -cn '{status:"reconcile",raw_status:"idempotency_conflict",error:"conflict"}'; exit 3 ;;
  invalid) jq -cn --arg id "run-$id" '{status:"failed",run_id:$id,raw_status:"completed",error:"completed run omitted text output"}'; exit 2 ;;
  failed) jq -cn --arg id "run-$id" '{status:"failed",run_id:$id,raw_status:"failed",error:"provider failed"}'; exit 2 ;;
  delay) ;;
  lost)
    psql -qAt -v ON_ERROR_STOP=1 -h "$REQUESTS_DB_HOST" -p "$REQUESTS_DB_PORT" -U "$REQUESTS_DB_USER" -d "$REQUESTS_DB_NAME" \
      -c "UPDATE requests SET lease_expires_at=now()-interval '1 second' WHERE id=$id" >/dev/null ;;
esac
output="output-$id"
result_run_id="${run_id:-run-$id}"
if [[ -n "$run_id_file" ]]; then
  printf '%s\n' "$result_run_id" > "$run_id_file.tmp"
  mv "$run_id_file.tmp" "$run_id_file"
  for _ in $(seq 1 300); do [[ -e "$run_id_ack_file" ]] && break; sleep 0.01; done
  [[ "$(cat "$run_id_ack_file")" == "$result_run_id" ]]
  stored="$(psql -qAt -v ON_ERROR_STOP=1 -h "$REQUESTS_DB_HOST" -p "$REQUESTS_DB_PORT" \
    -U "$REQUESTS_DB_USER" -d "$REQUESTS_DB_NAME" -c \
    "SELECT coalesce(hermes_run_id,'') FROM hermes_doc_runs WHERE request_id=$id")"
  [[ "$stored" == "$result_run_id" ]]
fi
[[ "$(cat "$MODE")" != delay ]] || sleep 3
jq -cn --arg id "$result_run_id" --arg out "$output" \
  --arg digest "$(printf '%s' "$output" | python3 -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')" \
  '{status:"completed",run_id:$id,raw_status:"completed",output:$out,output_digest:$digest,
    usage:{input_tokens:10,output_tokens:2}}'
SH
chmod +x "$tmp/fake-hermes-run"

new_request() {
  q "INSERT INTO requests(kind,payload,dedupe_key,status,run_nonce,lease_expires_at)
     VALUES('doc-write','{}','doc:$1','running','owner',now()+interval '5 minutes') RETURNING id;"
}
run_model() {
  local expected="$1" id="$2" payload="$3" gen="${4:-$generation}" phase="${5:-draft}" rc=0
  local args=("$phase" --request-id "$id" --queue-nonce owner --runtime-generation "$gen" --stage-root "$stage")
  [[ "$phase" == draft ]] && args+=(--payload "$payload")
  HERMES_RUN_BIN="$tmp/fake-hermes-run" HERMES_DOC_URL=http://fake.invalid \
    HERMES_DOC_API_KEY=0123456789abcdef HERMES_DOC_POLL_INTERVAL=0.01 \
    MODE="$tmp/mode" CALL_COUNT="$tmp/calls" CALL_LOG="$tmp/call-log" \
    bin/hermes-doc-model "${args[@]}" > "$tmp/out" || rc=$?
  [[ "$rc" == "$expected" ]] || { cat "$tmp/out" >&2; echo "expected rc $expected, got $rc" >&2; exit 1; }
}
reset_fake() { printf '%s' "$1" > "$tmp/mode"; : > "$tmp/call-log"; rm -f "$tmp/calls"; }
payload() { printf '{"doc_type":"seprd","title":"Doc %s","requirements":"r"}' "$1"; }

id1="$(new_request complete)"; reset_fake completed
run_model 0 "$id1" "$(payload 1)"
[[ "$(q "SELECT state||'/'||submit_count||'/'||raw_status||'/'||(usage->>'input_tokens') FROM hermes_doc_runs WHERE request_id=$id1;")" == completed/1/completed/10 ]]
[[ "$(cat "$tmp/calls")" == 1 ]]
digest="$(q "SELECT output_digest FROM hermes_doc_runs WHERE request_id=$id1;")"
python3 - "$stage/requests/$id1/draft-response.json" "$digest" <<'PY'
import hashlib, json, sys
value = json.load(open(sys.argv[1]))["output"].encode()
assert hashlib.sha256(value).hexdigest() == sys.argv[2]
PY
run_model 0 "$id1" "$(payload 1)"
[[ "$(cat "$tmp/calls")" == 1 ]]
run_model 3 "$id1" "$(payload 1)" "$(printf 'b%.0s' {1..64})"
[[ "$(cat "$tmp/calls")" == 1 ]]
run_model 3 "$id1" '{"doc_type":"seprd","title":"Doc 1","requirements":"changed"}'
[[ "$(cat "$tmp/calls")" == 1 ]]
printf '{"output":"tampered"}\n' > "$stage/requests/$id1/draft-response.json"
run_model 3 "$id1" "$(payload 1)"

id_durable="$(new_request durable-id)"; reset_fake delay
run_model 0 "$id_durable" "$(payload durable-id)" & model_pid=$!
durable_run_id=""
for _ in $(seq 1 30); do
  durable_run_id="$(q "SELECT coalesce(hermes_run_id,'') FROM hermes_doc_runs WHERE request_id=$id_durable;")"
  [[ -n "$durable_run_id" ]] && break
  sleep 0.1
done
[[ "$durable_run_id" == "run-$id_durable" ]]
[[ "$(q "SELECT state FROM hermes_doc_runs WHERE request_id=$id_durable;")" == submitting ]]
wait "$model_pid"

id2="$(new_request known)"
rendered="$(bin/hermes-doc-request draft --stage-root "$stage" --request-id "$id2" --payload "$(payload 2)")"
request_digest="$(jq -r .request_digest <<<"$rendered")"
q "INSERT INTO hermes_doc_runs(request_id,phase,request_digest,runtime_generation,state,replay_until,submit_count,hermes_run_id)
   VALUES($id2,'draft','$request_digest','$generation','submitting',now()+interval '23 hours',1,'run-known');" >/dev/null
reset_fake completed; run_model 0 "$id2" "$(payload 2)"
[[ "$(cat "$tmp/calls")" == 1 ]]
[[ "$(cut -f2 "$tmp/call-log")" == run-known ]]
[[ "$(q "SELECT state||'/'||submit_count FROM hermes_doc_runs WHERE request_id=$id2;")" == completed/1 ]]

id3="$(new_request replay)"; reset_fake unknown_once; run_model 0 "$id3" "$(payload 3)"
[[ "$(cat "$tmp/calls")" == 2 ]]
[[ "$(q "SELECT state||'/'||submit_count FROM hermes_doc_runs WHERE request_id=$id3;")" == completed/2 ]]
[[ "$(cut -f1 "$tmp/call-log" | sort -u)" == "doc:$id3:draft" ]]

id4="$(new_request unknown)"; reset_fake unknown; run_model 3 "$id4" "$(payload 4)"
[[ "$(cat "$tmp/calls")" == 2 ]]
[[ "$(q "SELECT state||'/'||submit_count FROM hermes_doc_runs WHERE request_id=$id4;")" == reconcile/2 ]]

id_lost_body="$(new_request lost-body)"; reset_fake unknown_delete; run_model 3 "$id_lost_body" "$(payload lost-body)"
[[ "$(cat "$tmp/calls")" == 1 ]]
[[ "$(q "SELECT state||'/'||submit_count||'/'||raw_status FROM hermes_doc_runs WHERE request_id=$id_lost_body;")" == reconcile/1/request_evidence_lost ]]

id5="$(new_request conflict)"; reset_fake conflict; run_model 3 "$id5" "$(payload 5)"
[[ "$(q "SELECT state||'/'||raw_status FROM hermes_doc_runs WHERE request_id=$id5;")" == reconcile/idempotency_conflict ]]
id6="$(new_request invalid)"; reset_fake invalid; run_model 3 "$id6" "$(payload 6)"
[[ "$(q "SELECT state||'/'||raw_status FROM hermes_doc_runs WHERE request_id=$id6;")" == reconcile/completed ]]
id7="$(new_request failure)"; reset_fake failed; run_model 2 "$id7" "$(payload 7)"
[[ "$(q "SELECT state||'/'||raw_status FROM hermes_doc_runs WHERE request_id=$id7;")" == failed/failed ]]

id8="$(new_request expired)"
rendered="$(bin/hermes-doc-request draft --stage-root "$stage" --request-id "$id8" --payload "$(payload 8)")"
request_digest="$(jq -r .request_digest <<<"$rendered")"
q "INSERT INTO hermes_doc_runs(request_id,phase,request_digest,runtime_generation,state,replay_until)
   VALUES($id8,'draft','$request_digest','$generation','submitting',now()-interval '1 second');" >/dev/null
reset_fake completed; run_model 3 "$id8" "$(payload 8)"
[[ ! -e "$tmp/calls" ]]
[[ "$(q "SELECT state||'/'||submit_count FROM hermes_doc_runs WHERE request_id=$id8;")" == reconcile/0 ]]

id9="$(new_request lost)"; reset_fake lost; run_model 3 "$id9" "$(payload 9)"
[[ "$(q "SELECT state FROM hermes_doc_runs WHERE request_id=$id9;")" == submitting ]]

id10="$(new_request council)"; mkdir -p "$stage/requests/$id10"; printf '# Draft\n' > "$stage/requests/$id10/draft.md"
reset_fake completed; run_model 0 "$id10" "" "$generation" council
[[ "$(q "SELECT phase||'/'||state||'/'||submit_count FROM hermes_doc_runs WHERE request_id=$id10;")" == council/completed/1 ]]
[[ "$(cut -f1 "$tmp/call-log")" == "doc:$id10:council" ]]

[[ "$(q "SELECT count(*) FROM doc_publications;")" == 0 ]]
[[ "$(q "SELECT count(*) FROM pending_maintenance_reviews;")" == 0 ]]

echo "PASS: Hermes doc model binds durable attempts, bounds replay, fences leases, and cannot publish"
