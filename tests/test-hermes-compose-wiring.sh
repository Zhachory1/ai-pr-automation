#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
services=(hermes-controller pr-producer-review pr-producer-maintain pr-safety-producer memory-curate-producer)
for service in "${services[@]}"; do grep -Eq "^  ${service}:" docker-compose.yml || { echo "missing $service" >&2; exit 1; }; done
grep -Fq 'HERMES_API_BASE_URL: http://host.docker.internal:8642' docker-compose.yml
grep -Fq 'HERMES_KANBAN_BRIDGE_URL: http://host.docker.internal:8766' docker-compose.yml
grep -Fq 'HERMES_KANBAN_BRIDGE_HOST: hermes-council.localhost:8766' docker-compose.yml
grep -Fq 'PR_SAFETY_ANALYSIS_ENGINE: ${PR_SAFETY_ANALYSIS_ENGINE:-single}' docker-compose.yml
grep -Fq 'hermes_api_keys' docker-compose.yml
[[ "$(grep -c 'hermes_kanban_bridge_key' docker-compose.yml)" == 3 ]]
grep -Fq 'GITHUB_TOKEN_FILE: /run/secrets/github_read_token' docker-compose.yml
grep -A5 '^  pr-safety-producer:' docker-compose.yml | grep -Fq 'user: "0:0"'
grep -Fq 'GITHUB_READ_TOKEN_FILE=' scripts/fleet.sh
grep -Fq -- '-h "$REQUESTS_DB_HOST" -p "$REQUESTS_DB_PORT"' scripts/hermes-compose-producer.sh
grep -Fq 'gh auth setup-git' scripts/hermes-compose-producer.sh
! grep -A35 '^  hermes-controller:' docker-compose.yml | grep -Eq '(/Users/.*/\.hermes|\.ssh|OAuth|docker.sock)'
! grep -Eq 'dispatcher-start' scripts/fleet.sh
grep -Fq 'hermes-native.sh" producer-start' scripts/fleet.sh
grep -Eq '^  producer-(start|stop)\)' scripts/hermes-native.sh
grep -Fq 'PR_SAFETY_QUEUE_ENGINE: ${PR_SAFETY_QUEUE_ENGINE:-postgres}' docker-compose.yml
grep -Fq '[[ "${PR_SAFETY_QUEUE_ENGINE:-postgres}" == kanban ]]' scripts/hermes-compose-producer.sh
grep -Fq '"$ROOT/scripts/compose.sh" up -d --build' scripts/fleet.sh
grep -Fq 'export HERMES_KANBAN_BRIDGE_KEY_FILE="${HERMES_KANBAN_BRIDGE_KEY_FILE:-$SHARED_RUNTIME/hermes-bridge-secrets/key.json}"' scripts/fleet.sh
grep -Fq 'export HERMES_KANBAN_BRIDGE_CONTROLLER_KEY_FILE="${HERMES_KANBAN_BRIDGE_CONTROLLER_KEY_FILE:-$SHARED_RUNTIME/secrets/hermes-kanban-bridge-key.json}"' scripts/fleet.sh
grep -Fq 'export PR_SAFETY_QUEUE_ENGINE="${PR_SAFETY_QUEUE_ENGINE:-postgres}"' scripts/fleet.sh
grep -Fq 'sudo env PR_SAFETY_QUEUE_ENGINE="$PR_SAFETY_QUEUE_ENGINE"' scripts/fleet.sh
grep -Fq 'PR_SAFETY_MERGED_PR_AUTHORS="$PR_SAFETY_MERGED_PR_AUTHORS"' scripts/fleet.sh
grep -Fq 'PR_SAFETY_ALLOWED_ORGS="$PR_SAFETY_ALLOWED_ORGS" "$ROOT/scripts/configure-hermes-role-env.sh"' scripts/fleet.sh
grep -Fq 'PR_SAFETY_QUEUE_ENGINE=${PR_SAFETY_QUEUE_ENGINE:-postgres}' scripts/configure-hermes-role-env.sh
grep -Fq '. "$HERMES_HOME/.env"' launchd/com.example.ai-pr-automation-pr-safety-producer.plist.template
grep -Fq 'file: ${HERMES_KANBAN_BRIDGE_CONTROLLER_KEY_FILE:?set HERMES_KANBAN_BRIDGE_CONTROLLER_KEY_FILE}' docker-compose.yml
! grep -Eq 'bridge-recovery-state|read_safety_engine|resume|/dev/null' scripts/fleet.sh
! grep -Fq 'bridge-recovery-state)' scripts/hermes-native.sh
! grep -Fq 'resume)' scripts/hermes-native.sh
! grep -Eq 'bridge-recovery-state|uses `/dev/null`|hermes-native\.sh resume' docs/hermes/README.md
grep -Fq 'A same-version `down`/`up`' docs/hermes/README.md
grep -Fq 'restart is supported' docs/hermes/README.md
grep -Fq 'must drain every open Kanban attempt in Postgres' docs/hermes/README.md
grep -Fq 'HERMES_KANBAN_BRIDGE_KEY_FILE=' .env.example
grep -Fq 'HERMES_KANBAN_BRIDGE_CONTROLLER_KEY_FILE=' .env.example
grep -Fq 'PR_SAFETY_ANALYSIS_ENGINE=single' .env.example
! grep -Fq 'hermes-kanban-safety-bridge' Dockerfile.hermes-controller
grep -Fq 'COPY scripts/hermes_pr_safety_result.py /app/hermes_pr_safety_result.py' Dockerfile.hermes-controller
python3 - <<'PY'
from pathlib import Path
source=Path('docker-compose.yml').read_text()
controller=source[source.index('  hermes-controller:'):source.index('\n  pr-producer-review:')]
assert 'hermes_kanban_bridge_key' in controller
fleet=Path('scripts/fleet.sh').read_text()
up=fleet[fleet.index('  up)'):fleet.index('  down)')]
down=fleet[fleet.index('  down)'):fleet.index('  status)')]
assert up.index('hermes-native.sh" producer-stop') < up.index('compose.sh" stop pr-safety-producer') < up.index('configure-hermes-role-env.sh')
assert up.count('sudo env HERMES_KANBAN_BRIDGE_KEY_FILE="$HERMES_KANBAN_BRIDGE_KEY_FILE"') == 2
copy='sudo install -m 0600 -o "$(id -u)" -g "$(id -g)"'
assert copy in up
assert up.index('hermes-native.sh" up') < up.index(copy) < up.index('hermes-native.sh" bridge-start') < up.index('hermes-api-conformance') < up.index('compose.sh" up') < up.index('hermes-native.sh" producer-start')
assert down.index('hermes-native.sh" producer-stop') < down.index('compose.sh" down') < down.index('hermes-native.sh" down')
assert 'eval ' not in fleet and 'source "$ROOT/.env"' not in fleet and '. "$ROOT/.env"' not in fleet
native=Path('scripts/hermes-native.sh').read_text()
native_up=native[native.index('  up)'):native.index('  down)')]
assert 'bridge-start' not in native_up
for name in ('pr-producer-review','pr-producer-maintain','pr-safety-producer','memory-curate-producer','hermes-api-conformance'):
    start=source.index(f'  {name}:')
    end=source.find('\n  ',start+3)
    assert 'hermes_kanban_bridge_key' not in source[start:end if end >= 0 else None], name
PY
! grep -Fq 'eval ' scripts/fleet.sh
echo 'PASS: bridge recovery and direct-Kanban producer ownership are wired'
