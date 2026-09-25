#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

native=scripts/hermes-native.sh
fleet=scripts/fleet.sh
template=launchd/com.example.ai-pr-automation-producer.plist.template
compose_producer=scripts/hermes-compose-producer.sh

bash -n "$native" "$fleet" "$compose_producer" "$0"
for source in bin/hermes-pr-producer scripts/hermes-pr-kanban-enqueue.py \
  scripts/hermes_direct_pr_journal.py scripts/hermes-direct-pr-kanban-preflight.py; do
  [[ -f "$source" ]] || { echo "missing source artifact: $source" >&2; exit 1; }
done
python3 - <<'PY'
from pathlib import Path
import stat
expected = {
    "bin/hermes-pr-producer": 0o755,
    "scripts/hermes-pr-kanban-enqueue.py": 0o644,
    "scripts/hermes_direct_pr_journal.py": 0o644,
    "scripts/hermes-direct-pr-kanban-preflight.py": 0o755,
}
for name, mode in expected.items():
    assert stat.S_IMODE(Path(name).stat().st_mode) == mode, name
PY

grep -Fq 'REVIEW_PRODUCER_PLIST="/Library/LaunchDaemons/com.example.ai-pr-automation-producer-review.plist"' "$native"
grep -Fq 'REVIEW_PRODUCER_STAGED_PLIST="$CONFIG_ROOT/com.example.ai-pr-automation-producer-review.plist"' "$native"
grep -Fq 'REVIEW_PRODUCER_LABEL="com.example.ai-pr-automation-producer-review"' "$native"
grep -Fq 'REVIEW_MODE_MARKER="$CONFIG_ROOT/pr-review-queue-engine"' "$native"
grep -Fq 'REVIEW_PRODUCER_INTERVAL="${PR_PRODUCER_INTERVAL_SECONDS:-300}"' "$native"
grep -Fq 'install -d -m 700 -o "$SERVICE_USER"' "$native"
grep -Fq '"$WORKFLOW_ROOT" "$REVIEW_WORK_ROOT"' "$native"
grep -Fq 'producer-review.out producer-review.err' "$native"
for pattern in \
  'install -m 0555 -o root -g wheel "$ROOT/bin/hermes-pr-producer" "$REVIEW_PRODUCER_BIN"' \
  'install -m 0555 -o root -g wheel "$ROOT/scripts/hermes-pr-kanban-enqueue.py" "$REVIEW_KANBAN_ENQUEUE"' \
  'install -m 0444 -o root -g wheel "$ROOT/scripts/hermes_direct_pr_journal.py" "$DIRECT_PR_JOURNAL"' \
  'install -m 0555 -o root -g wheel "$ROOT/scripts/hermes-direct-pr-kanban-preflight.py" "$DIRECT_PR_PREFLIGHT"' \
  '"$ROOT/bin/hermes-pr-producer:$REVIEW_PRODUCER_BIN"' \
  '"$ROOT/scripts/hermes-pr-kanban-enqueue.py:$REVIEW_KANBAN_ENQUEUE"' \
  '"$ROOT/scripts/hermes_direct_pr_journal.py:$DIRECT_PR_JOURNAL"' \
  '"$ROOT/scripts/hermes-direct-pr-kanban-preflight.py:$DIRECT_PR_PREFLIGHT"'; do
  grep -Fq "$pattern" "$native"
done

grep -Fq 'export PR_REVIEW_QUEUE_ENGINE=kanban' "$template"
for key in HOME HERMES_HOME HERMES_AUTHORITY_FILE HERMES_AUTHORITY_BIN HERMES_BIN HERMES_PYTHON HERMES_PR_KANBAN_ENQUEUE HERMES_PR_KANBAN_WORK_ROOT PATH; do
  grep -Fq "<key>$key</key>" "$template"
done
python3 - <<'PY'
from pathlib import Path
source = Path("scripts/hermes-native.sh").read_text()
install = source[source.index("install_native() {"):source.index("\npreflight() {")]
legacy = install[install.index("local legacy"):install.index("local logfile")]
assert "producer-maintain" in legacy
assert '"$SUPPORT_ROOT/hermes-pr-producer"' not in legacy
assert 'if ! service_loaded "$REVIEW_PRODUCER_LABEL"' in install
assert install.count('rm -f "$REVIEW_PRODUCER_PLIST"') == 1
assert install.index('if ! service_loaded "$REVIEW_PRODUCER_LABEL"') < install.index('rm -f "$REVIEW_PRODUCER_PLIST"')
assert '"$REVIEW_PRODUCER_STAGED_PLIST" "$SERVICE_USER"' in install
assert '"$REVIEW_PRODUCER_PLIST" "$SERVICE_USER"' not in install
assert 'plutil -lint "$REVIEW_PRODUCER_STAGED_PLIST"' in install
assert 'review-producer-start' not in install
assert "review-mode-kanban)" not in source
mode = source[source.index("  review-mode-set-kanban)"):source.index("  review-producer-start)")]
assert mode.index("printf 'kanban\\n'") < mode.index('chmod 0444') < mode.index('mv -f') < mode.index('review-producer-start')
assert 'review-producer-stop' in mode
start = source[source.index("  review-producer-start)"):source.index("  review-producer-stop)")]
assert "stat -f '%u:%Lp'" in start and 'cmp -s "$REVIEW_MODE_MARKER"' in start
assert "preflight" not in start
assert '"$DIRECT_PR_PREFLIGHT"' not in start
assert start.index('install -m 0644') < start.index('launchctl bootstrap system "$REVIEW_PRODUCER_PLIST"')
stop = source[source.index("  review-producer-stop)"):source.index("  bridge-start)")]
assert stop.index('wait_unloaded "$REVIEW_PRODUCER_LABEL"') < stop.index('rm -f "$REVIEW_MODE_MARKER" "$REVIEW_PRODUCER_PLIST"')
up = source[source.index("  up)"):source.index("  down)")]
down = source[source.index("  down)"):source.index("  status)")]
status = source[source.index("  status)"):source.index("  logs)")]
assert "review-producer-start" not in up and "review-mode-set-kanban" not in up
assert 'hermes-native.sh" review-producer-stop' in down
assert '"$REVIEW_PRODUCER_LABEL"' in status
assert "review-mode-set-kanban|review-producer-start|review-producer-stop" in source[source.index('*) echo "usage:') :]
compose = Path("scripts/hermes-compose-producer.sh").read_text()
review = compose[compose.index("    review)"):compose.index("    maintain)")]
assert '[[ "${PR_REVIEW_QUEUE_ENGINE:-postgres}" == kanban ]]' in review
assert review.index('sleep "$interval"') < review.index("continue") < review.index("hermes-pr-producer review")
assert "    maintain) /app/hermes-pr-producer maintain || true ;;" in compose
yaml = Path("docker-compose.yml").read_text()
review_service = yaml[yaml.index("  pr-producer-review:"):yaml.index("  pr-producer-maintain:")]
maintain_service = yaml[yaml.index("  pr-producer-maintain:"):yaml.index("  pr-safety-producer:")]
assert "PR_REVIEW_QUEUE_ENGINE: ${PR_REVIEW_QUEUE_ENGINE:-postgres}" in review_service
assert "PR_REVIEW_QUEUE_ENGINE" not in maintain_service
env = Path(".env.example").read_text()
assert "PR_REVIEW_QUEUE_ENGINE=postgres # postgres|kanban" in env
assert "scripts/fleet.sh review-kanban-up" in env
assert "scripts/fleet.sh review-postgres-up" in env
fleet = Path("scripts/fleet.sh").read_text()
kanban = fleet[fleet.index("  review-kanban-up)"):fleet.index("  review-postgres-up)")]
postgres = fleet[fleet.index("  review-postgres-up)"):fleet.index("  status)")]
assert kanban.index("recreate_review_producer kanban") < kanban.index("review-mode-set-kanban")
assert "review-producer-stop" in kanban
assert postgres.index("review-producer-stop") < postgres.index("recreate_review_producer postgres")
recreate = fleet[fleet.index("recreate_review_producer() {"):fleet.index("\ncase ")]
assert recreate.index("--force-recreate pr-producer-review") < recreate.index("ps -q pr-producer-review")
assert "{{.State.Running}}" in recreate and "PR_REVIEW_QUEUE_ENGINE=$engine" in recreate
assert "review-mode-set-kanban" not in fleet[fleet.index("  up)"):fleet.index("  down)")]
PY

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
python3 - "$template" "$tmp/review.plist" <<'PY'
import pathlib, sys
source, target = map(pathlib.Path, sys.argv[1:])
text = source.read_text()
values = {"__MODE__":"review","__HERMES_USER__":"hermes-agent","__SERVICE_HOME__":"/Users/hermes-agent",
          "__HERMES_HOME__":"/Users/hermes-agent/.hermes","__SUPPORT_ROOT__":"/usr/local/libexec/ai-pr-automation",
          "__AUTHORITY_FILE__":"/usr/local/etc/ai-pr-automation/authority.yaml","__INTERVAL_SECONDS__":"300",
          "__LOG_ROOT__":"/usr/local/var/log/ai-pr-automation/hermes","__HERMES_BIN__":"/Users/hermes-agent/.local/bin/hermes",
          "__HERMES_PYTHON__":"/Users/hermes-agent/.hermes/hermes-agent/venv/bin/python",
          "__HERMES_PR_KANBAN_ENQUEUE__":"/usr/local/libexec/ai-pr-automation/hermes-pr-kanban-enqueue.py",
          "__HERMES_PR_KANBAN_WORK_ROOT__":"/Users/hermes-agent/.local/share/ai-pr-automation/pr-review"}
for name, value in values.items(): text = text.replace(name, value)
assert "__" not in text
target.write_text(text)
PY
plutil -lint "$tmp/review.plist" >/dev/null

mkdir -p "$tmp/repo/scripts" "$tmp/repo/policy" "$tmp/bin"
cp "$fleet" "$tmp/repo/scripts/fleet.sh"
cp policy/pr-safety-policy-v1.md "$tmp/repo/policy/"
cat > "$tmp/repo/scripts/compose.sh" <<'SH'
#!/usr/bin/env bash
printf 'compose engine=%s %s\n' "${PR_REVIEW_QUEUE_ENGINE:-unset}" "$*" >> "$MOCK_LOG"
if [[ "$*" == 'ps -q pr-producer-review' ]]; then printf 'review-container\n'; fi
SH
cat > "$tmp/bin/docker" <<'SH'
#!/usr/bin/env bash
printf 'docker %s\n' "$*" >> "$MOCK_LOG"
if [[ "$*" == *State.Running* ]]; then
  printf 'true\n'
else
  printf 'PATH=/usr/bin\nPR_REVIEW_QUEUE_ENGINE=%s\n' "$MOCK_INSPECT_ENGINE"
fi
SH
cat > "$tmp/bin/sudo" <<'SH'
#!/usr/bin/env bash
printf 'sudo %s\n' "$*" >> "$MOCK_LOG"
[[ "${MOCK_FAIL_MODE:-false}" == true && "$*" == *review-mode-set-kanban ]] && exit 1
exit 0
SH
chmod +x "$tmp/repo/scripts/compose.sh" "$tmp/bin/docker" "$tmp/bin/sudo"
export PATH="$tmp/bin:$PATH" MOCK_LOG="$tmp/fleet.log"

: > "$MOCK_LOG"
MOCK_INSPECT_ENGINE=kanban bash "$tmp/repo/scripts/fleet.sh" review-kanban-up
python3 - "$MOCK_LOG" <<'PY'
from pathlib import Path
import sys
lines = Path(sys.argv[1]).read_text().splitlines()
assert lines[0].startswith("compose engine=kanban up -d --no-deps --force-recreate pr-producer-review")
assert lines[1] == "compose engine=unset ps -q pr-producer-review"
assert lines[2].startswith("docker inspect --format {{.State.Running}} review-container")
assert lines[3].startswith("docker inspect --format {{range .Config.Env}}")
assert lines[4].endswith("hermes-native.sh review-mode-set-kanban")
PY

: > "$MOCK_LOG"
if MOCK_INSPECT_ENGINE=kanban MOCK_FAIL_MODE=true bash "$tmp/repo/scripts/fleet.sh" review-kanban-up; then
  echo 'review-kanban-up unexpectedly survived native start failure' >&2; exit 1
fi
[[ "$(grep -c '^compose engine=kanban up ' "$MOCK_LOG")" == 1 ]]
grep -Fq 'hermes-native.sh review-mode-set-kanban' "$MOCK_LOG"
grep -Fq 'hermes-native.sh review-producer-stop' "$MOCK_LOG"
! grep -Fq 'engine=postgres' "$MOCK_LOG"

: > "$MOCK_LOG"
MOCK_INSPECT_ENGINE=postgres bash "$tmp/repo/scripts/fleet.sh" review-postgres-up
python3 - "$MOCK_LOG" <<'PY'
from pathlib import Path
import sys
lines = Path(sys.argv[1]).read_text().splitlines()
assert lines[0].endswith("hermes-native.sh review-producer-stop")
assert lines[1].startswith("compose engine=postgres up -d --no-deps --force-recreate pr-producer-review")
assert lines[-1].startswith("docker inspect --format {{range .Config.Env}}")
PY

echo 'PASS: direct PR review wiring is staged and mode-switched atomically'
