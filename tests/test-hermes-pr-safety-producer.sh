#!/usr/bin/env bash
# hermes-pr-safety-producer: merged-PR discovery, event-ledger dedupe/snapshot-identity binding,
# stale-head supersede, and repo-authority scoping. Adapted from the retired Docker producer's test
# (git show 9ec1d3d~1:tests/test-pr-safety-merged-pr-producer.sh) for the native
# hermes_enqueue_pr_safety_event SQL path and the YAML authority allowlist.
set -euo pipefail
cd "$(dirname "$0")/.."
CID="hermes-pr-safety-producer-test-$$"
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
for f in docker/initdb/{01-schema,02-agent-server,03-human-review-queue,04-pending-decision-approval,04-pr-safety-review,05-pr-safety-merged-pr-producer,07-hermes-autonomy,09-hermes-yaml-authority,10-hermes-queue-depth,11-hermes-pr-safety}.sql; do
  docker cp "$f" "$CID:/tmp/${f##*/}"
  docker exec "$CID" psql -U postgres -d fleet -q -v ON_ERROR_STOP=1 -f "/tmp/${f##*/}" >/dev/null
done
docker exec "$CID" psql -U postgres -d fleet -q -c "CREATE ROLE hermes_runtime LOGIN PASSWORD 't'; GRANT hermes_worker TO hermes_runtime;" >/dev/null

# Local source repo with three commits: base (parent) and two successive "merge" heads.
SOURCE="$TMP/source"; mkdir -p "$SOURCE"
git -C "$SOURCE" init -q; git -C "$SOURCE" config user.email t@e.com; git -C "$SOURCE" config user.name t
printf 'base\n' > "$SOURCE/x"; git -C "$SOURCE" add x; git -C "$SOURCE" commit -qm base
BASE="$(git -C "$SOURCE" rev-parse HEAD)"
printf 'head\n' >> "$SOURCE/x"; git -C "$SOURCE" commit -am head -q
MERGE="$(git -C "$SOURCE" rev-parse HEAD)"
printf 'head2\n' >> "$SOURCE/x"; git -C "$SOURCE" commit -am head2 -q
MERGE2="$(git -C "$SOURCE" rev-parse HEAD)"

POLICY_ROOT="$TMP/policies"; mkdir -p "$POLICY_ROOT"; printf 'pinned policy\n' > "$POLICY_ROOT/policy.md"
export REQUESTS_DB_USER=hermes_runtime REQUESTS_DB_NAME=fleet REQUESTS_DB_HOST=localhost REQUESTS_DB_PORT="$PORT" PGPASSWORD=t
export PR_SAFETY_MERGED_PR_AUTHORS=roktfleet
export PR_SAFETY_SNAPSHOT_ROOT="$TMP/snapshots" PR_SAFETY_POLICY_ROOT="$POLICY_ROOT" PR_SAFETY_POLICY_PATH="$POLICY_ROOT/policy.md" PR_SAFETY_POLICY_VERSION=v1
export PR_SAFETY_POLICY_DIGEST="$(shasum -a 256 "$POLICY_ROOT/policy.md" | awk '{print $1}')"
mkdir -p "$TMP/bin"
cat > "$TMP/authority.yaml" <<'EOF'
repos:
  - owner/repo
EOF
export HERMES_AUTHORITY_FILE="$TMP/authority.yaml" HERMES_AUTHORITY_BIN="$PWD/scripts/hermes-authority.py"

# fake gh: search returns records; Pulls API resolves merge/base; repo clone uses SOURCE.
cat > "$TMP/bin/gh" <<SH
#!/usr/bin/env bash
set -euo pipefail
case "\$1 \$2" in
  "search prs")
    [[ " \$* " == *" --sort updated "* && " \$* " == *" --order desc "* ]] || exit 2
    cat "\$PR_SAFETY_TEST_SEARCH_JSON"
    ;;
  "api repos"*) cat "\$PR_SAFETY_TEST_VIEW_JSON" ;;
  "repo clone") git clone --no-checkout "$SOURCE" "\$4" >/dev/null 2>&1 ;;
  *) exit 2 ;;
esac
SH
chmod +x "$TMP/bin/gh"
export PATH="$TMP/bin:$PATH"
chmod +x bin/hermes-pr-safety-producer
q() { docker exec "$CID" psql -U postgres -d fleet -tAc "$1"; }
fail=0; check() { if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1" >&2; fail=1; fi; }

# search yields one merged PR; view resolves it to MERGE/BASE
printf '[{"number":7,"repository":{"nameWithOwner":"owner/repo"}}]\n' > "$TMP/search.json"
printf '{"number":7,"state":"closed","merge_commit_sha":"%s","base":{"sha":"%s"},"merged_at":"2026-01-01T00:00:00Z"}\n' "$MERGE" "$BASE" > "$TMP/view.json"
export PR_SAFETY_TEST_SEARCH_JSON="$TMP/search.json" PR_SAFETY_TEST_VIEW_JSON="$TMP/view.json"

bin/hermes-pr-safety-producer
check "merged PR enqueues complete immutable job" "q \"SELECT count(*) FROM requests WHERE kind='pr-safety-review' AND payload->>'repo'='owner/repo' AND payload->>'head_sha'='$MERGE' AND payload->>'base_sha'='$BASE' AND payload->>'policy_digest'='$PR_SAFETY_POLICY_DIGEST' AND payload->>'snapshot_path' LIKE '%/snapshots/%';\" | grep -qx 1"
check "snapshot is clean and read-only" "snapshot=\$(q \"SELECT payload->>'snapshot_path' FROM requests WHERE kind='pr-safety-review' LIMIT 1;\"); [[ -z \"\$(git -C \"\$snapshot\" status --porcelain --untracked-files=all)\" ]] && [[ ! -w \"\$snapshot/x\" ]]"
check "merge event ledgered with digest" "q \"SELECT count(*) FROM pr_safety_merged_pr_events WHERE merge_sha='$MERGE' AND payload_digest ~ '^[0-9a-f]{64}\$';\" | grep -qx 1"

# same merge again: dedupes, no second row
bin/hermes-pr-safety-producer
check "duplicate merge sha queues once" "q \"SELECT count(*) FROM requests WHERE kind='pr-safety-review';\" | grep -qx 1 && q \"SELECT count(*) FROM pr_safety_merged_pr_events;\" | grep -qx 1"

# a NEW merge commit (advanced head) is a distinct event -> a second job, and supersedes the older one
printf '{"number":7,"state":"closed","merge_commit_sha":"%s","base":{"sha":"%s"},"merged_at":"2026-01-02T00:00:00Z"}\n' "$MERGE2" "$BASE" > "$TMP/view.json"
bin/hermes-pr-safety-producer
check "new merge commit creates fresh job" "q \"SELECT count(*) FROM requests WHERE kind='pr-safety-review';\" | grep -qx 2 && q \"SELECT count(*) FROM pr_safety_merged_pr_events;\" | grep -qx 2"
check "new head supersedes the older queued head for the same PR" "q \"SELECT status FROM requests WHERE kind='pr-safety-review' AND dedupe_key='owner/repo#7@$MERGE';\" | grep -qx superseded && q \"SELECT status FROM requests WHERE kind='pr-safety-review' AND dedupe_key='owner/repo#7@$MERGE2';\" | grep -qx queued"

# non-merged PR (state OPEN) is ignored
printf '{"number":8,"state":"open","merge_commit_sha":null,"base":{"sha":"%s"},"merged_at":null}\n' "$BASE" > "$TMP/view.json"
printf '[{"number":8,"repository":{"nameWithOwner":"owner/repo"}}]\n' > "$TMP/search.json"
bin/hermes-pr-safety-producer
check "non-merged PR is ignored" "q \"SELECT count(*) FROM requests WHERE kind='pr-safety-review';\" | grep -qx 2 && q \"SELECT count(*) FROM pr_safety_merged_pr_events;\" | grep -qx 2"

# an ungranted repo is skipped entirely (scope-of-attention allowlist), even if merged
printf '{"number":11,"state":"closed","merge_commit_sha":"%s","base":{"sha":"%s"},"merged_at":"2026-01-03T00:00:00Z"}\n' "$MERGE2" "$BASE" > "$TMP/view.json"
printf '[{"number":11,"repository":{"nameWithOwner":"other/repo"}}]\n' > "$TMP/search.json"
bin/hermes-pr-safety-producer
check "ungranted repo is never enqueued" "q \"SELECT count(*) FROM requests WHERE payload->>'repo'='other/repo';\" | grep -qx 0"

# SAML-403 in search output is a loud fatal, not a silent empty result
cat > "$TMP/bin/gh" <<'SH'
#!/usr/bin/env bash
echo "GraphQL: Resource protected by organization SAML enforcement. You must grant your Personal Access token access to this organization." >&2
exit 1
SH
chmod +x "$TMP/bin/gh"
if bin/hermes-pr-safety-producer >/dev/null 2>"$TMP/saml.err"; then samlrc=0; else samlrc=$?; fi
check "expired SAML is a loud fatal (exit 4), not silent" "[[ \"$samlrc\" == 4 ]] && grep -q 'SAML authorization expired' \"$TMP/saml.err\""

(( fail == 0 )) && echo 'PASS: hermes-pr-safety-producer dedupe, supersede, and authority scoping' || exit 1
