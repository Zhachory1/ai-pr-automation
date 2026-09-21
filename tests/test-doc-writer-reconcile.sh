#!/usr/bin/env bash
# Prepared publication crash recovery: operator reconciler republishes exact staged bytes (or accepts
# an already-existing matching target) and completes DB state. Different target bytes must block.
set -euo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/stage/requests/7" "$tmp/inbox"
printf 'approved bytes' > "$tmp/stage/requests/7/publish.md"
digest="$(shasum -a 256 "$tmp/stage/requests/7/publish.md" | awk '{print $1}')"
generation="hermes:$(printf x | shasum -a 256 | awk '{print $1}')"
cat > "$tmp/bin/psql" <<SH
#!/usr/bin/env bash
input="\$(cat || true)"
printf '%s\n' "\$input" >> "$tmp/sql.log"
if [[ "\$input" == *"FROM doc_publications"* && "\$input" == *"state='prepared'"* ]]; then
  jq -cn --arg d "$digest" --arg g "$generation" '{id:7,staged_path:"requests/7/publish.md",target_path:"dd-2026-09-21-recovered-7.md",content_digest:\$d,document_generation:\$g}'
elif [[ "\$input" == *"UPDATE doc_publications SET state='published'"* ]]; then echo 7
fi
SH
chmod +x "$tmp/bin/psql" bin/doc-writer-reconcile bin/doc-writer-publication
PATH="$tmp/bin:$PATH" DOC_WRITER_STAGE_DIR="$tmp/stage" DOC_WRITER_INBOX_DIR="$tmp/inbox" \
  DOC_WRITER_PUBLICATION_BIN="$PWD/bin/doc-writer-publication" REQUESTS_DB_USER=fleet PGPASSWORD=x \
  bin/doc-writer-reconcile | grep -q 'request=7 reconciled'
[[ "$(cat "$tmp/inbox/dd-2026-09-21-recovered-7.md")" == 'approved bytes' ]]
# Retry after copy-before-mark is idempotent: matching existing target succeeds again.
PATH="$tmp/bin:$PATH" DOC_WRITER_STAGE_DIR="$tmp/stage" DOC_WRITER_INBOX_DIR="$tmp/inbox" \
  DOC_WRITER_PUBLICATION_BIN="$PWD/bin/doc-writer-publication" REQUESTS_DB_USER=fleet PGPASSWORD=x \
  bin/doc-writer-reconcile | grep -q 'request=7 reconciled'
# Existing different bytes are never overwritten.
printf 'different' > "$tmp/inbox/dd-2026-09-21-recovered-7.md"
set +e
out="$(PATH="$tmp/bin:$PATH" DOC_WRITER_STAGE_DIR="$tmp/stage" DOC_WRITER_INBOX_DIR="$tmp/inbox" \
  DOC_WRITER_PUBLICATION_BIN="$PWD/bin/doc-writer-publication" REQUESTS_DB_USER=fleet PGPASSWORD=x \
  bin/doc-writer-reconcile 2>&1)"; rc=$?
set -e
[[ "$out" == *'existing target mismatch'* ]] || { echo "FAIL: mismatch not reported: $out" >&2; exit 1; }
[[ "$(cat "$tmp/inbox/dd-2026-09-21-recovered-7.md")" == different ]]
echo 'PASS: prepared publication reconciles idempotently and never overwrites mismatched target'
