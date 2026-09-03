#!/usr/bin/env bash
# launchd wrapper for the agent-server. Keeps secrets out of the plist: sources .env from the repo,
# derives PGPASSWORD, then execs bin/agent-server. Point the plist ProgramArguments here.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"

# Load .env (gitignored) for DB password, hindsight key, runner path, etc.
if [[ -f "$HERE/.env" ]]; then
  set -a; . "$HERE/.env"; set +a
fi

# Queue DB password for psql (never put this in the plist).
export PGPASSWORD="${REQUESTS_DB_PASSWORD:?set REQUESTS_DB_PASSWORD in .env}"
: "${AGENT_SERVER_RUNNER:?set AGENT_SERVER_RUNNER in .env (the agent runner executable)}"

exec "$HERE/bin/agent-server"
