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

env_file="$ROOT/.env"
env_file_count=0
validate_controller=false
unsupported_override=false
for ((i=1; i<=$#; i++)); do
  argument="${!i}"
  case "$argument" in
    up|start|restart|run|create) validate_controller=true ;;
    -f|--file|-f=*|--file=*|--project-directory|--project-directory=*) unsupported_override=true ;;
    --env-file=*) env_file="${argument#*=}"; env_file_count=$((env_file_count + 1)) ;;
    --env-file)
      next=$((i + 1))
      (( next <= $# )) || { echo "--env-file requires a path" >&2; exit 2; }
      env_file="${!next}"
      env_file_count=$((env_file_count + 1))
      ;;
  esac
done
if [[ "$validate_controller" == true ]]; then
  if [[ "$unsupported_override" == true || -n "${COMPOSE_FILE:-}" || -n "${COMPOSE_ENV_FILES:-}" \
     || "${COMPOSE_DISABLE_ENV_FILE:-false}" == true || "$env_file_count" -gt 1 ]]; then
    echo "Fleet Controller startup requires root docker-compose.yml and at most one --env-file" >&2
    exit 2
  fi
  python3 "$ROOT/scripts/validate-fleet-controller-secrets.py" --env-file "$env_file" --repo "$ROOT"
fi
exec docker compose "$@"
