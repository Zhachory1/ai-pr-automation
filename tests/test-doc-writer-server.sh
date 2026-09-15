#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

tmp="$(mktemp -d)"
cid="$(docker run -d --rm -p 127.0.0.1::5432 -e POSTGRES_PASSWORD=t -e POSTGRES_DB=fleet postgres:16)"
cleanup() { docker rm -f "$cid" >/dev/null 2>&1 || true; rm -rf "$tmp"; }
trap cleanup EXIT
for _ in $(seq 1 30); do docker exec "$cid" pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
for file in 01-schema.sql 02-agent-server.sql 03-human-review-queue.sql 04-pending-decision-approval.sql 06-hermes-doc-foundation.sql; do
  docker cp "docker/initdb/$file" "$cid:/tmp/$file"
done
docker exec "$cid" psql -U postgres -d fleet -q -v ON_ERROR_STOP=1 \
  -f /tmp/01-schema.sql -f /tmp/02-agent-server.sql -f /tmp/03-human-review-queue.sql \
  -f /tmp/04-pending-decision-approval.sql -f /tmp/06-hermes-doc-foundation.sql >/dev/null
port="$(docker port "$cid" 5432/tcp | awk -F: '{print $NF}')"
export REQUESTS_DB_USER=postgres REQUESTS_DB_NAME=fleet REQUESTS_DB_HOST=127.0.0.1 REQUESTS_DB_PORT="$port" PGPASSWORD=t
# shellcheck source=../lib/queue.sh disable=SC1091
. lib/queue.sh
q() { docker exec "$cid" psql -U postgres -d fleet -qAt -v ON_ERROR_STOP=1 -c "$1"; }

stage="$tmp/stage"; inbox="$tmp/inbox"; mkdir -p "$stage" "$inbox"
cat > "$tmp/fake-harness" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
: "${DOC_WRITER_REQUEST_ID:?}" "${DOC_WRITER_STAGE_DIR:?}"
printf '%s\n' "$DOC_WRITER_REQUEST_ID" >> "$HARNESS_CALLS"
dir="$DOC_WRITER_STAGE_DIR/requests/$DOC_WRITER_REQUEST_ID"
mkdir -p "$dir"
printf '%s\n' '---' 'human_reviewed: true' '---' '' '# Approved bytes' > "$dir/publish.md"
digest="$(python3 -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$dir/publish.md")"
jq -cn --arg d "$digest" '{status:"awaiting_approval",staged_path:"requests/'"$DOC_WRITER_REQUEST_ID"'/publish.md",
  target_path:"dd-2026-09-15-approved.md",content_digest:$d,
  document_generation:"legacy:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",council:"skipped"}'
SH
chmod +x "$tmp/fake-harness"
cat > "$tmp/fake-publication" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
[[ "$1" == publish ]]; shift
while [[ $# -gt 0 ]]; do
  case "$1" in
    --stage-root) stage="$2"; shift 2 ;;
    --inbox-root) inbox="$2"; shift 2 ;;
    --staged-path) staged="$2"; shift 2 ;;
    --target-path) target="$2"; shift 2 ;;
    --digest) digest="$2"; shift 2 ;;
    *) exit 2 ;;
  esac
done
cp "$stage/$staged" "$inbox/$target"
jq -cn --arg t "$target" --arg d "$digest" '{status:"published",target_path:$t,content_digest:$d}'
SH
chmod +x "$tmp/fake-publication"

request_id="$(q "INSERT INTO requests(kind,payload,dedupe_key) VALUES(
  'doc-write','{\"doc_type\":\"dd\",\"title\":\"Approved\",\"requirements\":\"r\"}','doc:server') RETURNING id;")"
common=(
  DOC_WRITER_ONCE=true DOC_WRITER_POLL_INTERVAL=1 DOC_WRITER_LEASE_SECONDS=120
  DOC_WRITER_LEASE_HEARTBEAT=30 DOC_WRITER_LEASE_DB_TIMEOUT=10
  DOC_WRITER_HARNESS="$tmp/fake-harness" DOC_WRITER_PUBLICATION_BIN="$tmp/fake-publication"
  DOC_WRITER_STAGE_DIR="$stage" DOC_WRITER_INBOX_DIR="$inbox" HARNESS_CALLS="$tmp/harness-calls"
  REQUESTS_DB_USER=postgres REQUESTS_DB_NAME=fleet REQUESTS_DB_HOST=127.0.0.1
  REQUESTS_DB_PORT="$port" PGPASSWORD=t
)
env "${common[@]}" bin/doc-writer-server >/dev/null
[[ "$(q "SELECT r.status||'/'||p.state||'/'||h.state FROM requests r
  JOIN doc_publications p ON p.request_id=r.id JOIN pending_maintenance_reviews h ON h.request_id=r.id
  WHERE r.id=$request_id;")" == done/awaiting_approval/pending ]]
[[ ! -e "$inbox/dd-2026-09-15-approved.md" ]]
[[ "$(wc -l < "$tmp/harness-calls" | tr -d ' ')" == 1 ]]

[[ "$(doc_publication_approve "$request_id")" == "$request_id" ]]
[[ "$(q "SELECT status||'/'||(payload->>'publication_only') FROM requests WHERE id=$request_id;")" == queued/true ]]
env "${common[@]}" bin/doc-writer-server >/dev/null
[[ "$(q "SELECT r.status||'/'||p.state||'/'||r.posted_ref FROM requests r
  JOIN doc_publications p ON p.request_id=r.id WHERE r.id=$request_id;")" == done/published/dd-2026-09-15-approved.md ]]
[[ -f "$inbox/dd-2026-09-15-approved.md" ]]
[[ "$(wc -l < "$tmp/harness-calls" | tr -d ' ')" == 1 ]]
echo "PASS: doc controller stages approval, skips model on publish, and settles exact target"
