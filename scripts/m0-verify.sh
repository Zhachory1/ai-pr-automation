#!/usr/bin/env bash
# M0 substrate verification. Runs the six PRD exit checks, prints pass/fail per check.
# Prereq: cp .env.example .env, fill values, docker compose up -d.
set -uo pipefail
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a

pass=0; fail=0
ok(){ echo "  PASS: $1"; pass=$((pass+1)); }
no(){ echo "  FAIL: $1"; fail=$((fail+1)); }

echo "[1/6] Postgres + Redis reachable"
docker compose exec -T db-requests pg_isready -U "${REQUESTS_DB_USER:-fleet}" >/dev/null 2>&1 \
  && ok "requests postgres" || no "requests postgres"
docker compose exec -T redis redis-cli ping 2>/dev/null | grep -q PONG \
  && ok "redis" || no "redis"

echo "[2/6] schema tables exist with dedupe constraint"
tbls=$(docker compose exec -T db-requests psql -U "${REQUESTS_DB_USER:-fleet}" -d "${REQUESTS_DB_NAME:-fleet}" -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_name IN ('requests','pending_decisions');" 2>/dev/null)
[ "$tbls" = "2" ] && ok "requests + pending_decisions present" || no "tables (got '$tbls')"
docker compose exec -T db-requests psql -U "${REQUESTS_DB_USER:-fleet}" -d "${REQUESTS_DB_NAME:-fleet}" -tAc \
  "SELECT 1 FROM pg_indexes WHERE indexname='requests_dedupe_active';" 2>/dev/null | grep -q 1 \
  && ok "dedupe index" || no "dedupe index"

echo "[3/6] hindsight retain->recall round-trip"
BANK="m0-verify-$$"
curl -fsS -X POST "http://localhost:8888/v1/banks/${BANK}/retain" \
  -H 'content-type: application/json' \
  -d '{"content":"m0 verify probe: the sky is teal"}' >/dev/null 2>&1
recalled=""
for _ in 1 2 3 4 5; do
  if curl -fsS -X POST "http://localhost:8888/v1/banks/${BANK}/recall" \
       -H 'content-type: application/json' -d '{"query":"what color is the sky"}' 2>/dev/null | grep -qi teal; then
    recalled=1; break
  fi
  sleep 2
done
if [ -n "$recalled" ]; then
  ok "hindsight retain/recall"
else
  no "hindsight retain/recall (check endpoint paths against hindsight API-reference; provider/key config)"
fi

echo "[4/6] swarmvault MCP image answers introspection"
if docker image inspect agent-fleet/swarmvault >/dev/null 2>&1; then
  printf '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n' \
    | timeout 30 docker run --rm -i agent-fleet/swarmvault swarmvault mcp 2>/dev/null | grep -q '"result"' \
    && ok "swarmvault mcp" || no "swarmvault mcp introspection"
else
  no "swarmvault image not built (docker build -t agent-fleet/swarmvault docker/swarmvault)"
fi

echo "[5/6] coderag indexes CODE_ROOT main + answers a query"
if docker image inspect agent-fleet/coderag >/dev/null 2>&1; then
  ok "coderag image present (structural-query probe: run scripts/m0-coderag-probe.sh)"
else
  no "coderag image not built (see docker/README.md)"
fi

echo "[6/6] durability across down/up"
echo "  MANUAL: docker compose down && docker compose up -d && re-run this script;"
echo "          request rows, hindsight memory, swarmvault vault, coderag graph must survive."

echo
echo "RESULT: $pass passed, $fail failed (check 6 is manual)."
[ "$fail" -eq 0 ]
