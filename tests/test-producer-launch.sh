#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

cat > "$TMP/producer" <<'SH'
#!/bin/sh
printf '%s\t%s\t%s\t%s\n' "$1" "$PGPASSWORD" "$PR_PRODUCER_ORGS" "$REQUESTS_DB_HOST"
SH
chmod +x "$TMP/producer"
cat > "$TMP/.env" <<'EOF'
REQUESTS_DB_PASSWORD=test-password
PR_PRODUCER_ORGS=ROKT
EOF
chmod 600 "$TMP/.env"

run() {
  env -i HOME="$HOME" PATH="/usr/bin:/bin:/usr/sbin:/sbin" \
    PR_PRODUCER_ENV_FILE="$TMP/.env" PR_PRODUCER_BIN="$TMP/producer" \
    /bin/bash scripts/producer-launch.sh review
}

[[ "$(run)" == $'review\ttest-password\tROKT\tlocalhost' ]]
echo "PASS: env loaded and producer invoked"

chmod 644 "$TMP/.env"
if run >/dev/null 2>&1; then
  echo "FAIL: group-readable env accepted" >&2
  exit 1
fi
echo "PASS: group-readable env rejected"
