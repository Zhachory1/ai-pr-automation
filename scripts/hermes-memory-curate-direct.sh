#!/usr/bin/env bash
set -euo pipefail
ROOT_ENV="$HOME/.hermes/.env"
[[ -r "$ROOT_ENV" ]] || { echo "Hermes root environment unavailable" >&2; exit 2; }
set -a
. "$ROOT_ENV"
set +a
exec /usr/local/libexec/ai-pr-automation/hermes-memory-curate --direct
