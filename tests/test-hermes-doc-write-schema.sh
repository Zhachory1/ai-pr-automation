#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
container="hermes-doc-write-schema-$$"
cleanup() { docker rm -f "$container" >/dev/null 2>&1 || true; }
trap cleanup EXIT
port="$(python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(('127.0.0.1',0)); print(sock.getsockname()[1])
PY
)"
docker run --rm -d --name "$container" -e POSTGRES_PASSWORD=test -e POSTGRES_DB=fleet \
  -p "$port:5432" postgres:16 >/dev/null
for _ in $(seq 1 30); do docker exec "$container" psql -U postgres -d fleet -c 'SELECT 1' >/dev/null 2>&1 && break; sleep 1; done
docker exec "$container" psql -U postgres -d fleet -c 'SELECT 1' >/dev/null
for sql in 01-schema.sql 02-agent-server.sql 03-human-review-queue.sql 06-hermes-doc-foundation.sql \
  07-hermes-autonomy.sql 09-hermes-yaml-authority.sql 10-hermes-queue-depth.sql 06-hermes-doc-foundation.sql; do
  docker cp "docker/initdb/$sql" "$container:/tmp/$sql"
  docker exec "$container" psql -U postgres -d fleet -v ON_ERROR_STOP=1 -qf "/tmp/$sql"
done
q() { (export PGPASSWORD=test; psql -h 127.0.0.1 -p "$port" -U postgres -d fleet -qAt -v ON_ERROR_STOP=1 "$@"); }

nonce1=11111111111111111111111111111111
id1="$(q -c "INSERT INTO requests(kind,payload,dedupe_key) VALUES('doc-write','{\"doc_type\":\"dd\"}','doc:questions') RETURNING id;")"
q -c "SET ROLE hermes_worker; SELECT hermes_claim_request('doc-write','run-questions','$nonce1',120);" >/dev/null
proposal1="{\"kind\":\"doc-open-questions\",\"draft_path\":\"requests/$id1/draft.md\",\"open_questions\":[\"Which?\"]}"
[[ "$(q -c "SET ROLE hermes_worker; SELECT hermes_settle_doc_questions($id1,'$nonce1','$proposal1'::jsonb,'{}'::jsonb);")" == t ]]
[[ "$(q -c "SELECT status||':'||(SELECT proposal->>'kind' FROM pending_maintenance_reviews WHERE request_id=$id1) FROM requests WHERE id=$id1;")" == 'done:doc-open-questions' ]]

nonce2=22222222222222222222222222222222
digest="$(printf exact | shasum -a 256 | awk '{print $1}')"
generation="hermes:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
id2="$(q -c "INSERT INTO requests(kind,payload,dedupe_key) VALUES('doc-write','{\"doc_type\":\"dd\"}','doc:final') RETURNING id;")"
q -c "SET ROLE hermes_worker; SELECT hermes_claim_request('doc-write','run-final','$nonce2',120);" >/dev/null
staged="requests/$id2/publish.md"; target="dd-2026-09-21-schema-$id2.md"
proposal2="{\"kind\":\"doc-publication-approval\",\"staged_path\":\"$staged\",\"target_path\":\"$target\",\"content_digest\":\"$digest\",\"document_generation\":\"$generation\"}"
[[ "$(q -c "SET ROLE hermes_worker; SELECT hermes_stage_doc_publication($id2,'$nonce2','$staged','$target','$digest','$generation','$proposal2'::jsonb,'{}'::jsonb);")" == t ]]
[[ "$(q -c "SELECT r.status||':'||p.state FROM requests r JOIN doc_publications p ON p.request_id=r.id WHERE r.id=$id2;")" == 'done:awaiting_approval' ]]

# Worker role cannot approve or edit bindings; Fleet Controller performs this human-only transition.
if q -c "SET ROLE hermes_worker; UPDATE doc_publications SET state='approved',approved_at=now() WHERE request_id=$id2;" >/dev/null 2>&1; then
  echo 'FAIL: hermes_worker directly approved publication' >&2; exit 1
fi
q -c "UPDATE doc_publications SET state='approved',approved_at=now() WHERE request_id=$id2;
  UPDATE pending_maintenance_reviews SET state='reviewed',reviewed_at=now() WHERE request_id=$id2;
  UPDATE requests SET status='queued',started_at=NULL,finished_at=NULL,posted_ref=NULL,run_id=NULL,run_nonce=NULL,
    lease_expires_at=NULL,payload=jsonb_set(payload,'{publication_only}','true') WHERE id=$id2;" >/dev/null
nonce3=33333333333333333333333333333333
q -c "SET ROLE hermes_worker; SELECT hermes_claim_request('doc-write','run-publish','$nonce3',120);" >/dev/null
binding="$(q -c "SET ROLE hermes_worker; SELECT hermes_doc_publication_claimed($id2,'$nonce3');")"
[[ "$(jq -r .content_digest <<<"$binding")" == "$digest" ]]
prepared="$(q -c "SET ROLE hermes_worker; SELECT hermes_prepare_doc_publication($id2,'$nonce3');")"
[[ "$(jq -r .target_path <<<"$prepared")" == "$target" ]]
[[ "$(q -c "SELECT r.status||':'||p.state FROM requests r JOIN doc_publications p ON p.request_id=r.id WHERE r.id=$id2;")" == 'reconcile:prepared' ]]
[[ "$(q -c "SET ROLE hermes_worker; SELECT hermes_mark_doc_published($id2,'ffffffffffffffffffffffffffffffff','$target','$digest','$generation');")" == f ]]
[[ "$(q -c "SET ROLE hermes_worker; SELECT hermes_mark_doc_published($id2,'$nonce3','$target','$digest','$generation');")" == t ]]
[[ "$(q -c "SELECT r.status||':'||p.state||':'||r.posted_ref FROM requests r JOIN doc_publications p ON p.request_id=r.id WHERE r.id=$id2;")" == "done:published:$target" ]]

if q -c "SET ROLE hermes_worker; SELECT * FROM requests;" >/dev/null 2>&1; then
  echo 'FAIL: hermes_worker has direct queue SELECT' >&2; exit 1
fi

echo 'PASS: doc-write schema is idempotent, atomic, publication-fenced, and least-privilege'
