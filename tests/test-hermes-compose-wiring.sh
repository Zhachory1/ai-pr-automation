#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
services=(hermes-controller pr-producer-review pr-producer-maintain pr-safety-producer memory-curate-producer)
for service in "${services[@]}"; do grep -Eq "^  ${service}:" docker-compose.yml || { echo "missing $service" >&2; exit 1; }; done
grep -Fq 'HERMES_API_BASE_URL: http://host.docker.internal:8642' docker-compose.yml
grep -Fq 'hermes_api_keys' docker-compose.yml
grep -Fq 'GITHUB_TOKEN_FILE: /run/secrets/github_read_token' docker-compose.yml
grep -Fq 'GITHUB_READ_TOKEN_FILE=' scripts/fleet.sh
grep -Fq -- '-h "$REQUESTS_DB_HOST" -p "$REQUESTS_DB_PORT"' scripts/hermes-compose-producer.sh
! grep -A35 '^  hermes-controller:' docker-compose.yml | grep -Eq '(/Users/.*/\.hermes|\.ssh|OAuth|docker.sock)'
! grep -Eq 'dispatcher-start|producer-start' scripts/fleet.sh
! grep -Eq '^  (dispatcher|producer)-(start|stop)\)' scripts/hermes-native.sh
grep -Fq '"$ROOT/scripts/compose.sh" up -d --build' scripts/fleet.sh
grep -Fq 'sudo "$ROOT/scripts/hermes-native.sh" dashboard-start' scripts/fleet.sh
echo 'PASS: Compose owns controller/producers; host lifecycle keeps gateway/dashboard only'
