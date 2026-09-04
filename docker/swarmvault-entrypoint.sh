#!/bin/sh
set -eu

if [ "${1:-}" = "swarmvault" ]; then
  shift
fi

if [ "$#" -eq 0 ]; then
  set -- watch
fi

if [ "${1:-}" = "serve-mcp" ]; then
  exec supergateway --stdio "node /app/packages/cli/dist/index.js mcp" \
    --outputTransport streamableHttp --port "${SWARMVAULT_MCP_PORT:-9760}" \
    --streamableHttpPath /mcp --healthEndpoint /healthz --stateful
fi

if [ "${1:-watch}" = "mcp" ]; then
  shift
  exec node /app/packages/cli/dist/index.js mcp "$@"
fi

if [ "${1:-watch}" = "watch" ] && [ ! -f swarmvault.config.json ]; then
  node /app/packages/cli/dist/index.js init
fi

exec node /app/packages/cli/dist/index.js "$@"
