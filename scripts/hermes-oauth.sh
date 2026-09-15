#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
action="${1:-}"
provider="${2:-openai-codex}"
case "$provider" in openai-codex|anthropic) ;; *) echo "provider must be openai-codex or anthropic" >&2; exit 2;; esac
case "$action" in login|status|logout|start|stop) ;; *)
  echo "usage: $0 login|status|logout|start|stop [openai-codex|anthropic]" >&2
  exit 2
esac

if [[ "${HERMES_OAUTH_LOCK_HELD:-}" != 1 ]]; then
  exec python3 "$ROOT/scripts/hermes-lifecycle-lock.py" "$0" "$@"
fi
compose() { "$ROOT/scripts/compose.sh" "$@"; }
state_volume="${HERMES_STATE_VOLUME:-agent-fleet_hermes_doc_state}"
cleanup_proxy() { compose --profile hermes-oauth stop hermes-oauth-egress >/dev/null 2>&1 || true; }
trap 'exit 130' INT TERM

case "$action" in
  start)
    mounting="$(docker ps -q --filter "volume=$state_volume")"
    gateway="$(compose --profile hermes-m2a ps -q hermes-doc)"
    if [[ -n "$mounting" && "$mounting" != "$gateway" ]]; then
      echo "another running container mounts Hermes state" >&2
      exit 1
    fi
    [[ -n "$mounting" ]] || compose --profile hermes-m2a up -d hermes-doc
    ;;
  stop)
    compose --profile hermes-m2a stop hermes-doc
    ;;
  login|status|logout)
    compose --profile hermes-m2a stop hermes-doc
    if [[ -n "$(docker ps -q --filter "volume=$state_volume")" ]]; then
      echo "another running container mounts Hermes state" >&2
      exit 1
    fi
    trap cleanup_proxy EXIT
    case "$action" in
      login) command=(auth add "$provider" --type oauth --no-browser) ;;
      status) command=(auth status "$provider") ;;
      logout) command=(auth logout "$provider") ;;
    esac
    compose --profile hermes-oauth run --rm hermes-doc-auth "${command[@]}"
    echo "Hermes gateway remains stopped. Run: scripts/hermes-oauth.sh start"
    ;;
esac
