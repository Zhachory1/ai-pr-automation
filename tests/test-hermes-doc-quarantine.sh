#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

cid="$(docker run -d --rm -p 127.0.0.1::5432 -e POSTGRES_PASSWORD=t -e POSTGRES_DB=fleet postgres:16)"
cleanup() { docker rm -f "$cid" >/dev/null 2>&1 || true; }
trap cleanup EXIT
for _ in $(seq 1 30); do docker exec "$cid" pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
docker exec "$cid" pg_isready -U postgres >/dev/null
for file in 01-schema.sql 02-agent-server.sql 06-hermes-doc-foundation.sql; do docker cp "docker/initdb/$file" "$cid:/tmp/$file"; done
docker exec "$cid" psql -U postgres -d fleet -q -v ON_ERROR_STOP=1 \
  -f /tmp/01-schema.sql -f /tmp/02-agent-server.sql -f /tmp/06-hermes-doc-foundation.sql >/dev/null

port="$(docker port "$cid" 5432/tcp | awk -F: '{print $NF}')"
export REQUESTS_DB_USER=postgres REQUESTS_DB_NAME=fleet REQUESTS_DB_HOST=127.0.0.1 REQUESTS_DB_PORT="$port" PGPASSWORD=t
# shellcheck source=../lib/queue.sh disable=SC1091
. lib/queue.sh
sql() { docker exec "$cid" psql -U postgres -d fleet -qAt -v ON_ERROR_STOP=1 -c "$1"; }
hex="$(printf 'a%.0s' {1..64})"

attached_running="$(sql "INSERT INTO requests(kind,payload,dedupe_key,status,run_nonce,lease_expires_at)
  VALUES('doc-write','{}','attached-running','running','owner',now()+interval '1 hour') RETURNING id;")"
attached_queued="$(sql "INSERT INTO requests(kind,payload,dedupe_key) VALUES('doc-write','{}','attached-queued') RETURNING id;")"
attached_terminal="$(sql "INSERT INTO requests(kind,payload,dedupe_key) VALUES('doc-write','{}','attached-terminal') RETURNING id;")"
unattached="$(sql "INSERT INTO requests(kind,payload,dedupe_key) VALUES('doc-write','{}','unattached') RETURNING id;")"
for id in "$attached_running" "$attached_queued"; do
  sql "INSERT INTO hermes_doc_runs(request_id,phase,request_digest,runtime_generation,state,replay_until,submit_count)
    VALUES($id,'draft','$hex','$hex','submitting',now()+interval '23 hours',1);" >/dev/null
done
sql "INSERT INTO hermes_doc_runs(request_id,phase,request_digest,runtime_generation,state,replay_until,error)
  VALUES($attached_terminal,'draft','$hex','$hex','failed',now(),'known failure');" >/dev/null

started="$(date +%s)"
hermes_doc_quarantine >/dev/null
elapsed=$(( $(date +%s) - started ))
[[ "$elapsed" -lt 900 ]]
[[ "$(sql "SELECT string_agg(r.status||'/'||h.state||'/'||h.submit_count,',' ORDER BY r.id)
  FROM requests r JOIN hermes_doc_runs h ON h.request_id=r.id;")" == "reconcile/reconcile/1,reconcile/reconcile/1,reconcile/failed/0" ]]
[[ "$(sql "SELECT status FROM requests WHERE id=$unattached;")" == queued ]]
[[ "$(sql "SELECT count(*) FROM hermes_doc_runs;")" == 3 ]]

# Repeated rollback is a no-op; attached work never becomes legacy-eligible.
hermes_doc_quarantine >/dev/null
[[ "$(sql "SELECT count(*) FROM requests r JOIN hermes_doc_runs h ON h.request_id=r.id
  WHERE r.status='queued';")" == 0 ]]

echo "PASS: rollback quarantines attached Hermes work without POST and leaves unattached work queued"
