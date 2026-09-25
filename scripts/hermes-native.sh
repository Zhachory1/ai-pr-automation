#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
. "$ROOT/agent-config/hermes/native.env"
SERVICE_USER="${HERMES_SERVICE_USER:-hermes-agent}"
SERVICE_HOME="${HERMES_SERVICE_HOME:-/Users/$SERVICE_USER}"
HERMES_HOME="${HERMES_NATIVE_HOME:-$SERVICE_HOME/.hermes}"
INSTALL_DIR="${HERMES_NATIVE_INSTALL_DIR:-$HERMES_HOME/hermes-agent}"
LAUNCHER="$SERVICE_HOME/.local/bin/hermes"
SUPPORT_ROOT="${HERMES_NATIVE_SUPPORT_ROOT:-/usr/local/libexec/ai-pr-automation}"
CONFIG_ROOT="${HERMES_NATIVE_CONFIG_ROOT:-/usr/local/etc/ai-pr-automation}"
LOG_ROOT="${HERMES_NATIVE_LOG_ROOT:-/usr/local/var/log/ai-pr-automation/hermes}"
PROFILE_ROOT="$CONFIG_ROOT/hermes-profiles"
MANIFEST="$CONFIG_ROOT/hermes-native.json"
MAINTENANCE_FILE="$CONFIG_ROOT/hermes-maintenance"
WRAPPER="$SUPPORT_ROOT/hermes-native-gateway"
PLIST="/Library/LaunchDaemons/com.example.ai-pr-automation-hermes.plist"
LABEL="com.example.ai-pr-automation-hermes"
DASHBOARD_PLIST="/Library/LaunchDaemons/com.example.ai-pr-automation-hermes-dashboard.plist"
DASHBOARD_LABEL="com.example.ai-pr-automation-hermes-dashboard"
SAFETY_PRODUCER_PLIST="/Library/LaunchDaemons/com.example.ai-pr-automation-producer-pr-safety.plist"
SAFETY_PRODUCER_LABEL="com.example.ai-pr-automation-producer-pr-safety"
SAFETY_PRODUCER_BIN="$SUPPORT_ROOT/hermes-pr-safety-producer"
BRIDGE_PLIST="/Library/LaunchDaemons/com.example.ai-pr-automation-hermes-kanban-safety-bridge.plist"
BRIDGE_LABEL="com.example.ai-pr-automation-hermes-kanban-safety-bridge"
BRIDGE_BIN="$SUPPORT_ROOT/hermes-kanban-safety-bridge"
BRIDGE_RECONCILE="$SUPPORT_ROOT/hermes-kanban-safety-bridge-reconcile.py"
BRIDGE_PREFLIGHT="$SUPPORT_ROOT/hermes-kanban-safety-bridge-preflight.py"
KANBAN_PREFLIGHT="$SUPPORT_ROOT/hermes-kanban-workflow-preflight.py"
RISK_COUNCIL="$SUPPORT_ROOT/hermes-kanban-risk-council.py"
SAFETY_RESULT="$SUPPORT_ROOT/hermes_pr_safety_result.py"
KANBAN_ENQUEUE="$SUPPORT_ROOT/hermes-pr-safety-kanban-enqueue.py"
PROFILE_CONFIGURATOR="$SUPPORT_ROOT/configure-hermes-kanban-profiles.py"
BRIDGE_CONTRACT="$SUPPORT_ROOT/pr-risk-council-kanban-v2.json"
RUNTIME_CONTRACT="$SUPPORT_ROOT/hermes-native.env"
V2_PROFILES=(council-orchestrator-v2 council-reviewer-v2 council-security-v2 council-reliability-v2 council-architect-v2)
BRIDGE_STATE_ROOT="${HERMES_KANBAN_BRIDGE_STATE_ROOT:-$SERVICE_HOME/.local/state/ai-pr-automation/hermes-kanban-safety-bridge}"
WORKFLOW_ROOT="${PR_SAFETY_WORKFLOW_ROOT:-$HERMES_HOME/workflow-runs}"
BRIDGE_PORT="${HERMES_KANBAN_BRIDGE_PORT:-8766}"
BRIDGE_HOST_HEADER="${HERMES_KANBAN_BRIDGE_HOST_HEADER:-hermes-council.localhost:$BRIDGE_PORT}"
SHARED_RUNTIME="${HERMES_SHARED_RUNTIME_ROOT:-/Users/Shared/ai-pr-automation-runtime}"
SNAPSHOT_ROOT="${PR_SAFETY_SNAPSHOT_ROOT:-$SHARED_RUNTIME/safety-snapshots}"
POLICY_PATH="${PR_SAFETY_POLICY_PATH:-$CONFIG_ROOT/pr-safety-policy-v1.md}"
POLICY_VERSION="${PR_SAFETY_POLICY_VERSION:-v1}"
AUTHORITY_FILE="${HERMES_AUTHORITY_FILE:-$CONFIG_ROOT/authority.yaml}"
SAFETY_PRODUCER_INTERVAL="${PR_SAFETY_PRODUCER_INTERVAL_SECONDS:-60}"
BRIDGE_KEY_FILE="${HERMES_KANBAN_BRIDGE_KEY_FILE:-$SHARED_RUNTIME/hermes-bridge-secrets/key.json}"
BRIDGE_KEY_PARENT="${BRIDGE_KEY_FILE%/*}"
HERMES_API_KEYS_FILE="${HERMES_API_KEYS_FILE:-/Users/Shared/ai-pr-automation-runtime/secrets/hermes-api-keys.json}"
GITHUB_READ_TOKEN_FILE="${GITHUB_READ_TOKEN_FILE:-/Users/Shared/ai-pr-automation-runtime/secrets/github-read-token}"

need_root() { [[ "$EUID" == 0 ]] || { echo "run as root" >&2; exit 2; }; }
need_user() { id "$SERVICE_USER" >/dev/null 2>&1 || { echo "create $SERVICE_USER before install" >&2; exit 2; }; }
repair_runtime_venv_ownership() {
  local venv="$INSTALL_DIR/venv" path
  [[ "$SERVICE_HOME" == /* && "$(cd "$SERVICE_HOME" && pwd -P)" == "$SERVICE_HOME" ]] \
    || { echo "refusing noncanonical Hermes service home" >&2; exit 2; }
  [[ "$HERMES_HOME" == "$SERVICE_HOME/.hermes" && "$INSTALL_DIR" == "$HERMES_HOME/hermes-agent" ]] \
    || { echo "refusing noncanonical Hermes install layout" >&2; exit 2; }
  for path in "$SERVICE_HOME" "$HERMES_HOME" "$INSTALL_DIR" "$venv"; do
    [[ ! -L "$path" ]] || { echo "refusing symlinked Hermes path: $path" >&2; exit 2; }
  done
  [[ -e "$venv" ]] || return 0
  [[ -d "$venv" ]] || { echo "Hermes venv is not a directory: $venv" >&2; exit 2; }
  chown -RhP "$SERVICE_USER:$(id -gn "$SERVICE_USER")" "$venv"
}
wait_unloaded() {
  local label="$1" i
  for i in $(seq 1 50); do
    launchctl print "system/$label" >/dev/null 2>&1 || return 0
    sleep .2
  done
  echo "$label did not unload within 10 seconds" >&2; exit 2
}

prepare_bridge_support_sync() {
  local bridge_was_loaded=false producer_was_loaded=false scan_error
  launchctl print "system/$BRIDGE_LABEL" >/dev/null 2>&1 && bridge_was_loaded=true
  launchctl print "system/$SAFETY_PRODUCER_LABEL" >/dev/null 2>&1 && producer_was_loaded=true
  launchctl bootout "system/$SAFETY_PRODUCER_LABEL" 2>/dev/null || true
  launchctl bootout "system/$BRIDGE_LABEL" 2>/dev/null || true
  wait_unloaded "$SAFETY_PRODUCER_LABEL"
  wait_unloaded "$BRIDGE_LABEL"
  if ! scan_error="$(python3 - "$BRIDGE_STATE_ROOT/workflows" 2>&1 <<'PY'
import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
for path in root.glob("*.json") if root.is_dir() else ():
    try: phase = json.loads(path.read_text()).get("phase")
    except (OSError, json.JSONDecodeError): raise SystemExit(f"invalid bridge state: {path.name}")
    if phase != "archived": raise SystemExit(f"nonarchived bridge workflow blocks support sync: {path.stem}")
PY
)"; then
    if [[ "$bridge_was_loaded" == true ]]; then
      launchctl bootstrap system "$BRIDGE_PLIST" >/dev/null 2>&1 \
        || echo "failed to restart previous bridge plist: $BRIDGE_PLIST" >&2
    fi
    if [[ "$producer_was_loaded" == true ]]; then
      launchctl bootstrap system "$SAFETY_PRODUCER_PLIST" >/dev/null 2>&1 \
        || echo "failed to restart previous safety producer plist: $SAFETY_PRODUCER_PLIST" >&2
    fi
    echo "$scan_error" >&2
    return 1
  fi
}

service_loaded() {
  launchctl print "system/$1" >/dev/null 2>&1
}

require_v2_services_unloaded() {
  local label
  for label in "$LABEL" "$DASHBOARD_LABEL" "$BRIDGE_LABEL" "$SAFETY_PRODUCER_LABEL"; do
    if service_loaded "$label"; then
      echo "v2 council profiles missing while Hermes services are loaded; run scripts/fleet.sh down, rerun install/sync-support, then scripts/fleet.sh up" >&2
      return 1
    fi
  done
}

provision_v2_profiles() {
  local count=0 name
  for name in "${V2_PROFILES[@]}"; do
    [[ ! -e "$HERMES_HOME/profiles/$name" && ! -L "$HERMES_HOME/profiles/$name" ]] || count=$((count + 1))
  done
  if [[ "$count" == 0 ]]; then
    require_v2_services_unloaded || return
    sudo -u "$SERVICE_USER" env HOME="$SERVICE_HOME" HERMES_HOME="$HERMES_HOME" \
      "$INSTALL_DIR/venv/bin/python" -B "$PROFILE_CONFIGURATOR" --hermes-home "$HERMES_HOME" \
      --service-user "$SERVICE_USER" --contract "$BRIDGE_CONTRACT" --apply
  elif [[ "$count" != "${#V2_PROFILES[@]}" ]]; then
    echo "partial v2 council profile set; restore a fully stopped fleet before install/sync-support" >&2
    return 1
  fi
}

sync_profile() {
  local source name target canonical file relative mode
  install -d -m 700 -o "$SERVICE_USER" "$HERMES_HOME/profiles"
  for source in "$ROOT"/agent-config/hermes/profiles/*; do
    [[ -d "$source" ]] || continue
    name="${source##*/}"; target="$HERMES_HOME/profiles/$name"; canonical="$PROFILE_ROOT/$name"
    install -d -m 700 -o "$SERVICE_USER" "$target"
    install -d -m 755 "$canonical"
    while IFS= read -r file; do
      relative="${file#"$source"/}"; mode=0444
      [[ -x "$file" ]] && mode=0555
      install -d -m 755 "$(dirname "$target/$relative")" "$(dirname "$canonical/$relative")"
      install -m "$mode" "$file" "$target/$relative"
      install -m "$mode" "$file" "$canonical/$relative"
    done < <(find "$source" -type f -print | sort)
    chown "$SERVICE_USER" "$target"
  done
  chown -R root:wheel "$PROFILE_ROOT"
}

install_native() {
  need_root; need_user
  prepare_bridge_support_sync
  install -d -m 755 "$SUPPORT_ROOT" "$CONFIG_ROOT" "$LOG_ROOT" "$SHARED_RUNTIME" "$SHARED_RUNTIME/secrets"
  install -d -m 700 -o "$SERVICE_USER" "$SERVICE_HOME" "$HERMES_HOME" "$BRIDGE_STATE_ROOT" \
    "$BRIDGE_STATE_ROOT/workflows" "$WORKFLOW_ROOT"
  install -d -m 0750 -o root -g staff "$SNAPSHOT_ROOT"
  install -d -m 0750 -o "$SERVICE_USER" -g staff "$SNAPSHOT_ROOT/direct-kanban"
  install -d -m 0750 -o root -g staff "$BRIDGE_KEY_PARENT"
  install -m 0444 -o root -g wheel "$ROOT/policy/pr-safety-policy-v1.md" "$POLICY_PATH"
  # Retire old host queue lifecycle. Compose owns all producers except direct-Kanban PR safety.
  local legacy
  for legacy in com.example.ai-pr-automation-watchdog com.example.ai-pr-automation-dispatcher \
    com.example.ai-pr-automation-producer-review com.example.ai-pr-automation-producer-maintain \
    com.example.ai-pr-automation-memory-curate-producer; do
    launchctl bootout "system/$legacy" 2>/dev/null || true
  done
  rm -f /Library/LaunchDaemons/com.example.ai-pr-automation-watchdog.plist \
    /Library/LaunchDaemons/com.example.ai-pr-automation-dispatcher.plist \
    /Library/LaunchDaemons/com.example.ai-pr-automation-producer-{review,maintain}.plist \
    /Library/LaunchDaemons/com.example.ai-pr-automation-memory-curate-producer.plist \
    "$SUPPORT_ROOT/hermes-postgres-watchdog" "$SUPPORT_ROOT/hermes-dispatcher" \
    "$SUPPORT_ROOT/hermes-queue-runner" "$SUPPORT_ROOT/hermes-pr-producer" \
    "$SUPPORT_ROOT/hermes-pr-safety-runner" "$SUPPORT_ROOT/hermes-memory-curate" "$SUPPORT_ROOT/hermes-doc-write-runner" \
    "$LOG_ROOT/watchdog.out.log" "$LOG_ROOT/watchdog.err.log"
  install -d -m 700 -o "$SERVICE_USER" "$SERVICE_HOME/.local/share/ai-pr-automation/doc-writer"
  local logfile
  for logfile in gateway.out gateway.err dashboard.out dashboard.err bridge.out bridge.err producer-pr-safety.out producer-pr-safety.err; do
    install -m 0600 -o "$SERVICE_USER" -g staff /dev/null "$LOG_ROOT/$logfile.log"
  done
  local installer=""
  if [[ "${HERMES_SUPPORT_ONLY:-false}" != true ]]; then
    installer="$(mktemp /private/tmp/hermes-install.XXXXXX)"
    trap 'rm -f "$installer"' RETURN
    curl -fsSL "$HERMES_INSTALLER_URL" -o "$installer"
    chmod 0444 "$installer"
    [[ "$(shasum -a 256 "$installer" | awk '{print $1}')" == "$HERMES_INSTALLER_SHA256" ]] \
      || { echo "Hermes installer digest mismatch" >&2; exit 2; }
    repair_runtime_venv_ownership
    sudo -u "$SERVICE_USER" env HOME="$SERVICE_HOME" HERMES_HOME="$HERMES_HOME" \
      bash "$installer" --commit "$HERMES_NATIVE_COMMIT" --force-commit --skip-setup \
        --non-interactive --no-skills --dir "$INSTALL_DIR" --hermes-home "$HERMES_HOME"
  fi
  sync_profile
  python3 "$ROOT/scripts/configure-hermes-api.py" --hermes-home "$HERMES_HOME" \
    --service-user "$SERVICE_USER" --launcher "$LAUNCHER" --keys-file "$HERMES_API_KEYS_FILE" \
    --github-token-file "$GITHUB_READ_TOKEN_FILE" --repo-root "$ROOT"
  install -m 0555 "$ROOT/bin/hermes-native-gateway" "$WRAPPER"
  install -m 0555 "$ROOT/scripts/hermes-authority.py" "$SUPPORT_ROOT/hermes-authority.py"
  [[ ! -x "$ROOT/bin/hermes-memory-recall-shim" ]] || install -m 0555 "$ROOT/bin/hermes-memory-recall-shim" "$SUPPORT_ROOT/hermes-memory-recall-shim"
  install -m 0555 -o root -g wheel "$ROOT/bin/hermes-council-tools" "$SUPPORT_ROOT/hermes-council-tools"
  install -m 0555 -o root -g wheel "$ROOT/bin/hermes-kanban-safety-bridge" "$BRIDGE_BIN"
  install -m 0555 -o root -g wheel "$ROOT/scripts/hermes-kanban-safety-bridge-reconcile.py" "$BRIDGE_RECONCILE"
  install -m 0555 -o root -g wheel "$ROOT/scripts/hermes-kanban-safety-bridge-preflight.py" "$BRIDGE_PREFLIGHT"
  install -m 0555 -o root -g wheel "$ROOT/scripts/hermes-kanban-workflow-preflight.py" "$KANBAN_PREFLIGHT"
  install -m 0555 -o root -g wheel "$ROOT/scripts/hermes-kanban-risk-council.py" "$RISK_COUNCIL"
  install -m 0444 -o root -g wheel "$ROOT/scripts/hermes_pr_safety_result.py" "$SAFETY_RESULT"
  install -m 0555 -o root -g wheel "$ROOT/scripts/hermes-pr-safety-kanban-enqueue.py" "$KANBAN_ENQUEUE"
  install -m 0555 -o root -g wheel "$ROOT/bin/hermes-pr-safety-producer" "$SAFETY_PRODUCER_BIN"
  install -m 0555 -o root -g wheel "$ROOT/scripts/configure-hermes-kanban-profiles.py" "$PROFILE_CONFIGURATOR"
  install -m 0444 -o root -g wheel "$ROOT/agent-config/hermes/workflows/pr-risk-council-kanban-v2.json" "$BRIDGE_CONTRACT"
  install -m 0444 -o root -g wheel "$ROOT/agent-config/hermes/native.env" "$RUNTIME_CONTRACT"
  for pair in "$ROOT/bin/hermes-council-tools:$SUPPORT_ROOT/hermes-council-tools" \
    "$ROOT/bin/hermes-kanban-safety-bridge:$BRIDGE_BIN" \
    "$ROOT/scripts/hermes-kanban-safety-bridge-reconcile.py:$BRIDGE_RECONCILE" \
    "$ROOT/scripts/hermes-kanban-safety-bridge-preflight.py:$BRIDGE_PREFLIGHT" \
    "$ROOT/scripts/hermes-kanban-workflow-preflight.py:$KANBAN_PREFLIGHT" \
    "$ROOT/scripts/hermes-kanban-risk-council.py:$RISK_COUNCIL" \
    "$ROOT/scripts/hermes_pr_safety_result.py:$SAFETY_RESULT" \
    "$ROOT/scripts/hermes-pr-safety-kanban-enqueue.py:$KANBAN_ENQUEUE" \
    "$ROOT/bin/hermes-pr-safety-producer:$SAFETY_PRODUCER_BIN" \
    "$ROOT/scripts/configure-hermes-kanban-profiles.py:$PROFILE_CONFIGURATOR" \
    "$ROOT/agent-config/hermes/workflows/pr-risk-council-kanban-v2.json:$BRIDGE_CONTRACT" \
    "$ROOT/agent-config/hermes/native.env:$RUNTIME_CONTRACT"; do
    cmp -s "${pair%%:*}" "${pair#*:}" || { echo "Hermes support install mismatch" >&2; exit 2; }
  done
  provision_v2_profiles
  python3 - "$PROFILE_CONFIGURATOR" "$HERMES_HOME" "$BRIDGE_CONTRACT" \
    "$SNAPSHOT_ROOT" "$WORKFLOW_ROOT" "$INSTALL_DIR/venv/bin/python" "$SERVICE_USER" <<'PY'
import pathlib, pwd, runpy, sys
module, home, contract, snapshots, workflows, python, user = sys.argv[1:]
config = runpy.run_path(module); account = pwd.getpwnam(user)
config["configure_worker_env"](pathlib.Path(home), config["load_contract"](pathlib.Path(contract)),
    pathlib.Path(snapshots), pathlib.Path(workflows), pathlib.Path(python), account.pw_uid, account.pw_gid)
PY
  if [[ ! -e "$BRIDGE_KEY_FILE" ]]; then
    python3 - "$BRIDGE_KEY_FILE" "$(id -u "$SERVICE_USER")" "$(id -g "$SERVICE_USER")" <<'PY'
import json, os, pathlib, secrets, sys
path, uid, gid = pathlib.Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, "w") as output:
    json.dump({"schema_version":1,"auth_generation":1,"key":secrets.token_hex(32)}, output,
              sort_keys=True, separators=(",", ":")); output.write("\n"); output.flush(); os.fsync(output.fileno())
os.chown(temporary, uid, gid); os.replace(temporary, path)
directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
try: os.fsync(directory)
finally: os.close(directory)
PY
  fi
  [[ ! -x "$ROOT/bin/doc-writer-reconcile" ]] || install -m 0555 "$ROOT/bin/doc-writer-reconcile" "$SUPPORT_ROOT/doc-writer-reconcile"
  python3 - "$ROOT/launchd/com.example.ai-pr-automation-hermes.plist.template" "$PLIST" \
    "$SERVICE_USER" "$SERVICE_HOME" "$HERMES_HOME" "$INSTALL_DIR" "$LAUNCHER" "$WRAPPER" \
    "$MAINTENANCE_FILE" "$SNAPSHOT_ROOT" "$WORKFLOW_ROOT" "$LOG_ROOT" <<'PY'
import os, pathlib, sys
source, target, user, home, hermes_home, install_dir, binary, wrapper, maintenance, snapshot, workflow, logs = sys.argv[1:]
text = pathlib.Path(source).read_text()
for key, value in {"__HERMES_USER__":user,"__SERVICE_HOME__":home,"__HERMES_HOME__":hermes_home,
                   "__HERMES_INSTALL_DIR__":install_dir,"__HERMES_BIN__":binary,
                   "__GATEWAY_WRAPPER__":wrapper,"__MAINTENANCE_FILE__":maintenance,
                   "__SNAPSHOT_ROOT__":snapshot,"__WORKFLOW_ROOT__":workflow,"__LOG_ROOT__":logs}.items():
    text = text.replace(key, value)
temporary = pathlib.Path(target + ".tmp")
temporary.write_text(text)
os.chmod(temporary, 0o644)
os.replace(temporary, target)
PY
  plutil -lint "$PLIST" >/dev/null
  python3 - "$ROOT/launchd/com.example.ai-pr-automation-hermes-dashboard.plist.template" "$DASHBOARD_PLIST" \
    "$SERVICE_USER" "$SERVICE_HOME" "$HERMES_HOME" "$LAUNCHER" "$LOG_ROOT" <<'PY'
import os, pathlib, sys
source, target, user, home, hermes_home, binary, logs = sys.argv[1:]
text = pathlib.Path(source).read_text()
for key, value in {"__HERMES_USER__":user,"__SERVICE_HOME__":home,"__HERMES_HOME__":hermes_home,
                   "__HERMES_BIN__":binary,"__LOG_ROOT__":logs}.items():
    text = text.replace(key, value)
temporary = pathlib.Path(target + ".tmp")
temporary.write_text(text); os.chmod(temporary, 0o644); os.replace(temporary, target)
PY
  plutil -lint "$DASHBOARD_PLIST" >/dev/null
  python3 - "$ROOT/launchd/com.example.ai-pr-automation-pr-safety-producer.plist.template" \
    "$SAFETY_PRODUCER_PLIST" "$SERVICE_USER" "$SERVICE_HOME" "$HERMES_HOME" "$SUPPORT_ROOT" \
    "$AUTHORITY_FILE" "$SAFETY_PRODUCER_INTERVAL" "$LOG_ROOT" "$INSTALL_DIR" "$LAUNCHER" "$WORKFLOW_ROOT" <<'PY'
import os, pathlib, sys
source, target, user, home, hermes_home, support, authority, interval, logs, install_dir, launcher, workflow = sys.argv[1:]
text = pathlib.Path(source).read_text()
for key, value in {"__HERMES_USER__":user,"__SERVICE_HOME__":home,"__HERMES_HOME__":hermes_home,
                   "__SUPPORT_ROOT__":support,"__AUTHORITY_FILE__":authority,
                   "__INTERVAL_SECONDS__":interval,"__LOG_ROOT__":logs,"__INSTALL_DIR__":install_dir,
                   "__LAUNCHER__":launcher,"__WORKFLOW_ROOT__":workflow}.items():
    text = text.replace(key, value)
temporary = pathlib.Path(target + ".tmp"); temporary.write_text(text)
os.chmod(temporary, 0o644); os.replace(temporary, target)
PY
  plutil -lint "$SAFETY_PRODUCER_PLIST" >/dev/null
  local policy_digest
  policy_digest="$(shasum -a 256 "$POLICY_PATH" | awk '{print $1}')"
  python3 - "$ROOT/launchd/com.example.ai-pr-automation-hermes-kanban-safety-bridge.plist.template" \
    "$BRIDGE_PLIST" "$SERVICE_USER" "$SERVICE_HOME" "$HERMES_HOME" "$INSTALL_DIR" "$BRIDGE_BIN" \
    "$BRIDGE_PORT" "$BRIDGE_HOST_HEADER" "$BRIDGE_STATE_ROOT" "$BRIDGE_KEY_FILE" "$BRIDGE_CONTRACT" \
    "$RISK_COUNCIL" "$RUNTIME_CONTRACT" "$WORKFLOW_ROOT" "$SNAPSHOT_ROOT" "$POLICY_PATH" \
    "$POLICY_VERSION" "$policy_digest" "$LOG_ROOT" <<'PY'
import os, pathlib, sys
source, target, user, home, hermes_home, install, bridge, port, host, state, key, contract, risk, runtime, workflow, snapshot, policy, version, digest, logs = sys.argv[1:]
text = pathlib.Path(source).read_text()
values = {"__HERMES_USER__":user,"__SERVICE_HOME__":home,"__HERMES_HOME__":hermes_home,
          "__HERMES_INSTALL_DIR__":install,"__BRIDGE_BIN__":bridge,"__BRIDGE_PORT__":port,
          "__BRIDGE_HOST_HEADER__":host,"__BRIDGE_STATE_ROOT__":state,"__BRIDGE_KEY_FILE__":key,
          "__BRIDGE_CONTRACT__":contract,"__RISK_COUNCIL__":risk,"__RUNTIME_CONTRACT__":runtime,
          "__WORKFLOW_ROOT__":workflow,"__SNAPSHOT_ROOT__":snapshot,"__POLICY_PATH__":policy,
          "__POLICY_VERSION__":version,"__POLICY_DIGEST__":digest,"__LOG_ROOT__":logs}
for name, value in values.items(): text = text.replace(name, value)
temporary = pathlib.Path(target + ".tmp"); temporary.write_text(text); os.chmod(temporary, 0o644); os.replace(temporary, target)
PY
  plutil -lint "$BRIDGE_PLIST" >/dev/null
  python3 - "$MANIFEST" "$HERMES_NATIVE_VERSION" "$HERMES_NATIVE_COMMIT" \
    "$HERMES_INSTALLER_SHA256" "$LAUNCHER" "$ROOT/agent-config/hermes/profiles/smoke-v1" <<'PY'
import hashlib, json, os, pathlib, sys
out, version, commit, installer, launcher, profile = sys.argv[1:]
def framed(root):
    value=hashlib.sha256()
    for path in sorted(pathlib.Path(root).iterdir()):
        if path.is_file() and not path.is_symlink():
            value.update(path.name.encode()+b"\0"+path.read_bytes()+b"\0")
    return value.hexdigest()
body={"version":version,"commit":commit,"installer_sha256":installer,
      "launcher_sha256":hashlib.sha256(pathlib.Path(launcher).read_bytes()).hexdigest(),
      "profile_digest":framed(profile)}
tmp=pathlib.Path(out+".tmp"); tmp.write_text(json.dumps(body,sort_keys=True,separators=(",",":"))+"\n")
os.chmod(tmp,0o644); os.replace(tmp,out)
PY
  if [[ -n "$installer" ]]; then rm -f "$installer"; trap - RETURN; fi
  "$ROOT/scripts/hermes-native.sh" preflight
  echo "Hermes native foundation and support files synced but not started"
}

preflight() {
  python3 "$ROOT/scripts/hermes-native-preflight.py" --contract "$ROOT/agent-config/hermes/native.env" \
    --manifest "$MANIFEST" --install-dir "$INSTALL_DIR" --hermes-home "$HERMES_HOME" \
    --profile-source "$PROFILE_ROOT/smoke-v1" --service-user "$SERVICE_USER" \
    --gateway-plist "$PLIST" --gateway-wrapper "$WRAPPER" --maintenance-file "$MAINTENANCE_FILE" \
    --snapshot-root "$SNAPSHOT_ROOT" --workflow-root "$WORKFLOW_ROOT" \
    --bridge-preflight "$BRIDGE_PREFLIGHT" \
    --bridge-argument=--service-user --bridge-argument="$SERVICE_USER" \
    --bridge-argument=--bridge --bridge-argument="$BRIDGE_BIN" \
    --bridge-argument=--reconcile --bridge-argument="$BRIDGE_RECONCILE" \
    --bridge-argument=--risk-council --bridge-argument="$RISK_COUNCIL" \
    --bridge-argument=--contract --bridge-argument="$BRIDGE_CONTRACT" \
    --bridge-argument=--runtime-contract --bridge-argument="$RUNTIME_CONTRACT" \
    --bridge-argument=--self-path --bridge-argument="$BRIDGE_PREFLIGHT" \
    --bridge-argument=--plist --bridge-argument="$BRIDGE_PLIST" \
    --bridge-argument=--key-file --bridge-argument="$BRIDGE_KEY_FILE" \
    --bridge-argument=--state-root --bridge-argument="$BRIDGE_STATE_ROOT" \
    --bridge-argument=--workflow-root --bridge-argument="$WORKFLOW_ROOT" \
    --bridge-argument=--hermes-home --bridge-argument="$HERMES_HOME" \
    --bridge-argument=--install-dir --bridge-argument="$INSTALL_DIR" \
    --bridge-argument=--snapshot-root --bridge-argument="$SNAPSHOT_ROOT" \
    --bridge-argument=--policy-path --bridge-argument="$POLICY_PATH" \
    --bridge-argument=--policy-version --bridge-argument="$POLICY_VERSION" \
    --bridge-argument=--policy-digest --bridge-argument="$(shasum -a 256 "$POLICY_PATH" | awk '{print $1}')" \
    --bridge-argument=--port --bridge-argument="$BRIDGE_PORT" \
    --bridge-argument=--host-header --bridge-argument="$BRIDGE_HOST_HEADER" \
    --bridge-argument=--installed-source --bridge-argument="$BRIDGE_BIN=$ROOT/bin/hermes-kanban-safety-bridge" \
    --bridge-argument=--installed-source --bridge-argument="$BRIDGE_RECONCILE=$ROOT/scripts/hermes-kanban-safety-bridge-reconcile.py" \
    --bridge-argument=--installed-source --bridge-argument="$BRIDGE_PREFLIGHT=$ROOT/scripts/hermes-kanban-safety-bridge-preflight.py" \
    --bridge-argument=--installed-source --bridge-argument="$KANBAN_PREFLIGHT=$ROOT/scripts/hermes-kanban-workflow-preflight.py" \
    --bridge-argument=--installed-source --bridge-argument="$RISK_COUNCIL=$ROOT/scripts/hermes-kanban-risk-council.py" \
    --bridge-argument=--installed-source --bridge-argument="$SAFETY_RESULT=$ROOT/scripts/hermes_pr_safety_result.py" \
    --bridge-argument=--installed-source --bridge-argument="$KANBAN_ENQUEUE=$ROOT/scripts/hermes-pr-safety-kanban-enqueue.py" \
    --bridge-argument=--installed-source --bridge-argument="$SAFETY_PRODUCER_BIN=$ROOT/bin/hermes-pr-safety-producer" \
    --bridge-argument=--installed-source --bridge-argument="$PROFILE_CONFIGURATOR=$ROOT/scripts/configure-hermes-kanban-profiles.py" \
    --bridge-argument=--installed-source --bridge-argument="$BRIDGE_CONTRACT=$ROOT/agent-config/hermes/workflows/pr-risk-council-kanban-v2.json" \
    --bridge-argument=--installed-source --bridge-argument="$RUNTIME_CONTRACT=$ROOT/agent-config/hermes/native.env"
  sudo -u "$SERVICE_USER" env HOME="$SERVICE_HOME" HERMES_HOME="$HERMES_HOME" \
    HERMES_KANBAN_BUSY_TIMEOUT_MS=120000 "$INSTALL_DIR/venv/bin/python" -B "$KANBAN_PREFLIGHT" \
    --hermes-home "$HERMES_HOME" --install-dir "$INSTALL_DIR" --contract "$BRIDGE_CONTRACT" >/dev/null
}

case "${1:-}" in
  install) install_native ;;
  sync-support) HERMES_SUPPORT_ONLY=true install_native ;;
  sync-profiles) need_root; need_user; prepare_bridge_support_sync; sync_profile; preflight ;;
  preflight) preflight ;;
  start)
    need_root; preflight; rm -f "$MAINTENANCE_FILE"
    launchctl bootstrap system "$PLIST" 2>/dev/null || launchctl kickstart -k "system/$LABEL"
    ;;
  stop)
    need_root; install -m 0444 /dev/null "$MAINTENANCE_FILE"
    launchctl bootout "system/$LABEL" 2>/dev/null || true
    wait_unloaded "$LABEL"
    ;;
  producer-start)
    need_root; preflight
    launchctl bootstrap system "$SAFETY_PRODUCER_PLIST" 2>/dev/null \
      || launchctl kickstart -k "system/$SAFETY_PRODUCER_LABEL"
    ;;
  producer-stop)
    need_root
    launchctl bootout "system/$SAFETY_PRODUCER_LABEL" 2>/dev/null || true
    wait_unloaded "$SAFETY_PRODUCER_LABEL"
    ;;
  bridge-start)
    need_root; preflight
    launchctl bootstrap system "$BRIDGE_PLIST" 2>/dev/null || launchctl kickstart -k "system/$BRIDGE_LABEL"
    ;;
  bridge-stop)
    need_root
    launchctl bootout "system/$BRIDGE_LABEL" 2>/dev/null || true
    wait_unloaded "$BRIDGE_LABEL"
    ;;
  bridge-reconcile)
    need_root
    policy_digest="$(shasum -a 256 "$POLICY_PATH" | awk '{print $1}')"
    sudo -u "$SERVICE_USER" env -i HOME="$SERVICE_HOME" HERMES_HOME="$HERMES_HOME" \
      HERMES_INSTALL_DIR="$INSTALL_DIR" HERMES_KANBAN_BRIDGE_BIND=127.0.0.1 \
      HERMES_KANBAN_BUSY_TIMEOUT_MS=120000 HERMES_KANBAN_BRIDGE_PORT="$BRIDGE_PORT" \
      HERMES_KANBAN_BRIDGE_HOST_HEADER="$BRIDGE_HOST_HEADER" HERMES_KANBAN_BRIDGE_STATE_ROOT="$BRIDGE_STATE_ROOT" \
      HERMES_KANBAN_BRIDGE_KEY_FILE="$BRIDGE_KEY_FILE" HERMES_KANBAN_BRIDGE_CONTRACT="$BRIDGE_CONTRACT" \
      HERMES_KANBAN_RISK_COUNCIL="$RISK_COUNCIL" HERMES_NATIVE_CONTRACT="$RUNTIME_CONTRACT" \
      PR_SAFETY_WORKFLOW_ROOT="$WORKFLOW_ROOT" PR_SAFETY_SNAPSHOT_ROOT="$SNAPSHOT_ROOT" \
      PR_SAFETY_POLICY_PATH="$POLICY_PATH" PR_SAFETY_POLICY_VERSION="$POLICY_VERSION" \
      PR_SAFETY_POLICY_DIGEST="$policy_digest" HERMES_KANBAN_BRIDGE_BIN="$BRIDGE_BIN" \
      "$INSTALL_DIR/venv/bin/python" -B "$BRIDGE_RECONCILE"
    ;;
  bridge-status)
    launchctl print "system/$BRIDGE_LABEL" 2>/dev/null | awk -v name="$BRIDGE_LABEL" \
      '/^[[:space:]]*state =/{print name ": " $0; found=1; exit} END{if(!found) print name ": not loaded"}'
    ;;
  dashboard-start)
    need_root
    launchctl bootstrap system "$DASHBOARD_PLIST" 2>/dev/null || launchctl kickstart -k "system/$DASHBOARD_LABEL"
    ;;
  dashboard-stop)
    need_root
    launchctl bootout "system/$DASHBOARD_LABEL" 2>/dev/null || true
    wait_unloaded "$DASHBOARD_LABEL"
    ;;
  up)
    need_root
    "$ROOT/scripts/hermes-native.sh" preflight >/dev/null 2>&1 \
      || "$ROOT/scripts/hermes-native.sh" sync-support
    "$ROOT/scripts/hermes-native.sh" start
    "$ROOT/scripts/hermes-native.sh" dashboard-start
    ;;
  down)
    need_root
    "$ROOT/scripts/hermes-native.sh" producer-stop
    "$ROOT/scripts/hermes-native.sh" bridge-stop
    "$ROOT/scripts/hermes-native.sh" dashboard-stop
    "$ROOT/scripts/hermes-native.sh" stop
    ;;
  status)
    for service in "$LABEL" "$DASHBOARD_LABEL" "$BRIDGE_LABEL" "$SAFETY_PRODUCER_LABEL"; do
      launchctl print "system/$service" 2>/dev/null | awk -v name="$service" \
        '/^[[:space:]]*state =/{print name ": " $0; found=1; exit} END{if(!found) print name ": not loaded"}'
    done
    ;;
  logs) tail -n 200 "$LOG_ROOT"/*.log 2>/dev/null ;;
  *) echo "usage: $0 install|sync-support|sync-profiles|preflight|start|stop|producer-start|producer-stop|bridge-start|bridge-stop|bridge-reconcile|bridge-status|dashboard-start|dashboard-stop|up|down|status|logs" >&2; exit 2 ;;
esac
