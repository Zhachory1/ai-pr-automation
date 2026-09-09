#!/usr/bin/env bash
# Verifies the hindsight-bank-init script: it PATCHes the shared bank to a read-only MCP tool set
# (no retain/sync_retain), fails loud if the API rejects or retain survives, and is idempotent.
# Uses a tiny mock hindsight (python http.server) so no live service is required.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1

fail=0
check() { if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1" >&2; fail=1; fi; }

# 1) static: the allowlist in the script must exclude the write tools.
check "script excludes retain from allowlist" \
  "! grep -oE 'ALLOW=.*' docker/hindsight-bank-init.sh | grep -qE '\"(retain|sync_retain)\"'"
check "script keeps recall in allowlist" \
  "grep -oE 'ALLOW=.*' docker/hindsight-bank-init.sh | grep -q '\"recall\"'"
check "script is POSIX sh valid" "sh -n docker/hindsight-bank-init.sh"

# 2) functional: run against a mock that echoes the PATCH body back like hindsight does.
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"; [[ -n "${srv:-}" ]] && kill "$srv" 2>/dev/null || true' EXIT
cat > "$tmp/mock.py" <<'PY'
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_GET(self):
        if self.path.endswith("/health/ready"):
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok"); return
        self.send_response(404); self.end_headers()
    def do_PATCH(self):
        n=int(self.headers.get("content-length",0)); body=json.loads(self.rfile.read(n) or b"{}")
        tools=body.get("updates",{}).get("mcp_enabled_tools")
        # echo the applied config back exactly like hindsight
        out=json.dumps({"config":{"mcp_enabled_tools":tools}}).encode()
        self.send_response(200); self.send_header("content-type","application/json")
        self.send_header("content-length",str(len(out))); self.end_headers(); self.wfile.write(out)
HTTPServer(("127.0.0.1",int(sys.argv[1])),H).serve_forever()
PY
port=8899
python3 "$tmp/mock.py" "$port" & srv=$!
for _ in $(seq 1 20); do curl -fsS "http://127.0.0.1:$port/health/ready" >/dev/null 2>&1 && break; sleep 0.2; done

out="$(HINDSIGHT_URL="http://127.0.0.1:$port" HINDSIGHT_SHARED_BANK=fleet-shared sh docker/hindsight-bank-init.sh 2>&1)"
check "init succeeds against mock" "grep -q \"done: 'fleet-shared'\" <<<\"\$out\""
check "init reports the read-only tool set" "grep -q '\"recall\"' <<<\"\$out\" && ! grep -q '\"retain\"' <<<\"\$out\""

# 3) idempotent: second run also succeeds.
out2="$(HINDSIGHT_URL="http://127.0.0.1:$port" sh docker/hindsight-bank-init.sh 2>&1)"
check "init is idempotent" "grep -q \"done: 'fleet-shared'\" <<<\"\$out2\""

# 4) fail-loud: a mock that returns retain still present must make the script exit non-zero.
cat > "$tmp/badmock.py" <<'PY'
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_GET(self):
        if self.path.endswith("/health/ready"): self.send_response(200); self.end_headers(); self.wfile.write(b"ok"); return
        self.send_response(404); self.end_headers()
    def do_PATCH(self):
        n=int(self.headers.get("content-length",0)); self.rfile.read(n)
        out=b'{"config":{"mcp_enabled_tools":["recall","retain"]}}'
        self.send_response(200); self.send_header("content-length",str(len(out))); self.end_headers(); self.wfile.write(out)
HTTPServer(("127.0.0.1",int(sys.argv[1])),H).serve_forever()
PY
kill "$srv" 2>/dev/null || true; sleep 0.3
python3 "$tmp/badmock.py" "$port" & srv=$!
for _ in $(seq 1 20); do curl -fsS "http://127.0.0.1:$port/health/ready" >/dev/null 2>&1 && break; sleep 0.2; done
rc=0; HINDSIGHT_URL="http://127.0.0.1:$port" sh docker/hindsight-bank-init.sh >/dev/null 2>&1 || rc=$?
check "init fails loud when retain survives the PATCH" "[[ \$rc -ne 0 ]]"

# 5) fail-loud: response silently drops mcp_enabled_tools (API drift) must NOT pass by omission.
cat > "$tmp/driftmock.py" <<'PY'
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_GET(self):
        if self.path.endswith("/health/ready"): self.send_response(200); self.end_headers(); self.wfile.write(b"ok"); return
        self.send_response(404); self.end_headers()
    def do_PATCH(self):
        n=int(self.headers.get("content-length",0)); self.rfile.read(n)
        out=b'{"config":{}}'  # field dropped entirely
        self.send_response(200); self.send_header("content-length",str(len(out))); self.end_headers(); self.wfile.write(out)
HTTPServer(("127.0.0.1",int(sys.argv[1])),H).serve_forever()
PY
kill "$srv" 2>/dev/null || true; sleep 0.3
python3 "$tmp/driftmock.py" "$port" & srv=$!
for _ in $(seq 1 20); do curl -fsS "http://127.0.0.1:$port/health/ready" >/dev/null 2>&1 && break; sleep 0.2; done
rc=0; HINDSIGHT_URL="http://127.0.0.1:$port" sh docker/hindsight-bank-init.sh >/dev/null 2>&1 || rc=$?
check "init fails loud when mcp_enabled_tools is dropped from the response" "[[ \$rc -ne 0 ]]"

# 6) cold volume: first PATCH 404s (bank absent) -> script creates bank + retries -> succeeds.
cat > "$tmp/coldmock.py" <<'PY'
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
state={"created":False}
class H(BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def do_GET(self):
        if self.path.endswith("/health/ready"): self.send_response(200); self.end_headers(); self.wfile.write(b"ok"); return
        self.send_response(404); self.end_headers()
    def do_POST(self):
        n=int(self.headers.get("content-length",0)); self.rfile.read(n)
        state["created"]=True; self.send_response(200); self.end_headers(); self.wfile.write(b'{}')
    def do_PATCH(self):
        n=int(self.headers.get("content-length",0)); body=json.loads(self.rfile.read(n) or b"{}")
        if not state["created"]:
            self.send_response(404); self.end_headers(); self.wfile.write(b'{"detail":"bank not found"}'); return
        tools=body.get("updates",{}).get("mcp_enabled_tools")
        out=json.dumps({"config":{"mcp_enabled_tools":tools}}).encode()
        self.send_response(200); self.send_header("content-length",str(len(out))); self.end_headers(); self.wfile.write(out)
HTTPServer(("127.0.0.1",int(sys.argv[1])),H).serve_forever()
PY
kill "$srv" 2>/dev/null || true; sleep 0.3
python3 "$tmp/coldmock.py" "$port" & srv=$!
for _ in $(seq 1 20); do curl -fsS "http://127.0.0.1:$port/health/ready" >/dev/null 2>&1 && break; sleep 0.2; done
coldout="$(HINDSIGHT_URL="http://127.0.0.1:$port" sh docker/hindsight-bank-init.sh 2>&1)" && coldrc=0 || coldrc=$?
check "init creates the bank and retries when first PATCH 404s" "[[ \$coldrc -eq 0 ]] && grep -q \"done: 'fleet-shared'\" <<<\"\$coldout\""

(( fail == 0 ))
