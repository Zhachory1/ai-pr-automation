#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

HERMES_IMAGE="nousresearch/hermes-agent@sha256:6d7285e1476d0661fc347e3d55245c99decb781d76d39d67582166d2c9561874"
PYTHON_IMAGE="python:3.11-alpine@sha256:0d55920083f1ce1e38ac292e2772f924b4f8bb4188d336c79bf66963039e6146"
CURL_IMAGE="curlimages/curl:8.11.1@sha256:c1fe1679c34d9784c1b0d1e5f62ac0a79fca01fb6377cdd33e90473c6f9f9a69"
DEBIAN_IMAGE="debian:bookworm-slim@sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171"
API_KEY=0123456789abcdef0123456789abcdef
id="$$-$RANDOM"
network="hermes-doc-spike-$id"
tmp="$(mktemp -d)"
state=""
containers=()
images=()
network_created=false
cleanup() {
  ((${#containers[@]} == 0)) || docker rm -f "${containers[@]}" >/dev/null 2>&1 || true
  [[ "$network_created" == false ]] || docker network rm "$network" >/dev/null 2>&1 || true
  [[ -z "$state" ]] || docker volume rm -f "$state" >/dev/null 2>&1 || true
  ((${#images[@]} == 0)) || docker image rm "${images[@]}" >/dev/null 2>&1 || true
  [[ -n "${owned_inbox:-}" ]] && rm -rf "$owned_inbox"
  rm -rf "$tmp"
}
trap cleanup EXIT
state="$(docker volume create)"

docker network create --internal "$network" >/dev/null
network_created=true

cp agent-config/hermes/doc-config.yaml "$tmp/config.yaml"

# Actual configured inbox may be supplied by the operator; CI uses a disposable bind.
inbox="${HERMES_SPIKE_INBOX:-}"
if [[ -z "$inbox" ]]; then owned_inbox="$(mktemp -d)"; inbox="$owned_inbox"; fi
doc_image="$(docker build -q -f Dockerfile.doc-writer .)"
images+=("$doc_image")
docker run --rm --entrypoint python3 -i -v "$inbox:/inbox" "$doc_image" - <<'PY'
import ctypes, errno, os, pathlib, uuid
root = pathlib.Path('/inbox')
staging = root / '.ai-pr-automation-staging'
staging.mkdir(mode=0o700, exist_ok=True)
tag = uuid.uuid4().hex
source = staging / f'renameat2-{tag}.tmp'
target = root / f'.renameat2-{tag}.tmp'
existing = root / f'.renameat2-existing-{tag}.tmp'
try:
    source.write_bytes(b'hermes-m2-renameat2-spike\n')
    existing.write_bytes(b'existing\n')
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = libc.renameat2
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    if renameat2(-100, os.fsencode(source), -100, os.fsencode(target), 1):
        raise OSError(ctypes.get_errno(), 'renameat2 first move failed')
    if source.exists() or target.read_bytes() != b'hermes-m2-renameat2-spike\n':
        raise RuntimeError('move was not exact')
    source.write_bytes(b'new\n')
    if renameat2(-100, os.fsencode(source), -100, os.fsencode(existing), 1) == 0:
        raise RuntimeError('existing target was overwritten')
    if ctypes.get_errno() != errno.EEXIST or existing.read_bytes() != b'existing\n':
        raise RuntimeError('existing target rejection was not EEXIST-safe')
finally:
    for path in (source, target, existing):
        try: path.unlink()
        except FileNotFoundError: pass
    try: staging.rmdir()
    except OSError: pass
PY

fake="$(docker run -d --network "$network" --network-alias fake-provider \
  -v "$PWD/tests/fake-hermes-provider.py:/app/server.py:ro" -v "$tmp:/capture" \
  -e CAPTURE=/capture/request.json "$PYTHON_IMAGE" python /app/server.py)"
containers+=("$fake")

start_hermes() {
  docker run -d --network "$network" --network-alias hermes-doc \
    -e API_SERVER_ENABLED=true -e API_SERVER_HOST=0.0.0.0 -e API_SERVER_PORT=8642 \
    -e API_SERVER_KEY="$API_KEY" -e OPENAI_API_KEY=test-key \
    -e OPENAI_BASE_URL=http://fake-provider:8000/v1 \
    -e HERMES_SAFE_MODE=1 -e HERMES_IGNORE_RULES=1 \
    -v "$state:/opt/data" -v "$tmp/config.yaml:/opt/data/config.yaml:ro" \
    "$HERMES_IMAGE" gateway run
}
hermes="$(start_hermes)"
containers+=("$hermes")

client() { docker run --rm --network "$network" "$CURL_IMAGE" "$@"; }
for _ in $(seq 1 60); do
  if client -fsS -H "Authorization: Bearer $API_KEY" http://hermes-doc:8642/v1/toolsets > "$tmp/tools.json" 2>/dev/null; then break; fi
  sleep 2
done
[[ "$(jq '[.data[] | select(.enabled==true)] | length' "$tmp/tools.json")" == 0 ]]

body='{"input":"Return exactly spike-ok","instructions":"Text only.","model":"gpt-4o-mini","provider":"openai-api"}'
post() {
  client -sS -w '\nHTTP:%{http_code}\n' -X POST -H "Authorization: Bearer $API_KEY" \
    -H 'Content-Type: application/json' -H 'Idempotency-Key: spike-key' \
    --data "$1" http://hermes-doc:8642/v1/runs
}
first="$(post "$body")"
[[ "$(printf '%s\n' "$first" | tail -1)" == HTTP:202 ]]
run_id="$(printf '%s\n' "$first" | sed '$d' | jq -r .run_id)"
for _ in $(seq 1 60); do
  status="$(client -fsS -H "Authorization: Bearer $API_KEY" "http://hermes-doc:8642/v1/runs/$run_id")"
  state_name="$(jq -r .status <<<"$status")"
  case "$state_name" in completed|failed|cancelled|interrupted) break;; esac
  sleep 1
done
jq -e '.status=="completed" and .output=="spike-ok"' <<<"$status" >/dev/null
jq -e '.path=="/v1/responses" and ((.body.tools // []) | length==0)' "$tmp/request.json" >/dev/null

replay="$(post "$body")"
jq -e --arg id "$run_id" '.replayed==true and .run_id==$id' <(printf '%s\n' "$replay" | sed '$d') >/dev/null
changed="$(post '{"input":"changed","model":"gpt-4o-mini","provider":"openai-api"}')"
[[ "$(printf '%s\n' "$changed" | tail -1)" == HTTP:409 ]]

docker rm -f "$hermes" >/dev/null
containers=("$fake")
hermes="$(start_hermes)"
containers+=("$hermes")
for _ in $(seq 1 60); do
  client -fsS -H "Authorization: Bearer $API_KEY" http://hermes-doc:8642/health >/dev/null 2>&1 && break
  sleep 2
done
restarted="$(post "$body")"
jq -e --arg id "$run_id" '.replayed==true and .run_id==$id' <(printf '%s\n' "$restarted" | sed '$d') >/dev/null

# Trace tooling spike: attach to the exact container PID namespace and follow all current descendants.
cat > "$tmp/Tracerfile" <<EOF
FROM $DEBIAN_IMAGE
RUN apt-get update && apt-get install -y --no-install-recommends strace procps && rm -rf /var/lib/apt/lists/*
EOF
tracer_image="$(docker build -q -f "$tmp/Tracerfile" "$tmp")"
images+=("$tracer_image")
pids="$(docker run --rm --pid="container:$hermes" "$tracer_image" ps -e -o pid= | xargs)"
trace_args=(); for pid in $pids; do trace_args+=( -p "$pid" ); done
tracer="$(docker run -d --pid="container:$hermes" --cap-add SYS_PTRACE --security-opt seccomp=unconfined \
  -v "$tmp:/trace" "$tracer_image" strace -ff -e trace=process,file,network -o /trace/trace "${trace_args[@]}")"
containers+=("$tracer")
sleep 2
slow_body='{"input":"slow return spike-ok","instructions":"Text only.","model":"gpt-4o-mini","provider":"openai-api"}'
slow="$(client -sS -X POST -H "Authorization: Bearer $API_KEY" -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: trace-key' --data "$slow_body" http://hermes-doc:8642/v1/runs)"
slow_id="$(jq -r .run_id <<<"$slow")"
for _ in $(seq 1 30); do
  slow_status="$(client -fsS -H "Authorization: Bearer $API_KEY" "http://hermes-doc:8642/v1/runs/$slow_id" | jq -r .status)"
  case "$slow_status" in completed|failed|cancelled|interrupted) break;; esac
  sleep 1
done
docker stop "$tracer" >/dev/null || true
grep -hEq 'connect\(|openat\(|execve\(' "$tmp"/trace*
inspect_id="$(docker inspect -f '{{.Id}}' "$hermes")"
[[ -n "$inspect_id" && -n "$pids" && "$slow_status" == completed ]]

# Egress topology spike: internal-network clients have no direct external route.
if client --max-time 5 -fsS https://api.openai.com >/dev/null 2>&1; then
  echo "FAIL: internal doc network reached internet directly" >&2
  exit 1
fi
cat > "$tmp/doc-egress.conf" <<'CONF'
http_port 3128
cache_effective_user proxy
acl allowed_provider dstdomain api.openai.com
acl SSL_ports port 443
acl CONNECT method CONNECT
http_access deny !CONNECT
http_access deny CONNECT !SSL_ports
http_access allow CONNECT allowed_provider
http_access deny all
cache deny all
access_log daemon:/var/log/squid/access.log
cache_log /var/log/squid/cache.log
CONF
proxy_image="$(docker build -q -f docker/Dockerfile.pr-safety-egress .)"
images+=("$proxy_image")
proxy="$(docker run -d --read-only --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /run:rw,noexec,nosuid,nodev,mode=1777 \
  --tmpfs /var/log/squid:rw,noexec,nosuid,nodev,mode=1777 \
  --tmpfs /var/spool/squid:rw,noexec,nosuid,nodev,mode=1777 \
  -v "$tmp/doc-egress.conf:/etc/squid/squid.conf:ro" "$proxy_image")"
containers+=("$proxy")
docker network connect --alias hermes-doc-egress "$network" "$proxy"
for _ in $(seq 1 20); do docker exec "$proxy" squid -k parse >/dev/null 2>&1 && break; sleep 1; done
docker exec "$proxy" squid -k parse >/dev/null 2>&1
proxy_ready=false
for _ in $(seq 1 20); do
  if client --max-time 10 -sS -x http://hermes-doc-egress:3128 https://api.openai.com/v1/models >/dev/null 2>&1; then
    proxy_ready=true; break
  fi
  sleep 1
done
[[ "$proxy_ready" == true ]]
if client --max-time 10 -sS -x http://hermes-doc-egress:3128 https://example.com >/dev/null 2>&1; then
  echo "FAIL: doc egress proxy allowed off-list host" >&2
  exit 1
fi

echo "PASS: renameat2, zero-tool runtime, durable idempotency, trace attachment, and egress spikes"
