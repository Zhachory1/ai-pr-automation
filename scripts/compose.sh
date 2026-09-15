#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ "${HERMES_OAUTH_LOCK_HELD:-}" != 1 ]]; then
  for argument in "$@"; do
    case "$argument" in
      up|start|restart|run|stop|down|rm)
        exec python3 "$ROOT/scripts/hermes-lifecycle-lock.py" "$ROOT/scripts/compose.sh" "$@"
        ;;
    esac
  done
fi

"$ROOT/scripts/validate-swarmvault-vault.sh"
exec docker compose "$@"
