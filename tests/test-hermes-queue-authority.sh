#!/usr/bin/env bash
# After the enrollment→YAML collapse: the queue API no longer takes a proof or checks enrollment.
# Verify the reduced-arg functions enqueue/claim/settle, preserve dedupe/retry/supersede/round-cap
# and least-privilege (hermes_worker has no direct table DML), and that the enrollment table and
# proof column are gone. Repo scope-of-attention now lives in the YAML allowlist (tested separately).
set -euo pipefail
cd "$(dirname "$0")/.."

tmp="$(mktemp -d)"; container="hermes-queue-authority-$$"
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
for sql in 01-schema.sql 02-agent-server.sql 07-hermes-autonomy.sql 08-hermes-swe-pilot.sql 09-hermes-yaml-authority.sql; do
  docker cp "docker/initdb/$sql" "$container:/tmp/$sql"
  docker exec "$container" psql -U postgres -d fleet -v ON_ERROR_STOP=1 -qf "/tmp/$sql"
done
q() { (export PGPASSWORD=test; psql -h 127.0.0.1 -p "$port" -U postgres -d fleet -qAt -v ON_ERROR_STOP=1 "$@"); }

# Enrollment machinery is gone.
[[ "$(q -c "SELECT to_regclass('hermes_repository_enrollments') IS NULL")" == t ]] || { echo 'FAIL: enrollment table still exists' >&2; exit 1; }
[[ "$(q -c "SELECT count(*) FROM information_schema.columns WHERE table_name='requests' AND column_name='hermes_enrollment_proof'")" == 0 ]] || { echo 'FAIL: proof column still exists' >&2; exit 1; }
[[ "$(q -c "SELECT count(*) FROM pg_proc WHERE proname='hermes_repository_authorized'")" == 0 ]] || { echo 'FAIL: authorized fn still exists' >&2; exit 1; }

# Reduced-arg enqueue works and dedupes; no proof argument.
payload='{"repo":"owner/repo","pr":"7"}'
id="$(q -c "SET ROLE hermes_worker; SELECT hermes_enqueue_request('pr-review','$payload'::jsonb,'owner/repo#7@head');")"
[[ "$id" =~ ^[0-9]+$ ]] || { echo 'FAIL: enqueue' >&2; exit 1; }
[[ -z "$(q -c "SET ROLE hermes_worker; SELECT hermes_enqueue_request('pr-review','$payload'::jsonb,'owner/repo#7@head');")" ]] || { echo 'FAIL: dedupe' >&2; exit 1; }

# Unsupported kind still rejected.
if q -c "SET ROLE hermes_worker; SELECT hermes_enqueue_request('memory-nuke','$payload'::jsonb,'x');" >/dev/null 2>&1; then
  echo 'FAIL: unsupported kind enqueued' >&2; exit 1; fi

# Claim/renew/settle, nonce-fenced.
nonce=0123456789abcdef0123456789abcdef
claim="$(q -c "SET ROLE hermes_worker; SELECT hermes_claim_request('pr-review','run-1','$nonce',120);")"
[[ "$(jq -r .id <<<"$claim")" == "$id" ]] || { echo 'FAIL: claim' >&2; exit 1; }
[[ "$(jq -r 'has("enrollment_proof")' <<<"$claim")" == false ]] || { echo 'FAIL: claim still returns proof' >&2; exit 1; }
[[ "$(q -c "SET ROLE hermes_worker; SELECT hermes_renew_request('$id','$nonce',120);")" == t ]]
[[ "$(q -c "SET ROLE hermes_worker; SELECT hermes_settle_request('$id','ffffffffffffffffffffffffffffffff','done',NULL);")" == f ]]
[[ "$(q -c "SET ROLE hermes_worker; SELECT hermes_settle_request('$id','$nonce','done',NULL);")" == t ]]

# Least privilege intact: no direct table DML.
if q -c "SET ROLE hermes_worker; UPDATE requests SET status='done' WHERE id=$id;" >/dev/null 2>&1; then
  echo 'FAIL: Hermes role has direct table DML' >&2; exit 1; fi

# pr-maintain three-round cap preserved. The cap counts non-superseded rows per lineage; each round
# must reach a terminal non-superseded state (done/failed) to count, so settle each before the next.
for i in 1 2 3; do
  rid="$(q -c "SET ROLE hermes_worker; SELECT hermes_enqueue_request('pr-maintain','{\"repo\":\"o/r\"}'::jsonb,'o/r#9@h$i');")"
  rn="$(printf '%032x' "$i")"
  q -c "SET ROLE hermes_worker; SELECT hermes_claim_request('pr-maintain','run-m$i','$rn',120);" >/dev/null
  q -c "SET ROLE hermes_worker; SELECT hermes_settle_request('$rid','$rn','done',NULL);" >/dev/null
done
[[ -z "$(q -c "SET ROLE hermes_worker; SELECT hermes_enqueue_request('pr-maintain','{\"repo\":\"o/r\"}'::jsonb,'o/r#9@h4');")" ]] \
  || { echo 'FAIL: fourth maintain round enqueued past cap' >&2; exit 1; }

# swe typed settle still works.
swe_id="$(q -c "SET ROLE hermes_worker; SELECT hermes_enqueue_request('swe-implement','{\"repo\":\"owner/repo\",\"source\":\"prompt\",\"prompt\":\"t\"}'::jsonb,'swe:test');")"
swe_nonce=abcdef0123456789abcdef0123456789
q -c "SET ROLE hermes_worker; SELECT hermes_claim_request('swe-implement','run-swe','$swe_nonce',120);" >/dev/null
url=https://github.com/owner/repo/pull/9
[[ "$(q -c "SET ROLE hermes_worker; SELECT hermes_settle_swe_request('$swe_id','$swe_nonce','done','ok','$url');")" == t ]]
[[ "$(q -c "SELECT posted_ref FROM requests WHERE id=$swe_id;")" == "$url" ]]

echo 'PASS: collapsed queue API (no proof/enrollment) preserves dedupe, cap, least-privilege, typed settle'
