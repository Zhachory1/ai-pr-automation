#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cp docker-compose.yml .env.example "$tmp/"
cat >> "$tmp/.env.example" <<'EOF'
GH_TOKEN=test-token
CODE_ROOT=/tmp/code
SWARMVAULT_VAULT=/tmp/vault
HERMES_DOC_API_KEY=0123456789abcdef0123456789abcdef
HERMES_DOC_OPENAI_API_KEY=fake-provider-key
EOF
mv "$tmp/.env.example" "$tmp/.env"

docker compose -f "$tmp/docker-compose.yml" --env-file "$tmp/.env" \
  --profile hermes-m0 config --format json > "$tmp/config.json"

jq -e '
  .services["hermes-doc"] as $h |
  ($h.profiles == ["hermes-m0", "hermes-m2a", "doc-writer", "hermes-dashboard"]) and
  ($h.image == "nousresearch/hermes-agent@sha256:6d7285e1476d0661fc347e3d55245c99decb781d76d39d67582166d2c9561874") and
  ($h.command == ["gateway", "run"]) and
  ($h.environment.API_SERVER_ENABLED == "true") and
  ($h.environment.API_SERVER_HOST == "0.0.0.0") and
  ($h.environment.API_SERVER_PORT == "8642") and
  ($h.environment.API_SERVER_KEY == "0123456789abcdef0123456789abcdef") and
  ($h.environment.OPENAI_API_KEY == "fake-provider-key") and
  ($h.environment.OPENAI_BASE_URL == "https://api.openai.com/v1") and
  ($h.environment.HERMES_SAFE_MODE == "1") and
  ($h.environment.HERMES_IGNORE_RULES == "1") and
  ($h.environment.HERMES_API_CALL_STALE_TIMEOUT == "300") and
  ($h.environment.HERMES_CODEX_EVENT_STALE_TIMEOUT_SECONDS == "300") and
  ($h.environment.HERMES_DASHBOARD == "false") and
  ($h.environment.HTTPS_PROXY == "http://hermes-doc-egress:3128") and
  ($h.environment | keys == ["API_SERVER_ENABLED", "API_SERVER_HOST", "API_SERVER_KEY", "API_SERVER_PORT", "HERMES_API_CALL_STALE_TIMEOUT", "HERMES_CODEX_EVENT_STALE_TIMEOUT_SECONDS", "HERMES_DASHBOARD", "HERMES_DASHBOARD_BASIC_AUTH_PASSWORD", "HERMES_DASHBOARD_BASIC_AUTH_USERNAME", "HERMES_DASHBOARD_HOST", "HERMES_DASHBOARD_PORT", "HERMES_IGNORE_RULES", "HERMES_SAFE_MODE", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENSSL_CONF"]) and
  (($h | has("ports")) | not) and
  (($h | has("env_file")) | not) and
  (($h | has("secrets")) | not) and
  (($h | has("configs")) | not) and
  (($h | has("user")) | not) and
  (($h | has("init")) | not) and
  ($h.entrypoint == null) and
  ($h.networks | keys == ["hermes-doc"]) and
  ($h.pids_limit == 256) and
  ($h.mem_limit == "2147483648") and
  ($h.cpus == 1) and
  ($h.volumes | length == 3) and
  ([ $h.volumes[] | select(.type == "volume" and .source == "hermes_doc_state" and .target == "/opt/data") ] | length == 1) and
  ([ $h.volumes[] | select(.type == "bind" and .target == "/opt/data/config.yaml" and .read_only == true) ] | length == 1) and
  ([ $h.volumes[] | select(.type == "bind" and .target == "/etc/hermes/oauth-openssl.cnf" and .read_only == true) ] | length == 1) and
  ($h.environment.OPENSSL_CONF == "/etc/hermes/oauth-openssl.cnf") and
  ($h.healthcheck.test[1] | contains("/health/detailed")) and
  ($h.healthcheck.test[1] | contains("Authorization")) and
  ($h.healthcheck.test[1] | contains("Bearer ")) and
  ($h.healthcheck.test[1] | contains("json.load")) and
  ($h.healthcheck.test[1] | contains("gateway_state")) and
  ($h.healthcheck.test[1] | contains("api_server")) and
  ($h.healthcheck.test[1] | contains("background_queues")) and
  (.volumes | has("hermes_doc_state")) and
  (.networks["hermes-doc"].internal == true) and
  (.services["hermes-doc-preflight"].profiles == ["hermes-m0", "hermes-m2a", "doc-writer", "hermes-dashboard"]) and
  (.services["hermes-doc-preflight"].image == "busybox@sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662") and
  (.services["hermes-doc-preflight"].network_mode == "none") and
  (.services["hermes-doc-preflight"].command[2] | contains("#API_SERVER_KEY")) and
  (.services["hermes-doc-egress"].profiles == ["hermes-m0", "hermes-m2a", "doc-writer", "hermes-dashboard"]) and
  (.services["hermes-doc-egress"].image == "agent-fleet/hermes-doc-egress:m2a") and
  (.services["hermes-doc-egress"].read_only == true) and
  (.services["hermes-doc-egress"].cap_drop == ["ALL"]) and
  (.services["hermes-doc-egress"].networks | keys == ["default", "hermes-doc"]) and
  (.services["hermes-doc-egress"].healthcheck.test[1] | contains("/run/squid.pid")) and
  (.services["hermes-doc-egress"].healthcheck.test[1] | contains(":0C38")) and
  (.services["hermes-doc"].depends_on["hermes-doc-preflight"].condition == "service_completed_successfully") and
  (.services["hermes-doc"].depends_on["hermes-doc-egress"].condition == "service_healthy") and
  ([.services | to_entries[] | select(.key != "hermes-doc") |
    ((.value.depends_on // {}) | has("hermes-doc"))] | any | not) and
  ([.services | to_entries[] | select(.key != "hermes-doc" and .key != "hermes-doc-egress") |
    ((.value.networks // {}) | has("hermes-doc"))] | any | not) and
  ([.services | to_entries[] | select(.key != "hermes-doc") |
    (.value.volumes // [])[]? | select(.source == "hermes_doc_state")] | length == 0)
' "$tmp/config.json" >/dev/null

docker compose -f "$tmp/docker-compose.yml" --env-file "$tmp/.env" config --services \
  | sort > "$tmp/default-services"
cat > "$tmp/expected-services" <<'EOF'
agent-server-maintain
agent-server-review
coderag
db-requests
hindsight
hindsight-bank-init
hindsight-db
pr-producer-maintain
pr-producer-review
schema-migrate
status
swarmvault-mcp
swarmvault-preflight
swarmvault-watch
EOF
if ! diff -u "$tmp/expected-services" "$tmp/default-services"; then
  echo "FAIL: default service set changed" >&2
  exit 1
fi
if docker run --rm --network none -e API_SERVER_KEY=short busybox@sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662 \
  sh -ec 'test ${#API_SERVER_KEY} -ge 16'; then
  echo "FAIL: Hermes preflight accepted short API key" >&2
  exit 1
fi
docker run --rm --network none -e API_SERVER_KEY=0123456789abcdef busybox@sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662 \
  sh -ec 'test ${#API_SERVER_KEY} -ge 16'

echo "PASS: Hermes doc runtime is pinned, zero-tool configured, egress-isolated, and not host-published"
