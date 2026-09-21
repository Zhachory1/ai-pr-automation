#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/code" "$tmp/vault" "$tmp/stage" "$tmp/handoffs"
env CODE_ROOT="$tmp/code" SWARMVAULT_VAULT="$tmp/vault" REQUESTS_DB_PASSWORD=x HINDSIGHT_DB_PASSWORD=x \
  DOC_WRITER_STAGE_HOST="$tmp/stage" HANDOFF_ROOT="$tmp/handoffs" \
  FLEET_CONTROLLER_PASSWORD_FILE=/dev/null FLEET_CONTROLLER_SESSION_SECRET_FILE=/dev/null \
  FLEET_CONTROLLER_TLS_CA_CERT_FILE=/dev/null FLEET_CONTROLLER_TLS_CERT_FILE=/dev/null \
  FLEET_CONTROLLER_TLS_KEY_FILE=/dev/null UI_BASIC_AUTH_FILE=/dev/null docker compose config --format json > "$tmp/config.json"
python3 - "$tmp/config.json" <<'PY'
import json,sys
s=json.load(open(sys.argv[1]))['services']
assert {p['target'] for p in s['ui-proxy']['ports']} == {8080}
assert all(p.get('host_ip') == '127.0.0.1' for p in s['ui-proxy']['ports'])
assert not s['status'].get('ports')
assert {p['target'] for p in s['hindsight'].get('ports',[])} == {8888}
assert not s['coderag'].get('ports')
PY
for pair in \
  'fleet.localhost https://status:8080' \
  'hermes.localhost http://host.docker.internal:9119' \
  'memory.localhost http://hindsight:9999' \
  'code.localhost http://coderag:9749'; do
  host="${pair%% *}"; upstream="${pair#* }"
  grep -Fq "server_name $host;" docker/ui-proxy.conf
  grep -Fq "proxy_pass $upstream;" docker/ui-proxy.conf
done
[[ "$(grep -c 'auth_basic_user_file /run/secrets/ui_basic_auth;' docker/ui-proxy.conf)" == 3 ]]
grep -Fq 'proxy_set_header Host $http_host;' docker/ui-proxy.conf
grep -Fq 'proxy_set_header Origin $http_origin;' docker/ui-proxy.conf
if grep -Fq 'proxy_set_header Origin https://127.0.0.1' docker/ui-proxy.conf; then
  echo 'FAIL: proxy spoofs an allowed Origin and bypasses Fleet CSRF checks' >&2; exit 1
fi
echo 'PASS: nginx is sole UI port, routes all UIs, preserves Fleet CSRF, and auth-gates diagnostics'
