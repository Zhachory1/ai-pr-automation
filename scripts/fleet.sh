#!/usr/bin/env bash
# One operator command for the two appropriate runtimes:
#   Compose: Postgres, Fleet Controller, Hindsight, Coderag, SwarmVault
#   launchd: host-native Hermes gateway, dispatcher, producers
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SHARED_RUNTIME="${HERMES_SHARED_RUNTIME_ROOT:-/Users/Shared/ai-pr-automation-runtime}"
# Exported values override stale/missing .env values and match the service-account role bootstrap.
export DOC_WRITER_STAGE_HOST="${DOC_WRITER_STAGE_HOST:-$SHARED_RUNTIME/doc-writer}"
export HANDOFF_ROOT="${HANDOFF_ROOT:-$SHARED_RUNTIME/safety-handoffs}"
export HERMES_API_KEYS_FILE="${HERMES_API_KEYS_FILE:-/Users/Shared/zhach-ai-pr-automation/hermes-api-keys.json}"

case "${1:-}" in
  up)
    sudo "$ROOT/scripts/configure-hermes-role-env.sh"
    # Provision profile API keys/listener before Compose resolves its controller-only secret.
    sudo "$ROOT/scripts/hermes-native.sh" sync-support
    "$ROOT/scripts/compose.sh" up -d --build
    sudo "$ROOT/scripts/hermes-native.sh" start
    sudo "$ROOT/scripts/hermes-native.sh" dashboard-start
    sudo "$ROOT/scripts/hermes-native.sh" dispatcher-start
    sudo "$ROOT/scripts/hermes-native.sh" producer-start
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
