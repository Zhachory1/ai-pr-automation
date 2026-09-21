#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"
cat > "$tmp/bin/pg_isready" <<'SH'
#!/usr/bin/env bash
exit 1
SH
cat > "$tmp/bin/launchctl" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$TEST_STATE/launchctl.log"
SH
cat > "$tmp/bin/osascript" <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod +x "$tmp/bin/"*
set +e
PATH="$tmp/bin:$PATH" TEST_STATE="$tmp" HERMES_DB_WATCHDOG_INTERVAL=0.01 \
  HERMES_MAINTENANCE_FILE="$tmp/maintenance" \
  HERMES_GATEWAY_LABEL=com.test.gateway HERMES_DISPATCHER_LABEL=com.test.dispatcher \
  bin/hermes-postgres-watchdog >/dev/null 2>&1
rc=$?
set -e
[[ "$rc" == 1 ]] || { echo "FAIL: watchdog rc=$rc" >&2; exit 1; }
[[ -f "$tmp/maintenance" ]] || { echo 'FAIL: maintenance file absent' >&2; exit 1; }
grep -qx 'bootout system/com.test.dispatcher' "$tmp/launchctl.log" || { echo 'FAIL: dispatcher not stopped' >&2; exit 1; }
grep -qx 'bootout system/com.test.gateway' "$tmp/launchctl.log" || { echo 'FAIL: gateway not stopped' >&2; exit 1; }
echo 'PASS: watchdog fences claims and stops dispatcher + gateway after two DB failures'
