#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1

if grep -Eq '^(valid_memory_decisions|pending_decision_insert)\(\)' lib/queue.sh; then
  echo "legacy memory approval helper still present" >&2
  exit 1
fi
jq -e '.mcpServers.hindsight.args == ["-y", "mcp-remote@0.3.0", "http://hindsight:8888/mcp/fleet-shared/", "--allow-http"]' agent-config/mcp.json >/dev/null

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
AGENT_RESULT_FILE="$tmp/result.json" AGENT_RUN_NONCE=test-nonce tests/fake-runner.sh /dev/null >/dev/null
jq -e '.nonce == "test-nonce" and .memory == null' "$tmp/result.json" >/dev/null

echo "PASS: agents use direct Hindsight MCP without memory approval proposals"
