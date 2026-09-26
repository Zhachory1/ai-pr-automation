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
[[ -z "${TEST_PSQL_FORBIDDEN:-}" ]] || { touch "$TEST_STATE/psql-forbidden"; exit 99; }
if [[ "$args" == *hermes_enqueue_request* || "$input" == *hermes_enqueue_request* ]]; then
  printf '%s\n' "$input" > "$TEST_STATE/enqueue.sql"
  echo 42   # self-enqueue tick
elif [[ "$args" == *hermes_claim_request* ]]; then
  touch "$TEST_STATE/claimed"
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
printf 'run\n' >> "$TEST_STATE/model.calls"
[[ -z "${TEST_MODEL_FAIL:-}" ]] || exit 1
if [[ -n "${TEST_MODEL_BLOCK:-}" ]]; then
  touch "$TEST_MODEL_BLOCK.started"
  while [[ -e "$TEST_MODEL_BLOCK" ]]; do sleep 0.05; done
fi
result="$(grep -Eo '/[^ ]+/proposals.json' "$query" | head -1)"
if [[ -n "${TEST_PROPOSAL:-}" ]]; then
  printf '%s\n' "$TEST_PROPOSAL" > "$result"
  exit 0
fi
cat > "$result" <<'JSON'
{"memories":[
  {"content":"The fleet queue supersedes an older queued head when a newer head lands, so rely on it instead of manual cleanup across runs.","sources":["a","b"]},
  {"content":"token leak ghp_abcdefghijklmnopqrstuvwxyz012345 do not store","sources":["a","b"]},
  {"content":"too short"},
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
[[ -z "${TEST_CURL_FAIL:-}" ]] || exit 22
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

# Standing producer mode enqueues and exits; dispatcher owns the claim/consumer concurrency.
PATH="$tmp/bin:$PATH" TEST_STATE="$tmp" HERMES_BIN="$tmp/bin/hermes" \
  REQUESTS_DB_USER=hermes_runtime PGPASSWORD=fake \
  MEMORY_CURATOR_STATE_DIR="$tmp/state" MEMORY_CURATOR_TASKS_DIR="$tmp/tasks" \
  MEMORY_CURATOR_PRIVATE_DOCS="$tmp/none" HERMES_QUEUE_LEASE_SECONDS=4 \
  bin/hermes-memory-curate --enqueue-only >/dev/null
[[ -s "$tmp/enqueue.sql" ]] || { echo 'FAIL: enqueue-only did not enqueue' >&2; exit 1; }
[[ ! -e "$tmp/claimed" ]] || { echo 'FAIL: enqueue-only also claimed' >&2; exit 1; }
rm -f "$tmp/enqueue.sql"

out="$(PATH="$tmp/bin:$PATH" TEST_STATE="$tmp" HERMES_BIN="$tmp/bin/hermes" \
  REQUESTS_DB_USER=hermes_runtime PGPASSWORD=fake \
  MEMORY_CURATOR_STATE_DIR="$tmp/state" MEMORY_CURATOR_TASKS_DIR="$tmp/tasks" \
  MEMORY_CURATOR_PRIVATE_DOCS="$tmp/none" HERMES_QUEUE_LEASE_SECONDS=4 \
  bin/hermes-memory-curate --enqueue)"

echo "$out" | grep -q 'request=5 status=done' || { echo "FAIL: not done: $out" >&2; exit 1; }
grep -q 'hermes_enqueue_request' "$tmp/enqueue.sql" || { echo 'FAIL: self-enqueue SQL missing' >&2; exit 1; }

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

# Direct mode reuses the same gather/model/gates without touching PostgreSQL.
: > "$team"; : > "$org"; printf '0\n' > "$tmp/state/last-run"
rm -f "$tmp/psql-forbidden"
out="$(PATH="$tmp/bin:$PATH" TEST_STATE="$tmp" TEST_PSQL_FORBIDDEN=1 HERMES_BIN="$tmp/bin/hermes" \
  MEMORY_CURATOR_STATE_DIR="$tmp/state" MEMORY_CURATOR_TASKS_DIR="$tmp/tasks" \
  MEMORY_CURATOR_PRIVATE_DOCS="$tmp/none" bin/hermes-memory-curate --direct)"
[[ "$out" == 'status=done' ]] || { echo "FAIL: direct not done: $out" >&2; exit 1; }
[[ ! -e "$tmp/psql-forbidden" ]] || { echo 'FAIL: direct called psql' >&2; exit 1; }
[[ "$(grep -c . "$team")" == 3 ]] || { echo 'FAIL: direct team routing differs' >&2; exit 1; }
[[ "$(grep -c . "$org")" == 1 ]] || { echo 'FAIL: direct org routing differs' >&2; exit 1; }
[[ "$(cat "$tmp/state/last-run")" != 0 ]] || { echo 'FAIL: direct watermark not advanced' >&2; exit 1; }

# Empty direct sweep advances its watermark, skips the model, and exits successfully.
mkdir -p "$tmp/state-empty" "$tmp/tasks-empty"; rm -f "$tmp/model.calls"
out="$(PATH="$tmp/bin:$PATH" TEST_STATE="$tmp" TEST_PSQL_FORBIDDEN=1 HERMES_BIN="$tmp/bin/hermes" \
  MEMORY_CURATOR_STATE_DIR="$tmp/state-empty" MEMORY_CURATOR_TASKS_DIR="$tmp/tasks-empty" \
  MEMORY_CURATOR_PRIVATE_DOCS="$tmp/none" bin/hermes-memory-curate --direct)"
[[ "$out" == 'status=skipped' ]] || { echo "FAIL: empty direct not skipped: $out" >&2; exit 1; }
[[ -s "$tmp/state-empty/last-run" && ! -e "$tmp/model.calls" ]] || { echo 'FAIL: empty direct ran model or held watermark' >&2; exit 1; }

# Model and required transport failures reconcile with a held watermark and exit 1.
for failure in model transport; do
  state="$tmp/state-$failure"; mkdir -p "$state"; printf '123\n' > "$state/last-run"
  set +e
  if [[ "$failure" == model ]]; then
    out="$(PATH="$tmp/bin:$PATH" TEST_STATE="$tmp" TEST_PSQL_FORBIDDEN=1 TEST_MODEL_FAIL=1 HERMES_BIN="$tmp/bin/hermes" \
      MEMORY_CURATOR_STATE_DIR="$state" MEMORY_CURATOR_TASKS_DIR="$tmp/tasks" \
      MEMORY_CURATOR_PRIVATE_DOCS="$tmp/none" bin/hermes-memory-curate --direct)"; rc=$?
  else
    out="$(PATH="$tmp/bin:$PATH" TEST_STATE="$tmp" TEST_PSQL_FORBIDDEN=1 TEST_CURL_FAIL=1 HERMES_BIN="$tmp/bin/hermes" \
      MEMORY_CURATOR_STATE_DIR="$state" MEMORY_CURATOR_TASKS_DIR="$tmp/tasks" \
      MEMORY_CURATOR_PRIVATE_DOCS="$tmp/none" bin/hermes-memory-curate --direct)"; rc=$?
  fi
  set -e
  [[ "$rc" == 1 && "$out" == 'status=reconcile' && "$(cat "$state/last-run")" == 123 ]] || {
    echo "FAIL: direct $failure failure rc=$rc out=$out watermark=$(cat "$state/last-run")" >&2; exit 1; }
done

# Every proposal item is validated before iteration; malformed output reconciles and holds watermark.
assert_invalid_proposal() {
  local name="$1" proposal="$2" proposal_mode state out rc
  for proposal_mode in postgres direct; do
    state="$tmp/state-invalid-$name-$proposal_mode"; mkdir -p "$state"; printf '123\n' > "$state/last-run"
    rm -f "$tmp/psql-forbidden" "$tmp/settle.sql"
    set +e
    if [[ "$proposal_mode" == direct ]]; then
      out="$(PATH="$tmp/bin:$PATH" TEST_STATE="$tmp" TEST_PSQL_FORBIDDEN=1 TEST_PROPOSAL="$proposal" HERMES_BIN="$tmp/bin/hermes" \
        MEMORY_CURATOR_STATE_DIR="$state" MEMORY_CURATOR_TASKS_DIR="$tmp/tasks" \
        MEMORY_CURATOR_PRIVATE_DOCS="$tmp/none" bin/hermes-memory-curate --direct)"; rc=$?
    else
      out="$(PATH="$tmp/bin:$PATH" TEST_STATE="$tmp" TEST_PROPOSAL="$proposal" HERMES_BIN="$tmp/bin/hermes" \
        REQUESTS_DB_USER=hermes_runtime PGPASSWORD=fake MEMORY_CURATOR_STATE_DIR="$state" \
        MEMORY_CURATOR_TASKS_DIR="$tmp/tasks" MEMORY_CURATOR_PRIVATE_DOCS="$tmp/none" \
        HERMES_QUEUE_LEASE_SECONDS=4 bin/hermes-memory-curate)"; rc=$?
    fi
    set -e
    if [[ "$proposal_mode" == direct ]]; then
      [[ "$rc" == 1 && "$out" == 'status=reconcile' && ! -e "$tmp/psql-forbidden" ]] || {
        echo "FAIL: direct accepted $name rc=$rc out=$out" >&2; exit 1; }
    else
      [[ "$rc" == 0 && "$out" == 'request=5 status=reconcile' && -s "$tmp/settle.sql" ]] || {
        echo "FAIL: postgres did not settle $name rc=$rc out=$out" >&2; exit 1; }
    fi
    [[ "$(cat "$state/last-run")" == 123 ]] || { echo "FAIL: $proposal_mode $name advanced watermark" >&2; exit 1; }
  done
}

assert_invalid_proposal null-entry '{"memories":[null]}'
assert_invalid_proposal null-content '{"memories":[{"content":null}]}'
assert_invalid_proposal wrong-content '{"memories":[{"content":42}]}'
assert_invalid_proposal null-convention '{"memories":[{"content":"valid string","convention":null}]}'
assert_invalid_proposal wrong-convention '{"memories":[{"content":"valid string","convention":"true"}]}'
assert_invalid_proposal null-sources '{"memories":[{"content":"valid string","sources":null}]}'
assert_invalid_proposal wrong-sources '{"memories":[{"content":"valid string","sources":"a"}]}'
assert_invalid_proposal wrong-source-item '{"memories":[{"content":"valid string","sources":[1]}]}'
oversized="$(jq -cn '{memories:[range(16)|{content:"valid string"}]}')"
assert_invalid_proposal oversized "$oversized"

# Lock remains held through the model run; an overlap fails before invoking another model.
state="$tmp/state-overlap"; mkdir -p "$state"; printf '0\n' > "$state/last-run"
block="$tmp/model.block"; touch "$block"; rm -f "$block.started" "$tmp/model.calls"
PATH="$tmp/bin:$PATH" TEST_STATE="$tmp" TEST_PSQL_FORBIDDEN=1 TEST_MODEL_BLOCK="$block" HERMES_BIN="$tmp/bin/hermes" \
  MEMORY_CURATOR_STATE_DIR="$state" MEMORY_CURATOR_TASKS_DIR="$tmp/tasks" \
  MEMORY_CURATOR_PRIVATE_DOCS="$tmp/none" bin/hermes-memory-curate --direct >"$tmp/first.out" & first=$!
for _ in {1..100}; do [[ -e "$block.started" ]] && break; sleep 0.05; done
[[ -e "$block.started" ]] || { echo 'FAIL: first direct run did not reach model' >&2; kill "$first"; exit 1; }
set +e
out="$(PATH="$tmp/bin:$PATH" TEST_STATE="$tmp" TEST_PSQL_FORBIDDEN=1 HERMES_BIN="$tmp/bin/hermes" \
  MEMORY_CURATOR_STATE_DIR="$state" MEMORY_CURATOR_TASKS_DIR="$tmp/tasks" \
  MEMORY_CURATOR_PRIVATE_DOCS="$tmp/none" bin/hermes-memory-curate --direct)"; rc=$?
set -e
[[ "$rc" == 1 && "$out" == 'status=reconcile' ]] || { echo "FAIL: overlap rc=$rc out=$out" >&2; exit 1; }
[[ "$(grep -c . "$tmp/model.calls")" == 1 ]] || { echo 'FAIL: overlap invoked model' >&2; exit 1; }
rm -f "$block"; wait "$first"
[[ "$(cat "$tmp/first.out")" == 'status=done' ]] || { echo 'FAIL: first direct run did not finish' >&2; exit 1; }

rm -f "$tmp/model.calls" "$tmp/psql-forbidden"
set +e
PATH="$tmp/bin:$PATH" TEST_STATE="$tmp" TEST_PSQL_FORBIDDEN=1 HERMES_BIN="$tmp/bin/hermes" \
  bin/hermes-memory-curate --direct --enqueue >/dev/null 2>&1; rc=$?
set -e
[[ "$rc" == 2 && ! -e "$tmp/model.calls" && ! -e "$tmp/psql-forbidden" ]] || { echo 'FAIL: invalid args accepted or performed work' >&2; exit 1; }

echo 'PASS: memory-curate Postgres and direct modes gate writes, failures, and overlap'
