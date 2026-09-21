#!/usr/bin/env bash
# Generate a new local CA + leaf for the unified *.localhost UI hostnames. Refuses overwrite. The CA
# signing key is destroyed after issuing the leaf. Trust is an explicit separate operator command.
set -euo pipefail
out="${1:-$HOME/.config/ai-pr-automation-ui}"
[[ ! -e "$out" ]] || { echo "refusing to overwrite existing path: $out" >&2; exit 2; }
umask 077; mkdir -p "$out"; tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
openssl genrsa -out "$tmp/ca.key" 3072 >/dev/null 2>&1
openssl req -x509 -new -sha256 -days 3650 -key "$tmp/ca.key" -out "$out/fleet-controller-ca.crt" \
  -subj '/CN=AI PR Automation Local UI CA' \
  -addext 'basicConstraints=critical,CA:TRUE,pathlen:0' -addext 'keyUsage=critical,keyCertSign,cRLSign'
openssl req -new -newkey rsa:3072 -nodes -keyout "$out/fleet-controller.key" \
  -out "$tmp/leaf.csr" -subj '/CN=localhost' >/dev/null 2>&1
cat > "$tmp/leaf.ext" <<'EOF'
basicConstraints=critical,CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=DNS:localhost,IP:127.0.0.1,DNS:fleet.localhost,DNS:hermes.localhost,DNS:memory.localhost,DNS:code.localhost
EOF
openssl x509 -req -sha256 -days 365 -in "$tmp/leaf.csr" -CA "$out/fleet-controller-ca.crt" \
  -CAkey "$tmp/ca.key" -CAcreateserial -out "$out/fleet-controller.crt" -extfile "$tmp/leaf.ext" >/dev/null 2>&1
rm -f "$out/fleet-controller-ca.srl" "$tmp/ca.key"
chmod 600 "$out/fleet-controller.key"; chmod 644 "$out"/*.crt
cat <<EOF
Generated UI TLS material in $out (CA signing key destroyed).

Set in .env:
FLEET_CONTROLLER_TLS_CA_CERT_FILE=$out/fleet-controller-ca.crt
FLEET_CONTROLLER_TLS_CERT_FILE=$out/fleet-controller.crt
FLEET_CONTROLLER_TLS_KEY_FILE=$out/fleet-controller.key

Then trust the CA (explicit login-Keychain change):
security add-trusted-cert -r trustRoot -k "$HOME/Library/Keychains/login.keychain-db" "$out/fleet-controller-ca.crt"
EOF
