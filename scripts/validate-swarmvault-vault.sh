#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -f "$ROOT/.env" ]]; then
  set -a; . "$ROOT/.env"; set +a
fi

: "${CODE_ROOT:?set CODE_ROOT}"
: "${SWARMVAULT_VAULT:?set SWARMVAULT_VAULT}"

python3 - "$CODE_ROOT" "$SWARMVAULT_VAULT" <<'PY'
import os
import sys

code_root, vault = map(os.path.realpath, sys.argv[1:])
if os.path.commonpath((code_root, vault)) == code_root:
    raise SystemExit(f"SWARMVAULT_VAULT must be outside CODE_ROOT: {vault}")
PY
