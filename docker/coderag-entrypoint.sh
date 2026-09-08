#!/bin/sh
set -eu

case "${1:-}" in
  install|update|uninstall)
    echo "coderag container does not permit lifecycle commands" >&2
    exit 2
    ;;
  serve-all)
    ui_port="${CBM_UI_PORT:-9749}"
    /usr/local/bin/codebase-memory-mcp daemon start --port="$ui_port"
    # The UI daemon listens on 127.0.0.1:${ui_port}; socat forwards the container's external IP to it
    # so the published host port works. `hostname -i` returns EVERY container IP, so on a multi-homed
    # container (coderag joins both the default and pr-safety-analyst networks) it is a space-
    # separated list. Take the first IP only: a bare list makes `bind=<list>` malformed, and 0.0.0.0
    # collides with the daemon's 127.0.0.1 bind ("address already in use"). Either kills the forwarder.
    host="$(hostname -i | awk '{print $1}')"
    socat "TCP-LISTEN:${ui_port},bind=${host},reuseaddr,fork" "TCP:127.0.0.1:${ui_port}" &
    exec supergateway --stdio /usr/local/bin/codebase-memory-mcp \
      --outputTransport streamableHttp --port "${CODERAG_MCP_PORT:-9750}" \
      --streamableHttpPath /mcp --healthEndpoint /healthz --stateful
    ;;
esac

exec /usr/local/bin/codebase-memory-mcp "$@"
