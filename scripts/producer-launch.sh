#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="${PR_PRODUCER_PATH:-/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin}"
ENV_FILE="${PR_PRODUCER_ENV_FILE:-$ROOT/.env}"
PRODUCER="${PR_PRODUCER_BIN:-$ROOT/bin/pr-producer}"

[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || { echo "missing regular env file: $ENV_FILE" >&2; exit 2; }
[[ "$(stat -f '%u' "$ENV_FILE")" == "$(id -u)" ]] || { echo "env file must be owned by current user" >&2; exit 2; }
mode=$((8#$(stat -f '%Lp' "$ENV_FILE")))
(( (mode & 0077) == 0 )) || { echo "env file must not be group/world-readable" >&2; exit 2; }
set -a
# shellcheck source=/dev/null
. "$ENV_FILE"
set +a

: "${REQUESTS_DB_PASSWORD:?set REQUESTS_DB_PASSWORD in $ENV_FILE}"
export PGPASSWORD="$REQUESTS_DB_PASSWORD"
export REQUESTS_DB_HOST="${REQUESTS_DB_HOST:-localhost}" REQUESTS_DB_PORT="${REQUESTS_DB_PORT:-5432}"

exec "$PRODUCER" "$@"
