#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

tmp="$(mktemp -d)"; container="hermes-autonomy-db-$$"
cleanup() { docker rm -f "$container" >/dev/null 2>&1 || true; rm -rf "$tmp"; }
trap cleanup EXIT
port="$(python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1",0)); print(sock.getsockname()[1])
PY
)"
docker run --rm -d --name "$container" -e POSTGRES_PASSWORD=test -e POSTGRES_DB=fleet \
  -p "$port:5432" postgres:16 >/dev/null
for _ in $(seq 1 30); do docker exec "$container" psql -U postgres -d fleet -c 'SELECT 1' >/dev/null 2>&1 && break; sleep 1; done
docker exec "$container" psql -U postgres -d fleet -c 'SELECT 1' >/dev/null
for sql in 01-schema.sql 02-agent-server.sql 07-hermes-autonomy.sql 08-hermes-swe-pilot.sql 09-hermes-local-roles.sql; do
  docker cp "docker/initdb/$sql" "$container:/tmp/$sql"
  docker exec "$container" psql -U postgres -d fleet -v ON_ERROR_STOP=1 -qf "/tmp/$sql"
done
docker exec "$container" psql -U postgres -d fleet -v ON_ERROR_STOP=1 -qf /tmp/07-hermes-autonomy.sql
q() { (export PGPASSWORD=test; psql -h 127.0.0.1 -p "$port" -U postgres -d fleet -qAt -v ON_ERROR_STOP=1 "$@"); }

now="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
python3 - "$tmp/evidence.json" "$now" <<'PY'
import json,pathlib,sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({
 "schema_version":1,"repo":"owner/repo","checked_at":sys.argv[2],
 "credential_fingerprint":"a"*64,"ruleset_digest":"b"*64,"workflow_digest":"c"*64,
 "environment_policy_digest":"d"*64,
 "denials":{"protected_push":True,"unsafe_workflow_execution":True,"deployment":True,"administration":True},
 "allowed":{"unprotected_push":True,"draft_pr":True,"review":True}}))
PY
proof="$(scripts/hermes-repo-gate.py "$tmp/evidence.json" | jq -r .proof_digest)"
[[ "$proof" =~ ^[0-9a-f]{64}$ ]]
jq '.denials.protected_push=false' "$tmp/evidence.json" > "$tmp/bad.json"
if scripts/hermes-repo-gate.py "$tmp/bad.json" >/dev/null 2>&1; then echo 'FAIL: failed denial accepted' >&2; exit 1; fi
jq '.checked_at="2020-01-01T00:00:00Z"' "$tmp/evidence.json" > "$tmp/stale.json"
if scripts/hermes-repo-gate.py "$tmp/stale.json" >/dev/null 2>&1; then echo 'FAIL: stale evidence accepted' >&2; exit 1; fi

REQUESTS_DB_HOST=127.0.0.1 REQUESTS_DB_PORT="$port" REQUESTS_DB_USER=postgres REQUESTS_DB_NAME=fleet \
  PGPASSWORD=test scripts/hermes-repo-enroll.py "$tmp/evidence.json" | jq -e --arg proof "$proof" \
  '.status=="enrolled" and .proof_digest==$proof' >/dev/null
[[ "$(q -c "SELECT hermes_repository_authorized('owner/repo','$proof')")" == t ]]
REQUESTS_DB_HOST=127.0.0.1 REQUESTS_DB_PORT="$port" REQUESTS_DB_USER=postgres REQUESTS_DB_NAME=fleet \
  PGPASSWORD=test scripts/hermes-authority-watch.py "$tmp/evidence.json" | jq -e '.status=="current"' >/dev/null

payload='{"repo":"owner/repo","pr":"7"}'
id="$(q -c "SET ROLE hermes_worker; SELECT hermes_enqueue_request('pr-review','$payload'::jsonb,'owner/repo#7@head','$proof');")"
[[ "$id" =~ ^[0-9]+$ ]]
[[ -z "$(q -c "SET ROLE hermes_worker; SELECT hermes_enqueue_request('pr-review','$payload'::jsonb,'owner/repo#7@head','$proof');")" ]]
if q -c "SET ROLE hermes_worker; SELECT hermes_enqueue_request('pr-review','{\"repo\":\"other/repo\",\"pr\":\"8\"}'::jsonb,'other/repo#8@head','$proof');" \
  >/dev/null 2>&1; then echo 'FAIL: unenrolled repo enqueued' >&2; exit 1; fi
nonce=0123456789abcdef0123456789abcdef
claim="$(q -c "SET ROLE hermes_worker; SELECT hermes_claim_request('pr-review','run-1','$nonce',120);")"
[[ "$(jq -r .id <<<"$claim")" == "$id" ]]
[[ "$(q -c "SET ROLE hermes_worker; SELECT hermes_renew_request('$id','$nonce',120);")" == t ]]
[[ "$(q -c "SET ROLE hermes_worker; SELECT hermes_settle_request('$id','ffffffffffffffffffffffffffffffff','done',NULL);")" == f ]]
[[ "$(q -c "SET ROLE hermes_worker; SELECT hermes_settle_request('$id','$nonce','done',NULL);")" == t ]]
if q -c "SET ROLE hermes_worker; UPDATE requests SET status='done' WHERE id=$id;" >/dev/null 2>&1; then
  echo 'FAIL: Hermes role has direct table DML' >&2; exit 1
fi
if q -c "SET ROLE hermes_worker; SELECT hermes_enqueue_request('doc-write','$payload'::jsonb,'doc:1','$proof');" \
  >/dev/null 2>&1; then echo 'FAIL: unsupported kind enqueued' >&2; exit 1; fi
if q -c "SET ROLE hermes_worker; SELECT hermes_settle_request('$id','$nonce','queued',NULL);" \
  >/dev/null 2>&1; then echo 'FAIL: invalid transition accepted' >&2; exit 1; fi
swe_payload='{"repo":"owner/repo","source":"prompt","prompt":"test"}'
swe_id="$(q -c "SET ROLE hermes_worker; SELECT hermes_enqueue_request('swe-implement','$swe_payload'::jsonb,'swe:test','$proof');")"
swe_nonce=abcdef0123456789abcdef0123456789
q -c "SET ROLE hermes_worker; SELECT hermes_claim_request('swe-implement','run-swe','$swe_nonce',120);" >/dev/null
url=https://github.com/owner/repo/pull/9
[[ "$(q -c "SET ROLE hermes_worker; SELECT hermes_settle_swe_request('$swe_id','$swe_nonce','done','ok','$url');")" == t ]]
[[ "$(q -c "SELECT posted_ref FROM requests WHERE id=$swe_id;")" == "$url" ]]
q -c "UPDATE hermes_repository_enrollments SET checked_at=clock_timestamp()-interval '11 minutes' WHERE repo='owner/repo';" >/dev/null
if q -c "SET ROLE hermes_worker; SELECT hermes_enqueue_request('pr-review','$payload'::jsonb,'owner/repo#8@head','$proof');" \
  >/dev/null 2>&1; then echo 'FAIL: stale enrollment enqueued' >&2; exit 1; fi

echo 'PASS: Hermes enrollment proof and least-privilege queue API'
