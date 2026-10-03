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
assert up.index('config --format json') < up.index('mirror_authority') < up.index('"$ROOT/scripts/compose.sh" up -d --build')
assert all(command not in fleet for command in ('hermes-native.sh', 'sudo', 'launchctl', 'osascript'))
assert '  review-kanban-up)' not in fleet and '  review-postgres-up)' not in fleet
cron = Path("scripts/hermes-review-cron-entrypoint.sh").read_text()
assert 'PATH=/usr/local/bin:/usr/bin:/bin' in cron
assert '/app/bin/hermes-pr-producer review' in cron
PY

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/repo/scripts" "$tmp/bin"
cp scripts/fleet.sh "$tmp/repo/scripts/fleet.sh"
printf 'repos:\n  - owner/repo\n' > "$tmp/authority.yaml"
mirror="$tmp/mirror/authority.yaml"
printf '#!/bin/sh\nexit 0\n' > "$tmp/repo/scripts/hermes-authority.py"
cat > "$tmp/repo/scripts/compose.sh" <<'SH'
#!/bin/sh
if [ "$*" = 'config --format json' ]; then
  printf '{"secrets":{"github_discovery_token":{"file":"%s"},"hermes_review_key":{"file":"%s"},"hermes_maintain_key":{"file":"%s"}}}\n' \
    "${MOCK_TOKEN_FILE:-/dev/null}" "${MOCK_KEY_FILE:-/dev/null}" "${MOCK_MAINTAIN_FILE:-/dev/null}"
else
  printf 'compose %s\n' "$*" >> "$MOCK_LOG"
fi
SH
cat > "$tmp/bin/hermes" <<'SH'
#!/bin/sh
printf 'hermes %s\n' "$*" >> "$MOCK_LOG"
SH
cat > "$tmp/bin/sudo" <<'SH'
#!/bin/sh
printf 'sudo %s\n' "$*" >> "$MOCK_LOG"; exit 1
SH
cat > "$tmp/bin/launchctl" <<'SH'
#!/bin/sh
printf 'launchctl %s\n' "$*" >> "$MOCK_LOG"; exit 1
SH
chmod +x "$tmp/repo/scripts/compose.sh" "$tmp/repo/scripts/hermes-authority.py" "$tmp/bin/hermes" "$tmp/bin/sudo" "$tmp/bin/launchctl"
reject() {
  : > "$tmp/ops.log"
  if PATH="$tmp/bin:$PATH" MOCK_LOG="$tmp/ops.log" HERMES_AUTHORITY_SOURCE_FILE="$tmp/authority.yaml" \
     HERMES_DOCKER_AUTHORITY_FILE="$mirror" MOCK_TOKEN_FILE="${MOCK_TOKEN_FILE:-/dev/null}" \
     MOCK_KEY_FILE="${MOCK_KEY_FILE:-/dev/null}" MOCK_MAINTAIN_FILE="${MOCK_MAINTAIN_FILE:-/dev/null}" \
     bash "$tmp/repo/scripts/fleet.sh" up >"$tmp/output" 2>&1; then
    echo 'FAIL: fleet up accepted invalid direct-PR secrets' >&2; exit 1
  fi
  grep -Fq 'configure owner-only' "$tmp/output"
  [[ ! -s "$tmp/ops.log" && ! -e "$mirror" ]] || { echo 'FAIL: fleet mutated before secret preflight' >&2; exit 1; }
}
reject
mkdir -m 700 "$tmp/secrets"
printf 'fake-token\n' > "$tmp/secrets/token"
printf 'fake-review-key\n' > "$tmp/secrets/review"
printf 'fake-maintain-key\n' > "$tmp/secrets/maintain"
chmod 0600 "$tmp/secrets/"*
export MOCK_TOKEN_FILE="$tmp/secrets/token" MOCK_KEY_FILE="$tmp/secrets/review" MOCK_MAINTAIN_FILE="$tmp/secrets/maintain"
for secret in token review maintain; do
  chmod 0644 "$tmp/secrets/$secret"
  reject
  chmod 0600 "$tmp/secrets/$secret"
done
chmod 0755 "$tmp/secrets"; reject; chmod 0700 "$tmp/secrets"
PATH="$tmp/bin:$PATH" MOCK_LOG="$tmp/ops.log" HERMES_AUTHORITY_SOURCE_FILE="$tmp/authority.yaml" \
  HERMES_DOCKER_AUTHORITY_FILE="$mirror" bash "$tmp/repo/scripts/fleet.sh" up
cmp "$tmp/authority.yaml" "$mirror"
[[ "$(stat -f %Lp "$mirror")" == 644 ]]
[[ "$(cat "$tmp/ops.log")" == 'compose up -d --build' ]]
: > "$tmp/ops.log"
PATH="$tmp/bin:$PATH" MOCK_LOG="$tmp/ops.log" HERMES_AUTHORITY_SOURCE_FILE="$tmp/authority.yaml" \
  HERMES_DOCKER_AUTHORITY_FILE="$mirror" MOCK_TOKEN_FILE=/dev/null MOCK_KEY_FILE=/dev/null \
  MOCK_MAINTAIN_FILE=/dev/null bash "$tmp/repo/scripts/fleet.sh" support-up
[[ "$(cat "$tmp/ops.log")" == 'compose up -d --no-build hindsight-db hindsight coderag signal ui-proxy' ]]
: > "$tmp/ops.log"
PATH="$tmp/bin:$PATH" MOCK_LOG="$tmp/ops.log" HERMES_BIN="$tmp/bin/hermes" \
  bash "$tmp/repo/scripts/fleet.sh" status > "$tmp/status"
grep -Fxq '=== Personal Hermes gateway ===' "$tmp/status"
[[ "$(cat "$tmp/ops.log")" == $'compose ps\nhermes gateway status' ]]
: > "$tmp/ops.log"
PATH="$tmp/bin:$PATH" MOCK_LOG="$tmp/ops.log" bash "$tmp/repo/scripts/fleet.sh" pause
[[ "$(cat "$tmp/ops.log")" == 'compose stop pr-producer-review pr-producer-maintain' ]]
printf '%s\n' 'PASS: direct PR secrets preflight, support-only startup, pause, and personal Hermes status'
