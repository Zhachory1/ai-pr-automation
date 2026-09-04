#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
CID="pr-safety-chat-producer-test-$$"
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
for f in docker/initdb/{01-schema,02-agent-server,03-human-review-queue,04-pr-safety-review,05-pr-safety-chat-producer}.sql; do docker cp "$f" "$CID:/tmp/${f##*/}"; docker exec "$CID" psql -U postgres -d fleet -q -v ON_ERROR_STOP=1 -f "/tmp/${f##*/}" >/dev/null; done

SOURCE="$TMP/source"; mkdir -p "$SOURCE"
git -C "$SOURCE" init -q; git -C "$SOURCE" config user.email test@example.com; git -C "$SOURCE" config user.name test
printf 'base\n' > "$SOURCE/x"; git -C "$SOURCE" add x; git -C "$SOURCE" commit -qm base
BASE="$(git -C "$SOURCE" rev-parse HEAD)"
printf 'first head\n' >> "$SOURCE/x"; git -C "$SOURCE" commit -am first-head -q
HEAD_ONE="$(git -C "$SOURCE" rev-parse HEAD)"
printf 'second head\n' >> "$SOURCE/x"; git -C "$SOURCE" commit -am second-head -q
HEAD_TWO="$(git -C "$SOURCE" rev-parse HEAD)"

POLICY_ROOT="$TMP/policies"; mkdir -p "$POLICY_ROOT"; printf 'pinned policy\n' > "$POLICY_ROOT/policy.md"
export REQUESTS_DB_USER=postgres REQUESTS_DB_NAME=fleet REQUESTS_DB_HOST=localhost REQUESTS_DB_PORT="$PORT" PGPASSWORD=t
export GOOGLE_CHAT_ACCESS_TOKEN=fixture-token GOOGLE_CHAT_SPACE=spaces/AAAA PR_SAFETY_CHAT_BOT_SENDER=users/123456789
export PR_SAFETY_SNAPSHOT_ROOT="$TMP/snapshots" PR_SAFETY_POLICY_ROOT="$POLICY_ROOT" PR_SAFETY_POLICY_PATH="$POLICY_ROOT/policy.md" PR_SAFETY_POLICY_VERSION=v1
export PR_SAFETY_POLICY_DIGEST="$(shasum -a 256 "$POLICY_ROOT/policy.md" | awk '{print $1}')"
export PR_SAFETY_TEST_REPO="$SOURCE" PR_SAFETY_TEST_REPO_NAME=owner/repo PR_SAFETY_TEST_BASE="$BASE" PR_SAFETY_TEST_HEAD="$HEAD_ONE"
mkdir -p "$TMP/bin"
ln -s "$PWD/tests/fake-pr-safety-chat-gh.sh" "$TMP/bin/gh"
ln -s "$PWD/tests/fake-pr-safety-chat-curl.sh" "$TMP/bin/curl"
ln -s "$PWD/tests/fake-pr-safety-chat-gcloud.sh" "$TMP/bin/gcloud"
export PATH="$TMP/bin:$PATH"
chmod +x tests/fake-pr-safety-chat-{curl,gcloud,gh}.sh bin/pr-safety-chat-producer

q() { docker exec "$CID" psql -U postgres -d fleet -tAc "$1"; }
run_fixture() { PR_SAFETY_CHAT_INPUT_FILE="$1" bin/pr-safety-chat-producer; }
fail=0
check() { if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1" >&2; fail=1; fi; }

run_fixture tests/fixtures/pr-safety-chat-authorized.json
check "authorized exact command enqueues complete immutable job" "q \"SELECT count(*) FROM requests WHERE kind='pr-safety-review' AND payload->>'repo'='owner/repo' AND payload->>'head_sha'='$HEAD_ONE' AND payload->>'base_sha'='$BASE' AND payload->>'policy_digest'='$PR_SAFETY_POLICY_DIGEST' AND payload->>'snapshot_path' LIKE '%/snapshots/%';\" | grep -qx 1"
check "snapshot is clean and read-only" "snapshot=\$(q \"SELECT payload->>'snapshot_path' FROM requests WHERE kind='pr-safety-review' LIMIT 1;\"); [[ -z \"\$(git -C \"\$snapshot\" status --porcelain --untracked-files=all)\" ]] && [[ ! -w \"\$snapshot/x\" ]]"
check "accepted message is ledgered with digest" "q \"SELECT count(*) FROM pr_safety_chat_events WHERE provider_message_id='spaces/AAAA/messages/authorized-1' AND payload_digest ~ '^[0-9a-f]{64}$';\" | grep -qx 1"

run_fixture tests/fixtures/pr-safety-chat-authorized.json
check "duplicate provider message queues once" "q \"SELECT count(*) FROM requests WHERE kind='pr-safety-review';\" | grep -qx 1 && q \"SELECT count(*) FROM pr_safety_chat_events;\" | grep -qx 1"

MARKER="$TMP/injected"
sed "s|__MARKER__|$MARKER|" tests/fixtures/pr-safety-chat-ignored.json > "$TMP/ignored.json"
run_fixture "$TMP/ignored.json"
check "other senders, free-form, and injected content are ignored" "q \"SELECT count(*) FROM requests WHERE kind='pr-safety-review';\" | grep -qx 1 && q \"SELECT count(*) FROM pr_safety_chat_events;\" | grep -qx 1 && [[ ! -e \"$MARKER\" ]]"

export PR_SAFETY_TEST_HEAD="$HEAD_TWO"
run_fixture tests/fixtures/pr-safety-chat-new-head.json
check "new head creates fresh immutable job" "q \"SELECT count(*) FROM requests WHERE kind='pr-safety-review';\" | grep -qx 2 && q \"SELECT count(*) FROM requests WHERE dedupe_key='owner/repo#7@$HEAD_TWO' AND status='queued';\" | grep -qx 1 && q \"SELECT count(*) FROM pr_safety_chat_events;\" | grep -qx 2"

export PR_SAFETY_TEST_CURL_ARGS="$TMP/curl.args" PR_SAFETY_TEST_CURL_RESPONSE="$PWD/tests/fixtures/pr-safety-chat-authorized.json"
unset GOOGLE_CHAT_ACCESS_TOKEN
GOOGLE_CLOUD_QUOTA_PROJECT=fixture-project bin/pr-safety-chat-producer
check "network reads refresh ADC token and include quota project" "grep -Fx 'Authorization: Bearer refreshed-token' \"$TMP/curl.args\" >/dev/null && grep -Fx 'x-goog-user-project: fixture-project' \"$TMP/curl.args\" >/dev/null"

(( fail == 0 ))
