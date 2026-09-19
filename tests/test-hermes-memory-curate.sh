#!/usr/bin/env bash
# The curate runner is the WRITE GATE: the model only proposes. Verify a secret, a too-short item,
# an unsourced convention, and a near-duplicate are all dropped deterministically before any
# Hindsight write, that a clean memory is written, and that the queue row settles done.
set -euo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/state" "$tmp/tasks/run-1"
printf 'a durable lesson about the fleet queue that is clearly long enough to pass the size gate\n' > "$tmp/tasks/run-1/final.txt"

# Fake psql: claim returns a memory-curate row; renew true; settle captured.
cat > "$tmp/bin/psql" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
args="$*"; input="$(cat || true)"
if [[ "$args" == *hermes_claim_request* ]]; then
  jq -cn '{id:5,kind:"memory-curate",payload:{repo:"local/fleet"},dedupe_key:"memory-curate:1"}'
elif [[ "$args" == *hermes_renew_request* ]]; then
  echo t
elif [[ "$input" == *hermes_settle_request* ]]; then
  printf '%s\n' "$input" > "$TEST_STATE/settle.sql"
  echo t
else
  exit 2
fi
SH

# Fake hermes: write the proposals the "model" produced (one clean, one secret, one too-short,
# one unsourced convention, one near-duplicate of the clean one).
cat > "$tmp/bin/hermes" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
query=""; while (($#)); do [[ "$1" != --query-file ]] || { query="$2"; shift; }; shift; done
result="$(grep -Eo '/[^ ]+/proposals.json' "$query" | head -1)"
cat > "$result" <<'JSON'
{"memories":[
  {"content":"The fleet queue supersedes an older queued head when a newer head lands; rely on it instead of manual cleanup.","sources":["a"]},
  {"content":"token leak ghp_abcdefghijklmnopqrstuvwxyz012345 do not store","sources":["a"]},
  {"content":"too short","sources":["a"]},
  {"content":"This convention about branch naming is asserted but only cites a single source document here.","convention":true,"sources":["a"]},
  {"content":"The fleet queue supersedes an older queued head when a newer head lands rely on it instead of manual cleanup","sources":["a"]}
]}
JSON
SH

# Fake curl: capture Hindsight writes; recall returns the already-written memories for dedup.
cat > "$tmp/bin/curl" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
url=""; data=""; expect_data=0
for a in "$@"; do
  if (( expect_data )); then data="$a"; expect_data=0; continue; fi
  case "$a" in -d) expect_data=1;; http*) url="$a";; esac
done
if [[ "$url" == *"/memories/recall" ]]; then
  # return everything written so far as recall results (valid JSON only)
  printf '{"results":%s}\n' "$(if [[ -f "$TEST_STATE/written.jsonl" ]]; then jq -cs 'map({text:.text})' "$TEST_STATE/written.jsonl"; else echo '[]'; fi)"
elif [[ "$url" == *"/memories" && -n "$data" ]]; then
  content="$(jq -r '.items[0].content' <<<"$data")"
  jq -cn --arg t "$content" '{text:$t}' >> "$TEST_STATE/written.jsonl"
  echo '{"success":true}'
else
  echo '{}'
fi
SH
chmod +x "$tmp/bin/psql" "$tmp/bin/hermes" "$tmp/bin/curl"

out="$(PATH="$tmp/bin:$PATH" TEST_STATE="$tmp" HERMES_BIN="$tmp/bin/hermes" \
  REQUESTS_DB_USER=hermes_runtime PGPASSWORD=fake \
  MEMORY_CURATOR_STATE_DIR="$tmp/state" MEMORY_CURATOR_TASKS_DIR="$tmp/tasks" \
  MEMORY_CURATOR_PRIVATE_DOCS="$tmp/none" HERMES_QUEUE_LEASE_SECONDS=120 \
  bin/hermes-memory-curate)"

echo "$out" | grep -q 'request=5 status=done' || { echo "FAIL: did not settle done: $out" >&2; exit 1; }
grep -q 'hermes_settle_request' "$tmp/settle.sql" || { echo 'FAIL: wrong settle function' >&2; exit 1; }

# Exactly ONE memory written: the clean one. Secret, too-short, unsourced-convention, near-dup dropped.
n_written="$(wc -l < "$tmp/written.jsonl" | tr -d ' ')"
[[ "$n_written" == 1 ]] || { echo "FAIL: expected 1 write, got $n_written" >&2; cat "$tmp/written.jsonl" >&2; exit 1; }
grep -q 'supersedes an older queued head' "$tmp/written.jsonl" || { echo 'FAIL: clean memory not written' >&2; exit 1; }
if grep -q 'ghp_' "$tmp/written.jsonl"; then echo 'FAIL: secret reached the bank' >&2; exit 1; fi

# Watermark advanced (model ran successfully).
[[ -s "$tmp/state/last-run" ]] || { echo 'FAIL: watermark not advanced' >&2; exit 1; }

echo 'PASS: memory-curate runner gates writes (secret/size/convention/dedup) and settles'
