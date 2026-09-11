#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
cp docker-compose.yml .env.example "$TMP/"
printf '\nGH_TOKEN=test-token\nCODE_ROOT=/tmp/code\nSWARMVAULT_VAULT=/tmp/vault\nAGENT_SERVER_MAINTAIN_REPLICAS=3\n' >> "$TMP/.env.example"
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
  (.services.status.environment.DOC_WRITE_DAILY_CAP == "30")
' "$TMP/config.json" >/dev/null

echo "PASS: Compose config includes scoped producers, configurable maintain replicas, and status doc-write cap"
