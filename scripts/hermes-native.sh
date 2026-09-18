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
  local installer; installer="$(mktemp /private/tmp/hermes-install.XXXXXX)"
  trap 'rm -f "$installer"' RETURN
  curl -fsSL "$HERMES_INSTALLER_URL" -o "$installer"
  chmod 0444 "$installer"
  [[ "$(shasum -a 256 "$installer" | awk '{print $1}')" == "$HERMES_INSTALLER_SHA256" ]] \
    || { echo "Hermes installer digest mismatch" >&2; exit 2; }
  sudo -u "$SERVICE_USER" env HOME="$SERVICE_HOME" HERMES_HOME="$HERMES_HOME" \
    bash "$installer" --commit "$HERMES_NATIVE_COMMIT" --force-commit --skip-setup \
      --non-interactive --no-skills --dir "$INSTALL_DIR" --hermes-home "$HERMES_HOME"
  sync_profile
  install -m 0555 "$ROOT/bin/hermes-native-gateway" "$WRAPPER"
  [[ ! -x "$ROOT/bin/hermes-queue-runner" ]] || install -m 0555 "$ROOT/bin/hermes-queue-runner" "$SUPPORT_ROOT/hermes-queue-runner"
  [[ ! -x "$ROOT/bin/hermes-postgres-watchdog" ]] || install -m 0555 "$ROOT/bin/hermes-postgres-watchdog" "$SUPPORT_ROOT/hermes-postgres-watchdog"
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
  rm -f "$installer"; trap - RETURN
  "$ROOT/scripts/hermes-native.sh" preflight
  echo "Hermes native foundation installed but not started"
}

preflight() {
  python3 "$ROOT/scripts/hermes-native-preflight.py" --contract "$ROOT/agent-config/hermes/native.env" \
    --manifest "$MANIFEST" --install-dir "$INSTALL_DIR" --hermes-home "$HERMES_HOME" \
    --profile-source "$PROFILE_ROOT/smoke-v1"
}

case "${1:-}" in
  install) install_native ;;
  sync-profiles) need_root; need_user; sync_profile; preflight ;;
  preflight) preflight ;;
  start)
    need_root; preflight; rm -f "$MAINTENANCE_FILE"
    launchctl bootstrap system "$PLIST" 2>/dev/null || launchctl kickstart -k "system/$LABEL"
    ;;
  stop)
    need_root; install -m 0444 /dev/null "$MAINTENANCE_FILE"
    launchctl bootout "system/$LABEL" 2>/dev/null || true
    ;;
  status) launchctl print "system/$LABEL" ;;
  logs) tail -n 200 "$LOG_ROOT"/gateway.*.log ;;
  *) echo "usage: $0 install|sync-profiles|preflight|start|stop|status|logs" >&2; exit 2 ;;
esac
