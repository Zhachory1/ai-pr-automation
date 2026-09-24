#!/usr/bin/env bash
# Real pinned-Hermes conformance with a local fake model provider: profile auth isolation, exact
# idempotency replay/conflict, 429 cap, stop terminal state, restart->interrupted, loopback bind, and
# Docker Desktop container reachability. No provider/GitHub effects.
set -euo pipefail
cd "$(dirname "$0")/.."
install_root="${HERMES_TEST_INSTALL_ROOT:-$HOME/.hermes/hermes-agent}"
hermes="${HERMES_TEST_BIN:-$HOME/.local/bin/hermes}"
[[ -x "$hermes" && -d "$install_root/.git" ]] || { echo 'SKIP: pinned operator Hermes install unavailable'; exit 0; }
# shellcheck disable=SC1091
. agent-config/hermes/native.env
[[ "$(git -C "$install_root" rev-parse HEAD)" == "$HERMES_NATIVE_COMMIT" ]] \
  || { echo 'FAIL: Hermes test install is not pinned commit' >&2; exit 1; }
tmp="$(mktemp -d)"; gateway=""; provider=""
cleanup(){
  if [[ -n "$gateway" ]]; then kill -9 "$gateway" 2>/dev/null || true; wait "$gateway" 2>/dev/null || true; fi
  if [[ -n "$provider" ]]; then kill "$provider" 2>/dev/null || true; wait "$provider" 2>/dev/null || true; fi
  rm -rf "$tmp" 2>/dev/null || true
}
trap cleanup EXIT
port(){ python3 - <<'PY'
import socket
s=socket.socket(); s.bind(('127.0.0.1',0)); print(s.getsockname()[1]); s.close()
PY
}
api_port="$(port)"; provider_port="$(port)"
while [[ "$provider_port" == "$api_port" ]]; do provider_port="$(port)"; done
home="$tmp/home"; mkdir -p "$home/profiles/api-test-v1" "$home/profiles/api-test-v2"
cat > "$home/config.yaml" <<EOF
model:
  provider: openai-api
  default: gpt-4o-mini
  base_url: http://127.0.0.1:$provider_port/v1
  api_mode: responses
gateway:
  multiplex_profiles: true
  api_server:
    enabled: true
    host: 127.0.0.1
    port: $api_port
    max_concurrent_runs: 1
EOF
sed 's/enabled: true/enabled: false/' "$home/config.yaml" > "$home/profiles/api-test-v1/config.yaml"
sed 's/enabled: true/enabled: false/' "$home/config.yaml" > "$home/profiles/api-test-v2/config.yaml"
default_key=default-key-000000000000000000000000000
profile_key=profile-key-1111111111111111111111111111
profile_key2=profile-key-2222222222222222222222222222
printf 'API_SERVER_KEY=%s\nOPENAI_API_KEY=fake-key\n' "$default_key" > "$home/.env"
printf 'API_SERVER_KEY=%s\nOPENAI_API_KEY=fake-key\n' "$profile_key" > "$home/profiles/api-test-v1/.env"
printf 'API_SERVER_KEY=%s\nOPENAI_API_KEY=fake-key\n' "$profile_key2" > "$home/profiles/api-test-v2/.env"
printf 'Return exactly the requested text. Do not use tools.\n' > "$home/profiles/api-test-v1/SOUL.md"
printf 'name: api-test-v1\nversion: 1.0.0\n' > "$home/profiles/api-test-v1/distribution.yaml"
cp "$home/profiles/api-test-v1/SOUL.md" "$home/profiles/api-test-v2/SOUL.md"
printf 'name: api-test-v2\nversion: 1.0.0\n' > "$home/profiles/api-test-v2/distribution.yaml"
touch "$home/profiles/api-test-v1/.no-bundled-skills" "$home/profiles/api-test-v2/.no-bundled-skills"
printf '{"schema_version":1,"auth_generation":1,"profiles":{"api-test-v1":"%s","api-test-v2":"%s"}}\n' "$profile_key" "$profile_key2" > "$tmp/keys.json"
PORT="$provider_port" CAPTURE="$tmp/provider-request.json" python3 tests/fake-hermes-api-provider.py >"$tmp/provider.log" 2>&1 & provider=$!
start_gateway(){ HOME="$HOME" HERMES_HOME="$home" API_SERVER_ENABLED=true API_SERVER_HOST=127.0.0.1 \
  API_SERVER_PORT="$api_port" API_SERVER_KEY="$default_key" GATEWAY_MULTIPLEX_PROFILES=true OPENAI_API_KEY=fake-key \
  "$hermes" gateway run >"$tmp/gateway.log" 2>&1 & gateway=$!; }
wait_ready(){ for _ in $(seq 1 150); do curl -fsS -H "Authorization: Bearer $default_key" "http://127.0.0.1:$api_port/v1/models" >/dev/null 2>&1 && return; sleep .2; done; cat "$tmp/gateway.log" >&2; return 1; }
start_gateway; wait_ready
for profile in api-test-v1 api-test-v2; do
  code="$(curl -sS -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $default_key" \
    "http://127.0.0.1:$api_port/p/$profile/v1/models")"
  [[ "$code" == 401 ]] || { echo "FAIL: default listener key reached $profile (HTTP $code)" >&2; exit 1; }
done
lsof -nP -iTCP:"$api_port" -sTCP:LISTEN | grep -Fq "127.0.0.1:$api_port" || { echo 'FAIL: API not loopback-bound' >&2; exit 1; }
python3 scripts/hermes-api-conformance.py --base-url "http://127.0.0.1:$api_port" --keys-file "$tmp/keys.json" --run-profile api-test-v1 --run-timeout 20 >/dev/null
# Controller-container reachability to host loopback through Docker Desktop.
docker run --rm --add-host host.docker.internal:host-gateway curlimages/curl:8.11.1 -fsS \
  -H "Authorization: Bearer $profile_key" "http://host.docker.internal:$api_port/p/api-test-v1/v1/models" >/dev/null
post_run(){ local key="$1" input="$2"; curl -fsS -D "$tmp/headers" -H "Authorization: Bearer $profile_key" \
  -H 'Content-Type: application/json' -H "Idempotency-Key: $key" -d "{\"input\":\"$input\"}" \
  "http://127.0.0.1:$api_port/p/api-test-v1/v1/runs"; }
# 429 while one slow run occupies the global slot.
slow="$(post_run cap-slow 'slow CONFORMANCE_OK')"; slow_id="$(jq -r .run_id <<<"$slow")"
code="$(curl -sS -o "$tmp/second" -w '%{http_code}' -H "Authorization: Bearer $profile_key" -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: cap-second' -d '{"input":"CONFORMANCE_OK"}' "http://127.0.0.1:$api_port/p/api-test-v1/v1/runs")"
[[ "$code" == 429 ]] || { echo "FAIL: expected 429, got $code" >&2; exit 1; }
# Stop reaches a terminal status.
curl -fsS -X POST -H "Authorization: Bearer $profile_key" "http://127.0.0.1:$api_port/p/api-test-v1/v1/runs/$slow_id/stop" >/dev/null
terminal=""; for _ in $(seq 1 30); do terminal="$(curl -fsS -H "Authorization: Bearer $profile_key" "http://127.0.0.1:$api_port/p/api-test-v1/v1/runs/$slow_id" | jq -r .status)"; [[ "$terminal" =~ ^(cancelled|completed|failed|interrupted)$ ]] && break; sleep .3; done
[[ "$terminal" =~ ^(cancelled|completed|failed|interrupted)$ ]] || { echo 'FAIL: stop never terminal' >&2; exit 1; }
# Gateway restart turns an active owner into durable interrupted.
restart="$(post_run restart-slow 'slow CONFORMANCE_OK')"; restart_id="$(jq -r .run_id <<<"$restart")"
kill -9 "$gateway"; wait "$gateway" 2>/dev/null || true; gateway=""; start_gateway; wait_ready
status="$(curl -fsS -H "Authorization: Bearer $profile_key" "http://127.0.0.1:$api_port/p/api-test-v1/v1/runs/$restart_id" | jq -r .status)"
[[ "$status" == interrupted ]] || { echo "FAIL: restart status=$status" >&2; exit 1; }
echo 'PASS: pinned Hermes Runs API auth/replay/409/429/stop/restart/loopback/container conformance'
