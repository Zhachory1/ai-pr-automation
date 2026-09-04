#!/usr/bin/env bash
# mewritecode runner for the containerized agent-server. Contract: runner <prompt_file>, reads
# PR_* + AGENT_RESULT_FILE + AGENT_RUN_NONCE from env. Runs mewritecode non-interactively on the
# prompt (which instructs the agent to review + post a COMMENT and write result.json). If the agent
# did not write a valid nonce-bound result.json, synthesize a minimal one so the write-path gate
# still gets typed input.
#
# mewritecode is the public engine roktcode wraps. Skills + MCP come from $MEWRITE_CODING_AGENT_DIR
# (set in the image to the vendored /app/agent-config). Reads OPENAI_API_KEY from env.
set -uo pipefail
prompt_file="${1:?prompt file}"
: "${AGENT_RESULT_FILE:?}"; : "${AGENT_RUN_NONCE:?}"

MEWRITE="${MEWRITECODE_BIN:-mewritecode}"
log(){ echo "mewritecode-runner: $*" >&2; }

out="$("$MEWRITE" exec "$(cat "$prompt_file")" 2>&1)"; status=$?
log "mewritecode exit $status"
printf '%s\n' "$out" | tail -20 >&2

# Safety net: agent must emit a valid nonce-bound result.json; if not, synthesize a minimal one.
# verdict defaults to 'comment' (the only unattended-safe verdict).
if ! jq -e --arg n "$AGENT_RUN_NONCE" '.nonce == $n' "$AGENT_RESULT_FILE" >/dev/null 2>&1; then
  log "agent did not write a valid result.json; synthesizing minimal one"
  jq -cn --arg nonce "$AGENT_RUN_NONCE" --arg summary "$(printf '%s' "$out" | tail -c 800)" \
    '{nonce:$nonce, verdict:"comment", findings:[], summary:$summary, ready_for_human_review:false, memory:{decisions:[]}}' \
    > "$AGENT_RESULT_FILE"
fi

exit "$status"
