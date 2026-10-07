#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
bash -n scripts/hermes-maintain-cron-entrypoint.sh bin/hermes-pr-producer
python3 - <<'PY'
from pathlib import Path
compose = Path("docker-compose.yml").read_text()
direct = compose.split("  pr-producer-maintain:\n", 1)[1].split("\n  ui-proxy:", 1)[0]
assert 'profiles:' not in direct and 'pr-producer-maintain-direct:' not in compose
assert 'entrypoint: ["/app/bin/hermes-maintain-cron-entrypoint.sh"]' in direct
assert 'github_discovery_token, pr_maintain_ingress_key' in direct
assert 'REQUESTS_DB_HOST' not in direct and 'schema-migrate' not in direct
image = Path("Dockerfile.hermes-pr-review-cron").read_text()
assert 'hermes-maintain-submit.py' in image and 'hermes-maintain-cron-entrypoint.sh' in image
cron = Path("scripts/hermes-maintain-cron-entrypoint.sh").read_text()
assert 'PATH=/usr/local/bin:/usr/bin:/bin' in cron
assert 'PR_MAINTAIN_QUEUE_ENGINE=bridge' in cron
assert '$(cat /run/secrets/github_discovery_token)' in cron
assert 'schedule="${PR_MAINTAIN_CRON_SCHEDULE:-2-59/5 * * * *}"' in cron
assert 'PR_MAINTAIN_CRON_SCHEDULE: ${PR_MAINTAIN_CRON_SCHEDULE:-2-59/5 * * * *}' in direct
sample = Path('.env.example').read_text()
assert 'PR_MAINTAIN_CRON_SCHEDULE="2-59/5 * * * *"' in sample
assert 'PR_MAINTAIN_INGRESS_KEY_FILE=/Users/YOU/.hermes/secrets/pr-maintain-ingress-key' in sample
PY
printf '%s\n' 'PASS: default maintenance cron wiring'
