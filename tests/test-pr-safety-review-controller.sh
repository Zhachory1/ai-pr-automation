#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
CID="pr-safety-controller-test-$$"; PORT="$(python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)"; TMP="$(mktemp -d)"
cleanup() { docker rm -f "$CID" >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT
docker run --rm -d --name "$CID" -e POSTGRES_PASSWORD=t -e POSTGRES_DB=fleet -p "$PORT:5432" postgres:16 >/dev/null
for _ in $(seq 1 30); do docker exec "$CID" pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
for f in docker/initdb/{01-schema,02-agent-server,03-human-review-queue,04-pr-safety-review}.sql; do docker cp "$f" "$CID:/tmp/${f##*/}"; docker exec "$CID" psql -U postgres -d fleet -q -v ON_ERROR_STOP=1 -f "/tmp/${f##*/}" >/dev/null; done
export REQUESTS_DB_USER=postgres REQUESTS_DB_NAME=fleet REQUESTS_DB_HOST=localhost REQUESTS_DB_PORT="$PORT" PGPASSWORD=t OPENAI_API_KEY=test-key
export PR_SAFETY_SNAPSHOT_ROOT="$TMP/snapshots" PR_SAFETY_POLICY_ROOT="$TMP/policies" HANDOFF_ROOT="$TMP/handoffs" PR_SAFETY_WORK_ROOT="$TMP/work"
export PR_SAFETY_ANALYST_RUNNER="$PWD/tests/fake-pr-safety-analyst.sh" PR_SAFETY_CONTROLLER_ONCE=true
mkdir -p "$PR_SAFETY_SNAPSHOT_ROOT/op" "$PR_SAFETY_POLICY_ROOT" "$HANDOFF_ROOT" "$PR_SAFETY_WORK_ROOT"
git -C "$PR_SAFETY_SNAPSHOT_ROOT/op" init -q; git -C "$PR_SAFETY_SNAPSHOT_ROOT/op" config user.email test@example.com; git -C "$PR_SAFETY_SNAPSHOT_ROOT/op" config user.name test
printf 'base\n' > "$PR_SAFETY_SNAPSHOT_ROOT/op/x"; git -C "$PR_SAFETY_SNAPSHOT_ROOT/op" add x; git -C "$PR_SAFETY_SNAPSHOT_ROOT/op" commit -qm base
BASE="$(git -C "$PR_SAFETY_SNAPSHOT_ROOT/op" rev-parse HEAD)"; printf 'head\n' >> "$PR_SAFETY_SNAPSHOT_ROOT/op/x"; git -C "$PR_SAFETY_SNAPSHOT_ROOT/op" commit -am head -q
HEAD="$(git -C "$PR_SAFETY_SNAPSHOT_ROOT/op" rev-parse HEAD)"; DIFF="$(git -C "$PR_SAFETY_SNAPSHOT_ROOT/op" diff --no-ext-diff "$BASE" "$HEAD" | shasum -a 256 | awk '{print $1}')"
printf 'policy\n' > "$PR_SAFETY_POLICY_ROOT/policy.md"
POLICY_DIGEST="$(shasum -a 256 "$PR_SAFETY_POLICY_ROOT/policy.md" | awk '{print $1}')"
export PR_SAFETY_POLICY_PATH="$PR_SAFETY_POLICY_ROOT/policy.md" PR_SAFETY_POLICY_VERSION=v1 PR_SAFETY_POLICY_DIGEST="$POLICY_DIGEST"
# shellcheck source=../lib/queue.sh
. lib/queue.sh
q() { docker exec "$CID" psql -U postgres -d fleet -tAc "$1"; }
fail=0; check() { if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1" >&2; fail=1; fi; }
payload() { jq -cn --arg op "$1" --arg head "$2" --arg diff "$3" --arg base "$BASE" --arg path "$PR_SAFETY_SNAPSHOT_ROOT/op" --arg policy "$PR_SAFETY_POLICY_ROOT/policy.md" --arg policy_digest "$POLICY_DIGEST" '{operation_id:$op,repo:"o/r",pr:7,head_sha:$head,base_sha:$base,diff_hash:$diff,policy_version:"v1",policy_digest:$policy_digest,snapshot_path:$path,policy_path:$policy}'; }

export TEST_OPERATION_ID=op-success TEST_REPO=o/r TEST_PR=7 TEST_HEAD="$HEAD" TEST_BASE="$BASE" TEST_DIFF="$DIFF" TEST_POLICY=v1
queue_enqueue pr-safety-review "$(payload op-success "$HEAD" "$DIFF")" op-success >/dev/null
bin/pr-safety-review-controller
check "success promoted private handoff" '[[ -f "$HANDOFF_ROOT/op-success.md" ]] && [[ "$(stat -f "%Lp" "$HANDOFF_ROOT/op-success.md")" == 600 ]]'
check "success queued once with digest provenance" "q \"SELECT count(*) FROM pending_maintenance_reviews WHERE request_id=(SELECT id FROM requests WHERE dedupe_key='op-success');\" | grep -qx 1 && q \"SELECT provenance->>'handoff_digest' FROM pending_maintenance_reviews;\" | grep -Eq '^[0-9a-f]{64}$'"
export TEST_OPERATION_ID=op-clear TEST_HEAD="$HEAD" TEST_DIFF="$DIFF"
queue_enqueue pr-safety-review "$(payload op-clear "$HEAD" "$DIFF")" op-clear >/dev/null
bin/pr-safety-review-controller
check "clear result needs no handoff or human queue item" "q \"SELECT status||'/'||coalesce(posted_ref,'') FROM requests WHERE dedupe_key='op-clear';\" | grep -qx 'done/clear' && [[ ! -e \"$HANDOFF_ROOT/op-clear.md\" ]] && q \"SELECT count(*) FROM pending_maintenance_reviews;\" | grep -qx 1"
export TEST_OPERATION_ID=op-clear-with-finding TEST_HEAD="$HEAD" TEST_DIFF="$DIFF"
queue_enqueue pr-safety-review "$(payload op-clear-with-finding "$HEAD" "$DIFF")" op-clear-with-finding >/dev/null
bin/pr-safety-review-controller
check "clear with finding fails without discarding evidence" "q \"SELECT status FROM requests WHERE dedupe_key='op-clear-with-finding';\" | grep -qx failed && [[ ! -e \"$HANDOFF_ROOT/op-clear-with-finding.md\" ]]"
policy_mismatch="$(payload op-policy-mismatch "$HEAD" "$DIFF" | jq '.policy_version = "v2"')"
queue_enqueue pr-safety-review "$policy_mismatch" op-policy-mismatch >/dev/null
bin/pr-safety-review-controller
check "active policy mismatch fails without analyst handoff" "q \"SELECT status FROM requests WHERE dedupe_key='op-policy-mismatch';\" | grep -qx failed && [[ ! -e \"$HANDOFF_ROOT/op-policy-mismatch.md\" ]]"
_psql -v payload="$(payload op-success "$HEAD" "$DIFF")" <<'SQL' >/dev/null
INSERT INTO requests(kind, payload, dedupe_key) VALUES ('pr-safety-review', :'payload'::jsonb, 'op-success');
SQL
bin/pr-safety-review-controller
check "existing handoff queues no duplicate operation" "q \"SELECT count(*) FROM requests WHERE dedupe_key='op-success';\" | grep -qx 2 && q \"SELECT count(*) FROM pending_maintenance_reviews;\" | grep -qx 1"

BAD_HEAD="$(printf 'f%.0s' {1..40})"; export TEST_OPERATION_ID=op-stale TEST_HEAD="$BAD_HEAD" TEST_DIFF="$DIFF"
queue_enqueue pr-safety-review "$(payload op-stale "$BAD_HEAD" "$DIFF")" op-stale >/dev/null
bin/pr-safety-review-controller
check "immutable mismatch is superseded" "q \"SELECT status FROM requests WHERE dedupe_key='op-stale';\" | grep -qx superseded"
check "superseded operation does not queue handoff" "q \"SELECT count(*) FROM pending_maintenance_reviews;\" | grep -qx 1"

touch "$PR_SAFETY_SNAPSHOT_ROOT/op/untracked"
export TEST_OPERATION_ID=op-dirty TEST_HEAD="$HEAD" TEST_DIFF="$DIFF"
queue_enqueue pr-safety-review "$(payload op-dirty "$HEAD" "$DIFF")" op-dirty >/dev/null
bin/pr-safety-review-controller
check "dirty snapshot is superseded" "q \"SELECT status FROM requests WHERE dedupe_key='op-dirty';\" | grep -qx superseded"
rm "$PR_SAFETY_SNAPSHOT_ROOT/op/untracked"

queue_enqueue pr-safety-review '{}' bad-payload >/dev/null
bin/pr-safety-review-controller
check "invalid payload fails without handoff" "q \"SELECT status FROM requests WHERE dedupe_key='bad-payload';\" | grep -qx failed && q \"SELECT count(*) FROM pending_maintenance_reviews;\" | grep -qx 1"

escape_payload="$(payload op-path-escape "$HEAD" "$DIFF" | jq --arg path "$TMP" '.snapshot_path = $path')"
queue_enqueue pr-safety-review "$escape_payload" op-path-escape >/dev/null
bin/pr-safety-review-controller
check "path outside snapshot root fails" "q \"SELECT status FROM requests WHERE dedupe_key='op-path-escape';\" | grep -qx failed"

export TEST_OPERATION_ID=op-invalid-result TEST_HEAD="$HEAD" TEST_DIFF="$DIFF"
queue_enqueue pr-safety-review "$(payload op-invalid-result "$HEAD" "$DIFF")" op-invalid-result >/dev/null
bin/pr-safety-review-controller
check "invalid result fails without handoff" "q \"SELECT status FROM requests WHERE dedupe_key='op-invalid-result';\" | grep -qx failed && [[ ! -e \"$HANDOFF_ROOT/op-invalid-result.md\" ]]"

export TEST_OPERATION_ID=op-invalid-handoff TEST_HEAD="$HEAD" TEST_DIFF="$DIFF"
queue_enqueue pr-safety-review "$(payload op-invalid-handoff "$HEAD" "$DIFF")" op-invalid-handoff >/dev/null
bin/pr-safety-review-controller
check "invalid handoff fails without promotion" "q \"SELECT status FROM requests WHERE dedupe_key='op-invalid-handoff';\" | grep -qx failed && [[ ! -e \"$HANDOFF_ROOT/op-invalid-handoff.md\" ]]"

(( fail == 0 ))
