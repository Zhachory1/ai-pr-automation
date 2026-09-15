#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

tmp="$(mktemp -d)"
image=""
cleanup() {
  [[ -z "$image" ]] || docker image rm "$image" >/dev/null 2>&1 || true
  rm -rf "$tmp"
}
trap cleanup EXIT
mkdir -p "$tmp/stage" "$tmp/inbox"
image="$(docker build -q -f Dockerfile.doc-writer .)"

publication() {
  docker run --rm -i --entrypoint /app/bin/doc-writer-publication \
    -v "$tmp/stage:/stage" -v "$tmp/inbox:/inbox" "$image" "$@"
}

content='---
human_reviewed: true
---

# Exact document
'
staged="$(printf '%s' "$content" | publication stage --stage-root /stage --request-id 42)"
digest="$(jq -r .content_digest <<<"$staged")"
[[ "$(jq -r .staged_path <<<"$staged")" == requests/42/publish.md ]]
[[ -f "$tmp/stage/requests/42/publish.md" ]]
[[ "$(cat "$tmp/stage/requests/42/publish.md")" == "${content%$'\n'}" ]]

# Same bytes recover idempotently; different bytes never replace staged approval content.
printf '%s' "$content" | publication stage --stage-root /stage --request-id 42 >/dev/null
if printf different | publication stage --stage-root /stage --request-id 42 >/dev/null 2>&1; then
  echo "FAIL: stage replaced existing bytes" >&2
  exit 1
fi

published="$(publication publish --stage-root /stage --inbox-root /inbox \
  --staged-path requests/42/publish.md --target-path dd-2026-09-14-exact.md --digest "$digest")"
[[ "$(jq -r .status <<<"$published")" == published ]]
[[ -f "$tmp/inbox/dd-2026-09-14-exact.md" ]]
[[ ! -e "$tmp/inbox/.ai-pr-automation-staging/request-42-$digest.tmp" ]]
[[ "$(publication inspect --inbox-root /inbox --target-path dd-2026-09-14-exact.md --digest "$digest" | jq -r .status)" == matching ]]
# Reconcile of an already-matching target performs no rewrite.
[[ "$(publication publish --stage-root /stage --inbox-root /inbox \
  --staged-path requests/42/publish.md --target-path dd-2026-09-14-exact.md --digest "$digest" | jq -r .status)" == matching ]]

rmdir "$tmp/inbox/.ai-pr-automation-staging"
mkdir "$tmp/outside"
ln -s "$tmp/outside" "$tmp/inbox/.ai-pr-automation-staging"
if publication publish --stage-root /stage --inbox-root /inbox \
  --staged-path requests/42/publish.md --target-path dd-2026-09-14-hidden-symlink.md --digest "$digest" >/dev/null 2>&1; then
  echo "FAIL: publish followed hidden staging symlink" >&2
  exit 1
fi
[[ -z "$(ls -A "$tmp/outside")" ]]
rm "$tmp/inbox/.ai-pr-automation-staging"

printf original > "$tmp/inbox/seprd-2026-09-14-existing.md"
if publication publish --stage-root /stage --inbox-root /inbox \
  --staged-path requests/42/publish.md --target-path seprd-2026-09-14-existing.md --digest "$digest" >/dev/null 2>&1; then
  echo "FAIL: publish overwrote mismatching target" >&2
  exit 1
fi
[[ "$(cat "$tmp/inbox/seprd-2026-09-14-existing.md")" == original ]]

ln -s /etc/passwd "$tmp/inbox/dd-2026-09-14-symlink.md"
if publication inspect --inbox-root /inbox --target-path dd-2026-09-14-symlink.md --digest "$digest" >/dev/null 2>&1; then
  echo "FAIL: inspect followed target symlink" >&2
  exit 1
fi
if publication publish --stage-root /stage --inbox-root /inbox \
  --staged-path ../escape --target-path dd-2026-09-14-escape.md --digest "$digest" >/dev/null 2>&1; then
  echo "FAIL: publish accepted traversal" >&2
  exit 1
fi
if publication inspect --inbox-root /inbox --target-path ../escape.md --digest "$digest" >/dev/null 2>&1; then
  echo "FAIL: inspect accepted traversal" >&2
  exit 1
fi

# Content cap fails before durable stage write.
if python3 - <<'PY' | publication stage --stage-root /stage --request-id 43 >/dev/null 2>&1
import sys
sys.stdout.write("x" * (256 * 1024 + 1))
PY
then
  echo "FAIL: stage accepted oversized content" >&2
  exit 1
fi
[[ ! -e "$tmp/stage/requests/43/publish.md" ]]

echo "PASS: doc publication stages exact bytes and publishes atomically without overwrite"
