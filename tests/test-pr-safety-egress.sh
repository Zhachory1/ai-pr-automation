#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
IMAGE="agent-fleet/pr-safety-egress:test-$$"
NETWORK="pr-safety-egress-test-$$"
PROXY="pr-safety-egress-test-proxy-$$"
cleanup() {
  docker rm -f "$PROXY" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker build -q -f docker/Dockerfile.pr-safety-egress -t "$IMAGE" . >/dev/null
docker network create --internal "$NETWORK" >/dev/null
docker run -d --rm --name "$PROXY" --network bridge --read-only --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /run:rw,noexec,nosuid,nodev,mode=1777 --tmpfs /var/log/squid:rw,noexec,nosuid,nodev,mode=1777 \
  --tmpfs /var/spool/squid:rw,noexec,nosuid,nodev,mode=1777 "$IMAGE" >/dev/null
docker network connect --alias pr-safety-egress "$NETWORK" "$PROXY"
for _ in $(seq 1 20); do docker exec "$PROXY" squid -k parse >/dev/null 2>&1 && break; sleep 1; done
docker exec "$PROXY" squid -k parse >/dev/null
[[ "$(docker exec "$PROXY" id -u)" != 0 ]]
if docker run --rm --network "$NETWORK" node:24-bookworm-slim node -e 'fetch("https://api.github.com", {signal: AbortSignal.timeout(5000)}).then(() => process.exit(1)).catch(() => process.exit(0))'; then :; else exit 1; fi
for host in api.openai.com/v1/models api.github.com api.buildkite.com api.datadoghq.com/api/v1/validate; do
  docker run --rm --network "$NETWORK" -e HTTPS_PROXY=http://pr-safety-egress:3128 -e NODE_USE_ENV_PROXY=1 node:24-bookworm-slim node -e "fetch('https://$host', {signal: AbortSignal.timeout(10000)}).then(() => process.exit(0)).catch(() => process.exit(1))"
done
docker run --rm --network "$NETWORK" -e HTTPS_PROXY=http://pr-safety-egress:3128 -e NODE_USE_ENV_PROXY=1 node:24-bookworm-slim node -e 'fetch("https://example.com", {signal: AbortSignal.timeout(10000)}).then(() => process.exit(1)).catch(() => process.exit(0))'
grep -Fq -- '--network "$network"' bin/pr-safety-review-runner
grep -Fq -- '-e HTTPS_PROXY="$proxy" -e HTTP_PROXY="$proxy" -e NODE_USE_ENV_PROXY=1' bin/pr-safety-review-runner
echo "PASS: PR safety read-services egress"
