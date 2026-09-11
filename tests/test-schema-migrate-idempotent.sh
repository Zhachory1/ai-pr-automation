#!/usr/bin/env bash
# Guards #97: schema-migrate discovers numbered upgrades, excludes the immutable 01 baseline, and
# upgrades the oldest baseline idempotently.
set -euo pipefail
cd "$(dirname "$0")/.."
fail=0; check(){ if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1" >&2; fail=1; fi; }

check "migrate command globs numbered upgrades only" \
  "grep -Fq 'for f in /migrations/0[2-9]-*.sql' docker-compose.yml"

CID="schema97-guard-$$"
docker run --rm -d --name "$CID" -e POSTGRES_PASSWORD=t -e POSTGRES_DB=fleet -e POSTGRES_USER=fleet postgres:16 >/dev/null
trap 'docker rm -f "$CID" >/dev/null 2>&1 || true' EXIT
for _ in $(seq 1 30); do docker exec "$CID" pg_isready -U fleet >/dev/null 2>&1 && break; sleep 1; done
docker cp docker/initdb "$CID:/migrations" >/dev/null
docker exec "$CID" psql -U fleet -d fleet -v ON_ERROR_STOP=1 -f /migrations/01-schema.sql >/dev/null
apply_upgrades() { docker exec "$CID" sh -c 'for f in /migrations/0[2-9]-*.sql; do psql -U fleet -d fleet -v ON_ERROR_STOP=1 -f "$f" >/dev/null 2>&1 || exit 1; done'; }
check "numbered upgrades apply to the 01 baseline" "apply_upgrades"
check "numbered upgrades re-apply idempotently" "apply_upgrades"
check "core tables present after upgrade" \
  "[[ \$(docker exec \"$CID\" psql -U fleet -d fleet -tAc \"SELECT count(*) FROM information_schema.tables WHERE table_name IN ('requests','pending_decisions','pending_maintenance_reviews')\") -eq 3 ]]"

docker exec "$CID" createdb -U fleet fresh
docker exec "$CID" sh -c 'for f in /migrations/0[1-9]-*.sql; do psql -U fleet -d fresh -v ON_ERROR_STOP=1 -f "$f" >/dev/null 2>&1 || exit 1; done'
check "upgraded 01 baseline matches the current fresh-install catalog" \
  "diff <(docker exec \"$CID\" pg_dump -U fleet --schema-only --no-owner --no-privileges --restrict-key=schema97 fleet) <(docker exec \"$CID\" pg_dump -U fleet --schema-only --no-owner --no-privileges --restrict-key=schema97 fresh)"

(( fail == 0 ))
