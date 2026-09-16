#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cp docker-compose.yml .env.example "$tmp/"
mv "$tmp/.env.example" "$tmp/.env"
common=(
  OPENAI_API_KEY=legacy-provider-key-1234567890
  HERMES_DOC_API_KEY=0123456789abcdef0123456789abcdef
  HERMES_DOC_OPENAI_API_KEY=hermes-provider-key-1234567890
  HERMES_DOC_APPROVED_GENERATION=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  REQUESTS_DB_PASSWORD=test
)
env "${common[@]}" docker compose -f "$tmp/docker-compose.yml" --env-file "$tmp/.env" \
  --profile doc-writer config --format json > "$tmp/hermes.json"
jq -e '
  .services["doc-writer-server"] as $d |
  ($d.environment.DOC_WRITER_RUNTIME == "hermes") and
  ($d.environment.HERMES_DOC_URL == "http://hermes-doc:8642") and
  ($d.environment.HERMES_DOC_API_KEY == "0123456789abcdef0123456789abcdef") and
  ($d.environment.HERMES_DOC_PROVIDER_KEY == "hermes-provider-key-1234567890") and
  ($d.environment.HERMES_DOC_APPROVED_GENERATION == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa") and
  ($d.restart == "no") and
  ($d.stop_grace_period == "31m0s") and
  ($d.networks | keys == ["default","hermes-doc"]) and
  ($d.depends_on["hermes-doc"].condition == "service_healthy") and
  ($d.depends_on.hindsight.condition == "service_healthy") and
  ($d.depends_on.coderag.condition == "service_healthy") and
  ((.services | has("hermes-doc-live-preflight")) | not)
' "$tmp/hermes.json" >/dev/null

env "${common[@]}" DOC_WRITER_RUNTIME=legacy HERMES_DOC_OPENAI_API_KEY= HERMES_DOC_APPROVED_GENERATION= \
  docker compose -f "$tmp/docker-compose.yml" --env-file "$tmp/.env" \
  --profile doc-writer config --format json > "$tmp/legacy.json"
jq -e '.services["doc-writer-server"].environment.DOC_WRITER_RUNTIME == "legacy"' "$tmp/legacy.json" >/dev/null

echo "PASS: doc controller defaults to fail-stop Hermes and retains explicit legacy mode"
