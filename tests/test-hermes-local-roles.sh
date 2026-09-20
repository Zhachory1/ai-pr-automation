#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

tmp="$(mktemp -d)"; container="hermes-local-roles-db-$$"
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
q() { (export PGPASSWORD=test; psql -h 127.0.0.1 -p "$port" -U postgres -d fleet -qAt -v ON_ERROR_STOP=1 "$@"); }

# Sentinel enrollment via the dedicated local path (no GitHub capability claim).
python3 - "$tmp/local.json" <<'PY'
import json,pathlib,sys
pathlib.Path(sys.argv[1]).write_text(json.dumps({"schema_version":1,"authority_digest":"a"*64}))
PY
proof="$(REQUESTS_DB_HOST=127.0.0.1 REQUESTS_DB_PORT="$port" REQUESTS_DB_USER=postgres REQUESTS_DB_NAME=fleet \
  PGPASSWORD=test scripts/hermes-local-enroll.py "$tmp/local.json" | jq -r .proof_digest)"
[[ "$proof" =~ ^[0-9a-f]{64}$ ]]
[[ "$(q -c "SELECT hermes_repository_authorized('local/fleet','$proof')")" == t ]]

# doc-write and memory-curate enqueue against the sentinel, claim, and settle generically.
for kind in doc-write memory-curate; do
  payload='{"repo":"local/fleet","doc_type":"dd","title":"t"}'
  id="$(q -c "SET ROLE hermes_worker; SELECT hermes_enqueue_request('$kind','$payload'::jsonb,'$kind:1','$proof');")"
  [[ "$id" =~ ^[0-9]+$ ]] || { echo "FAIL: $kind not enqueued" >&2; exit 1; }
  nonce=0123456789abcdef0123456789abcde$([[ "$kind" == doc-write ]] && echo a || echo b)
  claim="$(q -c "SET ROLE hermes_worker; SELECT hermes_claim_request('$kind','run-$kind','$nonce',120);")"
  [[ "$(jq -r .id <<<"$claim")" == "$id" ]] || { echo "FAIL: $kind not claimed" >&2; exit 1; }
  [[ "$(q -c "SET ROLE hermes_worker; SELECT hermes_settle_request('$id','$nonce','done','ok');")" == t ]] \
    || { echo "FAIL: $kind not settled" >&2; exit 1; }
done

# hermes_enqueue_local enqueues against the current active sentinel without the caller holding a proof.
local_id="$(q -c "SET ROLE hermes_worker; SELECT hermes_enqueue_local('memory-curate','{}'::jsonb,'memory-curate:auto');")"
[[ "$local_id" =~ ^[0-9]+$ ]] || { echo 'FAIL: hermes_enqueue_local did not enqueue' >&2; exit 1; }
[[ "$(q -c "SELECT payload->>'repo' FROM requests WHERE id=$local_id;")" == 'local/fleet' ]] \
  || { echo 'FAIL: enqueue_local did not stamp the sentinel repo' >&2; exit 1; }
if q -c "SET ROLE hermes_worker; SELECT hermes_enqueue_local('pr-review','{}'::jsonb,'x');" >/dev/null 2>&1; then
  echo 'FAIL: enqueue_local accepted a repo-scoped kind' >&2; exit 1; fi

# A local role MUST use the sentinel: a real repo payload is rejected even with a valid proof.
if q -c "SET ROLE hermes_worker; SELECT hermes_enqueue_request('doc-write','{\"repo\":\"owner/repo\"}'::jsonb,'doc:2','$proof');" \
  >/dev/null 2>&1; then echo 'FAIL: local role accepted a non-sentinel repo' >&2; exit 1; fi

# The sentinel does NOT authorize repo-scoped kinds (no GitHub capability was proven).
if q -c "SET ROLE hermes_worker; SELECT hermes_enqueue_request('pr-review','{\"repo\":\"local/fleet\",\"pr\":\"1\"}'::jsonb,'local/fleet#1@h','$proof');" \
  >/dev/null 2>&1; then echo 'FAIL: sentinel authorized a repo-scoped kind on a bogus repo' >&2; exit 1; fi

# Unknown/unsupported local kind still rejected.
if q -c "SET ROLE hermes_worker; SELECT hermes_enqueue_request('memory-nuke','{\"repo\":\"local/fleet\"}'::jsonb,'x','$proof');" \
  >/dev/null 2>&1; then echo 'FAIL: unsupported kind enqueued' >&2; exit 1; fi

# Stale sentinel cannot enqueue (direct or via the local helper).
q -c "UPDATE hermes_repository_enrollments SET checked_at=clock_timestamp()-interval '11 minutes' WHERE repo='local/fleet';" >/dev/null
if q -c "SET ROLE hermes_worker; SELECT hermes_enqueue_request('doc-write','{\"repo\":\"local/fleet\"}'::jsonb,'doc:3','$proof');" \
  >/dev/null 2>&1; then echo 'FAIL: stale sentinel enqueued' >&2; exit 1; fi
if q -c "SET ROLE hermes_worker; SELECT hermes_enqueue_local('memory-curate','{}'::jsonb,'memory-curate:stale');" \
  >/dev/null 2>&1; then echo 'FAIL: stale sentinel enqueued via helper' >&2; exit 1; fi

# Hermes role still has no direct table DML.
if q -c "SET ROLE hermes_worker; UPDATE requests SET status='done';" >/dev/null 2>&1; then
  echo 'FAIL: Hermes role has direct table DML' >&2; exit 1
fi

echo 'PASS: Hermes local-role sentinel enrollment and least-privilege queue API'
