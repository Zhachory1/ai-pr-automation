#!/usr/bin/env bash
set -euo pipefail
mode="${1:?review|maintain|pr-safety|memory}"
interval="${PRODUCER_INTERVAL_SECONDS:-300}"
export REQUESTS_DB_HOST="${REQUESTS_DB_HOST:-db-requests}"
export REQUESTS_DB_PORT="${REQUESTS_DB_PORT:-5432}"
export REQUESTS_DB_USER="${REQUESTS_DB_USER:-fleet}"
export REQUESTS_DB_NAME="${REQUESTS_DB_NAME:-fleet}"
export HERMES_AUTHORITY_BIN=/app/hermes-authority.py
if [[ "$mode" != memory ]]; then
  GH_TOKEN="$(cat "${GITHUB_TOKEN_FILE:-/run/secrets/github_read_token}")"
  [[ -n "$GH_TOKEN" ]] || { echo "GitHub read token is empty" >&2; exit 2; }
  export GH_TOKEN
fi
while true; do
  case "$mode" in
    review|maintain) /app/hermes-pr-producer "$mode" || true ;;
    pr-safety)
      if [[ -z "${PR_SAFETY_POLICY_DIGEST:-}" ]]; then
        PR_SAFETY_POLICY_DIGEST="$(sha256sum "$PR_SAFETY_POLICY_PATH" | awk '{print $1}')"
        export PR_SAFETY_POLICY_DIGEST
      fi
      /app/hermes-pr-safety-producer || true
      ;;
    memory)
      psql -v ON_ERROR_STOP=1 -qAt \
        -c "SELECT hermes_enqueue_request('memory-curate','{}'::jsonb,'memory-curate:'||to_char(now(),'YYYYMMDDHH24'));" >/dev/null || true
      ;;
    *) echo "unsupported producer mode: $mode" >&2; exit 2 ;;
  esac
  sleep "$interval"
done
