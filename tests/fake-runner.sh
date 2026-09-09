#!/usr/bin/env bash
# Fake agent runner for M1 e2e. Does NOT touch GitHub or run a real agent.
# Writes a schema-valid result.json (echoing the nonce) for runner-contract tests.
# Usage (server contract): fake-runner <prompt_file>; reads PR_* + AGENT_RESULT_FILE + AGENT_RUN_NONCE.
set -euo pipefail
: "${1:?prompt file}"

: "${AGENT_RESULT_FILE:?}"
: "${AGENT_RUN_NONCE:?}"

# Simulate work.
sleep 1

jq -cn --arg nonce "$AGENT_RUN_NONCE" \
  '{
     nonce: $nonce,
     verdict: "comment",
     findings: [{file:"src/x.py", line:10, severity:"minor", text:"nit: rename var"}],
     summary: "fake-runner e2e: no real review posted"
   }' > "$AGENT_RESULT_FILE"

echo "fake-runner: wrote $AGENT_RESULT_FILE (verdict=comment) for ${PR_REPO:-?}#${PR_NUMBER:-?}"
exit 0
