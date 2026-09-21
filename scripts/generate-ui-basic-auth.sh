#!/usr/bin/env bash
# Build nginx htpasswd entry from an existing owner-only plaintext password file. Refuses overwrite.
set -euo pipefail
password_file="${1:?usage: $0 PASSWORD_FILE OUTPUT_FILE [USERNAME]}"
out="${2:?usage: $0 PASSWORD_FILE OUTPUT_FILE [USERNAME]}"
user="${3:-fleet}"
[[ -f "$password_file" && ! -L "$password_file" ]] || { echo 'password file must be a regular file' >&2; exit 2; }
[[ ! -e "$out" ]] || { echo "refusing to overwrite: $out" >&2; exit 2; }
[[ "$user" =~ ^[A-Za-z0-9._-]+$ ]] || { echo 'invalid username' >&2; exit 2; }
install -d -m 0700 "$(dirname "$out")"
command -v htpasswd >/dev/null 2>&1 || { echo 'htpasswd is required (Apache httpd tools)' >&2; exit 2; }
umask 077; htpasswd -niB "$user" < "$password_file" > "$out"; chmod 0600 "$out"
echo "generated nginx Basic Auth file: $out (username: $user)"
