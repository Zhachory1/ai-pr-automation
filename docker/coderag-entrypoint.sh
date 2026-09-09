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
    # The daemon owns 127.0.0.1:${ui_port}, so forward every external interface separately. Docker
    # can publish through any attached network; binding only hostname's first IP breaks multi-homed
    # containers when the published port uses another interface.
    for host in $(hostname -i); do
      socat "TCP-LISTEN:${ui_port},bind=${host},reuseaddr,fork" "TCP:127.0.0.1:${ui_port}" &
    done
    exec supergateway --stdio /usr/local/bin/codebase-memory-mcp \
      --outputTransport streamableHttp --port "${CODERAG_MCP_PORT:-9750}" \
      --streamableHttpPath /mcp --healthEndpoint /healthz --stateful
    ;;
esac

exec /usr/local/bin/codebase-memory-mcp "$@"
