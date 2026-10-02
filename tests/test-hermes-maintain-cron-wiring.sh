#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
bash -n scripts/hermes-maintain-cron-entrypoint.sh bin/hermes-pr-producer
python3 - <<'PY'
from pathlib import Path
compose = Path("docker-compose.yml").read_text()
legacy = compose.split("  pr-producer-maintain:\n", 1)[1].split("\n  pr-producer-maintain-direct:", 1)[0]
direct = compose.split("  pr-producer-maintain-direct:\n", 1)[1].split("\n  pr-safety-producer:", 1)[0]
assert 'profiles:' not in legacy and 'PR_MAINTAIN_QUEUE_ENGINE: ${PR_MAINTAIN_QUEUE_ENGINE:-postgres}' in legacy
assert 'profiles: ["direct-maintain"]' in direct
assert 'entrypoint: ["/app/bin/hermes-maintain-cron-entrypoint.sh"]' in direct
assert 'github_discovery_token, hermes_maintain_key' in direct
assert 'REQUESTS_DB_HOST' not in direct and 'schema-migrate' not in direct
image = Path("Dockerfile.hermes-pr-review-cron").read_text()
assert 'hermes-maintain-submit.py' in image and 'hermes-maintain-cron-entrypoint.sh' in image
cron = Path("scripts/hermes-maintain-cron-entrypoint.sh").read_text()
assert 'PATH=/usr/local/bin:/usr/bin:/bin' in cron
assert 'PR_MAINTAIN_QUEUE_ENGINE=api' in cron
assert '$(cat /run/secrets/github_discovery_token)' in cron
assert 'pr-producer-maintain-direct' in Path("docs/operations.md").read_text()
PY
printf '%s\n' 'PASS: opt-in maintenance cron wiring'
