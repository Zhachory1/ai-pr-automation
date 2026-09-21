#!/usr/bin/env bash
# The curate runner is the WRITE GATE: the model only proposes; the runner filters, dedups, and
# writes over the memory MCP (curl). Verify: base gate drops secret/too-short; team bank gets clean
# keepers; the org bank gets ONLY keepers that also pass the stricter gate (>=2 sources, >=80 chars,
# no internal-topic term); near-dups are skipped; the row settles done.
set -euo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/state" "$tmp/tasks/run-1"
printf 'a durable lesson about the fleet queue long enough to pass the size gate\n' > "$tmp/tasks/run-1/final.txt"

cat > "$tmp/bin/psql" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
args="$*"; input="$(cat || true)"
if [[ "$args" == *hermes_enqueue_request* ]]; then
  echo 42   # self-enqueue tick
elif [[ "$args" == *hermes_claim_request* ]]; then
  jq -cn '{id:5,kind:"memory-curate",payload:{},dedupe_key:"memory-curate:1"}'
elif [[ "$args" == *hermes_renew_request* ]]; then echo t
elif [[ "$input" == *hermes_settle_request* ]]; then printf '%s\n' "$input" > "$TEST_STATE/settle.sql"; echo t
else exit 2; fi
SH

# Fake hermes: emit proposals — clean+multi-source (team+org), secret (drop), too-short (drop),
# team-only (single source, <80 chars: team yes, org no), internal-topic (team yes, org no), near-dup.
cat > "$tmp/bin/hermes" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
query=""; while (($#)); do [[ "$1" != --query-file ]] || { query="$2"; shift; }; shift; done
result="$(grep -Eo '/[^ ]+/proposals.json' "$query" | head -1)"
cat > "$result" <<'JSON'
{"memories":[
  {"content":"The fleet queue supersedes an older queued head when a newer head lands, so rely on it instead of manual cleanup across runs.","sources":["a","b"]},
  {"content":"token leak ghp_abcdefghijklmnopqrstuvwxyz012345 do not store","sources":["a","b"]},
  {"content":"too short","sources":["a"]},
  {"content":"Prefer force-with-lease when updating a PR branch head.","sources":["a"]},
  {"content":"The quarterly revenue forecast target was discussed at length and is worth remembering for planning across the team here.","sources":["a","b"]},
  {"content":"The fleet queue supersedes an older queued head when a newer head lands so rely on it instead of manual cleanup across runs","sources":["a","b"]}
]}
JSON
SH

# Fake curl: implements the memory MCP over local files, one "bank" per URL.
cat > "$tmp/bin/curl" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
url=""; data=""; want=0
for a in "$@"; do
  if (( want )); then data="$a"; want=0; continue; fi
  case "$a" in -d) want=1;; http*) url="$a";; esac
done
bank=team; [[ "$url" == *"Rokt%20Builders"* ]] && bank=org
store="$TEST_STATE/$bank.jsonl"; : >> "$store"
tool="$(jq -r '.params.name' <<<"$data" 2>/dev/null || echo)"
if [[ "$tool" == recall ]]; then
  q="$(jq -r '.params.arguments.query' <<<"$data")"
  # return existing bank contents as MCP text content blocks
  texts="$(jq -R -s 'split("\n")|map(select(length>0))|map({type:"text",text:.})' "$store" 2>/dev/null || echo '[]')"
  printf 'data: %s\n' "$(jq -cn --argjson c "$texts" '{jsonrpc:"2.0",id:1,result:{content:$c}}')"
elif [[ "$tool" == retain ]]; then
  c="$(jq -r '.params.arguments.content' <<<"$data")"
  printf '%s\n' "$c" >> "$store"
  printf 'data: %s\n' '{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"stored"}],"isError":false}}'
else
  printf 'data: %s\n' '{"jsonrpc":"2.0","id":1,"result":{}}'
fi
SH
chmod +x "$tmp/bin/psql" "$tmp/bin/hermes" "$tmp/bin/curl"

out="$(PATH="$tmp/bin:$PATH" TEST_STATE="$tmp" HERMES_BIN="$tmp/bin/hermes" \
  REQUESTS_DB_USER=hermes_runtime PGPASSWORD=fake \
  MEMORY_CURATOR_STATE_DIR="$tmp/state" MEMORY_CURATOR_TASKS_DIR="$tmp/tasks" \
  MEMORY_CURATOR_PRIVATE_DOCS="$tmp/none" HERMES_QUEUE_LEASE_SECONDS=120 \
  bin/hermes-memory-curate --enqueue)"

echo "$out" | grep -q 'request=5 status=done' || { echo "FAIL: not done: $out" >&2; exit 1; }

team="$tmp/team.jsonl"; org="$tmp/org.jsonl"
# Team bank: clean-multisource + force-with-lease + revenue-topic = 3 (secret/too-short/near-dup dropped).
tn="$(grep -c . "$team" 2>/dev/null || echo 0)"
[[ "$tn" == 3 ]] || { echo "FAIL: team writes=$tn expected 3" >&2; cat "$team" >&2; exit 1; }
if grep -q 'ghp_' "$team"; then echo 'FAIL: secret reached team bank' >&2; exit 1; fi
# Org bank: only the clean multi-source, >=80 char, non-internal keeper = 1.
on="$(grep -c . "$org" 2>/dev/null || echo 0)"
[[ "$on" == 1 ]] || { echo "FAIL: org writes=$on expected 1" >&2; cat "$org" >&2; exit 1; }
grep -q 'supersedes an older queued head' "$org" || { echo 'FAIL: wrong org memory' >&2; exit 1; }
if grep -qi 'revenue' "$org"; then echo 'FAIL: internal-topic reached org bank' >&2; exit 1; fi
if grep -q 'force-with-lease' "$org"; then echo 'FAIL: single-source/short memory reached org bank' >&2; exit 1; fi
[[ -s "$tmp/state/last-run" ]] || { echo 'FAIL: watermark not advanced' >&2; exit 1; }

echo 'PASS: memory-curate gates writes and routes team vs org tiers over MCP'
