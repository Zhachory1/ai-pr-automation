#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cat >"$TMP/hostname" <<'SH'
#!/bin/sh
printf '%s\n' '172.18.0.2 172.19.0.2'
SH
cat >"$TMP/socat" <<'SH'
#!/bin/sh
printf '%s\n' "$*" >>/out/socat.log
SH
cat >"$TMP/codebase-memory-mcp" <<'SH'
#!/bin/sh
exit 0
SH
cat >"$TMP/supergateway" <<'SH'
#!/bin/sh
sleep 1
SH
chmod +x "$TMP/"{hostname,socat,codebase-memory-mcp,supergateway}

docker run --rm --entrypoint sh \
  -v "$PWD/docker/coderag-entrypoint.sh:/entrypoint:ro" \
  -v "$TMP:/out" \
  -v "$TMP/hostname:/usr/local/bin/hostname:ro" \
  -v "$TMP/socat:/usr/local/bin/socat:ro" \
  -v "$TMP/codebase-memory-mcp:/usr/local/bin/codebase-memory-mcp:ro" \
  -v "$TMP/supergateway:/usr/local/bin/supergateway:ro" \
  node:24-bookworm-slim /entrypoint serve-all

grep -Fx 'TCP-LISTEN:9749,bind=172.18.0.2,reuseaddr,fork TCP:127.0.0.1:9749' "$TMP/socat.log"
grep -Fx 'TCP-LISTEN:9749,bind=172.19.0.2,reuseaddr,fork TCP:127.0.0.1:9749' "$TMP/socat.log"
[[ "$(wc -l <"$TMP/socat.log")" -eq 2 ]]
echo 'PASS: coderag UI forwards every container interface'
