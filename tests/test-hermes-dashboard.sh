#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"
container=""; state=""
cleanup() {
  [[ -z "$container" ]] || docker rm -f "$container" >/dev/null 2>&1 || true
  [[ -z "$state" ]] || docker volume rm -f "$state" >/dev/null 2>&1 || true
  rm -rf "$tmp"
}
trap cleanup EXIT
cp docker-compose.yml .env.example "$tmp/"
cat >> "$tmp/.env.example" <<'EOF'
REQUESTS_DB_PASSWORD=test
GH_TOKEN=test-token
HERMES_DOC_API_KEY=0123456789abcdef0123456789abcdef
HERMES_DASHBOARD_USERNAME=hermes
HERMES_DASHBOARD_PASSWORD=test-dashboard-password
EOF
mv "$tmp/.env.example" "$tmp/.env"
GH_TOKEN=test-token HERMES_DOC_API_KEY=0123456789abcdef0123456789abcdef \
HERMES_DASHBOARD_USERNAME=hermes \
HERMES_DASHBOARD_PASSWORD=test-dashboard-password \
  docker compose -f "$tmp/docker-compose.yml" --env-file "$tmp/.env" \
  --profile hermes-dashboard config --format json > "$tmp/config.json"
jq -e '
  .services["hermes-dashboard-preflight"] as $f |
  .services["hermes-dashboard"] as $d |
  .services["hermes-dashboard-proxy"] as $p |
  ((.services | has("hermes-doc")) | not) and
  ($d.image == "nousresearch/hermes-agent@sha256:6d7285e1476d0661fc347e3d55245c99decb781d76d39d67582166d2c9561874") and
  ($d.command == ["dashboard","--host","0.0.0.0","--port","9119","--no-open","--isolated"]) and
  ($d.environment.HERMES_DASHBOARD_BASIC_AUTH_USERNAME == "hermes") and
  ($d.environment.HERMES_DASHBOARD_BASIC_AUTH_PASSWORD == "test-dashboard-password") and
  ($d.networks | keys == ["hermes-dashboard"]) and
  ($d.depends_on["hermes-dashboard-preflight"].condition == "service_completed_successfully") and
  ($f.network_mode == "none") and ($f.command[2] | contains("#PASSWORD")) and
  ($d.volumes == [{"type":"volume","source":"hermes_doc_state","target":"/opt/data","volume":{}}]) and
  ($p.image == "alpine/socat@sha256:beb4a68d9e4fe6b0f21ea774a0fde6c31f580dde6368939ed70100c5385b015e") and
  ($p.read_only == true) and ($p.cap_drop == ["ALL"]) and
  ($p.user == "65534:65534") and
  ($p.networks | keys == ["hermes-dashboard","hermes-dashboard-host"]) and
  ($p.ports == [{"mode":"ingress","host_ip":"127.0.0.1","target":9119,"published":"9119","protocol":"tcp"}]) and
  (($p | has("environment")) | not) and (($p | has("volumes")) | not) and
  ($p.depends_on["hermes-dashboard"].condition == "service_healthy") and
  (.networks["hermes-dashboard"].internal == true) and
  ((.networks["hermes-dashboard-host"].internal // false) == false) and
  ([.services | to_entries[] | select(.key != "hermes-dashboard-proxy") |
    ((.value.networks // {}) | has("hermes-dashboard-host"))] | any | not)
' "$tmp/config.json" >/dev/null

# shellcheck disable=SC2016
preflight='test -n "$USERNAME" && test ${#PASSWORD} -ge 16'
image=busybox@sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662
if docker run --rm --network none -e USERNAME= -e PASSWORD=short "$image" sh -ec "$preflight"; then
  echo "FAIL: dashboard preflight accepted invalid credentials" >&2; exit 1
fi
docker run --rm --network none -e USERNAME=hermes -e PASSWORD=test-dashboard-password \
  "$image" sh -ec "$preflight"

state="$(docker volume create)"
container="$(docker run -d -p 127.0.0.1::9119 \
  -e API_SERVER_KEY=0123456789abcdef0123456789abcdef \
  -e HERMES_DASHBOARD_BASIC_AUTH_USERNAME=hermes \
  -e HERMES_DASHBOARD_BASIC_AUTH_PASSWORD=test-dashboard-password \
  -v "$state:/opt/data" \
  nousresearch/hermes-agent@sha256:6d7285e1476d0661fc347e3d55245c99decb781d76d39d67582166d2c9561874 \
  dashboard --host 0.0.0.0 --port 9119 --no-open --isolated)"
port="$(docker port "$container" 9119/tcp | awk -F: '{print $NF}')"
for _ in $(seq 1 90); do
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:$port/login" || true)"
  [[ "$code" == 200 ]] && break
  sleep 2
done
[[ "$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "http://127.0.0.1:$port/api/config")" == 401 ]]
[[ "$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 -H 'Content-Type: application/json' \
  --data '{"provider":"basic","username":"hermes","password":"wrong"}' \
  "http://127.0.0.1:$port/auth/password-login")" == 401 ]]
[[ "$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 -c "$tmp/cookies" \
  -H 'Content-Type: application/json' \
  --data '{"provider":"basic","username":"hermes","password":"test-dashboard-password"}' \
  "http://127.0.0.1:$port/auth/password-login")" == 200 ]]
[[ "$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 -b "$tmp/cookies" "http://127.0.0.1:$port/")" == 200 ]]

echo "PASS: Hermes dashboard is authenticated and exposed only through credential-free localhost proxy"
