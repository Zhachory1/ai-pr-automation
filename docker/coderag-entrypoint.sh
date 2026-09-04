#!/bin/sh
set -eu

case "${1:-}" in
  install|update|uninstall)
    echo "coderag container does not permit lifecycle commands" >&2
    exit 2
    ;;
  serve-ui)
    port="${CBM_UI_PORT:-9749}"
    /usr/local/bin/codebase-memory-mcp daemon start --port="$port"
    host="$(hostname -i)"
    exec socat "TCP-LISTEN:${port},bind=${host},reuseaddr,fork" "TCP:127.0.0.1:${port}"
    ;;
esac

exec /usr/local/bin/codebase-memory-mcp "$@"
