#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
action="${1:-}"
provider="${2:-openai-codex}"
[[ "$provider" == openai-codex ]] || { echo "provider must be openai-codex" >&2; exit 2; }
case "$action" in login|status|logout|start|stop) ;; *)
  echo "usage: $0 login|status|logout|start|stop [openai-codex]" >&2
  exit 2
esac

if [[ "$action" != status && "${HERMES_OAUTH_LOCK_HELD:-}" != 1 ]]; then
  exec python3 "$ROOT/scripts/hermes-lifecycle-lock.py" "$0" "$@"
fi
compose() { "$ROOT/scripts/compose.sh" "$@"; }
state_volume="${HERMES_STATE_VOLUME:-agent-fleet_hermes_doc_state}"
cleanup_proxy() { compose --profile hermes-oauth stop hermes-openai-oauth-egress >/dev/null 2>&1 || true; }
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
  status)
    container="$(compose --profile hermes-m2a ps -q hermes-doc)"
    if [[ -n "$container" ]] && [[ "$(docker inspect -f '{{.State.Running}}' "$container")" == true ]]; then
      docker exec "$container" hermes auth status "$provider"
    else
      trap cleanup_proxy EXIT
      compose --profile hermes-oauth run --rm hermes-doc-auth auth status "$provider"
      trap - EXIT
      cleanup_proxy
    fi
    ;;
  login|logout)
    compose --profile hermes-m2a stop hermes-doc
    if [[ -n "$(docker ps -q --filter "volume=$state_volume")" ]]; then
      echo "another running container mounts Hermes state" >&2
      exit 1
    fi
    trap cleanup_proxy EXIT
    if [[ "$action" == login ]]; then
      compose --profile hermes-oauth run --rm hermes-doc-auth auth add "$provider" --type oauth --no-browser
    else
      compose --profile hermes-oauth run --rm hermes-doc-auth auth logout "$provider"
    fi
    echo "Hermes gateway remains stopped. Run: scripts/hermes-oauth.sh start"
    ;;
esac
