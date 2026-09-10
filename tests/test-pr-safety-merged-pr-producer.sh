#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
CID="pr-safety-merged-pr-producer-test-$$"
PORT="$(python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)"
TMP="$(mktemp -d)"
cleanup() { docker rm -f "$CID" >/dev/null 2>&1 || true; chmod -R u+w "$TMP" 2>/dev/null || true; rm -rf "$TMP"; }
trap cleanup EXIT
docker run --rm -d --name "$CID" -e POSTGRES_PASSWORD=t -e POSTGRES_DB=fleet -p "$PORT:5432" postgres:16 >/dev/null
for _ in $(seq 1 30); do docker exec "$CID" pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
for f in docker/initdb/{01-schema,02-agent-server,03-human-review-queue,04-pr-safety-review,05-pr-safety-merged-pr-producer}.sql; do docker cp "$f" "$CID:/tmp/${f##*/}"; docker exec "$CID" psql -U postgres -d fleet -q -v ON_ERROR_STOP=1 -f "/tmp/${f##*/}" >/dev/null; done

# Local source repo with two commits: base (parent) and head (merge commit stand-in).
SOURCE="$TMP/source"; mkdir -p "$SOURCE"
git -C "$SOURCE" init -q; git -C "$SOURCE" config user.email t@e.com; git -C "$SOURCE" config user.name t
printf 'base\n' > "$SOURCE/x"; git -C "$SOURCE" add x; git -C "$SOURCE" commit -qm base
BASE="$(git -C "$SOURCE" rev-parse HEAD)"
printf 'head\n' >> "$SOURCE/x"; git -C "$SOURCE" commit -am head -q
MERGE="$(git -C "$SOURCE" rev-parse HEAD)"
printf 'head2\n' >> "$SOURCE/x"; git -C "$SOURCE" commit -am head2 -q
MERGE2="$(git -C "$SOURCE" rev-parse HEAD)"

POLICY_ROOT="$TMP/policies"; mkdir -p "$POLICY_ROOT"; printf 'pinned policy\n' > "$POLICY_ROOT/policy.md"
export REQUESTS_DB_USER=postgres REQUESTS_DB_NAME=fleet REQUESTS_DB_HOST=localhost REQUESTS_DB_PORT="$PORT" PGPASSWORD=t
export PR_SAFETY_MERGED_PR_AUTHORS=roktfleet
export PR_SAFETY_SNAPSHOT_ROOT="$TMP/snapshots" PR_SAFETY_POLICY_ROOT="$POLICY_ROOT" PR_SAFETY_POLICY_PATH="$POLICY_ROOT/policy.md" PR_SAFETY_POLICY_VERSION=v1
export PR_SAFETY_POLICY_DIGEST="$(shasum -a 256 "$POLICY_ROOT/policy.md" | awk '{print $1}')"
mkdir -p "$TMP/bin"
# fake gh: `search prs` returns configured records; `pr view` resolves merge/base; `repo clone` from SOURCE.
cat > "$TMP/bin/gh" <<SH
#!/usr/bin/env bash
set -euo pipefail
case "\$1 \$2" in
  "search prs")
    [[ " \$* " == *" --sort updated "* && " \$* " == *" --order desc "* ]] || exit 2
    cat "\$PR_SAFETY_TEST_SEARCH_JSON"
    ;;
  "pr view")    cat "\$PR_SAFETY_TEST_VIEW_JSON" ;;
  "repo clone") git clone --no-checkout "$SOURCE" "\$4" >/dev/null 2>&1 ;;
  *) exit 2 ;;
esac
SH
chmod +x "$TMP/bin/gh"
export PATH="$TMP/bin:$PATH"
chmod +x bin/pr-safety-merged-pr-producer
q() { docker exec "$CID" psql -U postgres -d fleet -tAc "$1"; }
fail=0; check() { if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1" >&2; fail=1; fi; }

# search yields one merged PR; view resolves it to MERGE/BASE
printf '[{"number":7,"repository":{"nameWithOwner":"owner/repo"}}]\n' > "$TMP/search.json"
printf '{"number":7,"state":"MERGED","mergeCommit":{"oid":"%s"},"baseRefOid":"%s","mergedAt":"2026-01-01T00:00:00Z"}\n' "$MERGE" "$BASE" > "$TMP/view.json"
export PR_SAFETY_TEST_SEARCH_JSON="$TMP/search.json" PR_SAFETY_TEST_VIEW_JSON="$TMP/view.json"

bin/pr-safety-merged-pr-producer
check "merged PR enqueues complete immutable job" "q \"SELECT count(*) FROM requests WHERE kind='pr-safety-review' AND payload->>'repo'='owner/repo' AND payload->>'head_sha'='$MERGE' AND payload->>'base_sha'='$BASE' AND payload->>'policy_digest'='$PR_SAFETY_POLICY_DIGEST' AND payload->>'snapshot_path' LIKE '%/snapshots/%';\" | grep -qx 1"
check "snapshot is clean and read-only" "snapshot=\$(q \"SELECT payload->>'snapshot_path' FROM requests WHERE kind='pr-safety-review' LIMIT 1;\"); [[ -z \"\$(git -C \"\$snapshot\" status --porcelain --untracked-files=all)\" ]] && [[ ! -w \"\$snapshot/x\" ]]"
check "merge event ledgered with digest" "q \"SELECT count(*) FROM pr_safety_merged_pr_events WHERE merge_sha='$MERGE' AND payload_digest ~ '^[0-9a-f]{64}\$';\" | grep -qx 1"

# same merge again: dedupes, no second row
bin/pr-safety-merged-pr-producer
check "duplicate merge sha queues once" "q \"SELECT count(*) FROM requests WHERE kind='pr-safety-review';\" | grep -qx 1 && q \"SELECT count(*) FROM pr_safety_merged_pr_events;\" | grep -qx 1"

# a NEW merge commit (advanced head) is a distinct event -> a second job
printf '[{"number":7,"repository":{"nameWithOwner":"owner/repo"}}]\n' > "$TMP/search.json"
printf '{"number":7,"state":"MERGED","mergeCommit":{"oid":"%s"},"baseRefOid":"%s","mergedAt":"2026-01-02T00:00:00Z"}\n' "$MERGE2" "$BASE" > "$TMP/view.json"
bin/pr-safety-merged-pr-producer
check "new merge commit creates fresh job" "q \"SELECT count(*) FROM requests WHERE kind='pr-safety-review';\" | grep -qx 2 && q \"SELECT count(*) FROM pr_safety_merged_pr_events;\" | grep -qx 2"
check "new head supersedes the older queued head for the same PR" "q \"SELECT status FROM requests WHERE kind='pr-safety-review' AND dedupe_key='owner/repo#7@$MERGE';\" | grep -qx superseded && q \"SELECT status FROM requests WHERE kind='pr-safety-review' AND dedupe_key='owner/repo#7@$MERGE2';\" | grep -qx queued"

# non-merged PR (state OPEN) is ignored
printf '[{"number":8,"repository":{"nameWithOwner":"owner/repo"}}]\n' > "$TMP/search.json"
printf '{"number":8,"state":"OPEN","mergeCommit":null,"baseRefOid":"%s","mergedAt":null}\n' "$BASE" > "$TMP/view.json"
bin/pr-safety-merged-pr-producer
check "non-merged PR is ignored" "q \"SELECT count(*) FROM requests WHERE kind='pr-safety-review';\" | grep -qx 2 && q \"SELECT count(*) FROM pr_safety_merged_pr_events;\" | grep -qx 2"

# GC: with two snapshots both referenced by QUEUED reviews, KEEP=1 must still keep both (in-flight).
printf '[]\n' > "$TMP/search.json"  # no new work this run
# force both reviews back to queued to isolate GC's in-flight guard from the supersede behavior
q "UPDATE requests SET status='queued', finished_at=NULL WHERE kind='pr-safety-review';" >/dev/null
PR_SAFETY_SNAPSHOT_KEEP=1 bin/pr-safety-merged-pr-producer
check "GC never deletes a snapshot with a queued review" "ls -d \"\$PR_SAFETY_SNAPSHOT_ROOT\"/pr-safety-* 2>/dev/null | wc -l | tr -d ' ' | grep -qx 2"

# GC: mark the OLDER snapshot's review done; now KEEP=1 removes it (terminal + beyond keep), keeps newest.
OLDOP="pr-safety-$(printf '%s' 'owner/repo#7@'"$MERGE" | { command -v sha256sum >/dev/null 2>&1 && sha256sum || shasum -a 256; } | awk '{print $1}')"
q "UPDATE requests SET status='done' WHERE kind='pr-safety-review' AND payload->>'operation_id'='$OLDOP';" >/dev/null
# ensure the newest (MERGE2) snapshot is newer by mtime than the older one
touch "$PR_SAFETY_SNAPSHOT_ROOT/pr-safety-$(printf '%s' 'owner/repo#7@'"$MERGE2" | { command -v sha256sum >/dev/null 2>&1 && sha256sum || shasum -a 256; } | awk '{print $1}')"
PR_SAFETY_SNAPSHOT_KEEP=1 bin/pr-safety-merged-pr-producer
check "GC removes terminal snapshot beyond keep, retains newest" "[[ ! -d \"\$PR_SAFETY_SNAPSHOT_ROOT/$OLDOP\" ]] && ls -d \"\$PR_SAFETY_SNAPSHOT_ROOT\"/pr-safety-* 2>/dev/null | wc -l | tr -d ' ' | grep -qx 1"

# SAML-403 in search output is a loud fatal, not a silent empty result
cat > "$TMP/bin/gh" <<'SH'
#!/usr/bin/env bash
echo "GraphQL: Resource protected by organization SAML enforcement. You must grant your Personal Access token access to this organization." >&2
exit 1
SH
chmod +x "$TMP/bin/gh"
if bin/pr-safety-merged-pr-producer >/dev/null 2>"$TMP/saml.err"; then samlrc=0; else samlrc=$?; fi
check "expired SAML is a loud fatal (exit 4), not silent" "[[ \"$samlrc\" == 4 ]] && grep -q 'SAML authorization expired' \"$TMP/saml.err\""

(( fail == 0 ))
