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
listener_ready=false
for _ in $(seq 1 20); do
  if docker exec "$proxy" sh -ec "test -s /run/squid.pid && kill -0 \$(cat /run/squid.pid) && grep -q ':0C38 ' /proc/net/tcp /proc/net/tcp6"; then
    listener_ready=true
    break
  fi
  sleep 1
done
[[ "$listener_ready" == true ]]
[[ "$(docker exec "$proxy" id -u)" != 0 ]]
grep -Fq 'FROM ubuntu:24.04@sha256:224a1869083a311ef3f13648a154ba79832fbef6364d31493642ca03082da254' docker/Dockerfile.hermes-doc-egress
grep -Fq 'squid=6.14-0ubuntu0.24.04.4' docker/Dockerfile.hermes-doc-egress

client() { docker run --rm --network "$network" curlimages/curl:8.11.1 "$@"; }
if client --max-time 5 -fsS https://api.openai.com >/dev/null 2>&1; then
  echo "FAIL: internal doc network reached internet directly" >&2
  exit 1
fi
grep -Fq 'acl allowed_provider dstdomain api.openai.com' docker/hermes-doc-egress.conf
grep -Fq 'http_access allow CONNECT allowed_provider' docker/hermes-doc-egress.conf
for url in https://example.com https://api.github.com; do
  if client --max-time 10 -sS -x http://hermes-doc-egress:3128 "$url" >/dev/null 2>&1; then
    echo "FAIL: doc proxy allowed $url" >&2
    exit 1
  fi
done

echo "PASS: Hermes doc egress allows OpenAI only and has no direct route"
