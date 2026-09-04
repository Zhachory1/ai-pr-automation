#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="${PR_SAFETY_CHAT_PATH:-/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin}"
ENV_FILE="${PR_SAFETY_CHAT_ENV_FILE:-$ROOT/.env}"
PRODUCER="${PR_SAFETY_CHAT_PRODUCER_BIN:-$ROOT/bin/pr-safety-chat-producer}"
[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || { echo "missing regular env file: $ENV_FILE" >&2; exit 2; }
[[ "$(stat -f '%u' "$ENV_FILE")" == "$(id -u)" ]] || { echo "env file must be owned by current user" >&2; exit 2; }
mode=$((8#$(stat -f '%Lp' "$ENV_FILE")))
(( (mode & 0077) == 0 )) || { echo "env file must not be group/world-readable" >&2; exit 2; }
set -a; . "$ENV_FILE"; set +a

: "${REQUESTS_DB_PASSWORD:?set REQUESTS_DB_PASSWORD}"
export PGPASSWORD="$REQUESTS_DB_PASSWORD"
export REQUESTS_DB_HOST="${REQUESTS_DB_HOST:-localhost}" REQUESTS_DB_PORT="${REQUESTS_DB_PORT:-5432}"
: "${GOOGLE_CHAT_SPACE:?set GOOGLE_CHAT_SPACE}"
: "${PR_SAFETY_CHAT_BOT_SENDER:?set PR_SAFETY_CHAT_BOT_SENDER}"
: "${PR_SAFETY_POLICY_PATH:?set PR_SAFETY_POLICY_PATH}"
: "${PR_SAFETY_POLICY_VERSION:?set PR_SAFETY_POLICY_VERSION}"
: "${PR_SAFETY_POLICY_DIGEST:?set PR_SAFETY_POLICY_DIGEST}"
command -v gcloud >/dev/null || { echo "missing gcloud" >&2; exit 2; }
gcloud auth application-default print-access-token >/dev/null

lock_dir="${PR_SAFETY_CHAT_LOCK_DIR:-$HOME/.local/state/agent-fleet}"
mkdir -p "$lock_dir"
chmod 700 "$lock_dir"
exec python3 -c 'import fcntl, os, sys
lock, command = sys.argv[1], sys.argv[2:]
fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
os.set_inheritable(fd, True)
try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit(0)
os.execvp(command[0], command)' "$lock_dir/pr-safety-chat-producer.lock" "$PRODUCER"
