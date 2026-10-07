#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/code"
printf 'repos: []\n' > "$tmp/authority.yaml"
env CODE_ROOT="$tmp/code" HERMES_AUTHORITY_FILE="$tmp/authority.yaml" HINDSIGHT_DB_PASSWORD=x \
  FLEET_CONTROLLER_TLS_CERT_FILE=/dev/null FLEET_CONTROLLER_TLS_KEY_FILE=/dev/null \
  docker compose config --format json > "$tmp/config.json"
env CODE_ROOT="$tmp/code" HERMES_AUTHORITY_FILE="$tmp/authority.yaml" HINDSIGHT_DB_PASSWORD=x \
  FLEET_CONTROLLER_TLS_CERT_FILE=/dev/null FLEET_CONTROLLER_TLS_KEY_FILE=/dev/null \
  docker compose --profile legacy-signal config --format json > "$tmp/legacy-config.json"
python3 - "$tmp/config.json" "$tmp/legacy-config.json" <<'PY'
import json,sys
config=json.load(open(sys.argv[1]))
s=config['services']
legacy=json.load(open(sys.argv[2]))
assert 'signal' not in s and 'signal_state' not in config['volumes']
assert {p['target'] for p in s['ui-proxy']['ports']} == {80, 8080}
assert all(p.get('host_ip') == '127.0.0.1' for p in s['ui-proxy']['ports'])
assert {p['target'] for p in s['hindsight'].get('ports',[])} == {8888}
assert {p['target'] for p in s['coderag']['ports']} == {9750}
assert all(p.get('host_ip') == '127.0.0.1' for p in s['coderag']['ports'])
signal=legacy['services']['signal']
assert signal['profiles'] == ['legacy-signal']
assert signal['image'].startswith('bbernhard/signal-cli-rest-api@sha256:')
assert {p['target'] for p in signal['ports']} == {8080}
assert all(p.get('host_ip') == '127.0.0.1' for p in signal['ports'])
assert any(v['target'] == '/home/.local/share/signal-cli' and v['type'] == 'volume' for v in signal['volumes'])
assert 'signal_state' in legacy['volumes']
assert set(s['ui-proxy']['depends_on']) == {'hindsight', 'coderag'}
PY
for pair in \
  'hermes.localhost http://host.docker.internal:8642' \
  'memory.localhost http://hindsight:9999' \
  'code.localhost http://coderag:9749' \
  'memory-api.localhost http://hindsight:8888'; do
  host="${pair%% *}"; upstream="${pair#* }"
  grep -Fq "server_name $host;" docker/ui-proxy.conf
  grep -Fq "proxy_pass $upstream;" docker/ui-proxy.conf
done
python3 - <<'PY'
from pathlib import Path
proxy = Path('docker/ui-proxy.conf').read_text()
dashboard = proxy.split('server_name dashboard.localhost;', 1)[1].split('\n}\n', 1)[0]
assert 'access_log off;' in dashboard
assert 'proxy_pass ' not in dashboard
assert 'if ($request_method !~ ^(GET|HEAD)$) { return 405; }' in dashboard
assert 'return 302 https://fleet.localhost:8080/;' in dashboard
assert '$request_uri' not in dashboard
PY
grep -Fq 'location /signal/ { return 410; }' docker/ui-proxy.conf
! grep -Fq 'proxy_pass http://signal:8080/;' docker/ui-proxy.conf
grep -Fq 'if ($http_origin != "") { return 403; }' docker/ui-proxy.conf
! grep -Fq 'proxy_set_header Host $http_host;' docker/ui-proxy.conf
grep -Fq 'server_name fleet.localhost;' docker/ui-proxy.conf
grep -Fq 'listen 80 default_server;' docker/ui-proxy.conf
grep -Fq 'return 444;' docker/ui-proxy.conf
grep -Fq 'server_name dashboard.localhost;' docker/ui-proxy.conf
grep -Fq 'return 302 https://fleet.localhost:8080/;' docker/ui-proxy.conf
grep -Fq 'location = / { return 302 https://fleet.localhost:8080/; }' docker/ui-proxy.conf
grep -Fq 'proxy_pass http://host.docker.internal:9119;' docker/ui-proxy.conf
grep -Fq 'proxy_set_header Host 127.0.0.1:9119;' docker/ui-proxy.conf
grep -Fq "'https://fleet.localhost:8080' 'http://127.0.0.1:9119';" docker/ui-proxy.conf
grep -Fq 'if ($fleet_dashboard_origin = invalid) { return 403; }' docker/ui-proxy.conf
grep -Fq 'proxy_set_header Origin $fleet_dashboard_origin;' docker/ui-proxy.conf
grep -Fq 'proxy_set_header X-Forwarded-Proto https;' docker/ui-proxy.conf
! grep -Fq 'proxy_set_header Origin http://127.0.0.1:9119;' docker/ui-proxy.conf
grep -Fq 'proxy_set_header Host 127.0.0.1:8642;' docker/ui-proxy.conf
grep -Fq 'http://hermes.localhost/health' docker/ui-index.html
grep -Fq 'https://fleet.localhost:8080/' docker/ui-index.html
! grep -Fq '/signal/' docker/ui-index.html
! grep -Fq 'Fleet Controller' docker/ui-index.html
grep -Fq 'proxy_set_header Host 127.0.0.1:9999;' docker/ui-proxy.conf
grep -Fq 'proxy_set_header Host 127.0.0.1:9749;' docker/ui-proxy.conf
grep -Fq 'location = /mcp {' docker/ui-proxy.conf
grep -Fq 'proxy_pass http://coderag:9750/mcp;' docker/ui-proxy.conf
grep -Fq 'proxy_buffering off;' docker/ui-proxy.conf
if grep -Fq 'proxy_set_header Origin https://127.0.0.1' docker/ui-proxy.conf; then
  echo 'FAIL: proxy spoofs an allowed Origin and bypasses Fleet CSRF checks' >&2; exit 1
fi
echo 'PASS: nginx routes Hermes Runs API and local support endpoints without legacy status'
