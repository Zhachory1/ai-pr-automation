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
HERMES_API_KEYS_FILE="${HERMES_API_KEYS_FILE:-/Users/Shared/ai-pr-automation-runtime/secrets/hermes-api-keys.json}"
GITHUB_READ_TOKEN_FILE="${GITHUB_READ_TOKEN_FILE:-/Users/Shared/ai-pr-automation-runtime/secrets/github-read-token}"

need_root() { [[ "$EUID" == 0 ]] || { echo "run as root" >&2; exit 2; }; }
need_user() { id "$SERVICE_USER" >/dev/null 2>&1 || { echo "create $SERVICE_USER before install" >&2; exit 2; }; }

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
  install -d -m 755 "$SUPPORT_ROOT" "$CONFIG_ROOT" "$LOG_ROOT"
  install -d -m 700 -o "$SERVICE_USER" "$SERVICE_HOME" "$HERMES_HOME"
  # Retire old host queue lifecycle. Compose owns dispatcher and producers.
  local legacy
  for legacy in com.example.ai-pr-automation-watchdog com.example.ai-pr-automation-dispatcher \
    com.example.ai-pr-automation-producer-review com.example.ai-pr-automation-producer-maintain \
    com.example.ai-pr-automation-producer-pr-safety com.example.ai-pr-automation-memory-curate-producer; do
    launchctl bootout "system/$legacy" 2>/dev/null || true
  done
  rm -f /Library/LaunchDaemons/com.example.ai-pr-automation-watchdog.plist \
    /Library/LaunchDaemons/com.example.ai-pr-automation-dispatcher.plist \
    /Library/LaunchDaemons/com.example.ai-pr-automation-producer-{review,maintain,pr-safety}.plist \
    /Library/LaunchDaemons/com.example.ai-pr-automation-memory-curate-producer.plist \
    "$SUPPORT_ROOT/hermes-postgres-watchdog" "$SUPPORT_ROOT/hermes-dispatcher" \
    "$SUPPORT_ROOT/hermes-queue-runner" "$SUPPORT_ROOT/hermes-pr-producer" \
    "$SUPPORT_ROOT/hermes-pr-safety-producer" "$SUPPORT_ROOT/hermes-pr-safety-runner" \
    "$SUPPORT_ROOT/hermes-memory-curate" "$SUPPORT_ROOT/hermes-doc-write-runner" \
    "$LOG_ROOT/watchdog.out.log" "$LOG_ROOT/watchdog.err.log"
  install -d -m 700 -o "$SERVICE_USER" "$SERVICE_HOME/.local/share/ai-pr-automation/doc-writer"
  local logfile
  for logfile in gateway.out gateway.err dashboard.out dashboard.err; do
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
  [[ ! -x "$ROOT/bin/doc-writer-reconcile" ]] || install -m 0555 "$ROOT/bin/doc-writer-reconcile" "$SUPPORT_ROOT/doc-writer-reconcile"
  python3 - "$ROOT/launchd/com.example.ai-pr-automation-hermes.plist.template" "$PLIST" \
    "$SERVICE_USER" "$SERVICE_HOME" "$HERMES_HOME" "$LAUNCHER" "$WRAPPER" "$MAINTENANCE_FILE" "$LOG_ROOT" <<'PY'
import os, pathlib, sys
source, target, user, home, hermes_home, binary, wrapper, maintenance, logs = sys.argv[1:]
text = pathlib.Path(source).read_text()
for key, value in {"__HERMES_USER__":user,"__SERVICE_HOME__":home,"__HERMES_HOME__":hermes_home,
                   "__HERMES_BIN__":binary,"__GATEWAY_WRAPPER__":wrapper,
                   "__MAINTENANCE_FILE__":maintenance,"__LOG_ROOT__":logs}.items():
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

workflow_change() {
  need_root; need_user
  if launchctl print "system/$LABEL" >/dev/null 2>&1 || launchctl print "system/$DASHBOARD_LABEL" >/dev/null 2>&1; then
    echo "stop Hermes gateway and dashboard before changing workflow profiles" >&2; exit 2
  fi
  sudo -u "$SERVICE_USER" env HOME="$SERVICE_HOME" HERMES_HOME="$HERMES_HOME" \
    python3 "$ROOT/scripts/configure-hermes-bot-workflow.py" --hermes-home "$HERMES_HOME" \
      --service-user "$SERVICE_USER" --contract "$ROOT/agent-config/hermes/workflows/pr-risk-council.json" "$1"
}

workflow_state_ready() {
  local state="$HERMES_HOME/workflow-backups/pr-risk-council.state" value
  [[ -e "$state" ]] || return 0
  value="$(cat "$state")" || { echo "workflow profile state is unreadable" >&2; exit 2; }
  [[ "$value" == applied ]] \
    || { echo "workflow profile change is incomplete; run restore-workflows" >&2; exit 2; }
}

preflight() {
  python3 "$ROOT/scripts/hermes-native-preflight.py" --contract "$ROOT/agent-config/hermes/native.env" \
    --manifest "$MANIFEST" --install-dir "$INSTALL_DIR" --hermes-home "$HERMES_HOME" \
    --profile-source "$PROFILE_ROOT/smoke-v1" --service-user "$SERVICE_USER"
}

case "${1:-}" in
  install) install_native ;;
  sync-support) HERMES_SUPPORT_ONLY=true install_native ;;
  sync-profiles) need_root; need_user; sync_profile; preflight ;;
  sync-workflows) workflow_change --apply ;;
  restore-workflows) workflow_change --restore ;;
  preflight) preflight ;;
  start)
    need_root; workflow_state_ready; preflight; rm -f "$MAINTENANCE_FILE"
    launchctl bootstrap system "$PLIST" 2>/dev/null || launchctl kickstart -k "system/$LABEL"
    ;;
  stop)
    need_root; install -m 0444 /dev/null "$MAINTENANCE_FILE"
    launchctl bootout "system/$LABEL" 2>/dev/null || true
    ;;
  dashboard-start)
    need_root
    launchctl bootstrap system "$DASHBOARD_PLIST" 2>/dev/null || launchctl kickstart -k "system/$DASHBOARD_LABEL"
    ;;
  dashboard-stop)
    need_root
    launchctl bootout "system/$DASHBOARD_LABEL" 2>/dev/null || true
    ;;
  up)
    need_root
    "$ROOT/scripts/hermes-native.sh" sync-support
    "$ROOT/scripts/hermes-native.sh" start
    "$ROOT/scripts/hermes-native.sh" dashboard-start
    ;;
  down)
    need_root
    "$ROOT/scripts/hermes-native.sh" dashboard-stop
    "$ROOT/scripts/hermes-native.sh" stop
    ;;
  status)
    for service in "$LABEL" "$DASHBOARD_LABEL"; do
      launchctl print "system/$service" 2>/dev/null | awk -v name="$service" \
        '/^[[:space:]]*state =/{print name ": " $0; found=1; exit} END{if(!found) print name ": not loaded"}'
    done
    ;;
  logs) tail -n 200 "$LOG_ROOT"/*.log 2>/dev/null ;;
  *) echo "usage: $0 install|sync-support|sync-profiles|sync-workflows|restore-workflows|preflight|start|stop|dashboard-start|dashboard-stop|up|down|status|logs" >&2; exit 2 ;;
esac
