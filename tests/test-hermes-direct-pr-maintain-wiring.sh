#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

native=scripts/hermes-native.sh
fleet=scripts/fleet.sh
template=launchd/com.example.ai-pr-automation-producer.plist.template
compose_producer=scripts/hermes-compose-producer.sh
bash -n "$native" "$fleet" "$compose_producer" "$0"

python3 - <<'PY'
from pathlib import Path
native = Path("scripts/hermes-native.sh").read_text()
for text in (
    'MAINTAIN_PRODUCER_PLIST="/Library/LaunchDaemons/com.example.ai-pr-automation-producer-maintain.plist"',
    'MAINTAIN_PRODUCER_STAGED_PLIST="$CONFIG_ROOT/com.example.ai-pr-automation-producer-maintain.plist"',
    'MAINTAIN_PRODUCER_LABEL="com.example.ai-pr-automation-producer-maintain"',
    'MAINTAIN_MODE_MARKER="$CONFIG_ROOT/pr-maintain-queue-engine"',
    'MAINTAIN_WORK_ROOT="${HERMES_PR_MAINTAIN_KANBAN_WORK_ROOT:-$SERVICE_HOME/.local/share/ai-pr-automation/pr-maintain}"',
): assert text in native
install = native[native.index("install_native() {"):native.index("\npreflight() {")]
assert '"$REVIEW_WORK_ROOT" "$MAINTAIN_WORK_ROOT"' in install
assert 'if ! service_loaded "$MAINTAIN_PRODUCER_LABEL"' in install
assert install.index('if ! service_loaded "$MAINTAIN_PRODUCER_LABEL"') < install.index('rm -f "$MAINTAIN_PRODUCER_PLIST"')
legacy = install[install.index("for legacy in"):install.index("local logfile")]
assert 'com.example.ai-pr-automation-producer-maintain ' not in legacy
assert 'producer-maintain.out producer-maintain.err' in install
assert '"$MAINTAIN_PRODUCER_STAGED_PLIST" "$SERVICE_USER"' in install
assert '"$REVIEW_PRODUCER_INTERVAL"' in install
assert '"$REVIEW_KANBAN_ENQUEUE" "$MAINTAIN_WORK_ROOT"' in install
assert 'plutil -lint "$MAINTAIN_PRODUCER_STAGED_PLIST"' in install
assert "maintain-producer-start" not in install
mode = native[native.index("  maintain-mode-set-kanban)"):native.index("  maintain-producer-start)")]
assert mode.index("printf 'kanban\\n'") < mode.index("chmod 0444") < mode.index("mv -f") < mode.index("maintain-producer-start")
assert "maintain-producer-stop" in mode
start = native[native.index("  maintain-producer-start)"):native.index("  maintain-producer-stop)")]
assert "stat -f '%u:%Lp'" in start and 'cmp -s "$MAINTAIN_MODE_MARKER"' in start
assert "preflight" not in start and "PROFILE_CONFIGURATOR" not in start and "DIRECT_PR_PREFLIGHT" not in start
assert start.index("install -m 0644") < start.index('launchctl bootstrap system "$MAINTAIN_PRODUCER_PLIST"')
stop = native[native.index("  maintain-producer-stop)"):native.index("  bridge-start)")]
assert stop.index('wait_unloaded "$MAINTAIN_PRODUCER_LABEL"') < stop.index('rm -f "$MAINTAIN_MODE_MARKER" "$MAINTAIN_PRODUCER_PLIST"')
up = native[native.index("  up)"):native.index("  down)")]
down = native[native.index("  down)"):native.index("  status)")]
status = native[native.index("  status)"):native.index("  logs)")]
assert "maintain-producer-start" not in up and "maintain-mode-set-kanban" not in up
assert 'review-producer-stop' in down and 'maintain-producer-stop' in down
assert '"$MAINTAIN_PRODUCER_LABEL"' in status
compose = Path('docker-compose.yml').read_text()
maintain = compose.split('  pr-producer-maintain:\n', 1)[1].split('\n  ui-proxy:', 1)[0]
assert 'profiles:' not in maintain and 'pr-producer-maintain-direct:' not in compose
assert 'entrypoint: ["/app/bin/hermes-maintain-cron-entrypoint.sh"]' in maintain
assert 'github_discovery_token, pr_maintain_ingress_key' in maintain
assert 'PR_MAINTAIN_QUEUE_ENGINE:' not in maintain and 'schema-migrate' not in maintain
fleet = Path('scripts/fleet.sh').read_text()
assert 'maintain-kanban-up)' not in fleet and 'maintain-postgres-up)' not in fleet
assert 'hermes-native.sh' not in fleet
PY

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
for mode in review maintain; do
  MODE="$mode" python3 - "$template" "$tmp/$mode.plist" <<'PY'
import os, pathlib, sys
source, target = map(pathlib.Path, sys.argv[1:])
mode = os.environ["MODE"]
values = {"__MODE__":mode,"__HERMES_USER__":"hermes-agent","__SERVICE_HOME__":"/Users/hermes-agent",
 "__HERMES_HOME__":"/Users/hermes-agent/.hermes","__SUPPORT_ROOT__":"/usr/local/libexec/ai-pr-automation",
 "__AUTHORITY_FILE__":"/usr/local/etc/ai-pr-automation/authority.yaml","__INTERVAL_SECONDS__":"300",
 "__LOG_ROOT__":"/usr/local/var/log/ai-pr-automation/hermes","__HERMES_BIN__":"/Users/hermes-agent/.local/bin/hermes",
 "__HERMES_PYTHON__":"/Users/hermes-agent/.hermes/hermes-agent/venv/bin/python",
 "__HERMES_PR_KANBAN_ENQUEUE__":"/usr/local/libexec/ai-pr-automation/hermes-pr-kanban-enqueue.py",
 "__HERMES_PR_KANBAN_WORK_ROOT__":f"/Users/hermes-agent/.local/share/ai-pr-automation/pr-{mode}"}
text = source.read_text()
for name, value in values.items(): text = text.replace(name, value)
assert "__" not in text
assert f"if [[ {mode} == review ]]" in text
assert "export PR_REVIEW_QUEUE_ENGINE=kanban" in text
assert "export PR_MAINTAIN_QUEUE_ENGINE=kanban" in text
target.write_text(text)
PY
  plutil -lint "$tmp/$mode.plist" >/dev/null
done

echo 'PASS: default direct PR maintain and standalone native helper wiring'
