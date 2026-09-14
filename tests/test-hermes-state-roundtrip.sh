#!/usr/bin/env bash
set -euo pipefail

tmp="$(mktemp -d)"
volumes=()
cleanup() {
  ((${#volumes[@]} == 0)) || docker volume rm -f "${volumes[@]}" >/dev/null 2>&1 || true
  rm -rf "$tmp"
}
trap cleanup EXIT
source_volume="$(docker volume create --label ai-pr-automation.hermes-test=source)"
volumes+=("$source_volume")
target_volume="$(docker volume create --label ai-pr-automation.hermes-test=target)"
volumes+=("$target_volume")

docker run --rm -v "$source_volume:/data" busybox:1.36 sh -ec '
  mkdir -p /data/sessions /data/config
  printf "%s\n" "m0-state-fixture" > /data/config/state.txt
  printf "%s\n" "session-fixture" > /data/sessions/one.jsonl
'

docker run --rm -v "$source_volume:/source:ro" -v "$tmp:/backup" busybox:1.36 \
  tar -C /source -cf /backup/state.tar .
hash="$(docker run --rm -v "$tmp:/backup:ro" busybox:1.36 \
  sha256sum /backup/state.tar | awk '{print $1}')"
printf '%s  %s\n' "$hash" state.tar > "$tmp/SHA256SUMS"
docker run --rm -v "$tmp:/backup:ro" busybox:1.36 \
  sh -ec 'cd /backup; sha256sum -c SHA256SUMS >/dev/null'

cp "$tmp/state.tar" "$tmp/original.tar"
restore() {
  docker run --rm -v "$1:/target" -v "$tmp:/backup:ro" busybox:1.36 sh -ec '
    test -z "$(find /target -mindepth 1 -print -quit)"
    cd /backup; sha256sum -c SHA256SUMS >/dev/null
    tar -C /target -xf state.tar
  '
}

docker run --rm -v "$tmp:/backup" busybox:1.36 sh -c 'printf x >> /backup/state.tar'
if restore "$target_volume" 2>/dev/null; then
  echo "FAIL: restore accepted tampered archive" >&2
  exit 1
fi
docker run --rm -v "$target_volume:/target" busybox:1.36 \
  sh -ec 'test -z "$(find /target -mindepth 1 -print -quit)"'
mv "$tmp/original.tar" "$tmp/state.tar"

docker run --rm -v "$target_volume:/target" busybox:1.36 sh -c 'printf occupied > /target/sentinel'
if restore "$target_volume" 2>/dev/null; then
  echo "FAIL: restore accepted non-empty target" >&2
  exit 1
fi
docker run --rm -v "$target_volume:/target" busybox:1.36 rm /target/sentinel
restore "$target_volume"

source_manifest="$(docker run --rm -v "$source_volume:/data:ro" busybox:1.36 sh -ec \
  'cd /data; find . -type f -print | sort | while read -r f; do sha256sum "$f"; done')"
target_manifest="$(docker run --rm -v "$target_volume:/data:ro" busybox:1.36 sh -ec \
  'cd /data; find . -type f -print | sort | while read -r f; do sha256sum "$f"; done')"
[[ "$source_manifest" == "$target_manifest" ]] || {
  echo "FAIL: restored files differ" >&2
  diff -u <(printf '%s\n' "$source_manifest") <(printf '%s\n' "$target_manifest") >&2 || true
  exit 1
}

listing="$(docker run --rm -v "$tmp:/backup:ro" busybox:1.36 tar -tf /backup/state.tar)"
printf '%s\n' "$listing" | grep -q '^\./config/state\.txt$'
printf '%s\n' "$listing" | grep -q '^\./sessions/one\.jsonl$'
if printf '%s\n' "$listing" | grep -Eq '(^|/)\.env$|/Users/|/home/'; then
  echo "FAIL: archive contains forbidden path" >&2
  exit 1
fi

echo "PASS: isolated Hermes state archive rejects tampering and restores byte-identical files"
