#!/usr/bin/env bash
set -euo pipefail

mode="${1:---check}"
[[ "$mode" == --check || "$mode" == --apply ]] || { echo 'usage: hermes-personal-compat.sh [--check|--apply] [runtime-dir]' >&2; exit 2; }
root="${2:-$HOME/.hermes/runtime-v0.21.5}"
root="$(cd "$root" && pwd -P)"
patch="$(cd "$(dirname "$0")/.." && pwd -P)/patches/hermes-v0.21.5-personal.patch"
[[ "$(git -C "$root" rev-parse HEAD)" == f97608f178d1ffeca59860195ab7da295f7c8e5f ]] || {
  echo 'Hermes runtime is not the pinned v0.21.5 revision' >&2; exit 1;
}
verify_runtime() {
  HERMES_COMPAT_ROOT="$root" "$root/venv/bin/python" -B - <<'PY'
import os
from pathlib import Path
from unittest.mock import patch
from agent.delegation_context import owned_kanban_task
from tools import mcp_tool_config as m
assert Path(m.__file__).resolve().is_relative_to(Path(os.environ['HERMES_COMPAT_ROOT']).resolve())
config = {'mcp_servers': {'council-tools': {'command': 'python3', 'worker_only': True},
                          'memory': {'command': 'python3'}}}
with patch('hermes_cli.config.load_config', return_value=config), \
     patch('hermes_cli.env_loader.load_hermes_dotenv'), \
     patch('utils.env_var_enabled', return_value=False), \
     patch.object(m, '_portable_mcp_servers'):
    with patch('agent.delegation_context.owned_kanban_task', return_value=''):
        assert set(m._load_mcp_config()) == {'memory'}
    with patch('agent.delegation_context.owned_kanban_task', return_value='t_probe'):
        assert set(m._load_mcp_config()) == {'council-tools', 'memory'}
PY
}

if git -C "$root" apply --reverse --check --unidiff-zero "$patch" >/dev/null 2>&1; then
  verify_runtime
  echo 'Hermes compatibility patch already installed'
  exit 0
fi
if ! git -C "$root" diff --quiet -- hermes_cli/kanban.py tools/mcp_tool_config.py ||
   ! git -C "$root" apply --check --unidiff-zero "$patch"; then
  echo 'Hermes runtime differs from the pinned baseline; inspect before applying' >&2
  exit 1
fi
[[ "$mode" == --apply ]] || { echo 'Hermes compatibility patch is not installed; run --apply while gateway is stopped' >&2; exit 1; }
git -C "$root" apply --unidiff-zero "$patch"
verify_runtime
echo 'Hermes compatibility patch installed; restart gateway only after private configuration is complete'
