#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

probe_agent_mcps() {
  docker compose exec -T "$1" bash -s -- "${2:-full}" <<'SH'
set -eu
mode="$1"
probe() {
  name="$1" delay="$2" tool="$3" arguments="${4:-}" required="${5:-$3}" forbidden="${6:-}"
  [ -n "$arguments" ] || arguments='{}'
  config="${MEWRITE_CODING_AGENT_DIR:-/app/agent-config}/mcp.json"
  jq -e --arg name "$name" '.mcpServers[$name].command == "npx" and .mcpServers[$name].args[:2] == ["-y", "mcp-remote@0.3.0"] and (.mcpServers[$name].args | index("--allow-http") != null)' "$config" >/dev/null
  mapfile -t args < <(jq -r --arg name "$name" '.mcpServers[$name].args[]' "$config")
  url="$(jq -r --arg name "$name" '.mcpServers[$name].args[] | select(startswith("http"))' "$config")"
  output="$(mktemp)"
  status=0
  { printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"agent-mcp-verify","version":"1"}}}'; sleep "$delay"; printf '%s\n' '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}'; sleep 1; printf '%s\n' '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'; sleep 1; printf '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"%s","arguments":%s}}\n' "$tool" "$arguments"; sleep 3; } | timeout 70 npx "${args[@]}" >"$output" 2>&1 || status=$?
  status="${status:-0}"
  [ "$status" -eq 0 ] || [ "$status" -eq 124 ]
  node - "$output" "$tool" "$required" "$forbidden" <<'NODE'
const fs = require("fs");
const [file, tool, required, forbidden] = process.argv.slice(2);
const messages = fs.readFileSync(file, "utf8").split("\n").flatMap(line => {
  try { return [JSON.parse(line)]; } catch { return []; }
});
for (const id of [1, 2, 3]) {
  const message = messages.find(candidate => candidate.id === id);
  if (!message?.result || message.error) throw new Error(`missing successful response for id ${id}`);
}
if (messages.find(candidate => candidate.id === 3).result.isError) {
  throw new Error(`${tool} returned an MCP tool error`);
}
const tools = messages.find(candidate => candidate.id === 2).result.tools ?? [];
for (const name of required.split(",")) {
  if (!tools.some(entry => entry.name === name)) throw new Error(`tools/list omitted ${name}`);
}
for (const name of forbidden.split(",").filter(Boolean)) {
  if (tools.some(entry => entry.name === name)) throw new Error(`tools/list exposed forbidden ${name}`);
}
NODE
  rm -f "$output"
  printf 'PASS %s %s\n' "$name" "$url"
}
probe coderag 40 list_projects
[ "$mode" = context ] || probe swarmvault 8 workspace_info
# fleet-shared is locked read-only by hindsight-bank-init (no retain/sync_retain); only recall+reflect
# are guaranteed on this bank.
probe hindsight 8 recall '{"query":"agent MCP verification"}' recall,reflect retain,sync_retain
SH
}

probe_agent_mcps agent-server-review
probe_agent_mcps agent-server-maintain
probe_agent_mcps doc-writer-server context
