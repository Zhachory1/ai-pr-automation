#!/usr/bin/env bash
# hermes-pr-safety-runner: snapshot/policy identity validation before the model, strict-result
# validation, and incident-only human-review routing through hermes_settle_pr_safety_request.
set -euo pipefail
cd "$(dirname "$0")/.."
CID="hermes-pr-safety-runner-test-$$"
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

q() { docker exec "$CID" psql -U postgres -d fleet -tAc "$1"; }
qw() { docker exec "$CID" psql -U hermes_runtime -d fleet -tAc "$1"; }
fail=0; check() { if eval "$2"; then echo "PASS: $1"; else echo "FAIL: $1" >&2; fail=1; fi; }

export REQUESTS_DB_USER=hermes_runtime REQUESTS_DB_NAME=fleet REQUESTS_DB_HOST=localhost REQUESTS_DB_PORT="$PORT" PGPASSWORD=t

# Immutable snapshot fixture: base commit + head commit.
SNAPSHOT_ROOT="$TMP/snapshots"; mkdir -p "$SNAPSHOT_ROOT/op"
git -C "$SNAPSHOT_ROOT/op" init -q; git -C "$SNAPSHOT_ROOT/op" config user.email t@e.com; git -C "$SNAPSHOT_ROOT/op" config user.name t
printf 'base\n' > "$SNAPSHOT_ROOT/op/x"; git -C "$SNAPSHOT_ROOT/op" add x; git -C "$SNAPSHOT_ROOT/op" commit -qm base
BASE="$(git -C "$SNAPSHOT_ROOT/op" rev-parse HEAD)"
printf 'head\n' >> "$SNAPSHOT_ROOT/op/x"; git -C "$SNAPSHOT_ROOT/op" commit -am head -q
HEAD="$(git -C "$SNAPSHOT_ROOT/op" rev-parse HEAD)"
DIFF="$(git -C "$SNAPSHOT_ROOT/op" diff --no-ext-diff "$BASE" "$HEAD" | shasum -a 256 | awk '{print $1}')"

POLICY_ROOT="$TMP/policies"; mkdir -p "$POLICY_ROOT"; printf 'pinned policy\n' > "$POLICY_ROOT/policy.md"
POLICY_DIGEST="$(shasum -a 256 "$POLICY_ROOT/policy.md" | awk '{print $1}')"

export PR_SAFETY_SNAPSHOT_ROOT="$SNAPSHOT_ROOT" PR_SAFETY_POLICY_ROOT="$POLICY_ROOT" \
  PR_SAFETY_POLICY_PATH="$POLICY_ROOT/policy.md" PR_SAFETY_POLICY_VERSION=v1 PR_SAFETY_POLICY_DIGEST="$POLICY_DIGEST" \
  HANDOFF_ROOT="$TMP/handoffs" HERMES_WORK_ROOT="$TMP/work" HERMES_QUEUE_LEASE_SECONDS=120
mkdir -p "$HANDOFF_ROOT" "$HERMES_WORK_ROOT" "$TMP/bin"

# Fake hermes: reads the prompt and derives fixture behavior from operation_id. This deliberately
# does not rely on inherited TEST_* env because the runner must scrub queue/model subprocess env.
cat > "$TMP/bin/hermes" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
for v in PGPASSWORD REQUESTS_DB_USER REQUESTS_DB_HOST REQUESTS_DB_PORT REQUESTS_DB_NAME; do
  [[ -z "${!v:-}" ]] || { echo "leaked queue credential: $v" >&2; exit 97; }
done
query=""; work=""; while (($#)); do
  [[ "$1" != --query-file ]] || { query="$2"; shift; }
  [[ "$1" != --in ]] || { work="$2"; shift; }
  shift
done
identity="$(grep -m1 '^{' "$query")"
result="$(grep -Eo '/[^ ]+/result\.json' "$query" | head -1)"
draft="$(grep -Eo '/[^ ]+/handoff\.md' "$query" | head -1)"
op="$(jq -r .operation_id <<<"$identity")"
case "$op" in
  op-incident) status=incident_candidate ;;
  op-changes) status=changes_requested ;;
  op-malformed) printf '{not valid json' > "$result"; exit 0 ;;
  *) status=clear ;;
esac
jq --arg status "$status" '. + {status:$status,intent:{claimed:"",evidence:[],needed:"unknown",smaller_existing_solution:null,matches_description:"unknown",description_divergence:null,simpler_alternative:null},findings:[],coverage:{status:"unavailable",command:null,changed_executable_line_coverage_percent:null,gaps:[]},documentation:{status:"not_applicable",required_updates:[]},observability:{status:"not_applicable",recommended_metrics:[],recommended_slos_or_runbooks:[],datadog_terraform_candidate:false},incident:{candidate:false,failure_mode:null,blast_radius:null,recommended_action:null,evidence:[]},human_decisions_needed:[]}' <<<"$identity" > "$result"
if [[ "$status" == incident_candidate ]]; then
  jq '.incident = {candidate:true,failure_mode:"unsafe deploy",blast_radius:"prod",recommended_action:"investigate",evidence:["fixture"]}' "$result" > "$result.tmp" && mv "$result.tmp" "$result"
fi
if [[ "$status" != clear ]]; then
  printf '# handoff\n%s\n\n## Concrete breakage\n\nNone.\n\n## Human decisions\n\nNone.\n' "$identity" > "$draft"
fi
SH
chmod +x "$TMP/bin/hermes"
export PATH="$TMP/bin:$PATH" HERMES_BIN="$TMP/bin/hermes"
chmod +x bin/hermes-pr-safety-runner

payload() { jq -cn --arg op "$1" --arg head "$2" --arg diff "$3" --arg base "$BASE" \
  --arg path "$SNAPSHOT_ROOT/op" --arg policy "$POLICY_ROOT/policy.md" --arg policy_digest "$POLICY_DIGEST" \
  '{operation_id:$op,repo:"o/r",pr:7,head_sha:$head,base_sha:$base,diff_hash:$diff,policy_version:"v1",policy_digest:$policy_digest,snapshot_path:$path,policy_path:$policy}'; }
enqueue() { qw "SELECT hermes_enqueue_request('pr-safety-review','$(payload "$1" "$2" "$3" | sed "s/'/''/g")'::jsonb,'$4');"; }

# --- clear result: settles done, no handoff, no pending row -----------------------------------
TEST_PR_SAFETY_RESULT_STATUS=clear enqueue op-clear "$HEAD" "$DIFF" op-clear >/dev/null
TEST_PR_SAFETY_RESULT_STATUS=clear bin/hermes-pr-safety-runner
check "clear settles done" "q \"SELECT status FROM requests WHERE dedupe_key='op-clear';\" | grep -qx done"
check "clear writes no handoff" "[[ ! -e \"$HANDOFF_ROOT/o__r__pr7__op-clear.md\" ]]"
check "clear queues no pending review" "q \"SELECT count(*) FROM pending_maintenance_reviews;\" | grep -qx 0"

# --- non-incident, non-clear result: handoff published, done, no pending row ------------------
TEST_PR_SAFETY_RESULT_STATUS=changes_requested enqueue op-changes "$HEAD" "$DIFF" op-changes >/dev/null
TEST_PR_SAFETY_RESULT_STATUS=changes_requested bin/hermes-pr-safety-runner
check "changes_requested settles done" "q \"SELECT status FROM requests WHERE dedupe_key='op-changes';\" | grep -qx done"
check "changes_requested writes immutable handoff" "[[ -f \"$HANDOFF_ROOT/o__r__pr7__op-changes.md\" ]] && [[ \"\$(stat -f '%Lp' \"$HANDOFF_ROOT/o__r__pr7__op-changes.md\")\" == 640 ]]"
check "non-incident handoff stays out of human queue" "q \"SELECT count(*) FROM pending_maintenance_reviews;\" | grep -qx 0"

# --- incident candidate: pending row inserted in same settle transaction ----------------------
TEST_PR_SAFETY_RESULT_STATUS=incident_candidate enqueue op-incident "$HEAD" "$DIFF" op-incident >/dev/null
TEST_PR_SAFETY_RESULT_STATUS=incident_candidate bin/hermes-pr-safety-runner
check "incident settles done" "q \"SELECT status FROM requests WHERE dedupe_key='op-incident';\" | grep -qx done"
check "incident candidate queues exactly one human review" "q \"SELECT count(*) FROM pending_maintenance_reviews WHERE request_id=(SELECT id FROM requests WHERE dedupe_key='op-incident');\" | grep -qx 1"
check "pending review provenance carries handoff digest" "q \"SELECT provenance->>'handoff_digest' FROM pending_maintenance_reviews;\" | grep -Eq '^[0-9a-f]{64}\$'"

# --- policy mismatch: fails before invoking the model, no handoff -----------------------------
policy_mismatch="$(payload op-policy-mismatch "$HEAD" "$DIFF" | jq -c '.policy_version = "v2"' | sed "s/'/''/g")"
qw "SELECT hermes_enqueue_request('pr-safety-review','$policy_mismatch'::jsonb,'op-policy-mismatch');" >/dev/null
bin/hermes-pr-safety-runner
check "policy mismatch fails without analyst handoff" "q \"SELECT status FROM requests WHERE dedupe_key='op-policy-mismatch';\" | grep -qx failed && [[ ! -e \"$HANDOFF_ROOT/o__r__pr7__op-policy-mismatch.md\" ]]"

# --- stale/mismatched snapshot head: supersedes, no handoff ------------------------------------
BAD_HEAD="$(printf 'f%.0s' {1..40})"
enqueue op-stale "$BAD_HEAD" "$DIFF" op-stale >/dev/null
bin/hermes-pr-safety-runner
check "snapshot head mismatch supersedes" "q \"SELECT status FROM requests WHERE dedupe_key='op-stale';\" | grep -qx superseded"
check "superseded queues no handoff or pending review" "[[ ! -e \"$HANDOFF_ROOT/o__r__pr7__op-stale.md\" ]] && q \"SELECT count(*) FROM pending_maintenance_reviews;\" | grep -qx 1"

# --- dirty snapshot: supersedes -----------------------------------------------------------------
touch "$SNAPSHOT_ROOT/op/untracked"
enqueue op-dirty "$HEAD" "$DIFF" op-dirty >/dev/null
bin/hermes-pr-safety-runner
check "dirty snapshot supersedes" "q \"SELECT status FROM requests WHERE dedupe_key='op-dirty';\" | grep -qx superseded"
rm -f "$SNAPSHOT_ROOT/op/untracked"

# --- diff hash mismatch: supersedes --------------------------------------------------------------
BAD_DIFF="$(printf 'e%.0s' {1..64})"
enqueue op-diff-mismatch "$HEAD" "$BAD_DIFF" op-diff-mismatch >/dev/null
bin/hermes-pr-safety-runner
check "diff hash mismatch supersedes" "q \"SELECT status FROM requests WHERE dedupe_key='op-diff-mismatch';\" | grep -qx superseded"

# --- malformed analyst result: fails, no handoff, no pending row -------------------------------
TEST_PR_SAFETY_MALFORMED=true enqueue op-malformed "$HEAD" "$DIFF" op-malformed >/dev/null
TEST_PR_SAFETY_MALFORMED=true bin/hermes-pr-safety-runner
check "malformed analyst result fails" "q \"SELECT status FROM requests WHERE dedupe_key='op-malformed';\" | grep -qx failed"
check "malformed result queues no handoff or pending review" "[[ ! -e \"$HANDOFF_ROOT/o__r__pr7__op-malformed.md\" ]] && q \"SELECT count(*) FROM pending_maintenance_reviews WHERE request_id=(SELECT id FROM requests WHERE dedupe_key='op-malformed');\" | grep -qx 0"

# --- path outside snapshot root: fails ------------------------------------------------------------
escape_payload="$(payload op-path-escape "$HEAD" "$DIFF" | jq --arg path "$TMP" -c '.snapshot_path = $path' | sed "s/'/''/g")"
qw "SELECT hermes_enqueue_request('pr-safety-review','$escape_payload'::jsonb,'op-path-escape');" >/dev/null
bin/hermes-pr-safety-runner
check "path outside snapshot root fails" "q \"SELECT status FROM requests WHERE dedupe_key='op-path-escape';\" | grep -qx failed"

# --- least privilege: hermes_worker has no direct table DML -------------------------------------
check "hermes_worker cannot write requests directly" "! qw \"UPDATE requests SET status='done' WHERE dedupe_key='op-clear';\" >/dev/null 2>&1"

(( fail == 0 )) && echo 'PASS: hermes-pr-safety-runner identity validation and incident-only routing' || exit 1
