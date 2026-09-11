#!/usr/bin/env bash
# Guards issue #91: the GH token must NEVER be interpolated into a git ARGV (ps-visible). Auth is done
# via git's insteadOf config injected through GIT_CONFIG_* env, so the token only lives in process env,
# never in argv and never on disk. This static check catches a regression that would re-expose it.
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0
check() { if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1" >&2; fail=1; fi; }

# 1) No token-bearing URL is ever passed as a bare git fetch/push argument. The token may appear ONLY
#    inside a GIT_CONFIG_KEY_0 insteadOf value.
tokenlines="$(grep -n 'x-access-token:${GH_TOKEN}\|x-access-token:\\${GH_TOKEN}' bin/agent-server || true)"
check "every token reference is in a GIT_CONFIG_KEY insteadOf (not a git argv)" \
  "! grep -nE 'x-access-token:.?[$]\{?GH_TOKEN\}?@' bin/agent-server | grep -v 'GIT_CONFIG_KEY_0' | grep -q ."

# 2) The old argv-injection variables are gone.
check "no auth_url/AUTH_URL argv token injection remains" \
  "! grep -qE 'auth_url=|AUTH_URL=' bin/agent-server"

# 3) The insteadOf rewrite is present at BOTH the fetch setup and the shim.
check "insteadOf auth present at fetch setup (local -x)" \
  "grep -q 'local -x GIT_CONFIG_KEY_0=\"url.https://x-access-token' bin/agent-server"
check "insteadOf auth present in the push shim (export)" \
  "grep -q 'export GIT_CONFIG_KEY_0=\"url.https://x-access-token' bin/agent-server"

# 4) The shim rewrites origin to a PLAIN https url (no token) before exec.
check "shim rewrites origin to plain https url (no token in argv)" \
  "grep -q 'HTTPS_URL=\"https://github.com/' bin/agent-server"

(( fail == 0 ))
