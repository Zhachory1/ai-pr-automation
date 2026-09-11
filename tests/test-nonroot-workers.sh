#!/usr/bin/env bash
# Guards #92: worker containers must not run as root. The 3 volume-only workers bake a non-root USER
# and pre-create their volume mountpoints owned by that user; the agent-server images run non-root via
# a compose `user:` (host-uid-matched for the code-root bind). Static checks (no docker build needed).
set -euo pipefail
cd "$(dirname "$0")/.."
fail=0; check(){ if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1" >&2; fail=1; fi; }

for df in Dockerfile.swe-implement-server Dockerfile.doc-writer Dockerfile.memory-curator; do
  check "$df drops to a non-root USER" "grep -qx 'USER fleet' '$df'"
  check "$df creates a non-root user (uid 10001)" "grep -q 'useradd .*--uid 10001 .*fleet' '$df'"
  check "$df pre-creates+chowns /work so a fresh volume is writable" \
    "grep -qE 'mkdir -p /work' '$df' && grep -qE 'chown -R fleet:fleet /app /work' '$df'"
done
check "memory-curator also pre-creates /state" "grep -q 'mkdir -p /work /state' Dockerfile.memory-curator"

# agent-server: NOT baked (needs host uid) but MUST get a compose user:
check "agent-server-review has compose user: FLEET_UID" \
  "grep -A6 '^  agent-server-review:' docker-compose.yml | grep -q 'user: \"\${FLEET_UID:-501}'"
check "agent-server-maintain has compose user: FLEET_UID" \
  "grep -A6 '^  agent-server-maintain:' docker-compose.yml | grep -q 'user: \"\${FLEET_UID:-501}'"
check "agent-server-pr-safety has compose user: FLEET_UID" \
  "grep -A6 '^  agent-server-pr-safety:' docker-compose.yml | grep -q 'user: \"\${FLEET_UID:-501}'"
check "FLEET_UID documented in .env.example" "grep -q '^FLEET_UID=' .env.example"

(( fail == 0 ))
