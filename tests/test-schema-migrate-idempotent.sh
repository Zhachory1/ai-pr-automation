#!/usr/bin/env bash
# Guards #97: schema-migrate must re-apply ALL migrations (globbed, incl. 01-schema.sql) idempotently,
# so a fresh-volume initdb and an existing DB can't drift. Applies every docker/initdb/*.sql TWICE to
# an ephemeral postgres and asserts both passes succeed.
set -euo pipefail
cd "$(dirname "$0")/.."
fail=0; check(){ if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1" >&2; fail=1; fi; }

# static: the compose migrate command globs (not a hand-maintained -f list), and 01 is idempotent.
check "migrate command globs the migrations dir" "grep -q 'for f in /migrations/0\[0-9\]-\*.sql' docker-compose.yml"
check "01-schema.sql uses IF NOT EXISTS (re-runnable)" \
  "grep -q 'CREATE TABLE IF NOT EXISTS requests' docker/initdb/01-schema.sql && grep -q 'CREATE UNIQUE INDEX IF NOT EXISTS requests_dedupe_active' docker/initdb/01-schema.sql"

# functional: apply everything twice to a throwaway postgres; both passes must succeed.
CID="schema97-guard-$$"
docker rm -f "$CID" >/dev/null 2>&1
docker run --rm -d --name "$CID" -e POSTGRES_PASSWORD=t -e POSTGRES_DB=fleet -e POSTGRES_USER=fleet postgres:16 >/dev/null
trap 'docker rm -f "$CID" >/dev/null 2>&1' EXIT
for _ in $(seq 1 30); do docker exec "$CID" pg_isready -U fleet >/dev/null 2>&1 && break; sleep 1; done
docker cp docker/initdb "$CID:/migrations" >/dev/null
apply() { docker exec "$CID" sh -c 'for f in /migrations/0[0-9]-*.sql; do psql -U fleet -d fleet -v ON_ERROR_STOP=1 -f "$f" >/dev/null 2>&1 || exit 1; done'; }
check "all migrations apply on a fresh DB (pass 1)" "apply"
check "all migrations re-apply idempotently (pass 2)" "apply"
check "core tables present after migrate" \
  "[[ \$(docker exec \"$CID\" psql -U fleet -d fleet -tAc \"SELECT count(*) FROM information_schema.tables WHERE table_name IN ('requests','pending_decisions','pending_maintenance_reviews')\") -eq 3 ]]"

(( fail == 0 ))
