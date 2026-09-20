#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

tmp="$(mktemp -d)"; container="fleet-controller-auth-test-$$"; rollback_container="fleet-controller-rollback-test-$$"; attacker=""
rollback_image="agent-fleet/status:pre-auth-b7fe7ed-test"
cleanup() {
  [[ -z "$attacker" ]] || kill "$attacker" >/dev/null 2>&1 || true
  docker rm -f "$container" "$rollback_container" >/dev/null 2>&1 || true
  docker image rm "$rollback_image" >/dev/null 2>&1 || true
  rm -rf "$tmp"
}
trap cleanup EXIT
mkdir -m 700 "$tmp/secrets" "$tmp/code"
printf 'test-controller-password\n' > "$tmp/secrets/password"
printf '0123456789abcdef0123456789abcdef\n' > "$tmp/secrets/session"
openssl genrsa -out "$tmp/secrets/ca.key" 2048 >/dev/null 2>&1
openssl req -x509 -new -sha256 -days 2 -key "$tmp/secrets/ca.key" \
  -out "$tmp/secrets/ca.crt" -subj '/CN=Fleet Controller Test CA' \
  -addext 'basicConstraints=critical,CA:TRUE,pathlen:0' \
  -addext 'keyUsage=critical,keyCertSign,cRLSign' >/dev/null 2>&1
openssl req -new -newkey rsa:2048 -nodes -keyout "$tmp/secrets/controller.key" \
  -out "$tmp/controller.csr" -subj '/CN=localhost' >/dev/null 2>&1
cat > "$tmp/leaf.ext" <<'EOF'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:localhost,IP:127.0.0.1
EOF
openssl x509 -req -sha256 -days 2 -in "$tmp/controller.csr" -CA "$tmp/secrets/ca.crt" \
  -CAkey "$tmp/secrets/ca.key" -CAcreateserial -out "$tmp/secrets/controller.crt" \
  -extfile "$tmp/leaf.ext" >/dev/null 2>&1
chmod 600 "$tmp/secrets/password" "$tmp/secrets/session" "$tmp/secrets/controller.key"
chmod 644 "$tmp/secrets/ca.crt" "$tmp/secrets/controller.crt"
rm -f "$tmp/secrets/ca.key" "$tmp/secrets/ca.srl"
mkdir "$tmp/project"
cp docker-compose.yml "$tmp/project/"
cp .env.example "$tmp/test.env"
cat >> "$tmp/test.env" <<EOF
CODE_ROOT=$tmp/code
FLEET_CONTROLLER_PASSWORD_FILE=$tmp/secrets/password
FLEET_CONTROLLER_SESSION_SECRET_FILE=$tmp/secrets/session
FLEET_CONTROLLER_TLS_CA_CERT_FILE=$tmp/secrets/ca.crt
FLEET_CONTROLLER_TLS_CERT_FILE=$tmp/secrets/controller.crt
FLEET_CONTROLLER_TLS_KEY_FILE=$tmp/secrets/controller.key
EOF
cp "$tmp/test.env" "$tmp/project/.env"
scripts/validate-fleet-controller-secrets.py --env-file "$tmp/test.env" --repo "$tmp/project" >/dev/null
if CODE_ROOT="$tmp/code" SWARMVAULT_VAULT="$tmp/secrets" \
  scripts/compose.sh -f "$tmp/project/docker-compose.yml" up status >/dev/null 2>&1; then
  echo 'FAIL: startup wrapper accepted Compose file override' >&2; exit 1
fi
if COMPOSE_FILE="$tmp/project/docker-compose.yml" \
  CODE_ROOT="$tmp/code" SWARMVAULT_VAULT="$tmp/secrets" scripts/compose.sh up status >/dev/null 2>&1; then
  echo 'FAIL: startup wrapper accepted COMPOSE_FILE override' >&2; exit 1
fi

chmod 644 "$tmp/secrets/password"
if scripts/validate-fleet-controller-secrets.py --env-file "$tmp/test.env" --repo "$tmp/project" >/dev/null 2>&1; then
  echo 'FAIL: preflight accepted loose password permissions' >&2; exit 1
fi
chmod 600 "$tmp/secrets/password"
printf 'short\n' > "$tmp/secrets/password"
if scripts/validate-fleet-controller-secrets.py --env-file "$tmp/test.env" --repo "$tmp/project" >/dev/null 2>&1; then
  echo 'FAIL: preflight accepted short password' >&2; exit 1
fi
printf 'test-controller-password\n' > "$tmp/secrets/password"
ln -s "$tmp/secrets/password" "$tmp/secrets/password-link"
sed "s#^FLEET_CONTROLLER_PASSWORD_FILE=.*#FLEET_CONTROLLER_PASSWORD_FILE=$tmp/secrets/password-link#" \
  "$tmp/test.env" > "$tmp/symlink.env"
if scripts/validate-fleet-controller-secrets.py --env-file "$tmp/symlink.env" --repo "$tmp/project" >/dev/null 2>&1; then
  echo 'FAIL: preflight accepted symlink secret' >&2; exit 1
fi
sed "s#^FLEET_CONTROLLER_TLS_CA_CERT_FILE=.*#FLEET_CONTROLLER_TLS_CA_CERT_FILE=$tmp/secrets/controller.crt#" \
  "$tmp/test.env" > "$tmp/leaf-as-ca.env"
if scripts/validate-fleet-controller-secrets.py --env-file "$tmp/leaf-as-ca.env" --repo "$tmp/project" >/dev/null 2>&1; then
  echo 'FAIL: preflight accepted leaf as CA' >&2; exit 1
fi
cat "$tmp/secrets/controller.crt" "$tmp/secrets/ca.crt" > "$tmp/secrets/certificate-bundle.crt"
sed "s#^FLEET_CONTROLLER_TLS_CERT_FILE=.*#FLEET_CONTROLLER_TLS_CERT_FILE=$tmp/secrets/certificate-bundle.crt#" \
  "$tmp/test.env" > "$tmp/certificate-bundle.env"
if scripts/validate-fleet-controller-secrets.py --env-file "$tmp/certificate-bundle.env" --repo "$tmp/project" >/dev/null 2>&1; then
  echo 'FAIL: preflight accepted certificate bundle' >&2; exit 1
fi
cp docker-compose.yml docker-compose.status-rollback.yml "$tmp/"
cp .env.example "$tmp/.env"
FLEET_CONTROLLER_PASSWORD_FILE=/dev/null FLEET_CONTROLLER_SESSION_SECRET_FILE=/dev/null \
FLEET_CONTROLLER_TLS_CA_CERT_FILE=/dev/null FLEET_CONTROLLER_TLS_CERT_FILE=/dev/null \
FLEET_CONTROLLER_TLS_KEY_FILE=/dev/null \
REQUESTS_DB_PASSWORD=test CODE_ROOT="$tmp/code" SWARMVAULT_VAULT="$tmp/secrets" \
  docker compose --env-file "$tmp/.env" -f "$tmp/docker-compose.yml" \
  -f "$tmp/docker-compose.status-rollback.yml" config --format json > "$tmp/rollback.json"
jq -e '.services.status.image == "agent-fleet/status:pre-auth-b7fe7ed" and ((.services.status.secrets // []) | length == 0)' \
  "$tmp/rollback.json" >/dev/null
git archive b7fe7ed Dockerfile.status bin/status-server \
  | docker build -q -f Dockerfile.status -t "$rollback_image" - >/dev/null
rollback_port="$(python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)"
docker run -d --name "$rollback_container" -p "127.0.0.1:$rollback_port:8080" \
  -e REQUESTS_DB_HOST=127.0.0.1 -e PGPASSWORD=fake -e STATUS_BIND=0.0.0.0 \
  "$rollback_image" >/dev/null
for _ in $(seq 1 30); do
  rollback_code="$(curl -sS -o "$tmp/rollback-page" -w '%{http_code}' \
    "http://127.0.0.1:$rollback_port/" || true)"
  [[ "$rollback_code" == 200 ]] && break
  sleep 1
done
[[ "$rollback_code" == 200 ]]
grep -q 'agent-fleet' "$tmp/rollback-page"
docker rm -f "$rollback_container" >/dev/null

port="$(python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)"
docker build -q -f Dockerfile.status -t agent-fleet/status:auth-test . >/dev/null
docker run -d --name "$container" -p "127.0.0.1:$port:8080" \
  -e REQUESTS_DB_HOST=127.0.0.1 -e PGPASSWORD=fake -e STATUS_BIND=0.0.0.0 \
  -e FLEET_CONTROLLER_PUBLIC_PORT="$port" -e FLEET_CONTROLLER_USERNAME=fleet \
  -e FLEET_CONTROLLER_PASSWORD_FILE=/run/secrets/password \
  -e FLEET_CONTROLLER_SESSION_SECRET_FILE=/run/secrets/session \
  -e FLEET_CONTROLLER_TLS_CERT_FILE=/run/secrets/controller.crt \
  -e FLEET_CONTROLLER_TLS_KEY_FILE=/run/secrets/controller.key \
  -v "$tmp/secrets/password:/run/secrets/password:ro" \
  -v "$tmp/secrets/session:/run/secrets/session:ro" \
  -v "$tmp/secrets/controller.crt:/run/secrets/controller.crt:ro" \
  -v "$tmp/secrets/controller.key:/run/secrets/controller.key:ro" \
  agent-fleet/status:auth-test >/dev/null
for _ in $(seq 1 30); do
  code="$(curl --cacert "$tmp/secrets/ca.crt" -sS -o /dev/null -w '%{http_code}' \
    "https://127.0.0.1:$port/login" || true)"
  [[ "$code" == 200 ]] && break
  sleep 1
done
[[ "$code" == 200 ]]
python3 - "$port" "$tmp/secrets/ca.crt" "$tmp/request-ready" "$tmp/request-closed" <<'PY' &
import pathlib, socket, ssl, sys, time
port = int(sys.argv[1])
context = ssl.create_default_context(cafile=sys.argv[2])
raw = socket.create_connection(("127.0.0.1", port))
sock = context.wrap_socket(raw, server_hostname="localhost")
headers = (f"POST /login HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
           f"Origin: https://127.0.0.1:{port}\r\nContent-Length: 20\r\n\r\n")
sock.sendall(headers.encode())
pathlib.Path(sys.argv[3]).touch()
closed = False
for _ in range(20):
    try:
        sock.sendall(b"x")
    except OSError:
        closed = True
        break
    time.sleep(1)
if closed:
    pathlib.Path(sys.argv[4]).touch()
sock.close()
PY
attacker="$!"
for _ in $(seq 1 30); do [[ -e "$tmp/request-ready" ]] && break; sleep 0.1; done
wait "$attacker"; attacker=""
[[ -e "$tmp/request-closed" ]] || { echo 'FAIL: slow request exceeded absolute deadline' >&2; exit 1; }
[[ "$(curl --cacert "$tmp/secrets/ca.crt" -sS -o /dev/null -w '%{http_code}' \
  "https://127.0.0.1:$port/")" == 303 ]]
curl --cacert "$tmp/secrets/ca.crt" -sS -D "$tmp/login.headers" -o /dev/null \
  -c "$tmp/cookies" -H "Origin: https://127.0.0.1:$port" \
  --data 'username=fleet&password=test-controller-password' "https://127.0.0.1:$port/login"
grep -qi '^set-cookie: __Host-fleet_controller_session=.*Secure.*HttpOnly.*SameSite=Strict' "$tmp/login.headers"
curl --cacert "$tmp/secrets/ca.crt" -fsS -b "$tmp/cookies" \
  "https://127.0.0.1:$port/" > "$tmp/page"
token="$(grep -o 'name=token value="[^"]*"' "$tmp/page" | head -1 | cut -d'"' -f2)"
[[ -n "$token" ]]
[[ "$(curl --cacert "$tmp/secrets/ca.crt" -sS -o /dev/null -w '%{http_code}' \
  -b "$tmp/cookies" -H "Origin: https://127.0.0.1:$port" --data-urlencode "token=$token" \
  "https://127.0.0.1:$port/logout")" == 303 ]]
logs="$(docker logs "$container" 2>&1)"
[[ "$logs" != *test-controller-password* && "$logs" != *0123456789abcdef0123456789abcdef* ]]
echo 'PASS: Fleet Controller HTTPS, auth, secret preflight, and logout'
