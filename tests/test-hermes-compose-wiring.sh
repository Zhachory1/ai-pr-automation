#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/code"
printf 'repos: []\n' > "$tmp/authority.yaml"
env CODE_ROOT="$tmp/code" HERMES_AUTHORITY_FILE="$tmp/authority.yaml" HINDSIGHT_DB_PASSWORD=test \
  GITHUB_DISCOVERY_TOKEN_FILE="$tmp/token" HERMES_REVIEW_API_KEY_FILE="$tmp/review" \
  HERMES_MAINTAIN_API_KEY_FILE="$tmp/maintain" \
  FLEET_CONTROLLER_TLS_CERT_FILE=/dev/null FLEET_CONTROLLER_TLS_KEY_FILE=/dev/null \
  docker compose config --format json > "$tmp/config.json"
python3 - "$tmp/config.json" <<'PY'
import json, sys
from pathlib import Path
config = json.load(open(sys.argv[1]))
from pathlib import Path
assert '\n  requests_pgdata:\n' in Path('docker-compose.yml').read_text()
services = config['services']
assert set(services) == {'hindsight-db', 'hindsight', 'hindsight-bank-init', 'coderag', 'signal',
                         'pr-producer-review', 'pr-producer-maintain', 'ui-proxy'}
assert {'hindsight_pgdata', 'coderag_cache', 'signal_state'} == set(config['volumes'])
assert set(config['secrets']) == {'fleet_controller_tls_cert', 'fleet_controller_tls_key',
                                  'github_discovery_token', 'hermes_review_key', 'hermes_maintain_key'}
for name, path in (('github_discovery_token', 'token'),
                   ('hermes_review_key', 'review'),
                   ('hermes_maintain_key', 'maintain')):
    assert config['secrets'][name]['file'] == str(Path(sys.argv[1]).parent / path)
for name, key in (('pr-producer-review', 'hermes_review_key'),
                  ('pr-producer-maintain', 'hermes_maintain_key')):
    service = services[name]
    assert not service.get('profiles') and not service.get('depends_on')
    assert service['volumes'][0]['source'] == str(Path(sys.argv[1]).parent / 'authority.yaml')
    assert {secret['source'] for secret in service['secrets']} == {'github_discovery_token', key}
    assert service['environment']['HERMES_API_BASE_URL'] == 'http://host.docker.internal:8642'
assert services['pr-producer-maintain']['entrypoint'] == ['/app/bin/hermes-maintain-cron-entrypoint.sh']
assert set(services['ui-proxy']['depends_on']) == {'hindsight', 'coderag', 'signal'}
assert set(services['ui-proxy']['networks']) == {'default'}
PY
bash -n scripts/compose.sh scripts/fleet.sh
! grep -Eq 'validate-swarmvault|validate-fleet-controller-secrets|hermes-native\.sh|sudo' scripts/compose.sh scripts/fleet.sh
printf '%s\n' 'PASS: default Compose support services and direct PR crons only'
