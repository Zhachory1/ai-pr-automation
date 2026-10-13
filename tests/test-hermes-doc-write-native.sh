#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/stage" "$tmp/inbox"

printf 'final bytes' | bin/doc-writer-publication stage --stage-root "$tmp/stage" --request-id 42 --name publish.md >/dev/null
printf original > "$tmp/exact"
if printf changed | bin/doc-writer-publication stage --stage-root "$tmp/stage" --request-id 42 --name publish.md >/dev/null 2>&1; then
  echo 'FAIL: changed staged bytes replaced immutable stage' >&2; exit 1
fi
mkdir "$tmp/symlink-stage"; ln -s "$tmp/stage/requests" "$tmp/symlink-stage/requests"
if bin/doc-writer-publication read --stage-root "$tmp/symlink-stage" --staged-path requests/42/publish.md >/dev/null 2>&1; then
  echo 'FAIL: symlinked stage hierarchy accepted' >&2; exit 1
fi
printf keep > "$tmp/inbox/dd-2026-09-21-existing-9.md"
digest="$(printf 'final bytes' | shasum -a 256 | awk '{print $1}')"
if bin/doc-writer-publication publish --stage-root "$tmp/stage" --inbox-root "$tmp/inbox" \
  --staged-path requests/42/publish.md --target-path dd-2026-09-21-existing-9.md --digest "$digest" >/dev/null 2>&1; then
  echo 'FAIL: existing target accepted' >&2; exit 1
fi
[[ "$(cat "$tmp/inbox/dd-2026-09-21-existing-9.md")" == keep ]]
printf 'final bytes' > "$tmp/inbox/dd-2026-09-21-matching-9.md"
bin/doc-writer-publication publish --stage-root "$tmp/stage" --inbox-root "$tmp/inbox" \
  --staged-path requests/42/publish.md --target-path dd-2026-09-21-matching-9.md --digest "$digest" \
  | jq -e '.status=="published" and .already_exists==true' >/dev/null
ln -s "$tmp/exact" "$tmp/inbox/dd-2026-09-21-symlink-10.md"
if bin/doc-writer-publication publish --stage-root "$tmp/stage" --inbox-root "$tmp/inbox" \
  --staged-path requests/42/publish.md --target-path dd-2026-09-21-symlink-10.md --digest "$digest" >/dev/null 2>&1; then
  echo 'FAIL: symlink publication target accepted' >&2; exit 1
fi
[[ -L "$tmp/inbox/dd-2026-09-21-symlink-10.md" ]]

echo 'PASS: document publication preserves exact bytes and refuses overwrite/symlinks'
