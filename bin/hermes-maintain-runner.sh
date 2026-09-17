#!/usr/bin/env bash
set -uo pipefail
prompt_file="${1:?prompt file}"
: "${AGENT_RESULT_FILE:?}"; : "${AGENT_RUN_NONCE:?}"; : "${PR_WORK_ROOT:?}"

HERMES="${HERMES_BIN:-hermes}"
log(){ echo "hermes-maintain-runner: $*" >&2; }

out="$(HERMES_HOME="$PR_WORK_ROOT/hermes" HERMES_WRITE_SAFE_ROOT="$PR_WORK_ROOT" \
  "$HERMES" chat --query-file "$prompt_file" --oneshot \
    --model gpt-5.6-sol --provider openai-api --toolsets terminal,file \
    --max-turns 100 --run-budget 1500 --safe-mode --ignore-rules --yolo \
    --in "$PR_WORK_ROOT/worktree" 2>&1)"; status=$?
log "Hermes exit $status"
printf '%s\n' "$out" | tail -20 >&2

if ! jq -e --arg nonce "$AGENT_RUN_NONCE" '
  type == "object" and (keys == ["findings","maintenance","nonce","summary","verdict"]) and
  .nonce == $nonce and .verdict == "comment" and
  (.summary | type == "string" and length <= 65536 and (contains("<!-- ai-pr-automation head=") | not)) and
  (.maintenance | type == "object" and keys == ["needs_human_review"] and
    (.needs_human_review | type == "boolean")) and
  (.findings | type == "array" and length <= 100) and
  all(.findings[];
    type == "object" and keys == ["file","line","severity","text"] and
    (.file | type == "string" and length <= 4096 and (contains("<!-- ai-pr-automation head=") | not)) and
    ((.line == null) or (.line | type == "number" and floor == . and . > 0)) and
    (.severity | IN("critical","major","minor","nit")) and
    (.text | type == "string" and length > 0 and length <= 16384 and
      (contains("<!-- ai-pr-automation head=") | not)))
' "$AGENT_RESULT_FILE" >/dev/null 2>&1; then
  log "agent did not write a valid maintain result.json"
  (( status != 0 )) && exit "$status"
  exit 2
fi

exit "$status"
