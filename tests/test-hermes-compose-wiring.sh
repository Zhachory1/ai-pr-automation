#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/code"
printf 'repos: []\n' > "$tmp/authority.yaml"
env CODE_ROOT="$tmp/code" HERMES_AUTHORITY_FILE="$tmp/authority.yaml" HINDSIGHT_DB_PASSWORD=test \
  GITHUB_DISCOVERY_TOKEN_FILE="$tmp/token" \
  PR_REVIEW_WORK_ROOT="$HOME/.local/share/ai-pr-automation/pr-kanban" \
  PR_MAINTAIN_INGRESS_KEY_FILE="$tmp/maintain" \
  FLEET_CONTROLLER_TLS_CERT_FILE=/dev/null FLEET_CONTROLLER_TLS_KEY_FILE=/dev/null \
  docker compose config --format json > "$tmp/config.json"
env CODE_ROOT="$tmp/code" HERMES_AUTHORITY_FILE="$tmp/authority.yaml" HINDSIGHT_DB_PASSWORD=test \
  GITHUB_DISCOVERY_TOKEN_FILE="$tmp/token" \
  PR_REVIEW_WORK_ROOT="$HOME/.local/share/ai-pr-automation/pr-kanban" \
  PR_MAINTAIN_INGRESS_KEY_FILE="$tmp/maintain" \
  FLEET_CONTROLLER_TLS_CERT_FILE=/dev/null FLEET_CONTROLLER_TLS_KEY_FILE=/dev/null \
  docker compose --profile legacy-signal config --format json > "$tmp/legacy-config.json"
env CODE_ROOT="$tmp/code" HERMES_AUTHORITY_FILE="$tmp/authority.yaml" HINDSIGHT_DB_PASSWORD=test \
  GITHUB_DISCOVERY_TOKEN_FILE="$tmp/token" PR_REVIEW_WORK_ROOT="$HOME/.local/share/ai-pr-automation/pr-kanban" \
  PR_MAINTAIN_INGRESS_KEY_FILE="$tmp/maintain" \
  FLEET_CONTROLLER_TLS_CERT_FILE=/dev/null FLEET_CONTROLLER_TLS_KEY_FILE=/dev/null \
  docker compose --profile pr-review-mvp config --format json > "$tmp/review-config.json"
python3 - "$tmp/config.json" "$tmp/legacy-config.json" "$tmp/review-config.json" <<'PY'
import json, sys
from pathlib import Path
config = json.load(open(sys.argv[1]))
legacy = json.load(open(sys.argv[2]))
review_config = json.load(open(sys.argv[3]))
from pathlib import Path
assert '\n  requests_pgdata:\n' not in Path('docker-compose.yml').read_text()
services = config['services']
assert set(services) == {'hindsight-db', 'hindsight', 'hindsight-bank-init', 'coderag',
                         'pr-producer-maintain', 'ui-proxy'}
assert set(review_config['services']) == set(services) | {'pr-producer-review'}
assert set(config['volumes']) == {'hindsight_pgdata', 'coderag_cache'}
assert set(legacy['services']) == set(services) | {'signal'}
assert legacy['services']['signal']['profiles'] == ['legacy-signal']
assert set(legacy['volumes']) == {'hindsight_pgdata', 'coderag_cache', 'signal_state'}
assert set(config['secrets']) == {'fleet_controller_tls_cert', 'fleet_controller_tls_key',
                                  'github_discovery_token', 'pr_maintain_ingress_key'}
for name, path in (('github_discovery_token', 'token'),
                   ('pr_maintain_ingress_key', 'maintain')):
    assert config['secrets'][name]['file'] == str(Path(sys.argv[1]).parent / path)
review = review_config['services']['pr-producer-review']
assert review['profiles'] == ['pr-review-mvp']
assert not review.get('depends_on')
assert {secret['source'] for secret in review['secrets']} == {'github_discovery_token'}
assert not review.get('volumes')
assert review['environment']['PR_REVIEW_WORK_ROOT'] == str(Path.home() / '.local/share/ai-pr-automation/pr-kanban')
assert 'HERMES_KANBAN_INGRESS_URL' not in review['environment']
maintain = services['pr-producer-maintain']
assert not maintain.get('profiles') and not maintain.get('depends_on')
assert maintain['volumes'][0]['source'] == str(Path(sys.argv[1]).parent / 'authority.yaml')
assert {secret['source'] for secret in maintain['secrets']} == {'github_discovery_token', 'pr_maintain_ingress_key'}
assert maintain['environment']['HERMES_KANBAN_INGRESS_URL'] == 'http://host.docker.internal:8767'
assert services['pr-producer-maintain']['entrypoint'] == ['/app/bin/hermes-maintain-cron-entrypoint.sh']
assert set(services['ui-proxy']['depends_on']) == {'hindsight', 'coderag'}
assert set(services['ui-proxy']['networks']) == {'default'}
PY
bash -n scripts/compose.sh scripts/fleet.sh
! grep -Eq 'validate-swarmvault|validate-fleet-controller-secrets|hermes-native\.sh|sudo' scripts/compose.sh scripts/fleet.sh
printf '%s\n' 'PASS: default Compose support services and direct PR crons only'
