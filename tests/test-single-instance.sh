#!/usr/bin/env bash
# COUNCIL-required: single-instance enforcement. Asserts a second holder of the advisory lock
# is refused while the first is alive, and that the lock releases when the first dies.
# Tests the SAME coproc-held-session pattern bin/agent-server uses.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

CID="m1-lock-test-$$"
PORT=55440
cleanup(){ docker rm -f "$CID" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker run --rm -d --name "$CID" -e POSTGRES_PASSWORD=t -e POSTGRES_DB=fleet -p "$PORT:5432" postgres:16 >/dev/null
for _ in $(seq 1 30); do docker exec "$CID" pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done

export REQUESTS_DB_USER=postgres REQUESTS_DB_NAME=fleet REQUESTS_DB_HOST=localhost REQUESTS_DB_PORT="$PORT" PGPASSWORD=t
export AGENT_SERVER_LOCK_KEY=774411
# shellcheck source=../lib/queue.sh
. lib/queue.sh
set +e
fail=0
check(){ if eval "$2"; then echo "  PASS: $1"; else echo "  FAIL: $1"; fail=1; fi; }

# Hold the lock in a long-lived coproc session (same pattern as bin/agent-server).
coproc HOLD { _psql 2>/dev/null; }
HOLD_PID=$!
printf "SELECT pg_try_advisory_lock(%s);\n" "$AGENT_SERVER_LOCK_KEY" >&"${HOLD[1]}"
read -r r1 <&"${HOLD[0]}"
exec {HOLD_KEEP}>&"${HOLD[1]}"   # keep write-fd open => session stays alive => lock held
check "holder 1 acquires lock" "[[ \"$r1\" == t ]]"

# Second attempt in a separate one-shot session while holder 1 is alive.
r2="$(_psql <<<"SELECT pg_try_advisory_lock($AGENT_SERVER_LOCK_KEY);")"
check "holder 2 refused while holder 1 alive" "[[ \"$r2\" == f ]]"

# Kill holder 1's session -> lock releases.
exec {HOLD_KEEP}>&-      # close our kept fd
kill "$HOLD_PID" 2>/dev/null
wait "$HOLD_PID" 2>/dev/null
sleep 1
r3="$(_psql <<<"SELECT pg_try_advisory_lock($AGENT_SERVER_LOCK_KEY);")"
check "holder 3 acquires after holder 1 dies" "[[ \"$r3\" == t ]]"

echo
[[ $fail -eq 0 ]] && echo "ALL PASS" || { echo "FAILURES"; exit 1; }
