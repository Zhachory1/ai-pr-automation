#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

tmp="$(mktemp -d)"
tmp="$(cd "$tmp" && pwd -P)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/install" "$tmp/service/.hermes/profiles/smoke-v1" "$tmp/service/.local/bin" "$tmp/source"
git -C "$tmp/install" init -q
git -C "$tmp/install" config user.email test@example.com
git -C "$tmp/install" config user.name test
printf 'pin\n' > "$tmp/install/pin"
git -C "$tmp/install" add pin
git -C "$tmp/install" commit -qm pin
commit="$(git -C "$tmp/install" rev-parse HEAD)"
for file in SOUL.md config.yaml distribution.yaml .no-bundled-skills; do
  printf '%s\n' "$file" > "$tmp/source/$file"
  cp "$tmp/source/$file" "$tmp/service/.hermes/profiles/smoke-v1/$file"
done
cat > "$tmp/service/.local/bin/hermes" <<EOF
#!/usr/bin/env bash
if [[ "\${1:-}" == --version ]]; then echo 'Hermes Agent v0.21.3'; exit 0; fi
[[ "\$*" == '-p smoke-v1 profile show smoke-v1' ]]
EOF
chmod +x "$tmp/service/.local/bin/hermes"
cat > "$tmp/contract.env" <<EOF
HERMES_NATIVE_VERSION=0.21.3
HERMES_NATIVE_COMMIT=$commit
HERMES_INSTALLER_SHA256=$(printf installer | shasum -a 256 | awk '{print $1}')
EOF
profile_digest="$(python3 - "$tmp/source" <<'PY'
import hashlib,pathlib,sys
root=pathlib.Path(sys.argv[1]); value=hashlib.sha256()
for name in ('.no-bundled-skills','SOUL.md','config.yaml','distribution.yaml'):
    value.update(name.encode()+b'\0'+(root/name).read_bytes()+b'\0')
print(value.hexdigest())
PY
)"
python3 - "$tmp/manifest.json" "$commit" "$tmp/service/.local/bin/hermes" "$profile_digest" <<'PY'
import hashlib,json,pathlib,sys
out,commit,launcher,profile=sys.argv[1:]
pathlib.Path(out).write_text(json.dumps({
  'version':'0.21.3','commit':commit,
  'installer_sha256':hashlib.sha256(b'installer').hexdigest(),
  'launcher_sha256':hashlib.sha256(pathlib.Path(launcher).read_bytes()).hexdigest(),
  'profile_digest':profile}))
PY
scripts/hermes-native-preflight.py --contract "$tmp/contract.env" --manifest "$tmp/manifest.json" \
  --install-dir "$tmp/install" --hermes-home "$tmp/service/.hermes" --profile-source "$tmp/source" \
  | jq -e '.status == "ready" and .profile == "smoke-v1"' >/dev/null
python3 - "$tmp/gateway.plist" "$tmp" "$(id -un)" <<'PY'
import pathlib, plistlib, sys
output, root, user = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
home=root/"service/.hermes"; install=root/"install"; snapshot=root/"snapshots"; workflow=home/"workflow-runs"
output.write_bytes(plistlib.dumps({"UserName":user,"ProgramArguments":[str(root/"gateway-wrapper")],
  "EnvironmentVariables":{"HOME":str(home.parent),"HERMES_HOME":str(home),
    "HERMES_BIN":str(home.parent/".local/bin/hermes"),
    "HERMES_COUNCIL_TOOLS_PYTHON":str(install/"venv/bin/python"),
    "HERMES_MAINTENANCE_FILE":str(root/"maintenance"),
    "HERMES_KANBAN_BUSY_TIMEOUT_MS":"120000","PR_SAFETY_SNAPSHOT_ROOT":str(snapshot),
    "PR_SAFETY_WORKFLOW_ROOT":str(workflow)},"RunAtLoad":False}))
PY
mkdir -p "$tmp/snapshots" "$tmp/service/.hermes/workflow-runs"
native_gateway=(scripts/hermes-native-preflight.py --contract "$tmp/contract.env" --manifest "$tmp/manifest.json"
  --install-dir "$tmp/install" --hermes-home "$tmp/service/.hermes" --profile-source "$tmp/source"
  --gateway-plist "$tmp/gateway.plist" --gateway-wrapper "$tmp/gateway-wrapper"
  --maintenance-file "$tmp/maintenance" --snapshot-root "$tmp/snapshots"
  --workflow-root "$tmp/service/.hermes/workflow-runs")
"${native_gateway[@]}" | jq -e '.gateway == "ready"' >/dev/null
python3 - "$tmp/gateway.plist" <<'PY'
import pathlib, plistlib, sys
path=pathlib.Path(sys.argv[1]); value=plistlib.loads(path.read_bytes())
value["EnvironmentVariables"]["PR_SAFETY_SNAPSHOT_ROOT"] += "-drift"
path.write_bytes(plistlib.dumps(value))
PY
if "${native_gateway[@]}" >/dev/null 2>&1; then
  echo 'FAIL: gateway snapshot root drift passed native preflight' >&2; exit 1
fi
if scripts/hermes-native-preflight.py --contract "$tmp/contract.env" --manifest "$tmp/manifest.json" \
  --install-dir "$tmp/install" --hermes-home "$tmp/service/.hermes" --profile-source "$tmp/source" \
  --service-user nobody >/dev/null 2>&1; then
  echo 'FAIL: non-root service-user preflight accepted' >&2; exit 1
fi
printf 'changed\n' >> "$tmp/service/.hermes/profiles/smoke-v1/SOUL.md"
if scripts/hermes-native-preflight.py --contract "$tmp/contract.env" --manifest "$tmp/manifest.json" \
  --install-dir "$tmp/install" --hermes-home "$tmp/service/.hermes" --profile-source "$tmp/source" >/dev/null 2>&1; then
  echo 'FAIL: changed installed profile passed preflight' >&2; exit 1
fi

plutil -lint launchd/com.example.ai-pr-automation-hermes.plist.template >/dev/null
plutil -lint launchd/com.example.ai-pr-automation-hermes-dashboard.plist.template >/dev/null
plutil -lint launchd/com.example.ai-pr-automation-hermes-kanban-safety-bridge.plist.template >/dev/null
grep -Fq 'mktemp /private/tmp/hermes-install.XXXXXX' scripts/hermes-native.sh
# shellcheck disable=SC2016
grep -Fq 'chmod 0444 "$installer"' scripts/hermes-native.sh
# Both install paths quiesce and inspect bridge state before any support/config write.
python3 - <<'PY'
from pathlib import Path
source=Path("scripts/hermes-native.sh").read_text()
install=source[source.index("install_native() {"):source.index("\npreflight() {")]
assert install.index("prepare_bridge_support_sync") < install.index("install -d")
assert install.index('configure-hermes-kanban-profiles.py" "$PROFILE_CONFIGURATOR"') < install.index("provision_v2_profiles")
assert install.index("provision_v2_profiles") < install.index('"$ROOT/scripts/hermes-native.sh" preflight')
assert "install) install_native ;;" in source
assert "sync-support) HERMES_SUPPORT_ONLY=true install_native ;;" in source
assert "sync-support) need_root; prepare_bridge_support_sync" not in source
sync_profiles="sync-profiles) need_root; need_user; prepare_bridge_support_sync; sync_profile; preflight ;;"
assert sync_profiles in source
prepare=source[source.index("prepare_bridge_support_sync() {"):source.index("\nservice_loaded() {")]
assert prepare.index('bridge_was_loaded=false') < prepare.index('launchctl bootout "system/$BRIDGE_LABEL"')
assert prepare.index('launchctl bootout "system/$BRIDGE_LABEL"') < prepare.index('wait_unloaded "$BRIDGE_LABEL"')
assert prepare.index('wait_unloaded "$BRIDGE_LABEL"') < prepare.index('python3 - "$BRIDGE_STATE_ROOT/workflows"')
assert prepare.index('python3 - "$BRIDGE_STATE_ROOT/workflows"') < prepare.index('launchctl bootstrap system "$BRIDGE_PLIST"')
PY
grep -Fq 'scripts/configure-hermes-api.py' scripts/hermes-native.sh
grep -Fq '/Users/hermes-agent/.hermes/hermes-agent/venv/bin/python scripts/hermes-kanban-workflow-preflight.py' docs/hermes/README.md
grep -Fq 'scripts/hermes-kanban-council-canary.py setup' docs/hermes/README.md
grep -Fq 'install -m 0555 "$ROOT/scripts/hermes-authority.py" "$SUPPORT_ROOT/hermes-authority.py"' scripts/hermes-native.sh
grep -Fq 'install -m 0555 -o root -g wheel "$ROOT/bin/hermes-council-tools" "$SUPPORT_ROOT/hermes-council-tools"' scripts/hermes-native.sh
grep -Fq '"$ROOT/bin/hermes-kanban-safety-bridge:$BRIDGE_BIN"' scripts/hermes-native.sh
grep -Fq '"$ROOT/scripts/hermes-kanban-safety-bridge-preflight.py:$BRIDGE_PREFLIGHT"' scripts/hermes-native.sh
grep -Fq '"$ROOT/scripts/hermes-kanban-workflow-preflight.py:$KANBAN_PREFLIGHT"' scripts/hermes-native.sh
grep -Fq '"$ROOT/scripts/hermes-kanban-risk-council.py:$RISK_COUNCIL"' scripts/hermes-native.sh
grep -Fq '"$ROOT/scripts/configure-hermes-kanban-profiles.py:$PROFILE_CONFIGURATOR"' scripts/hermes-native.sh
grep -Fq 'secrets.token_hex(32)' scripts/hermes-native.sh
grep -Fq 'BRIDGE_KEY_FILE="${HERMES_KANBAN_BRIDGE_KEY_FILE:-$SHARED_RUNTIME/hermes-bridge-secrets/key.json}"' scripts/hermes-native.sh
grep -Fq 'BRIDGE_KEY_PARENT="${BRIDGE_KEY_FILE%/*}"' scripts/hermes-native.sh
grep -Fq 'if [[ ! -e "$BRIDGE_KEY_FILE" ]]' scripts/hermes-native.sh
grep -Fq 'install -d -m 0750 -o root -g staff "$SNAPSHOT_ROOT" "$BRIDGE_KEY_PARENT"' scripts/hermes-native.sh
! grep -Fq '$SHARED_RUNTIME/secrets/hermes-kanban-safety-bridge-key.json' scripts/hermes-native.sh
python3 - <<'PY'
from pathlib import Path
source=Path('scripts/hermes-native.sh').read_text()
install=source[source.index('install_native() {'):source.index('\npreflight() {')]
assert install.index('"$BRIDGE_KEY_PARENT"') < install.index('if [[ ! -e "$BRIDGE_KEY_FILE" ]]')
assert install.index('if [[ ! -e "$BRIDGE_KEY_FILE" ]]') < install.index('"$BRIDGE_KEY_FILE" "$BRIDGE_CONTRACT"')
PY
grep -Fq 'nonarchived bridge workflow blocks support sync' scripts/hermes-native.sh
grep -Fq 'launchctl bootout "system/$BRIDGE_LABEL"' scripts/hermes-native.sh
mkdir -p "$tmp/fake-bin" "$tmp/guard-state/workflows"
cat > "$tmp/fake-bin/launchctl" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$TEST_LAUNCH_LOG"
case "$1" in
  print)
    [[ "$2" == "system/test-bridge" && -e "$TEST_LAUNCH_LOADED" ]] && exit 0
    [[ ",${TEST_LOADED_SERVICES:-}," == *",$2,"* ]]
    ;;
  bootout)
    [[ -e "$TEST_LAUNCH_LOADED" ]] || exit 1
    [[ "${TEST_CREATE_BRIDGE_STATE:-0}" == 1 ]] && printf '{"phase":"active"}\n' > "$TEST_BRIDGE_STATE/workflows/appeared.json"
    rm -f "$TEST_LAUNCH_LOADED"
    ;;
  bootstrap) touch "$TEST_LAUNCH_LOADED" ;;
esac
SH
chmod +x "$tmp/fake-bin/launchctl"
export TEST_LAUNCH_LOADED="$tmp/bridge-loaded" TEST_BRIDGE_STATE="$tmp/guard-state" \
  TEST_LAUNCH_LOG="$tmp/launchctl.log" TEST_CREATE_BRIDGE_STATE=1
BRIDGE_PLIST="$tmp/old-bridge.plist"; touch "$BRIDGE_PLIST" "$TEST_LAUNCH_LOADED"
PATH="$tmp/fake-bin:$PATH"
eval "$(python3 - <<'PY'
from pathlib import Path
source=Path('scripts/hermes-native.sh').read_text()
print(source[source.index('wait_unloaded() {'):source.index('\nsync_profile() {')])
PY
)"
BRIDGE_LABEL=test-bridge BRIDGE_STATE_ROOT="$TEST_BRIDGE_STATE"
if guard_error="$(prepare_bridge_support_sync 2>&1)"; then
  echo 'FAIL: workflow created before bridge unload was not detected' >&2; exit 1
fi
grep -Fq 'nonarchived bridge workflow blocks support sync: appeared' <<<"$guard_error"
[[ -e "$TEST_LAUNCH_LOADED" ]]
grep -Fq "bootstrap system $BRIDGE_PLIST" "$TEST_LAUNCH_LOG"
printf '{' > "$TEST_BRIDGE_STATE/workflows/invalid.json"
rm "$TEST_BRIDGE_STATE/workflows/appeared.json"
TEST_CREATE_BRIDGE_STATE=0
if guard_error="$(prepare_bridge_support_sync 2>&1)"; then
  echo 'FAIL: invalid bridge workflow state was accepted' >&2; exit 1
fi
grep -Fq 'invalid bridge state: invalid.json' <<<"$guard_error"
[[ -e "$TEST_LAUNCH_LOADED" ]]
restarts="$(grep -Fc "bootstrap system $BRIDGE_PLIST" "$TEST_LAUNCH_LOG")"
rm "$TEST_LAUNCH_LOADED"
if prepare_bridge_support_sync >/dev/null 2>&1; then
  echo 'FAIL: invalid state was accepted while bridge unloaded' >&2; exit 1
fi
[[ "$(grep -Fc "bootstrap system $BRIDGE_PLIST" "$TEST_LAUNCH_LOG")" == "$restarts" ]]

V2_PROFILES=(council-orchestrator-v2 council-reviewer-v2 council-security-v2 council-reliability-v2 council-architect-v2)
HERMES_HOME="$tmp/profile-home"; mkdir -p "$HERMES_HOME/profiles"
SERVICE_USER=test-user SERVICE_HOME="$tmp/service" INSTALL_DIR="$tmp/install" PROFILE_CONFIGURATOR="$tmp/configurator.py"
BRIDGE_CONTRACT="$tmp/v2.json" LABEL=test-gateway DASHBOARD_LABEL=test-dashboard BRIDGE_LABEL=test-bridge
: > "$tmp/sudo.log"
cat > "$tmp/fake-bin/sudo" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$TEST_SUDO_LOG"
SH
chmod +x "$tmp/fake-bin/sudo"
export TEST_SUDO_LOG="$tmp/sudo.log" TEST_LOADED_SERVICES=""
provision_v2_profiles
grep -Fq -- '--apply' "$TEST_SUDO_LOG"
: > "$TEST_SUDO_LOG"
for profile in "${V2_PROFILES[@]}"; do mkdir -p "$HERMES_HOME/profiles/$profile"; done
provision_v2_profiles
[[ ! -s "$TEST_SUDO_LOG" ]]
rm -rf "$HERMES_HOME/profiles"/*; mkdir "$HERMES_HOME/profiles/${V2_PROFILES[0]}"
if profile_error="$(provision_v2_profiles 2>&1)"; then
  echo 'FAIL: partial v2 profile set was accepted' >&2; exit 1
fi
grep -Fq 'partial v2 council profile set' <<<"$profile_error"
rm -rf "$HERMES_HOME/profiles"/*
TEST_LOADED_SERVICES=system/test-gateway
if profile_error="$(provision_v2_profiles 2>&1)"; then
  echo 'FAIL: missing profiles were applied while gateway loaded' >&2; exit 1
fi
grep -Fq 'fleet.sh down' <<<"$profile_error"
grep -Fq 'fleet.sh up' <<<"$profile_error"
[[ ! -s "$TEST_SUDO_LOG" ]]

grep -Fq 'bridge-start)' scripts/hermes-native.sh
grep -Fq 'sudo -u "$SERVICE_USER" env HOME="$SERVICE_HOME" HERMES_HOME="$HERMES_HOME"' scripts/hermes-native.sh
grep -Fq '"$INSTALL_DIR/venv/bin/python" -B "$KANBAN_PREFLIGHT"' scripts/hermes-native.sh
grep -Fq 'bridge-stop)' scripts/hermes-native.sh
grep -Fq 'bridge-status)' scripts/hermes-native.sh
grep -Fq 'bridge-reconcile)' scripts/hermes-native.sh
python3 - <<'PY'
from pathlib import Path
source=Path('scripts/hermes-native.sh').read_text()
command=source[source.index('  bridge-reconcile)'):source.index('  bridge-status)')]
for value in ('env -i','HOME="$SERVICE_HOME"','HERMES_HOME="$HERMES_HOME"',
              'HERMES_INSTALL_DIR="$INSTALL_DIR"','HERMES_KANBAN_BRIDGE_BIND=127.0.0.1',
              'HERMES_KANBAN_BUSY_TIMEOUT_MS=120000','HERMES_KANBAN_BRIDGE_PORT="$BRIDGE_PORT"',
              'HERMES_KANBAN_BRIDGE_HOST_HEADER="$BRIDGE_HOST_HEADER"',
              'HERMES_KANBAN_BRIDGE_STATE_ROOT="$BRIDGE_STATE_ROOT"',
              'HERMES_KANBAN_BRIDGE_KEY_FILE="$BRIDGE_KEY_FILE"',
              'HERMES_KANBAN_BRIDGE_CONTRACT="$BRIDGE_CONTRACT"',
              'HERMES_KANBAN_RISK_COUNCIL="$RISK_COUNCIL"','HERMES_NATIVE_CONTRACT="$RUNTIME_CONTRACT"',
              'PR_SAFETY_WORKFLOW_ROOT="$WORKFLOW_ROOT"','PR_SAFETY_SNAPSHOT_ROOT="$SNAPSHOT_ROOT"',
              'PR_SAFETY_POLICY_PATH="$POLICY_PATH"','PR_SAFETY_POLICY_VERSION="$POLICY_VERSION"',
              'PR_SAFETY_POLICY_DIGEST="$policy_digest"','HERMES_KANBAN_BRIDGE_BIN="$BRIDGE_BIN"',
              '"$INSTALL_DIR/venv/bin/python" -B "$BRIDGE_RECONCILE"'):
    assert value in command, value
PY
! grep -Fq '"$ROOT/scripts/hermes-native.sh" bridge-start' scripts/hermes-native.sh
grep -Fq 'if env != expected_env' scripts/hermes-kanban-safety-bridge-preflight.py
grep -Fq '<key>HERMES_KANBAN_BUSY_TIMEOUT_MS</key><string>120000</string>' launchd/com.example.ai-pr-automation-hermes-kanban-safety-bridge.plist.template
grep -Fq '<key>HERMES_KANBAN_BUSY_TIMEOUT_MS</key><string>120000</string>' launchd/com.example.ai-pr-automation-hermes.plist.template
grep -Fq '<key>PR_SAFETY_SNAPSHOT_ROOT</key><string>__SNAPSHOT_ROOT__</string>' launchd/com.example.ai-pr-automation-hermes.plist.template
grep -Fq '<key>PR_SAFETY_WORKFLOW_ROOT</key><string>__WORKFLOW_ROOT__</string>' launchd/com.example.ai-pr-automation-hermes.plist.template
grep -Fq '"HERMES_KANBAN_BUSY_TIMEOUT_MS":"120000"' scripts/hermes-kanban-safety-bridge-preflight.py
grep -Fq 'gateway launchd configuration mismatch' scripts/hermes-native-preflight.py
grep -Fq 'checked(args.snapshot_root, root_uid, 0o750, True, args.staff_gid)' scripts/hermes-kanban-safety-bridge-preflight.py
grep -Fq 'checked(args.key_file.parent, root_uid, 0o750, True, args.staff_gid)' scripts/hermes-kanban-safety-bridge-preflight.py
grep -Fq 'service_can_read(args.key_file, service)' scripts/hermes-kanban-safety-bridge-preflight.py
grep -Fq '/Users/Shared/ai-pr-automation-runtime/hermes-bridge-secrets/key.json' bin/hermes-kanban-safety-bridge
grep -Fq '<key>HERMES_KANBAN_BRIDGE_KEY_FILE</key><string>__BRIDGE_KEY_FILE__</string>' launchd/com.example.ai-pr-automation-hermes-kanban-safety-bridge.plist.template
! grep -Fq 'hermes-snapshot-reader' scripts/hermes-native.sh
grep -Fq '<key>HERMES_COUNCIL_TOOLS_PYTHON</key><string>__HERMES_INSTALL_DIR__/venv/bin/python</string>' launchd/com.example.ai-pr-automation-hermes.plist.template
grep -Fq '"__HERMES_INSTALL_DIR__":install_dir' scripts/hermes-native.sh
grep -Fq 'HERMES_API_KEYS_FILE=' scripts/hermes-native.sh
grep -Fq '/Users/Shared/ai-pr-automation-runtime/secrets/hermes-api-keys.json' scripts/hermes-native.sh
grep -Fq 'os.chown(args.keys_file, operator.pw_uid, operator.pw_gid)' scripts/configure-hermes-api.py
grep -Fq 'GITHUB_READ_TOKEN_FILE=' scripts/hermes-native.sh
grep -Fq 'os.chown(token_tmp, operator.pw_uid, operator.pw_gid)' scripts/configure-hermes-api.py
grep -Fq 'configure_github_cli(user, args.service_user, github_token)' scripts/configure-hermes-api.py
grep -Fq 'values["GH_CONFIG_DIR"] = github_config' scripts/configure-hermes-api.py
for profile in pr-review-v1 pr-maintain-v1 swe-implement-v1 doc-write-v1 memory-curate-v1 pr-safety-v1; do
  grep -A2 '^gateway:' "agent-config/hermes/profiles/$profile/config.yaml" | grep -Fq 'enabled: false'
done
for profile in pr-review-v1 pr-maintain-v1 swe-implement-v1; do
  grep -Fq 'env_passthrough: [GH_CONFIG_DIR]' "agent-config/hermes/profiles/$profile/config.yaml"
  grep -Fq 'disabled_toolsets: [delegation]' "agent-config/hermes/profiles/$profile/config.yaml"
  grep -Fq 'never delegate or start background work' "agent-config/hermes/profiles/$profile/SOUL.md"
done
grep -Fq 'skills/pr-review/SKILL.md' agent-config/hermes/profiles/pr-review-v1/distribution.yaml
grep -Fq 'Hermes Runs API override is authoritative' agent-config/hermes/profiles/pr-review-v1/SOUL.md
grep -Fq 'posted_ref` must be that exact marker' agent-config/hermes/profiles/pr-review-v1/skills/pr-review/SKILL.md
grep -Fq '`incident_candidate` is exceptional' agent-config/hermes/profiles/pr-safety-v1/SOUL.md
grep -Fq 'normal review handling is insufficient' policy/pr-safety-policy-v1.md
grep -Fq 'skills/pr-review-handler/SKILL.md' agent-config/hermes/profiles/pr-maintain-v1/distribution.yaml
grep -Fq 'full-reply-autonomy mode' agent-config/hermes/profiles/pr-maintain-v1/SOUL.md
grep -Fq 'Unattended scheduler mode' agent-config/hermes/profiles/pr-maintain-v1/skills/pr-review-handler/SKILL.md
grep -Fq 'top-level review body' agent-config/hermes/profiles/pr-maintain-v1/skills/pr-review-handler/SKILL.md
grep -Fq 'zero unresolved threads never means zero feedback' agent-config/hermes/profiles/pr-maintain-v1/SOUL.md
cmp -s agent-config/skills/pr-review-handler/SKILL.md agent-config/hermes/profiles/pr-maintain-v1/skills/pr-review-handler/SKILL.md
grep -Fq "if [[ \"\${HERMES_SUPPORT_ONLY:-false}\" != true ]]" scripts/hermes-native.sh
grep -Fq 'com.example.ai-pr-automation-dispatcher' scripts/hermes-native.sh
! grep -Fq 'install -m 0555 "$ROOT/bin/hermes-dispatcher"' scripts/hermes-native.sh
! grep -Fq 'install -m 0555 "$ROOT/bin/hermes-doc-write-runner"' scripts/hermes-native.sh
grep -Fq 'provider: anthropic' agent-config/hermes/profiles/doc-write-v1/config.yaml
grep -Fq 'cli: []' agent-config/hermes/profiles/doc-write-v1/config.yaml
grep -Fq 'DOC_WRITER_STAGE_DIR: /doc-stage' docker-compose.yml
grep -Fq 'DOC_WRITER_STAGE_HOST' docker-compose.yml
grep -Fq 'HANDOFF_ROOT:' docker-compose.yml
grep -Fq 'DOC_WRITER_STAGE_HOST=' .env.example
# LaunchDaemon log files must exist before bootstrap; hermes-agent cannot create files in root-owned
# LOG_ROOT and launchd otherwise exits EX_CONFIG before running the program.
grep -Fq 'install -m 0600 -o "$SERVICE_USER" -g staff /dev/null "$LOG_ROOT/$logfile.log"' scripts/hermes-native.sh
grep -Fq '"$ROOT/scripts/hermes-native.sh" dashboard-start' scripts/hermes-native.sh
grep -Fq 'wait_unloaded "$LABEL"' scripts/hermes-native.sh
grep -Fq 'wait_unloaded "$DASHBOARD_LABEL"' scripts/hermes-native.sh
! grep -Fq '"$ROOT/scripts/hermes-native.sh" dispatcher-start' scripts/hermes-native.sh
! grep -Fq '"$ROOT/scripts/hermes-native.sh" producer-start' scripts/hermes-native.sh
grep -Fq 'sudo "$ROOT/scripts/configure-hermes-role-env.sh"' scripts/fleet.sh
grep -Fq 'sudo "$ROOT/scripts/hermes-native.sh" sync-support' scripts/fleet.sh
grep -Fq 'HERMES_DOCKER_AUTHORITY_FILE:-/Users/Shared/zhach-ai-pr-automation/authority.yaml' scripts/fleet.sh
grep -Fq 'export HERMES_AUTHORITY_FILE="$DOCKER_AUTHORITY"' scripts/fleet.sh
grep -Fq 'cat "$AUTHORITY_SOURCE" > "$DOCKER_AUTHORITY"' scripts/fleet.sh
! grep -Fq 'mv "$temporary" "$DOCKER_AUTHORITY"' scripts/fleet.sh
grep -Fq 'sudo "$ROOT/scripts/hermes-native.sh" start' scripts/fleet.sh
grep -Fq 'export HANDOFF_ROOT=' scripts/fleet.sh
grep -Fq 'HERMES_SHARED_RUNTIME_ROOT:-/Users/Shared/ai-pr-automation-runtime' scripts/fleet.sh
grep -Fq 'MEMORY_CURATOR_STATE_DIR=$MEMORY_STATE' scripts/configure-hermes-role-env.sh
grep -Fq 'install -d -m 0770 -o "$SERVICE_USER" -g staff "$DOC_STAGE" "$HANDOFF" "$MEMORY_STATE"' scripts/configure-hermes-role-env.sh
grep -Fq 'install -d -m 0750 -o root -g staff "$SNAPSHOTS"' scripts/configure-hermes-role-env.sh
grep -Fq 'PR_SAFETY_POLICY_DIGEST=' scripts/configure-hermes-role-env.sh
mkdir -p "$tmp/runtime"
cat > "$tmp/fake-hermes" <<'SH'
#!/usr/bin/env bash
echo started > "$TEST_STARTED"
SH
chmod +x "$tmp/fake-hermes"
TEST_STARTED="$tmp/started" HERMES_HOME="$tmp/runtime" HERMES_BIN="$tmp/fake-hermes" \
  HERMES_MAINTENANCE_FILE="$tmp/maintenance" bin/hermes-native-gateway
[[ -e "$tmp/started" ]]
rm "$tmp/started"; touch "$tmp/maintenance"
TEST_STARTED="$tmp/started" HERMES_HOME="$tmp/runtime" HERMES_BIN="$tmp/fake-hermes" \
  HERMES_MAINTENANCE_FILE="$tmp/maintenance" bin/hermes-native-gateway
[[ ! -e "$tmp/started" ]]

echo 'PASS: native Hermes pin, profile, plist, and maintenance gate'
