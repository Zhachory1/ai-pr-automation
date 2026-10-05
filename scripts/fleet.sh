#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AUTHORITY_SOURCE="${HERMES_AUTHORITY_SOURCE_FILE:-${HERMES_AUTHORITY_FILE:-/usr/local/etc/ai-pr-automation/authority.yaml}}"
DOCKER_AUTHORITY="${HERMES_DOCKER_AUTHORITY_FILE:-/Users/Shared/zhach-ai-pr-automation/authority.yaml}"
export HERMES_AUTHORITY_FILE="$DOCKER_AUTHORITY"

mirror_authority() {
  "$ROOT/scripts/hermes-authority.py" --file "$AUTHORITY_SOURCE" >/dev/null
  [[ ! -L "$DOCKER_AUTHORITY" && ( ! -e "$DOCKER_AUTHORITY" || -f "$DOCKER_AUTHORITY" ) ]] \
    || { echo "invalid Docker authority mirror: $DOCKER_AUTHORITY" >&2; exit 2; }
  install -d -m 0700 "$(dirname "$DOCKER_AUTHORITY")"
  install -m 0644 "$AUTHORITY_SOURCE" "$DOCKER_AUTHORITY"
}

case "${1:-}" in
  up)
    "$ROOT/scripts/compose.sh" config --format json | python3 -c '
import json, os, pathlib, stat, sys
secrets = json.load(sys.stdin)["secrets"]
for name in ("github_discovery_token", "hermes_review_key", "hermes_maintain_key"):
    path = pathlib.Path(secrets[name]["file"])
    try:
        info, parent = path.lstat(), path.parent.lstat()
    except OSError:
        raise SystemExit(f"configure owner-only {name} before fleet up")
    if (not stat.S_ISREG(info.st_mode) or info.st_size == 0 or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.getuid() or not stat.S_ISDIR(parent.st_mode)
            or stat.S_IMODE(parent.st_mode) & 0o077 or parent.st_uid != os.getuid()):
        raise SystemExit(f"configure owner-only {name} before fleet up")
'
    mirror_authority
    "$ROOT/scripts/compose.sh" up -d --build
    ;;
  support-up)
    mirror_authority
    "$ROOT/scripts/compose.sh" up -d --no-build hindsight-db hindsight coderag signal ui-proxy
    ;;
  pause)
    "$ROOT/scripts/compose.sh" stop pr-producer-review pr-producer-maintain
    ;;
  down)
    "$ROOT/scripts/compose.sh" down
    ;;
  status)
    echo '=== Compose support services ==='
    "$ROOT/scripts/compose.sh" ps
    echo '=== Personal Hermes gateway ==='
    "${HERMES_BIN:-$HOME/.local/bin/hermes}" gateway status
    ;;
  logs)
    "$ROOT/scripts/compose.sh" logs --tail=200 hindsight-db hindsight hindsight-bank-init coderag signal pr-producer-review pr-producer-maintain ui-proxy
    ;;
  *) echo "usage: $0 up|support-up|pause|down|status|logs" >&2; exit 2 ;;
esac
