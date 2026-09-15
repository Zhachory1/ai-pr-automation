#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

network="hermes-doc-egress-test-$$-$RANDOM"
proxy=""
image=""
network_created=false
cleanup() {
  [[ -z "$proxy" ]] || docker rm -f "$proxy" >/dev/null 2>&1 || true
  [[ "$network_created" == false ]] || docker network rm "$network" >/dev/null 2>&1 || true
  [[ -z "$image" ]] || docker image rm "$image" >/dev/null 2>&1 || true
}
trap cleanup EXIT

image="$(docker build -q -f docker/Dockerfile.hermes-doc-egress .)"
docker network create --internal "$network" >/dev/null
network_created=true
proxy="$(docker run -d --read-only --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /run:rw,noexec,nosuid,nodev,mode=1777 \
  --tmpfs /var/log/squid:rw,noexec,nosuid,nodev,mode=1777 \
  --tmpfs /var/spool/squid:rw,noexec,nosuid,nodev,mode=1777 "$image")"
docker network connect --alias hermes-doc-egress "$network" "$proxy"
for _ in $(seq 1 20); do docker exec "$proxy" squid -k parse >/dev/null 2>&1 && break; sleep 1; done
docker exec "$proxy" squid -k parse >/dev/null 2>&1
[[ "$(docker exec "$proxy" id -u)" != 0 ]]

client() { docker run --rm --network "$network" curlimages/curl:8.11.1 "$@"; }
if client --max-time 5 -fsS https://api.openai.com >/dev/null 2>&1; then
  echo "FAIL: internal doc network reached internet directly" >&2
  exit 1
fi
proxy_ready=false
for _ in $(seq 1 20); do
  if client --max-time 10 -sS -x http://hermes-doc-egress:3128 https://api.openai.com/v1/models >/dev/null 2>&1; then
    proxy_ready=true; break
  fi
  sleep 1
done
[[ "$proxy_ready" == true ]]
for url in https://example.com https://api.github.com; do
  if client --max-time 10 -sS -x http://hermes-doc-egress:3128 "$url" >/dev/null 2>&1; then
    echo "FAIL: doc proxy allowed $url" >&2
    exit 1
  fi
done

echo "PASS: Hermes doc egress allows OpenAI only and has no direct route"
