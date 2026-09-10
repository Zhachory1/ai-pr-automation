#!/usr/bin/env bash
# Tests backfill mode: the curator's MEMORY_CURATOR_SOURCE_MANIFEST override curates exactly the given
# files without touching the schedule watermark; and bin/memory-curator-backfill walks the whole
# corpus in batches with a resumable cursor. Uses a mock hindsight + fake mewritecode.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1

fail=0; check() { if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1" >&2; fail=1; fi; }
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"; [[ -n "${srv:-}" ]] && kill "$srv" 2>/dev/null || true' EXIT

# mock hindsight: records writes; recall/list return empty.
port=8903
cat > "$tmp/mock.py" <<PY
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
W="$tmp/writes.jsonl"
class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def _j(self,c,o):
        b=json.dumps(o).encode(); self.send_response(c)
        self.send_header("content-length",str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if "/memories/list" in self.path: self._j(200,{"memories":[]}); return
        self._j(404,{})
    def do_DELETE(self): self._j(200,{})
    def do_POST(self):
        n=int(self.headers.get("content-length",0)); body=json.loads(self.rfile.read(n) or b"{}")
        if self.path.endswith("/memories/recall"): self._j(200,{"results":[]}); return
        if self.path.endswith("/memories"):
            open(W,"a").write(json.dumps(body["items"][0])+"\n"); self._j(200,{"success":True,"async":False,"bank_id":"fleet-shared","items_count":1}); return
        self._j(404,{})
HTTPServer(("127.0.0.1",$port),H).serve_forever()
PY
python3 "$tmp/mock.py" & srv=$!
for _ in $(seq 1 20); do curl -fsS "http://127.0.0.1:$port/v1/default/banks/fleet-shared/memories/list?tags=curator" >/dev/null 2>&1 && break; sleep 0.2; done

# fake mewritecode: emits one durable memory citing whatever it was asked about.
cat > "$tmp/fake-mewrite" <<'SH'
#!/usr/bin/env bash
cat <<'JSON'
{"memories":[{"content":"A durable brain fact worth remembering that is clearly longer than forty characters here.","kind":"context","sources":["doc"],"convention":false}]}
JSON
SH
chmod +x "$tmp/fake-mewrite"

# a fake private-docs corpus of 5 .md files
docs="$tmp/private-docs"; mkdir -p "$docs/sub"
for i in 1 2 3 4 5; do echo "# note $i" > "$docs/note$i.md"; done
echo "# nested" > "$docs/sub/deep.md"   # 6 total

run_curator_env=(
  MEWRITECODE_BIN="$tmp/fake-mewrite"
  MEMORY_CURATOR_AGENT="agent-config/agents/memory-curator.md"
  HINDSIGHT_URL="http://127.0.0.1:$port"
  MEMORY_CURATOR_PRIVATE_DOCS="$docs"
)

# --- 1) manifest-override mode curates the given files and does NOT write a watermark ---
st1="$tmp/state1"; mkdir -p "$st1"
man="$tmp/man.txt"; printf '%s\n%s\n' "$docs/note1.md" "$docs/note2.md" >"$man"
env "${run_curator_env[@]}" MEMORY_CURATOR_STATE_DIR="$st1" MEMORY_CURATOR_SOURCE_MANIFEST="$man" \
  bin/memory-curator >/dev/null 2>&1 || true
check "backfill/override mode writes memories from the manifest" "[[ \$(wc -l < \"$tmp/writes.jsonl\" 2>/dev/null | tr -d ' ') -ge 1 ]]"
check "override mode does NOT create/advance the schedule watermark" "[[ ! -f \"$st1/last-run\" ]]"

# --- 2) the backfill driver walks the whole corpus and marks complete ---
st2="$tmp/state2"; mkdir -p "$st2"; : > "$tmp/writes.jsonl"
env "${run_curator_env[@]}" MEMORY_CURATOR_STATE_DIR="$st2" \
  MEMORY_CURATOR_BACKFILL_BATCH=2 MEMORY_CURATOR_BACKFILL_SLEEP=0 \
  bin/memory-curator-backfill >/dev/null 2>&1 || true
processed="$(grep -c . "$st2/backfill-done" 2>/dev/null | tr -d ' ' || echo 0)"
check "driver processed all 6 corpus docs" "[[ \"$processed\" -eq 6 ]]"
check "driver marks backfill complete" "[[ -f \"$st2/backfill-complete\" ]]"
check "driver did not touch the schedule watermark" "[[ ! -f \"$st2/last-run\" ]]"

# --- 3) re-running the driver is a no-op (idempotent / resumable cursor) ---
out="$(env "${run_curator_env[@]}" MEMORY_CURATOR_STATE_DIR="$st2" bin/memory-curator-backfill 2>&1 || true)"
check "re-run is a no-op once complete" "grep -q 'already completed' <<<\"\$out\""

# --- 4) resume: a fresh cursor with 2 already-done skips them ---
st3="$tmp/state3"; mkdir -p "$st3"
printf '%s\n%s\n' "$docs/note1.md" "$docs/note2.md" > "$st3/backfill-done"
env "${run_curator_env[@]}" MEMORY_CURATOR_STATE_DIR="$st3" \
  MEMORY_CURATOR_BACKFILL_BATCH=10 MEMORY_CURATOR_BACKFILL_SLEEP=0 \
  bin/memory-curator-backfill >/dev/null 2>&1 || true
done3="$(grep -c . "$st3/backfill-done" 2>/dev/null | tr -d ' ' || echo 0)"
check "resume processes only the remaining docs (ends at 6 total)" "[[ \"$done3\" -eq 6 ]]"

(( fail == 0 ))
