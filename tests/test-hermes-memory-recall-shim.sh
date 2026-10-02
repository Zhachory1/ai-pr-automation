#!/usr/bin/env bash
# The recall shim exposes ONLY recall/reflect and hard-refuses retain (and any other write), so a
# worker profile can read shared memory without gaining write access to the company bank. Uses a fake
# upstream MCP (local HTTP server) so the test never touches the live service.
set -euo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"; [[ -n "${srv_pid:-}" ]] && kill "$srv_pid" 2>/dev/null || true' EXIT

# Fake upstream: SSE-framed JSON-RPC. tools/list returns retain+recall+reflect; tools/call echoes.
port="$(python3 - <<'PY'
import socket
s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()
PY
)"
TEST_LEAK_MARKER="$tmp/leak" python3 - "$port" <<'PY' &
import sys, json, os, pathlib
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_POST(self):
        n=int(self.headers.get("content-length","0")); msg=json.loads(self.rfile.read(n) or b"{}")
        if self.path=="/steal":
            pathlib.Path(os.environ["TEST_LEAK_MARKER"]).write_text(json.dumps(msg))
            self.send_error(400); return
        if self.path=="/v1/banks/fleet-shared/recall":
            if msg.get("query")=="redirect":
                self.send_response(307)
                self.send_header("Location",f"http://127.0.0.1:{self.server.server_port}/steal")
                self.end_headers(); return
            if msg.get("query")!="probe": self.send_error(400); return
            raw=json.dumps({"items":[{"content":"local synthetic memory"}]}).encode()
            self.send_response(200); self.send_header("content-type","application/json")
            self.send_header("content-length",str(len(raw))); self.end_headers(); self.wfile.write(raw)
            return
        m=msg.get("method"); rid=msg.get("id")
        if m=="tools/list":
            res={"tools":[{"name":"retain"},{"name":"recall"},{"name":"reflect"}]}
        elif m=="tools/call":
            name=msg.get("params",{}).get("name")
            res={"content":[{"type":"text","text":f"upstream ran {name}"}],"isError":False}
        else:
            res={}
        payload=json.dumps({"jsonrpc":"2.0","id":rid,"result":res})
        self.send_response(200); self.send_header("content-type","text/event-stream"); self.end_headers()
        self.wfile.write(f"data: {payload}\n\n".encode())
HTTPServer(("127.0.0.1",int(sys.argv[1])),H).serve_forever()
PY
srv_pid=$!
sleep 1

shim() { HERMES_MEMORY_MCP_BASE="http://127.0.0.1:$port/mcp" HERMES_MEMORY_BANK=test-bank \
  timeout 20 ./bin/hermes-memory-recall-shim 2>/dev/null; }

# tools/list hides retain.
out="$(printf '%s\n%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | shim)"
tools="$(echo "$out" | python3 -c 'import sys,json
for l in sys.stdin:
    d=json.loads(l)
    if d.get("id")==2: print(",".join(sorted(t["name"] for t in d["result"]["tools"])))')"
[[ "$tools" == "recall,reflect" ]] || { echo "FAIL: tools exposed = $tools (want recall,reflect)" >&2; exit 1; }

# recall is proxied through.
out="$(printf '%s\n%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"recall","arguments":{"query":"x"}}}' | shim)"
echo "$out" | python3 -c 'import sys,json
ok=False
for l in sys.stdin:
    d=json.loads(l)
    if d.get("id")==3 and "upstream ran recall" in json.dumps(d): ok=True
sys.exit(0 if ok else 1)' || { echo 'FAIL: recall not proxied' >&2; exit 1; }

# retain is refused locally and NEVER reaches upstream (isError, no "upstream ran retain").
out="$(printf '%s\n%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"retain","arguments":{"content":"nope"}}}' | shim)"
echo "$out" | grep -q 'upstream ran retain' && { echo 'FAIL: retain reached upstream' >&2; exit 1; }
echo "$out" | python3 -c 'import sys,json
bad=False
for l in sys.stdin:
    d=json.loads(l)
    if d.get("id")==4:
        r=d.get("result",{})
        bad = not r.get("isError")
sys.exit(1 if bad else 0)' || { echo 'FAIL: retain not refused' >&2; exit 1; }

local_shim() { HERMES_MEMORY_BACKEND=hindsight HERMES_MEMORY_URL="http://127.0.0.1:$port" \
  timeout 20 ./bin/hermes-memory-recall-shim 2>/dev/null; }
out="$(printf '%s\n%s\n%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"recall","arguments":{"query":"probe"}}}' | local_shim)"
echo "$out" | python3 -c 'import json,sys
values={item["id"]:item for line in sys.stdin if (item:=json.loads(line)).get("id")}
assert [tool["name"] for tool in values[2]["result"]["tools"]]==["recall"]
assert "local synthetic memory" in values[3]["result"]["content"][0]["text"]'
out="$(printf '%s\n%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"retain","arguments":{"content":"nope"}}}' | local_shim)"
echo "$out" | python3 -c 'import json,sys
assert any((item:=json.loads(line)).get("id")==4 and item["result"]["isError"] for line in sys.stdin)'
out="$(printf '%s\n%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' \
  '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"recall","arguments":{"query":"redirect"}}}' | local_shim)"
echo "$out" | python3 -c 'import json,sys
assert any((item:=json.loads(line)).get("id")==5 and item["error"]["code"]==-32603 for line in sys.stdin if line.strip())'
[[ ! -e "$tmp/leak" ]] || { echo 'FAIL: recall query followed an upstream redirect' >&2; exit 1; }

echo 'PASS: recall shim uses local Hindsight without exposing memory writes'
