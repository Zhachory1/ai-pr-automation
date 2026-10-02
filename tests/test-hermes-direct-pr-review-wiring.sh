#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

bash -n scripts/fleet.sh scripts/hermes-review-cron-entrypoint.sh bin/hermes-pr-producer
python3 - <<'PY'
from pathlib import Path
compose = Path("docker-compose.yml").read_text()
review = compose.split("  pr-producer-review:\n", 1)[1].split("\n  pr-producer-maintain:", 1)[0]
assert 'profiles:' not in review
assert 'Dockerfile.hermes-pr-review-cron' in review
assert 'github_discovery_token, hermes_review_key' in review
assert 'REQUESTS_DB_HOST' not in review and 'schema-migrate' not in review
assert 'pr-producer-review-direct:' not in compose
fleet = Path("scripts/fleet.sh").read_text()
up = fleet.split('  up)\n', 1)[1].split('  down)\n', 1)[0]
assert up.index('config --format json') < up.index('review-producer-stop') < up.index('"$ROOT/scripts/compose.sh" up -d --build')
assert '  review-kanban-up)' not in fleet and '  review-postgres-up)' not in fleet
cron = Path("scripts/hermes-review-cron-entrypoint.sh").read_text()
assert 'PATH=/usr/local/bin:/usr/bin:/bin' in cron
assert '/app/bin/hermes-pr-producer review' in cron
PY

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/repo/scripts" "$tmp/bin"
cp scripts/fleet.sh "$tmp/repo/scripts/fleet.sh"
printf 'repos:\n  - owner/repo\n' > "$tmp/authority.yaml"
printf '#!/bin/sh\nexit 0\n' > "$tmp/repo/scripts/hermes-authority.py"
cat > "$tmp/repo/scripts/compose.sh" <<'SH'
#!/bin/sh
if [ "$*" = 'config --format json' ]; then
  printf '{"secrets":{"github_discovery_token":{"file":"%s"},"hermes_review_key":{"file":"%s"}}}\n' \
    "${MOCK_TOKEN_FILE:-/dev/null}" "${MOCK_KEY_FILE:-/dev/null}"
else
  printf 'compose %s\n' "$*" >> "$MOCK_LOG"
fi
SH
cat > "$tmp/bin/sudo" <<'SH'
#!/bin/sh
printf 'sudo %s\n' "$*" >> "$MOCK_LOG"
SH
chmod +x "$tmp/repo/scripts/compose.sh" "$tmp/repo/scripts/hermes-authority.py" "$tmp/bin/sudo"
reject() {
  : > "$tmp/ops.log"
  if PATH="$tmp/bin:$PATH" MOCK_LOG="$tmp/ops.log" HERMES_AUTHORITY_SOURCE_FILE="$tmp/authority.yaml" \
     PR_SAFETY_POLICY_DIGEST=x MOCK_TOKEN_FILE="${MOCK_TOKEN_FILE:-/dev/null}" \
     MOCK_KEY_FILE="${MOCK_KEY_FILE:-/dev/null}" bash "$tmp/repo/scripts/fleet.sh" up >"$tmp/output" 2>&1; then
    echo 'FAIL: fleet up accepted invalid review secrets' >&2; exit 1
  fi
  grep -q 'configure owner-only' "$tmp/output"
  [[ ! -s "$tmp/ops.log" ]] || { echo 'FAIL: fleet mutated services before review-secret preflight' >&2; exit 1; }
}
reject
mkdir -m 700 "$tmp/secrets"
printf 'fake-token\n' > "$tmp/secrets/token"; chmod 0644 "$tmp/secrets/token"
printf 'fake-key\n' > "$tmp/secrets/key"; chmod 0600 "$tmp/secrets/key"
MOCK_TOKEN_FILE="$tmp/secrets/token" MOCK_KEY_FILE="$tmp/secrets/key" reject
chmod 0600 "$tmp/secrets/token"; chmod 0755 "$tmp/secrets"
MOCK_TOKEN_FILE="$tmp/secrets/token" MOCK_KEY_FILE="$tmp/secrets/key" reject
printf '%s\n' 'PASS: Compose review cron replaces legacy producer wiring'
