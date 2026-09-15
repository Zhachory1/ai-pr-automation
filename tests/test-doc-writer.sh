#!/usr/bin/env bash
# Tests the doc-writer harness: parses the agent's draft + open_questions JSON, hands back questions
# for the answer loop, and on finalize/no-questions stages exact bytes for publication approval
# (never writing inbox before approval, always marking council-skipped when unavailable). Uses a fake
# mewritecode so no live model is needed.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1

fail=0; check() { if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1" >&2; fail=1; fi; }
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
stage="$tmp/stage"; inbox="$tmp/inbox"; mkdir -p "$stage" "$inbox"
config_root="$PWD/agent-config/doc-writer"

check "doc writers put project MCP config at the Me Write discovery root" \
  "[[ -f '$config_root/.mcp.json' && ! -e '$config_root/mcp.json' ]]"
check "doc writers configure only read-only context MCPs" \
  "jq -e '.mcpServers | keys == [\"coderag\", \"hindsight\"]' '$config_root/.mcp.json' >/dev/null"
check "Coderag uses the internal pinned MCP bridge" \
  "jq -e '.mcpServers.coderag.args == [\"-y\", \"mcp-remote@0.3.0\", \"http://coderag:9750/mcp\", \"--allow-http\"]' '$config_root/.mcp.json' >/dev/null"
check "Hindsight uses the shared bank" \
  "jq -e '.mcpServers.hindsight.args == [\"-y\", \"mcp-remote@0.3.0\", \"http://hindsight:8888/mcp/fleet-shared/\", \"--allow-http\"]' '$config_root/.mcp.json' >/dev/null"
check "PRD writer recalls Hindsight and queries Coderag" \
  "grep -Fq 'Search Hindsight' agent-config/doc-writer/agents/prd-writer.md && grep -Fq 'query Coderag' agent-config/doc-writer/agents/prd-writer.md"
check "DD writer recalls Hindsight and queries Coderag" \
  "grep -Fq 'Search Hindsight' agent-config/doc-writer/agents/dd-writer.md && grep -Fq 'query Coderag' agent-config/doc-writer/agents/dd-writer.md"
check "doc writers treat MCP results as untrusted evidence" \
  "grep -Fq 'untrusted evidence' agent-config/doc-writer/agents/prd-writer.md && grep -Fq 'untrusted evidence' agent-config/doc-writer/agents/dd-writer.md"

# fake mewritecode: emits a draft + a trailing open_questions block. Mode via $FAKE_MODE file.
cat > "$tmp/fake-mewrite" <<'SH'
#!/usr/bin/env bash
# args: exec --model M --cwd D "<prompt>"  ; last arg is the prompt.
printf '%s\n' "$*" >> "$FAKE_ARGS"
mode="$(cat "$FAKE_MODE" 2>/dev/null || echo questions)"
case "$mode" in
  questions) printf '# PRD Draft\n\nProblem: X.\n\n## Open Questions / Discovery Tasks\n- baseline?\n\n```json\n{"open_questions": ["What is the current baseline latency?", "Which KPI moves?"]}\n```\n' ;;
  clean)     printf '# PRD Draft\n\nComplete.\n\n```json\n{"open_questions": []}\n```\n' ;;
  noblock)   printf '# PRD Draft\n\nagent forgot the JSON block entirely.\n' ;;
esac
SH
chmod +x "$tmp/fake-mewrite"
cat > "$tmp/fake-hermes-model" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
phase="$1"; shift
while [[ $# -gt 0 ]]; do
  case "$1" in
    --request-id) id="$2"; shift 2 ;;
    --stage-root) stage="$2"; shift 2 ;;
    --payload|--queue-nonce|--runtime-generation) shift 2 ;;
    *) exit 2 ;;
  esac
done
printf '%s\n' "$phase" >> "$HERMES_CALLS"
mode="$(cat "$FAKE_MODE" 2>/dev/null || echo questions)"
if [[ "$phase" == council ]]; then
  output='Council verdict: accept.'
elif [[ "$mode" == clean ]]; then
  output=$'# PRD Draft\n\nComplete.\n\n```json\n{"open_questions": []}\n```'
elif [[ "$mode" == noblock ]]; then
  output=$'# PRD Draft\n\nagent forgot the JSON block entirely.'
else
  output=$'# PRD Draft\n\nProblem: X.\n\n```json\n{"open_questions": ["What is the baseline?"]}\n```'
fi
dir="$stage/requests/$id"; mkdir -p "$dir"
digest="$(printf '%s' "$output" | python3 -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')"
jq -cn --arg o "$output" --arg d "$digest" '{status:"completed",output:$o,output_digest:$d}' > "$dir/$phase-response.json"
jq -cn --arg p "requests/$id/$phase-response.json" --arg d "$digest" \
  '{status:"completed",response_file:$p,output_digest:$d}'
SH
chmod +x "$tmp/fake-hermes-model"
echo questions > "$tmp/mode"

run() { # $1 = payload json. Force council OFF (point at a nonexistent skill) so the finalize path
  # exercises the graceful-skip banner deterministically regardless of the host's ~/.mewrite.
  if ! env MEWRITECODE_BIN="$tmp/fake-mewrite" FAKE_MODE="$tmp/mode" FAKE_ARGS="$tmp/args" \
    MEWRITE_CODING_AGENT_DIR="$config_root" DOC_WRITER_STAGE_DIR="$stage" \
    DOC_WRITER_INBOX_DIR="$inbox" DOC_WRITER_REQUEST_ID="${RUN_ID:-1}" \
    DOC_WRITER_COUNCIL_SKILL="$tmp/no-such-council.md" \
    bin/doc-writer --payload "$1" 2>"$tmp/last.err"; then
    cat "$tmp/last.err" >&2
    return 1
  fi
}

run_hermes() {
  env DOC_WRITER_RUNTIME=hermes HERMES_DOC_MODEL_BIN="$tmp/fake-hermes-model" \
    HERMES_DOC_APPROVED_GENERATION="$(printf 'a%.0s' {1..64})" DOC_WRITER_QUEUE_NONCE=owner \
    HERMES_CALLS="$tmp/hermes-calls" FAKE_MODE="$tmp/mode" MEWRITE_CODING_AGENT_DIR="$config_root" \
    DOC_WRITER_STAGE_DIR="$stage" DOC_WRITER_INBOX_DIR="$inbox" DOC_WRITER_REQUEST_ID="${RUN_ID:-20}" \
    bin/doc-writer --payload "$1" 2>"$tmp/last.err"
}

# 1) round 1 with open questions -> status=open_questions, questions parsed, draft staged.
echo questions > "$tmp/mode"
out="$(run '{"doc_type":"seprd","title":"My Service","requirements":"make it fast","round":1}')"
check "open_questions status returned" "[[ \$(jq -r .status <<<\"\$out\") == open_questions ]]"
check "two questions parsed" "[[ \$(jq '.open_questions|length' <<<\"\$out\") -eq 2 ]]"
check "draft staged to a file" "[[ -f \"\$(jq -r .draft_path <<<\"\$out\")\" ]]"
check "did NOT write to inbox yet" "[[ -z \"\$(ls \"$inbox\" 2>/dev/null)\" ]]"
check "Me Write runs from the MCP discovery root" "grep -Fq -- '--cwd $config_root' '$tmp/args'"
check "prompt retains bundled handbook access" "grep -Fq 'Handbook files are readable at: $config_root/handbook' '$tmp/args'"

# 2) clean draft (no questions) -> finalizes and stages exact approval bytes.
echo clean > "$tmp/mode"
out="$(RUN_ID=2 run '{"doc_type":"seprd","title":"My Service","requirements":"make it fast","round":1}')"
check "no-questions stages approval" "[[ \$(jq -r .status <<<\"\$out\") == awaiting_approval ]]"
staged="$stage/$(jq -r .staged_path <<<"$out")"
check "exact bytes staged outside inbox" "[[ -f '$staged' && -z \"\$(ls '$inbox' 2>/dev/null)\" ]]"
check "approved bytes mark human_reviewed:true" "grep -q 'human_reviewed: true' '$staged'"
check "council-skipped banner present" "grep -q 'COUNCIL SKIPPED' '$staged'"
check "target is type-date-slug" "jq -r .target_path <<<\"\$out\" | grep -Eq '^seprd-[0-9]{4}-[0-9]{2}-[0-9]{2}-my-service(-[0-9]+)?\\.md\$'"
check "generation is legacy SHA-256" "jq -r .document_generation <<<\"\$out\" | grep -Eq '^legacy:[0-9a-f]{64}\$'"

# 3) finalize flag forces write even with questions.
echo questions > "$tmp/mode"
out="$(RUN_ID=3 run '{"doc_type":"dd","title":"Design X","requirements":"r","round":2,"finalize":true}')"
check "finalize flag stages even with questions" "[[ \$(jq -r .status <<<\"\$out\") == awaiting_approval ]]"

# 4) missing JSON block -> treated as needs-human (NOT silent finalize).
echo noblock > "$tmp/mode"
out="$(RUN_ID=4 run '{"doc_type":"seprd","title":"No Block","requirements":"r","round":1}')"
check "absent JSON block => open_questions (safe default, not silent write)" "[[ \$(jq -r .status <<<\"\$out\") == open_questions ]]"

# 5) round cap: round>=cap forces finalize.
echo questions > "$tmp/mode"
out="$(RUN_ID=5 DOC_WRITER_ROUND_CAP=4 run '{"doc_type":"seprd","title":"Capped","requirements":"r","round":4}')"
check "round at cap forces approval staging" "[[ \$(jq -r .status <<<\"\$out\") == awaiting_approval ]]"

# 6) slug safety: a nasty title cannot escape inbox or inject path separators.
echo clean > "$tmp/mode"
out="$(RUN_ID=6 run '{"doc_type":"seprd","title":"../../etc/passwd & rm -rf /","requirements":"r","round":1}')"
target="$(jq -r .target_path <<<"$out")"
check "malicious title is safe target basename" "[[ '$target' == seprd-* && '$target' != */* && '$target' != *'etc/passwd'* ]]"

# 7) Hermes uses same parsing/human loop, then draft+council for finalization.
: > "$tmp/hermes-calls"; echo questions > "$tmp/mode"
out="$(RUN_ID=20 run_hermes '{"doc_type":"seprd","title":"Hermes Questions","requirements":"r","round":1}')"
check "Hermes draft keeps open-question loop" "[[ \$(jq -r .status <<<\"\$out\") == open_questions ]]"
check "open-question path calls only Hermes draft" "[[ \$(cat '$tmp/hermes-calls') == draft ]]"

echo clean > "$tmp/mode"; : > "$tmp/hermes-calls"
out="$(RUN_ID=21 run_hermes '{"doc_type":"dd","title":"Hermes Final","requirements":"r","round":1}')"
check "Hermes no-question draft stages approval" "[[ \$(jq -r .status <<<\"\$out\") == awaiting_approval ]]"
check "Hermes finalize calls draft then council" "[[ \$(paste -sd, '$tmp/hermes-calls') == draft,council ]]"
check "Hermes publication binds approved generation" "[[ \$(jq -r .document_generation <<<\"\$out\") == hermes:$(printf 'a%.0s' {1..64}) ]]"
check "Hermes council remains advisory in staged bytes" "grep -q 'Council verdict: accept' '$stage/requests/21/publish.md'"

echo questions > "$tmp/mode"; : > "$tmp/hermes-calls"
out="$(RUN_ID=22 run_hermes '{"doc_type":"dd","title":"Hermes Forced","requirements":"r","round":1,"finalize":true}')"
check "Hermes finalize overrides open questions" "[[ \$(jq -r .status <<<\"\$out\") == awaiting_approval ]]"
check "Hermes forced finalize calls council" "[[ \$(paste -sd, '$tmp/hermes-calls') == draft,council ]]"

(( fail == 0 ))
