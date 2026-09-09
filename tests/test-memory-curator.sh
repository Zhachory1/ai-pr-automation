#!/usr/bin/env bash
# Tests the memory-curator wrapper: it filters the agent's proposals (reject junk/secret/short,
# require >=2 sources for conventions), dedups against recall, and writes keepers via REST. Uses a
# mock hindsight (recall/write/list) and a fake mewritecode that emits a fixed proposal list.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1

fail=0; check() { if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1" >&2; fail=1; fi; }
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"; [[ -n "${srv:-}" ]] && kill "$srv" 2>/dev/null || true' EXIT

# --- mock hindsight: records writes to $tmp/writes.jsonl; recall returns a canned near-dup once. ---
port=8901
cat > "$tmp/mock.py" <<PY
import json, sys, os
from http.server import BaseHTTPRequestHandler, HTTPServer
W="$tmp/writes.jsonl"
class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def _j(self,code,obj):
        b=json.dumps(obj).encode(); self.send_response(code)
        self.send_header("content-type","application/json"); self.send_header("content-length",str(len(b)))
        self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        if "/memories/list" in self.path: self._j(200,{"memories":[]}); return
        self._j(404,{})
    def do_DELETE(self): self._j(200,{})
    def do_POST(self):
        n=int(self.headers.get("content-length",0)); body=json.loads(self.rfile.read(n) or b"{}")
        if self.path.endswith("/memories/recall"):
            q=body.get("query","")
            # Return a near-identical hit ONLY for the known duplicate probe.
            if "already in the bank verbatim duplicate sentence" in q:
                self._j(200,{"results":[{"text":q}]}); return
            self._j(200,{"results":[]}); return
        if self.path.endswith("/memories"):
            open(W,"a").write(json.dumps(body["items"][0])+"\n")
            self._j(200,{"success":True,"async":False,"bank_id":"fleet-shared","items_count":1}); return
        self._j(404,{})
HTTPServer(("127.0.0.1",$port),H).serve_forever()
PY
python3 "$tmp/mock.py" & srv=$!
for _ in $(seq 1 20); do curl -fsS "http://127.0.0.1:$port/v1/default/banks/fleet-shared/memories/list?tags=curator" >/dev/null 2>&1 && break; sleep 0.2; done

# --- fake mewritecode: emits a proposal list exercising every filter branch. ---
cat > "$tmp/fake-mewrite" <<'SH'
#!/usr/bin/env bash
# ignore args/prompt; emit the fixed JSON the wrapper must filter.
cat <<'JSON'
Here are the memories:
{"memories":[
 {"content":"The pr-safety analyst writes only to the pr-safety bank; the fleet-shared bank is read-only over MCP so agents cannot self-retain — durable architectural boundary worth knowing.","kind":"decision","sources":["a.txt"],"convention":false},
 {"content":"PR ROKT/op3#3550 reviewed with a verdict of comment","kind":"context","sources":["b.txt"],"convention":false},
 {"content":"short","kind":"context","sources":["c.txt"],"convention":false},
 {"content":"The team always requires idempotency keys on all settlement write paths without exception across every service and repository in the org.","kind":"convention","sources":["d.txt"],"convention":true},
 {"content":"A recurring lease bug: workers written against the pre-lease queue API restart-loop every 120s because their marks lack the nonce; always thread the run nonce through queue_mark_* calls.","kind":"root-cause","sources":["e.txt","f.txt"],"convention":false},
 {"content":"already in the bank verbatim duplicate sentence that recall will report as an existing near match here","kind":"context","sources":["g.txt"],"convention":false},
 {"content":"leaked token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345 must never appear in memory content anywhere ever ok","kind":"context","sources":["h.txt"],"convention":false},
 {"content":"a fine-grained pat github_pat_11ABCDE0Y0abcdefghij_KLMNOPqrstuvwxyz0123456789ABCDEFghij snuck into a note and should be rejected outright","kind":"context","sources":["i.txt"],"convention":false},
 {"content":"a google key AIzaSyA1234567890abcdefghijklmnopqrstuv embedded in strategy notes must be rejected before it reaches the shared bank","kind":"context","sources":["j.txt"],"convention":false}
]}
JSON
SH
chmod +x "$tmp/fake-mewrite"

# --- run the curator against the mock, with a source dir so it has "new material". ---
src="$tmp/tasks"; mkdir -p "$src/some-run"; echo "source material" > "$src/some-run/final.txt"
docs="$tmp/nodocs"  # no private-docs -> only transcripts
out="$(MECROMOCK=1 \
  MEWRITECODE_BIN="$tmp/fake-mewrite" \
  MEMORY_CURATOR_AGENT="agent-config/agents/memory-curator.md" \
  HINDSIGHT_URL="http://127.0.0.1:$port" \
  MEMORY_CURATOR_TASKS_DIR="$src" \
  MEMORY_CURATOR_PRIVATE_DOCS="$docs" \
  MEMORY_CURATOR_STATE_DIR="$tmp/state" \
  MEMORY_CURATOR_LOOKBACK_HOURS=99999 \
  bin/memory-curator 2>&1)"
echo "$out" | sed 's/^/  curator: /'

writes="$tmp/writes.jsonl"; touch "$writes"
w_content() { jq -r '.content' "$writes" 2>/dev/null; }

check "wrote the durable architecture decision" "w_content | grep -q 'read-only over MCP'"
check "wrote the lease root-cause" "w_content | grep -q 'restart-loop every 120s'"
check "rejected the review-verdict junk" "! w_content | grep -q 'reviewed with a verdict'"
check "rejected the too-short memory" "! w_content | grep -qx 'short'"
check "rejected the single-source 'convention'" "! w_content | grep -q 'idempotency keys on all settlement'"
check "deduped the existing near-match" "! w_content | grep -q 'verbatim duplicate sentence'"
check "rejected the ghp_ secret-bearing memory" "! w_content | grep -q 'ghp_ABCDEF'"
check "rejected the github_pat_ fine-grained token" "! w_content | grep -q 'github_pat_'"
check "rejected the google AIza key" "! w_content | grep -q 'AIzaSy'"
check "wrote exactly the 2 good memories" "[[ \$(wc -l < \"$writes\" | tr -d ' ') -eq 2 ]]"
check "tagged writes with curator + version" "jq -e '.tags | index(\"curator\")' \"$writes\" >/dev/null && jq -e '.tags | index(\"curator/v1\")' \"$writes\" >/dev/null"
check "doc_id is curator-prefixed content hash" "jq -re '.document_id' \"$writes\" | grep -q '^curator-[0-9a-f]\\{12\\}$'"

# idempotent: same input -> same doc_ids (content hash stable).
ids1="$(jq -r '.document_id' "$writes" | sort)"
: > "$writes"
MEWRITECODE_BIN="$tmp/fake-mewrite" MEMORY_CURATOR_AGENT="agent-config/agents/memory-curator.md" \
  HINDSIGHT_URL="http://127.0.0.1:$port" MEMORY_CURATOR_TASKS_DIR="$src" MEMORY_CURATOR_PRIVATE_DOCS="$docs" \
  MEMORY_CURATOR_STATE_DIR="$tmp/state2" MEMORY_CURATOR_LOOKBACK_HOURS=99999 bin/memory-curator >/dev/null 2>&1
ids2="$(jq -r '.document_id' "$writes" | sort)"
check "doc_ids are stable across runs (idempotent replace)" "[[ \"$ids1\" == \"$ids2\" ]]"

# F1: when the agent run FAILS, the watermark must NOT advance (source window retried next run).
fake_fail="$tmp/fake-fail"; printf '#!/usr/bin/env bash\nexit 7\n' > "$fake_fail"; chmod +x "$fake_fail"
st3="$tmp/state3"; mkdir -p "$st3"
MEWRITECODE_BIN="$fake_fail" MEMORY_CURATOR_AGENT="agent-config/agents/memory-curator.md" \
  HINDSIGHT_URL="http://127.0.0.1:$port" MEMORY_CURATOR_TASKS_DIR="$src" MEMORY_CURATOR_PRIVATE_DOCS="$docs" \
  MEMORY_CURATOR_STATE_DIR="$st3" MEMORY_CURATOR_LOOKBACK_HOURS=99999 bin/memory-curator >/dev/null 2>&1 || true
check "watermark not advanced when agent run fails" "[[ ! -f \"$st3/last-run\" ]]"

(( fail == 0 ))
