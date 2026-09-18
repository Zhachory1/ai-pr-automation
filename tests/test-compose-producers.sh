#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cp docker-compose.yml .env.example "$TMP/"
printf 'test-controller-password\n' > "$TMP/controller-password"
printf '0123456789abcdef0123456789abcdef\n' > "$TMP/controller-session"
printf '\nGH_TOKEN=test-token\nCODE_ROOT=/tmp/code\nSWARMVAULT_VAULT=/tmp/vault\nAGENT_SERVER_MAINTAIN_REPLICAS=3\nFLEET_CONTROLLER_PASSWORD_FILE=%s\nFLEET_CONTROLLER_SESSION_SECRET_FILE=%s\n' "$TMP/controller-password" "$TMP/controller-session" >> "$TMP/.env.example"
mv "$TMP/.env.example" "$TMP/.env"

docker compose -f "$TMP/docker-compose.yml" --env-file "$TMP/.env" config --format json > "$TMP/config.json"
jq -e '
  (.services["pr-producer-review"].command[0] | contains("/app/bin/pr-producer review")) and
  (.services["pr-producer-maintain"].command[0] | contains("/app/bin/pr-producer maintain")) and
  (.services["pr-producer-review"].environment.REQUESTS_DB_HOST == "db-requests") and
  (.services["pr-producer-review"].environment.PR_PRODUCER_INTERVAL_SECONDS == "900") and
  (.services["pr-producer-review"].environment | has("OPENAI_API_KEY") | not) and
  (.services["pr-producer-maintain"].environment | has("OPENAI_API_KEY") | not) and
  (.services["agent-server-maintain"].deploy.replicas == 3) and
  (.services.status.environment.DOC_WRITE_DAILY_CAP == "30") and
  (.services.status.environment.DOC_WRITER_STAGE_DIR == "/work/stage") and
  (.services.status.environment.FLEET_CONTROLLER_PUBLIC_PORT == "8080") and
  (.services.status.environment.FLEET_CONTROLLER_USERNAME == "fleet") and
  (.services.status.environment.FLEET_CONTROLLER_PASSWORD_FILE == "/run/secrets/fleet_controller_password") and
  (.services.status.environment.FLEET_CONTROLLER_SESSION_SECRET_FILE == "/run/secrets/fleet_controller_session_secret") and
  (.services.status.environment.FLEET_CONTROLLER_SESSION_SECONDS == "43200") and
  ([.services.status.secrets[] | select(.source == "fleet_controller_password" and .target == "/run/secrets/fleet_controller_password")] | length == 1) and
  ([.services.status.secrets[] | select(.source == "fleet_controller_session_secret" and .target == "/run/secrets/fleet_controller_session_secret")] | length == 1) and
  ([.services | to_entries[] | select(.key != "status") | (.value.environment // {}) | to_entries[] | select(.value == "test-controller-password" or .value == "0123456789abcdef0123456789abcdef")] | length == 0) and
  ([.services | to_entries[] | select(.key != "status") | (.value.secrets // [])[] | select(.source == "fleet_controller_password" or .source == "fleet_controller_session_secret")] | length == 0) and
  ([.services.status.volumes[] | select(.source == "doc_writer_work" and .target == "/work" and .read_only == true)] | length == 1) and
  (.services.status.networks | keys == ["status-host", "status-internal"]) and
  (.services["db-requests"].networks | has("status-internal")) and
  (.services.hindsight.networks | has("status-internal")) and
  ((.services["agent-server-review"].networks // {}) | has("status-internal") | not) and
  ((.services["agent-server-maintain"].networks // {}) | has("status-internal") | not) and
  (.networks["status-internal"].internal == true) and
  ((.networks["status-host"].internal // false) == false) and
  ([.services | to_entries[] | select(.key != "status") | ((.value.networks // {}) | has("status-host"))] | any | not)
' "$TMP/config.json" >/dev/null

echo "PASS: Compose config includes scoped producers, configurable maintain replicas, and status doc-write cap"
