#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

native=scripts/hermes-native.sh
fleet=scripts/fleet.sh
producer=scripts/hermes-compose-producer.sh
wrapper=scripts/hermes-memory-curate-direct.sh
bash -n "$native" "$fleet" "$producer" "$wrapper" "$0"

python3 - <<'PY'
from pathlib import Path
native = Path("scripts/hermes-native.sh").read_text()
cron = native[native.index("hermes_memory_cron() {"):native.index("\nrequire_v2_services_unloaded()")]
for text in (
    'sudo -u "$SERVICE_USER" env HOME="$SERVICE_HOME" HERMES_HOME="$HERMES_HOME"',
    '"$LAUNCHER" -p memory-curate-v1 cron "$@"',
    "hermes_memory_cron create 'every 6h' --name \"$MEMORY_CRON_NAME\"",
    '--script "${MEMORY_CRON_SCRIPT##*/}" --no-agent --deliver local --failure-deliver local',
    "--paused --paused-reason 'Installed paused; operator activation required.'",
    'hermes_memory_cron resume "$MEMORY_CRON_NAME"',
    'memory_state_owner "$SERVICE_USER" "$(id -gn "$SERVICE_USER")"',
    'hermes_memory_cron run "$MEMORY_CRON_NAME"',
    'hermes_memory_cron pause "$MEMORY_CRON_NAME"',
    'memory_state_owner "$SUDO_UID" "$SUDO_GID"',
): assert text in cron, text
for forbidden in ("profile show", "preflight", "PROFILE_CONFIGURATOR", "config.yaml"):
    assert forbidden not in cron, forbidden
install = native[native.index("install_native() {"):native.index("\npreflight() {")]
assert '"$(dirname "$MEMORY_CRON_SCRIPT")"' in install
assert 'profiles/memory-curate-v1/scripts/memory-curate-direct.sh' in native
assert 'install -m 0555 -o root -g wheel "$ROOT/bin/hermes-memory-curate" "$MEMORY_CURATE_BIN"' in install
assert 'install -m 0500 -o "$SERVICE_USER"' in install
assert '"$ROOT/scripts/hermes-memory-curate-direct.sh" "$MEMORY_CRON_SCRIPT"' in install
assert '"$ROOT/bin/hermes-memory-curate:$MEMORY_CURATE_BIN"' in install
assert '"$ROOT/scripts/hermes-memory-curate-direct.sh:$MEMORY_CRON_SCRIPT"' in install
assert '"$SERVICE_HOME/.local/share/ai-pr-automation/curator"' in install
legacy = install[install.index("for legacy in"):install.index("local logfile")]
assert '"$SUPPORT_ROOT/hermes-memory-curate"' not in legacy
up = native[native.index("  up)"):native.index("  down)")]
down = native[native.index("  down)"):native.index("  status)")]
assert "memory-cron-start" not in install and "memory-cron-start" not in up
assert down.index("memory-cron-stop") < down.index("review-producer-stop")
wrapper = Path("scripts/hermes-memory-curate-direct.sh").read_text()
assert 'ROOT_ENV="$HOME/.hermes/.env"' in wrapper
assert '. "$ROOT_ENV"' in wrapper
assert wrapper.rstrip().endswith('exec /usr/local/libexec/ai-pr-automation/hermes-memory-curate --direct')
compose = Path("scripts/hermes-compose-producer.sh").read_text()
idle = compose[compose.index("while true; do"):compose.index('  case "$mode" in')]
assert '"$mode" == memory' in idle and '${MEMORY_CURATE_QUEUE_ENGINE:-postgres}' in idle
assert idle.index('sleep "$interval"') < idle.index("continue")
yaml = Path("docker-compose.yml").read_text()
memory = yaml[yaml.index("  memory-curate-producer:"):yaml.index("  hermes-api-conformance:")]
assert "MEMORY_CURATE_QUEUE_ENGINE: ${MEMORY_CURATE_QUEUE_ENGINE:-postgres}" in memory
env = Path(".env.example").read_text()
for text in ("MEMORY_CURATE_QUEUE_ENGINE=postgres # postgres|cron", "memory-cron-up", "memory-postgres-up"):
    assert text in env
fleet = Path("scripts/fleet.sh").read_text()
cron_up = fleet[fleet.index("  memory-cron-up)"):fleet.index("  memory-postgres-up)")]
postgres = fleet[fleet.index("  memory-postgres-up)"):fleet.index("  status)")]
assert cron_up.index("recreate_producer memory memory-curate-producer MEMORY_CURATE_QUEUE_ENGINE cron") < cron_up.index("memory-cron-start")
assert "memory-cron-stop" in cron_up
assert postgres.index("memory-cron-stop") < postgres.index("recreate_producer memory memory-curate-producer MEMORY_CURATE_QUEUE_ENGINE postgres")
PY

tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"
cat > "$tmp/bin/sudo" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$MOCK_LOG"
case " $* " in
  *" cron list --all ")
    printf '    Name:      memory-curate-direct-old\n'
    [[ ! -e "$MOCK_STATE/job" ]] || printf '    Name:      memory-curate-direct\n'
    ;;
  *" cron create "*)
    mkdir -p "$MOCK_STATE"; touch "$MOCK_STATE/job"
    printf 'create\n' >> "$MOCK_STATE/actions"
    ;;
  *" cron resume "*) printf 'resume\n' >> "$MOCK_STATE/actions" ;;
  *" cron run "*) printf 'run\n' >> "$MOCK_STATE/actions" ;;
  *" cron pause "*) printf 'pause\n' >> "$MOCK_STATE/actions" ;;
esac
SH
chmod +x "$tmp/bin/sudo"
export PATH="$tmp/bin:$PATH" MOCK_LOG="$tmp/native.log" MOCK_STATE="$tmp/native-state"
eval "$(python3 - <<'PY'
from pathlib import Path
source = Path("scripts/hermes-native.sh").read_text()
print(source[source.index("hermes_memory_cron() {"):source.index("\nrequire_v2_services_unloaded()")])
PY
)"
SERVICE_USER="$(id -un)" SERVICE_HOME="$tmp/home" HERMES_HOME="$tmp/home/.hermes"
SHARED_RUNTIME="$tmp/shared"; mkdir -p "$SHARED_RUNTIME"
MEMORY_CURATOR_STATE_DIR="$SHARED_RUNTIME/memory-curator"
LAUNCHER="$tmp/home/.local/bin/hermes" MEMORY_CRON_NAME=memory-curate-direct
MEMORY_CRON_SCRIPT="$HERMES_HOME/profiles/memory-curate-v1/scripts/memory-curate-direct.sh"
memory_cron_install
memory_cron_install
memory_cron_start
memory_cron_stop
[[ "$(grep -c '^create$' "$MOCK_STATE/actions")" == 1 ]]
[[ "$(grep -c '^resume$' "$MOCK_STATE/actions")" == 1 ]]
[[ "$(grep -c '^run$' "$MOCK_STATE/actions")" == 1 ]]
[[ "$(grep -c '^pause$' "$MOCK_STATE/actions")" == 1 ]]
grep -Fq -- '-p memory-curate-v1 cron list --all' "$MOCK_LOG"
grep -Fq -- 'cron create every 6h --name memory-curate-direct --script memory-curate-direct.sh --no-agent --deliver local --failure-deliver local --paused --paused-reason' "$MOCK_LOG"
rm "$MOCK_STATE/job"; : > "$MOCK_STATE/actions"
memory_cron_stop
[[ ! -s "$MOCK_STATE/actions" ]]

mkdir -p "$tmp/repo/scripts" "$tmp/repo/policy"
cp "$fleet" "$tmp/repo/scripts/fleet.sh"
cp policy/pr-safety-policy-v1.md "$tmp/repo/policy/"
cat > "$tmp/repo/scripts/compose.sh" <<'SH'
#!/usr/bin/env bash
printf 'compose engine=%s %s\n' "${MEMORY_CURATE_QUEUE_ENGINE:-unset}" "$*" >> "$MOCK_LOG"
[[ "${MOCK_FAIL_COMPOSE:-false}" != true || "$*" != up\ * ]] || exit 1
[[ "$*" != 'ps -q memory-curate-producer' ]] || printf 'memory-container\n'
SH
cat > "$tmp/bin/docker" <<'SH'
#!/usr/bin/env bash
printf 'docker %s\n' "$*" >> "$MOCK_LOG"
if [[ "$*" == *State.Running* ]]; then printf 'true\n'; else printf 'MEMORY_CURATE_QUEUE_ENGINE=%s\n' "$MOCK_INSPECT_ENGINE"; fi
SH
cat > "$tmp/bin/sudo" <<'SH'
#!/usr/bin/env bash
printf 'sudo %s\n' "$*" >> "$MOCK_LOG"
[[ "${MOCK_FAIL_START:-false}" != true || "$*" != *memory-cron-start* ]]
SH
chmod +x "$tmp/repo/scripts/compose.sh" "$tmp/bin/docker" "$tmp/bin/sudo"
export MOCK_LOG="$tmp/fleet.log"
: > "$MOCK_LOG"
MOCK_INSPECT_ENGINE=cron bash "$tmp/repo/scripts/fleet.sh" memory-cron-up
python3 - "$MOCK_LOG" <<'PY'
from pathlib import Path
import sys
lines = Path(sys.argv[1]).read_text().splitlines()
assert lines[0].startswith("compose engine=cron up -d --no-deps --force-recreate memory-curate-producer")
assert lines[1] == "compose engine=unset ps -q memory-curate-producer"
assert "State.Running" in lines[2] and "Config.Env" in lines[3]
assert lines[4].endswith("hermes-native.sh memory-cron-start")
PY
: > "$MOCK_LOG"
if MOCK_INSPECT_ENGINE=cron MOCK_FAIL_START=true bash "$tmp/repo/scripts/fleet.sh" memory-cron-up; then
  echo 'memory-cron-up survived native start failure' >&2; exit 1
fi
grep -Fq 'hermes-native.sh memory-cron-stop' "$MOCK_LOG"
: > "$MOCK_LOG"
if MOCK_FAIL_COMPOSE=true bash "$tmp/repo/scripts/fleet.sh" memory-cron-up; then
  echo 'memory-cron-up survived Compose failure' >&2; exit 1
fi
grep -Fq 'hermes-native.sh memory-cron-stop' "$MOCK_LOG"
! grep -Fq 'hermes-native.sh memory-cron-start' "$MOCK_LOG"
: > "$MOCK_LOG"
MOCK_INSPECT_ENGINE=postgres bash "$tmp/repo/scripts/fleet.sh" memory-postgres-up
[[ "$(head -1 "$MOCK_LOG")" == *'hermes-native.sh memory-cron-stop' ]]
grep -Fq 'compose engine=postgres up -d --no-deps --force-recreate memory-curate-producer' "$MOCK_LOG"

cat > "$tmp/bin/sleep" <<'SH'
#!/usr/bin/env bash
exit 23
SH
cat > "$tmp/bin/psql" <<'SH'
#!/usr/bin/env bash
touch "$PSQL_CALLED"
SH
chmod +x "$tmp/bin/sleep" "$tmp/bin/psql"
set +e
PSQL_CALLED="$tmp/psql-called" MEMORY_CURATE_QUEUE_ENGINE=cron PRODUCER_INTERVAL_SECONDS=0 \
  bash "$producer" memory >/dev/null 2>&1
rc=$?
set -e
[[ "$rc" == 23 && ! -e "$tmp/psql-called" ]]

echo 'PASS: memory curate cron is installed paused, switched atomically, and leaves Compose idle'
