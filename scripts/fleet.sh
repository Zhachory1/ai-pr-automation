#!/usr/bin/env bash
# One operator command for the two appropriate runtimes:
#   Compose: Postgres, Fleet Controller, Hindsight, Coderag, SwarmVault
#   launchd: host-native Hermes gateway, dispatcher, producers, Postgres watchdog
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

case "${1:-}" in
  up)
    "$ROOT/scripts/compose.sh" up -d --build
    sudo "$ROOT/scripts/hermes-native.sh" up
    ;;
  down)
    sudo "$ROOT/scripts/hermes-native.sh" down
    "$ROOT/scripts/compose.sh" down
    ;;
  status)
    echo '=== Compose support services ==='
    "$ROOT/scripts/compose.sh" ps
    echo '=== Host-native Hermes ==='
    "$ROOT/scripts/hermes-native.sh" status
    ;;
  logs)
    echo '=== Host-native Hermes ==='
    sudo "$ROOT/scripts/hermes-native.sh" logs
    ;;
  *) echo "usage: $0 up|down|status|logs" >&2; exit 2 ;;
esac
