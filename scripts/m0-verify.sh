#!/usr/bin/env bash
# M0 substrate verification. Runs the six PRD exit checks, prints pass/fail per check.
# Prereq: cp .env.example .env, fill values, scripts/compose.sh up -d.
set -uo pipefail
cd "$(dirname "$0")/.." || { echo "FATAL: cannot cd to repo root"; exit 1; }
set -a; [ -f .env ] && . ./.env; set +a
"$PWD/scripts/validate-swarmvault-vault.sh" || exit 2
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
if ! curl -fsS -X POST "http://localhost:8888/v1/banks/${BANK}/retain" \
     -H 'content-type: application/json' \
     -d '{"content":"m0 verify probe: the sky is teal"}' >/dev/null 2>&1; then
  no "hindsight retain POST failed (endpoint path / provider / key config)"
else
  recalled=""
  for _ in 1 2 3 4 5; do
    if curl -fsS -X POST "http://localhost:8888/v1/banks/${BANK}/recall" \
         -H 'content-type: application/json' -d '{"query":"what color is the sky"}' 2>/dev/null | grep -qi teal; then
      recalled=1; break
    fi
    sleep 2
  done
  [ -n "$recalled" ] && ok "hindsight retain/recall" \
    || no "hindsight recall did not return the retained fact (check API paths / embedding delay)"
fi

echo "[4/6] swarmvault internal MCP bridge"
if docker image inspect agent-fleet/swarmvault >/dev/null 2>&1 \
   && docker compose up -d swarmvault-watch swarmvault-mcp >/dev/null 2>&1 \
   && docker compose exec -T swarmvault-mcp node - <<'NODE'
const endpoint = "http://127.0.0.1:9760/mcp";
const headers = { "content-type": "application/json", accept: "application/json, text/event-stream" };
async function rpc(payload, session) {
  const response = await fetch(endpoint, { method: "POST", headers: { ...headers, ...(session ? { "mcp-session-id": session } : {}) }, body: JSON.stringify(payload) });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffered = "";
  while (true) {
    const { done, value } = await reader.read();
    buffered += decoder.decode(value || new Uint8Array(), { stream: !done });
    let newline;
    while ((newline = buffered.indexOf("\n")) >= 0) {
      const line = buffered.slice(0, newline);
      buffered = buffered.slice(newline + 1);
      if (!line.startsWith("data: ")) continue;
      const message = JSON.parse(line.slice(6));
      if (message.error) throw new Error(message.error.message);
      if (message.result) return { message, session: response.headers.get("mcp-session-id") || session };
    }
    if (done) throw new Error("MCP response ended without a result");
  }
}
(async () => {
  const init = await rpc({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "m0-verify", version: "1" } } });
  if (!init.session) throw new Error("missing MCP session id");
  const initialized = await fetch(endpoint, { method: "POST", headers: { ...headers, "mcp-session-id": init.session }, body: JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized", params: {} }) });
  if (!initialized.ok) throw new Error(`initialized HTTP ${initialized.status}`);
  const tools = await rpc({ jsonrpc: "2.0", id: 2, method: "tools/list", params: {} }, init.session);
  if (!Array.isArray(tools.message.result.tools)) throw new Error("tools/list returned no tools");
})().catch(error => { console.error(error.message); process.exit(1); });
NODE
then
  if ! docker compose exec -T swarmvault-mcp sh -c 'touch /vault/.mcp-write-probe'; then
    ok "swarmvault MCP tools/list + read-only vault"
  else
    no "swarmvault MCP bridge can write its vault"
  fi
else
  no "swarmvault MCP bridge (check docker compose logs swarmvault-mcp)"
fi

echo "[5/6] coderag UI + internal MCP bridge"
ready=""
if docker image inspect agent-fleet/coderag >/dev/null 2>&1 \
   && docker compose up -d coderag swarmvault-watch >/dev/null 2>&1; then
  for _ in $(seq 1 20); do
    curl -fsS http://localhost:9749/ >/dev/null 2>&1 && { ready=1; break; }
    sleep 1
  done
fi
if [[ -n "$ready" ]] \
   && docker compose ps --status running --services swarmvault-watch | grep -qx swarmvault-watch \
   && docker compose exec -T coderag node - <<'NODE'
const endpoint = "http://127.0.0.1:9750/mcp";
const headers = { "content-type": "application/json", accept: "application/json, text/event-stream" };
async function rpc(payload, session) {
  const response = await fetch(endpoint, { method: "POST", headers: { ...headers, ...(session ? { "mcp-session-id": session } : {}) }, body: JSON.stringify(payload) });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  if (response.headers.get("content-type")?.includes("application/json")) {
    const message = await response.json();
    if (message.error) throw new Error(message.error.message);
    return { message, session: response.headers.get("mcp-session-id") || session };
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffered = "";
  while (true) {
    const { done, value } = await reader.read();
    buffered += decoder.decode(value || new Uint8Array(), { stream: !done });
    let newline;
    while ((newline = buffered.indexOf("\n")) >= 0) {
      const line = buffered.slice(0, newline);
      buffered = buffered.slice(newline + 1);
      if (!line.startsWith("data: ")) continue;
      const message = JSON.parse(line.slice(6));
      if (message.error) throw new Error(message.error.message);
      if (message.result) return { message, session: response.headers.get("mcp-session-id") || session };
    }
    if (done) throw new Error("MCP response ended without a result");
  }
}
(async () => {
  const init = await rpc({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2024-11-05", capabilities: {}, clientInfo: { name: "m0-verify", version: "1" } } });
  if (!init.session) throw new Error("missing MCP session id");
  const initialized = await fetch(endpoint, { method: "POST", headers: { ...headers, "mcp-session-id": init.session }, body: JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized", params: {} }) });
  if (!initialized.ok) throw new Error(`initialized HTTP ${initialized.status}`);
  const tools = await rpc({ jsonrpc: "2.0", id: 2, method: "tools/list", params: {} }, init.session);
  if (!Array.isArray(tools.message.result.tools)) throw new Error("tools/list returned no tools");
})().catch(error => { console.error(error.message); process.exit(1); });
NODE
then
  ok "coderag UI + MCP tools/list + swarmvault watcher"
else
  no "coderag UI, MCP bridge, or swarmvault watcher (check docker compose logs coderag swarmvault-watch)"
fi

echo "[6/6] durability across down/up"
echo "  MANUAL: scripts/compose.sh down && scripts/compose.sh up -d && re-run this script;"
echo "          request rows, hindsight memory, swarmvault vault, coderag graph must survive."

echo
echo "RESULT: $pass passed, $fail failed (check 6 is manual)."
[ "$fail" -eq 0 ]
