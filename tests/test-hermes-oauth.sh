#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"
network="hermes-oauth-test-$$-$RANDOM"
containers=()
image="hermes-oauth-egress-test:$$-$RANDOM"
cleanup() {
  ((${#containers[@]} == 0)) || docker rm -f "${containers[@]}" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  docker image rm "$image" >/dev/null 2>&1 || true
  rm -rf "$tmp"
}
trap cleanup EXIT

docker compose --profile hermes-oauth config --format json > "$tmp/compose.json"
jq -e '
  .services["hermes-doc-auth"] as $a |
  .services["hermes-openai-oauth-egress"] as $e |
  ($a.profiles == ["hermes-oauth"]) and
  ($a.image == "nousresearch/hermes-agent@sha256:6d7285e1476d0661fc347e3d55245c99decb781d76d39d67582166d2c9561874") and
  ($a.networks | keys == ["hermes-oauth"]) and
  ($a.ports == null) and
  ($a.environment | keys == ["HERMES_IGNORE_RULES","HERMES_SAFE_MODE","HTTPS_PROXY","HTTP_PROXY","NO_PROXY"]) and
  ($a.volumes | length == 1) and
  ($a.volumes[0].type == "volume" and $a.volumes[0].source == "hermes_doc_state" and $a.volumes[0].target == "/opt/data") and
  ($a.depends_on["hermes-openai-oauth-egress"].condition == "service_healthy") and
  ($e.networks | keys == ["default","hermes-oauth"]) and
  ($e.read_only == true) and ($e.cap_drop == ["ALL"]) and
  ([.services | to_entries[] | select(.key != "hermes-doc-auth") |
    ((.value.depends_on // {}) | has("hermes-doc-auth"))] | any | not) and
  (.networks["hermes-oauth"].internal == true)
' "$tmp/compose.json" >/dev/null

grep -Fxq 'acl allowed_oauth dstdomain auth.openai.com' docker/hermes-openai-oauth-egress.conf
grep -Fxq 'acl allowed_provider dstdomain api.openai.com auth.openai.com chatgpt.com' docker/hermes-doc-egress.conf
if scripts/hermes-oauth.sh login anthropic >/dev/null 2>&1; then
  echo "FAIL: unsupported provider accepted" >&2; exit 1
fi
if scripts/hermes-oauth.sh unknown openai-codex >/dev/null 2>&1; then
  echo "FAIL: unsupported action accepted" >&2; exit 1
fi

mkdir "$tmp/bin"
cat > "$tmp/bin/docker" <<'SH'
#!/usr/bin/env bash
echo docker-stub
SH
chmod +x "$tmp/bin/docker"
lock="$tmp/lifecycle.lock"
HERMES_OAUTH_LOCK_FILE="$lock" scripts/hermes-lifecycle-lock.py sleep 30 &
holder=$!
sleep 1
if HERMES_OAUTH_LOCK_FILE="$lock" PATH="$tmp/bin:$PATH" scripts/compose.sh up hermes-doc >/dev/null 2>&1; then
  echo "FAIL: Compose start ignored live OAuth lock" >&2; exit 1
fi
if HERMES_OAUTH_LOCK_FILE="$lock" PATH="$tmp/bin:$PATH" scripts/compose.sh run hermes-doc >/dev/null 2>&1; then
  echo "FAIL: Compose run ignored live OAuth lock" >&2; exit 1
fi
if HERMES_OAUTH_LOCK_FILE="$lock" PATH="$tmp/bin:$PATH" scripts/hermes-oauth.sh start >/dev/null 2>&1; then
  echo "FAIL: concurrent OAuth lifecycle accepted" >&2; exit 1
fi
child="$(pgrep -P "$holder")"
kill -KILL "$holder"
wait "$holder" 2>/dev/null || true
if HERMES_OAUTH_LOCK_FILE="$lock" scripts/hermes-lifecycle-lock.py true >/dev/null 2>&1; then
  echo "FAIL: supervisor death released child-held lock" >&2; exit 1
fi
kill -TERM "$child" >/dev/null 2>&1 || true
for _ in $(seq 1 20); do
  HERMES_OAUTH_LOCK_FILE="$lock" scripts/hermes-lifecycle-lock.py true >/dev/null 2>&1 && break
  sleep 0.1
done
HERMES_OAUTH_LOCK_FILE="$lock" scripts/hermes-lifecycle-lock.py true

grep -Fq 'auth add ' scripts/hermes-oauth.sh
grep -Fq -- '--type oauth --no-browser' scripts/hermes-oauth.sh
grep -Fq 'auth logout ' scripts/hermes-oauth.sh

# Exercise both proxy policies against controlled TLS/DNS; no real provider traffic.
openssl req -x509 -newkey rsa:2048 -nodes -keyout "$tmp/ca.key" -out "$tmp/ca.crt" \
  -subj '/CN=OAuth Test CA' -days 1 -addext 'basicConstraints=critical,CA:TRUE' \
  -addext 'keyUsage=critical,keyCertSign,cRLSign' >/dev/null 2>&1
openssl req -newkey rsa:2048 -nodes -keyout "$tmp/server.key" -out "$tmp/server.csr" \
  -subj '/CN=auth.openai.com' >/dev/null 2>&1
cat > "$tmp/server.ext" <<'EOF'
subjectAltName=DNS:auth.openai.com,DNS:chatgpt.com,DNS:example.com
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
EOF
openssl x509 -req -in "$tmp/server.csr" -CA "$tmp/ca.crt" -CAkey "$tmp/ca.key" -CAcreateserial \
  -out "$tmp/server.crt" -days 1 -extfile "$tmp/server.ext" >/dev/null 2>&1
docker network create --internal "$network" >/dev/null
provider_container="$(docker run -d --network "$network" -v "$PWD/tests/fake-hermes-provider.py:/app/server.py:ro" \
  -v "$tmp:/cert:ro" -e TLS_CERT=/cert/server.crt -e TLS_KEY=/cert/server.key \
  python:3.11-alpine@sha256:0d55920083f1ce1e38ac292e2772f924b4f8bb4188d336c79bf66963039e6146 \
  sh -ec 'PORT=443 python /app/server.py & PORT=8443 exec python /app/server.py')"
containers+=("$provider_container")
provider_ip="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$provider_container")"
docker build -q -t "$image" -f docker/Dockerfile.hermes-doc-egress . >/dev/null
start_proxy() {
  local alias="$1" config="$2"
  local id
  id="$(docker run -d --read-only --cap-drop ALL --security-opt no-new-privileges \
    --add-host "auth.openai.com:$provider_ip" --add-host "chatgpt.com:$provider_ip" --add-host "example.com:$provider_ip" \
    --tmpfs /run:rw,noexec,nosuid,nodev,mode=1777 \
    --tmpfs /var/log/squid:rw,noexec,nosuid,nodev,mode=1777 \
    --tmpfs /var/spool/squid:rw,noexec,nosuid,nodev,mode=1777 \
    -v "$PWD/$config:/etc/squid/squid.conf:ro" "$image")"
  containers+=("$id")
  docker network connect --alias "$alias" "$network" "$id"
  for _ in $(seq 1 20); do
    docker exec "$id" sh -ec "grep -q ':0C38 ' /proc/net/tcp /proc/net/tcp6" >/dev/null 2>&1 && break
    sleep 1
  done
}
start_proxy oauth-proxy docker/hermes-openai-oauth-egress.conf
start_proxy runtime-proxy docker/hermes-doc-egress.conf
client() {
  docker run --rm --network "$network" -v "$tmp/ca.crt:/ca.crt:ro" \
    curlimages/curl:8.11.1@sha256:c1fe1679c34d9784c1b0d1e5f62ac0a79fca01fb6377cdd33e90473c6f9f9a69 "$@"
}
client -fsS --cacert /ca.crt -x http://oauth-proxy:3128 https://auth.openai.com/v1/models >/dev/null
client -fsS --cacert /ca.crt -x http://runtime-proxy:3128 https://auth.openai.com/v1/models >/dev/null
client -fsS --cacert /ca.crt -x http://runtime-proxy:3128 https://chatgpt.com/v1/models >/dev/null
if client --max-time 5 -fsS https://auth.openai.com/v1/models >/dev/null 2>&1; then
  echo "FAIL: auth network has direct provider route" >&2; exit 1
fi
if client --max-time 5 -fsS -x http://oauth-proxy:3128 https://example.com >/dev/null 2>&1; then
  echo "FAIL: auth proxy allowed unrelated host" >&2; exit 1
fi
if client --max-time 5 -fsS -x http://oauth-proxy:3128 https://auth.openai.com:8443 >/dev/null 2>&1; then
  echo "FAIL: auth proxy allowed non-TLS port" >&2; exit 1
fi

echo "PASS: OpenAI OAuth helper is isolated, provider-fixed, lifecycle-locked, and proxy-scoped"
