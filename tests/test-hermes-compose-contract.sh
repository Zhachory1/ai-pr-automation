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
EOF
mv "$tmp/.env.example" "$tmp/.env"

docker compose -f "$tmp/docker-compose.yml" --env-file "$tmp/.env" \
  --profile hermes-m0 config --format json > "$tmp/config.json"

jq -e '
  .services["hermes-doc"] as $h |
  ($h.profiles == ["hermes-m0"]) and
  ($h.image == "nousresearch/hermes-agent@sha256:6d7285e1476d0661fc347e3d55245c99decb781d76d39d67582166d2c9561874") and
  ($h.command == ["gateway", "run"]) and
  ($h.environment.API_SERVER_ENABLED == "true") and
  ($h.environment.API_SERVER_HOST == "0.0.0.0") and
  ($h.environment.API_SERVER_PORT == "8642") and
  ($h.environment.API_SERVER_KEY == "0123456789abcdef0123456789abcdef") and
  ($h.environment | keys == ["API_SERVER_ENABLED", "API_SERVER_HOST", "API_SERVER_KEY", "API_SERVER_PORT"]) and
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
  ($h.volumes | length == 1) and
  ($h.volumes[0].type == "volume") and
  ($h.volumes[0].source == "hermes_doc_state") and
  ($h.volumes[0].target == "/opt/data") and
  ($h.healthcheck.test[1] | contains("/health/detailed")) and
  ($h.healthcheck.test[1] | contains("Authorization")) and
  ($h.healthcheck.test[1] | contains("Bearer ")) and
  ($h.healthcheck.test[1] | contains("json.load")) and
  ($h.healthcheck.test[1] | contains("get(\"status\")==\"ok\"")) and
  (.volumes | has("hermes_doc_state")) and
  ([.services | to_entries[] | select(.key != "hermes-doc") |
    ((.value.depends_on // {}) | has("hermes-doc"))] | any | not) and
  ([.services | to_entries[] | select(.key != "hermes-doc") |
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

echo "PASS: Hermes M0 service is pinned, profile-gated, isolated from current services, and not host-published"
