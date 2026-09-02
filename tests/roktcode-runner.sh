#!/usr/bin/env bash
# roktcode runner for the agent-server. Contract: runner <prompt_file>, reads PR_* + AGENT_RESULT_FILE
# + AGENT_RUN_NONCE from env. Runs roktcode non-interactively on the prompt (which instructs the
# agent to review + post a COMMENT and write result.json). If the agent did not write a valid
# result.json, synthesize a minimal one from the run so the write-path gate still has typed input.
set -uo pipefail
prompt_file="${1:?prompt file}"
: "${AGENT_RESULT_FILE:?}"; : "${AGENT_RUN_NONCE:?}"

ROKTCODE="${ROKTCODE_BIN:-/Users/zhach/.roktcode/bin/roktcode}"
log(){ echo "roktcode-runner: $*" >&2; }

# The prompt already carries the boundaries (COMMENT only, no auto-approve, write result.json with
# the nonce, use the head-dedup marker). Run it non-interactively.
out="$($ROKTCODE exec "$(cat "$prompt_file")" 2>&1)"; status=$?
log "roktcode exit $status"
printf '%s\n' "$out" | tail -20 >&2

# Safety net: if the agent didn't emit a valid nonce-bound result.json, synthesize one so the gate
# gets typed input. verdict defaults to 'comment' (the only unattended-safe verdict).
if ! jq -e --arg n "$AGENT_RUN_NONCE" '.nonce == $n' "$AGENT_RESULT_FILE" >/dev/null 2>&1; then
  log "agent did not write a valid result.json; synthesizing minimal one"
  jq -cn --arg nonce "$AGENT_RUN_NONCE" --arg summary "$(printf '%s' "$out" | tail -c 800)" \
    '{nonce:$nonce, verdict:"comment", findings:[], summary:$summary, memory:{decisions:[]}}' \
    > "$AGENT_RESULT_FILE"
fi

exit "$status"
