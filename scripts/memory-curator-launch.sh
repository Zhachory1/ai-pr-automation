#!/usr/bin/env bash
# launchd entrypoint for the fleet memory curator. Loads .env, exports what the curator needs, execs
# the curator. The curator holds its own single-instance lock, so no extra flock here.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="${MEMORY_CURATOR_PATH:-/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin}"
ENV_FILE="${MEMORY_CURATOR_ENV_FILE:-$ROOT/.env}"
CURATOR="${MEMORY_CURATOR_BIN:-$ROOT/bin/memory-curator}"

[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || { echo "missing regular env file: $ENV_FILE" >&2; exit 2; }
[[ "$(stat -f '%u' "$ENV_FILE")" == "$(id -u)" ]] || { echo "env file must be owned by current user" >&2; exit 2; }
mode=$((8#$(stat -f '%Lp' "$ENV_FILE")))
(( (mode & 0077) == 0 )) || { echo "env file must not be group/world-readable" >&2; exit 2; }
set -a; . "$ENV_FILE"; set +a

# The curator talks to Hindsight over the host-published port and to the model via OPENAI_API_KEY.
export HINDSIGHT_URL="${HINDSIGHT_URL:-http://localhost:8888}"
: "${OPENAI_API_KEY:?set OPENAI_API_KEY (curator runs mewritecode)}"
command -v mewritecode >/dev/null || { echo "missing mewritecode" >&2; exit 2; }

exec "$CURATOR"
