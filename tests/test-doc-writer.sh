#!/usr/bin/env bash
# Tests the doc-writer harness: parses the agent's draft + open_questions JSON, hands back questions
# for the answer loop, and on finalize/no-questions writes a provenance-stamped doc to the inbox
# (never clobbering, always marking council-skipped when council is unavailable). Uses a fake
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
echo questions > "$tmp/mode"

run() { # $1 = payload json. Force council OFF (point at a nonexistent skill) so the finalize path
  # exercises the graceful-skip banner deterministically regardless of the host's ~/.mewrite.
  MEWRITECODE_BIN="$tmp/fake-mewrite" FAKE_MODE="$tmp/mode" FAKE_ARGS="$tmp/args" \
  MEWRITE_CODING_AGENT_DIR="$config_root" \
  DOC_WRITER_STAGE_DIR="$stage" DOC_WRITER_INBOX_DIR="$inbox" \
  DOC_WRITER_COUNCIL_SKILL="$tmp/no-such-council.md" \
  bin/doc-writer --payload "$1" 2>/dev/null
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

# 2) clean draft (no questions) -> finalizes, writes to inbox with provenance + council-skipped banner.
echo clean > "$tmp/mode"
out="$(run '{"doc_type":"seprd","title":"My Service","requirements":"make it fast","round":1}')"
check "no-questions finalizes (status=written)" "[[ \$(jq -r .status <<<\"\$out\") == written ]]"
doc="$(jq -r .doc_path <<<"$out")"
check "doc written to inbox" "[[ -f '$doc' && '$doc' == $inbox/* ]]"
check "frontmatter marks human_reviewed:false" "grep -q 'human_reviewed: false' '$doc'"
check "council-skipped banner present (no council in test)" "grep -q 'COUNCIL SKIPPED' '$doc'"
check "filename is type-date-slug" "basename '$doc' | grep -Eq '^seprd-[0-9]{4}-[0-9]{2}-[0-9]{2}-my-service(-[0-9]+)?\\.md\$'"

# 3) finalize flag forces write even with questions.
echo questions > "$tmp/mode"
out="$(run '{"doc_type":"dd","title":"Design X","requirements":"r","round":2,"finalize":true}')"
check "finalize flag writes even with questions" "[[ \$(jq -r .status <<<\"\$out\") == written ]]"

# 4) missing JSON block -> treated as needs-human (NOT silent finalize).
echo noblock > "$tmp/mode"
out="$(run '{"doc_type":"seprd","title":"No Block","requirements":"r","round":1}')"
check "absent JSON block => open_questions (safe default, not silent write)" "[[ \$(jq -r .status <<<\"\$out\") == open_questions ]]"

# 5) round cap: round>=cap forces finalize.
echo questions > "$tmp/mode"
out="$(DOC_WRITER_ROUND_CAP=4 run '{"doc_type":"seprd","title":"Capped","requirements":"r","round":4}')"
check "round at cap forces finalize" "[[ \$(jq -r .status <<<\"\$out\") == written ]]"

# 6) slug safety: a nasty title cannot escape inbox or inject path separators.
echo clean > "$tmp/mode"
out="$(run '{"doc_type":"seprd","title":"../../etc/passwd & rm -rf /","requirements":"r","round":1}')"
doc="$(jq -r .doc_path <<<"$out")"
check "malicious title is slugified inside inbox" "[[ '$doc' == $inbox/seprd-* && '$doc' != *'/../'* && '$doc' != *'etc/passwd'* ]]"

(( fail == 0 ))
