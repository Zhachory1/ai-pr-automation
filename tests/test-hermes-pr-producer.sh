#!/usr/bin/env bash
# hermes-pr-producer discovers matching PRs, keeps only granted repos, resolves head SHA, and
# enqueues one row per PR with a per-commit dedupe key. Fakes gh/psql; authority allowlist is real.
set -euo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"

cat > "$tmp/authority.yaml" <<'EOF'
repos:
  - Zhachory1/ai-pr-automation
  - ROKT/*
EOF

# Fake gh: review discovery plus paginated maintenance feedback endpoints.
cat > "$tmp/bin/gh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$TEST_STATE/gh.log"
if [[ "$1" == search && "$2" == prs ]]; then
  if [[ "${GH_SCENARIO:-review}" == maintain-batch ]]; then
    printf 'Zhachory1/ai-pr-automation\t7\thttps://x/7\tGranted PR\t1700000000\n'
    printf 'ROKT/ml\t8\thttps://x/8\tOrganization PR\t1700000000\n'
  elif [[ "${GH_SCENARIO:-review}" == maintain ]]; then
    printf 'Zhachory1/ai-pr-automation\t7\thttps://x/7\tGranted PR\t1700000000\n'
  else
    printf 'Zhachory1/ai-pr-automation\t7\thttps://x/7\tGranted PR\t1700000000\n'
    printf 'ROKT/ml\t8\thttps://x/8\tOrganization PR\t1700000000\n'
    printf 'other/repo\t9\thttps://x/9\tUngranted PR\t1700000000\n'
  fi
elif [[ "$1" == pr && "$2" == view ]]; then
  printf '%s\n' "${HEAD_SHA:-deadbeefdeadbeefdeadbeefdeadbeefdeadbeef}"
elif [[ "$1" == api && "$2" == user ]]; then
  echo maintainer
elif [[ -n "${FAIL_API_PR:-}" && "$*" == *"/pulls/$FAIL_API_PR/"* ]]; then
  exit 9
elif [[ "$1" == api && "$*" == *'/reviews?per_page=100'* ]]; then
  jq -cn '[{id:1,user:{login:"maintainer"},body:"own review",submitted_at:"2026-01-01T00:00:00Z"}]'
  if [[ "${FEEDBACK_VERSION:-none}" != none && "${FEEDBACK_KIND:-all}" =~ ^(all|review)$ ]]; then jq -cn --arg v "$FEEDBACK_VERSION" '[{id:10,user:{login:"reviewer"},body:("review-"+$v),submitted_at:"2026-01-02T00:00:00Z"}]'; fi
elif [[ "$1" == api && "$2" == graphql ]]; then
  [[ -z "${FAIL_API_PR:-}" || "$*" != *"number=$FAIL_API_PR"* ]] || exit 9
  jq -cn '{data:{repository:{pullRequest:{reviewThreads:{nodes:[{id:"T-own",isResolved:false,isOutdated:false,comments:{nodes:[{id:"C-own",databaseId:1,author:{login:"maintainer"},body:"own thread reply",createdAt:"2026-01-01T00:00:00Z",updatedAt:"2026-01-01T00:00:00Z"}]}}]}}}}}'
  if [[ "${FEEDBACK_VERSION:-none}" != none && "${FEEDBACK_KIND:-all}" =~ ^(all|thread)$ ]]; then
    resolved=false; outdated=false
    [[ "${THREAD_STATE:-active}" == resolved ]] && resolved=true
    [[ "${THREAD_STATE:-active}" == outdated ]] && outdated=true
    jq -cn --arg v "$FEEDBACK_VERSION" --argjson resolved "$resolved" --argjson outdated "$outdated" '{data:{repository:{pullRequest:{reviewThreads:{nodes:[{id:"T2",isResolved:$resolved,isOutdated:$outdated,comments:{nodes:[{id:"C2",databaseId:22,author:{login:"reviewer"},body:("thread-"+$v),createdAt:"2026-01-02T00:00:00Z",updatedAt:"2026-01-03T00:00:00Z"}]}}]}}}}}'
  fi
elif [[ "$1" == pr && "$2" == checks ]]; then
  [[ -z "${FAIL_API_PR:-}" || "$3" != "$FAIL_API_PR" ]] || exit 9
  if [[ "${FEEDBACK_VERSION:-none}" != none && "${FEEDBACK_KIND:-all}" =~ ^(all|check)$ ]]; then jq -cn '[{name:"ci",bucket:"fail",state:"FAILURE",link:"https://ci/fail"}]'
  else jq -cn '[{name:"ci",bucket:"pass",state:"SUCCESS",link:"https://ci/pass"}]'; fi
  exit "${CHECKS_RC:-0}"
else
  echo "unexpected gh: $*" >&2; exit 2
fi
SH

# Fake psql: capture the invocation and return an id. Fidelity guard: real psql does NOT interpolate
# :'var' in a -c string (only via stdin/file), so reject that exact broken form the producer once had.
cat > "$tmp/bin/psql" <<'SH'
#!/usr/bin/env bash
args="$*"; sql="$(cat 2>/dev/null || true)"
printf '%s\n' "$args" >> "$TEST_STATE/psql.log"
[[ -n "$sql" ]] && printf 'STDIN_SQL: %s\n' "$sql" >> "$TEST_STATE/psql.log"
if [[ "$args" == *" -c "* && "$args" == *":'"* ]]; then
  echo "psql: -c cannot interpolate :'var' (use stdin)" >&2; exit 1
fi
echo 101
SH
chmod +x "$tmp/bin/gh" "$tmp/bin/psql"

out="$(PATH="$tmp/bin:$PATH" TEST_STATE="$tmp" \
  HERMES_AUTHORITY_FILE="$tmp/authority.yaml" \
  HERMES_AUTHORITY_BIN="$PWD/scripts/hermes-authority.py" \
  REQUESTS_DB_USER=x PGPASSWORD=x \
  bin/hermes-pr-producer review 2>&1)"

# Exact and organization-wide grants enqueue; ungranted repo is skipped.
# The enqueue SQL must arrive via stdin (with the :'var' substitutions), not a -c string.
grep -q 'STDIN_SQL: SELECT hermes_enqueue_request' "$tmp/psql.log" \
  || { echo 'FAIL: enqueue not fed via stdin (psql -c would not interpolate :var)' >&2; cat "$tmp/psql.log" >&2; exit 1; }
n_enq="$(grep -c 'STDIN_SQL: SELECT hermes_enqueue_request' "$tmp/psql.log" 2>/dev/null | tr -dc 0-9 || echo 0)"
[[ "${n_enq:-0}" == 2 ]] || { echo "FAIL: expected 2 enqueues, got ${n_enq:-0}" >&2; echo "$out" >&2; cat "$tmp/psql.log" >&2; exit 1; }
grep -q "kind=pr-review" "$tmp/psql.log" || { echo 'FAIL: wrong kind' >&2; exit 1; }
# Per-commit dedupe key present (passed as a -v var).
grep -q 'dk=Zhachory1/ai-pr-automation#7@deadbeefdeadbeefdeadbeefdeadbeefdeadbeef' "$tmp/psql.log" \
  || { echo 'FAIL: dedupe key not per-commit' >&2; cat "$tmp/psql.log" >&2; exit 1; }
grep -q 'other/repo' "$tmp/psql.log" && { echo 'FAIL: ungranted repo enqueued' >&2; exit 1; }
echo "$out" | grep -q 'enqueued=2' || { echo "FAIL: summary wrong: $out" >&2; exit 1; }

# maintain mode uses the author filter and pr-maintain kind.
: > "$tmp/psql.log"
PATH="$tmp/bin:$PATH" TEST_STATE="$tmp" HERMES_AUTHORITY_FILE="$tmp/authority.yaml" \
  HERMES_AUTHORITY_BIN="$PWD/scripts/hermes-authority.py" REQUESTS_DB_USER=x PGPASSWORD=x \
  bin/hermes-pr-producer maintain >/dev/null 2>&1
grep -q 'pr-maintain' "$tmp/psql.log" || { echo 'FAIL: maintain kind not enqueued' >&2; exit 1; }

# Direct review mode admits current discoveries only and never touches PostgreSQL or invokes Hermes.
mkdir -p "$tmp/support" "$tmp/home" "$tmp/work/historical-operation"
cp scripts/hermes_direct_pr_journal.py "$tmp/support/"
printf '{"historical":true}\n' > "$tmp/work/historical-operation/request.json"
chmod 755 "$tmp/work"
cat > "$tmp/bin/hermes" <<'SH'
#!/usr/bin/env bash
touch "$TEST_STATE/model-called"
exit 99
SH
cat > "$tmp/support/enqueue.py" <<'PY'
import json, os, pathlib, sys
raw=sys.stdin.read(); request=json.loads(raw); mode=os.environ.get("TEST_MODE", "ok")
kind=sys.argv[sys.argv.index("--kind")+1]
with pathlib.Path(os.environ["TEST_STATE"], "direct.log").open("a") as stream:
    stream.write(json.dumps({"argv":sys.argv[1:],"raw":raw},separators=(",",":"))+"\n")
if kind == "pr-maintain":
    workspace=pathlib.Path(sys.argv[sys.argv.index("--workspace-root")+1],request["operation_id"])
    workspace.mkdir(mode=0o700,exist_ok=True); path=workspace/"request.json"; card=workspace/"card"
    if not path.exists(): path.write_bytes(raw.encode()); path.chmod(0o440)
if mode == "fail-first" and request["number"] == 7: raise SystemExit(1)
if kind == "pr-maintain" and not card.exists():
    card.touch()
    with pathlib.Path(os.environ["TEST_STATE"], "cards.log").open("a") as stream: stream.write(request["operation_id"]+"\n")
if mode == "malformed": print("{}")
elif mode == "oversize": print("x"*5000)
else:
    print(json.dumps({"kind":kind,"board":kind,"operation_id":request["operation_id"],
        "task_id":f't_{request["number"]:08x}',"status":"ready"},sort_keys=True,separators=(",",":")))
PY
chmod +x "$tmp/bin/hermes"
direct() {
  PATH="$tmp/bin:$PATH" TEST_STATE="$tmp" TEST_MODE="${TEST_MODE:-ok}" PR_REVIEW_QUEUE_ENGINE=kanban \
    HERMES_AUTHORITY_FILE="$tmp/authority.yaml" HERMES_AUTHORITY_BIN="$PWD/scripts/hermes-authority.py" \
    HERMES_BIN="$tmp/bin/hermes" HERMES_HOME="$tmp/home" HERMES_PYTHON="$(command -v python3)" \
    HERMES_PR_KANBAN_ENQUEUE="$tmp/support/enqueue.py" HERMES_PR_KANBAN_WORK_ROOT="$tmp/work" \
    bin/hermes-pr-producer review
}
: > "$tmp/psql.log"; rm -f "$tmp/direct.log" "$tmp/model-called"
direct_out="$(direct 2>&1)"
[[ ! -s "$tmp/psql.log" && ! -e "$tmp/model-called" ]] || { echo 'FAIL: direct mode called psql or Hermes' >&2; exit 1; }
[[ "$(wc -l < "$tmp/direct.log" | tr -d ' ')" == 2 ]] || { echo 'FAIL: direct mode did not admit exactly two current grants' >&2; exit 1; }
op7="$(PYTHONPATH=scripts python3 -c 'from hermes_direct_pr_journal import identity; print(identity("pr-review", "Zhachory1/ai-pr-automation", 7, "deadbeef"*5)["operation_id"])')"
op8="$(PYTHONPATH=scripts python3 -c 'from hermes_direct_pr_journal import identity; print(identity("pr-review", "ROKT/ml", 8, "deadbeef"*5)["operation_id"])')"
jq -se --arg op7 "$op7" --arg op8 "$op8" --arg home "$tmp/home" --arg bin "$tmp/bin/hermes" --arg root "$tmp/work" '
  all(.[]; .argv == ["--kind","pr-review","--hermes-home",$home,"--hermes-bin",$bin,"--workspace-root",$root] and .raw == (.raw|fromjson|tojson)) and
  (map(.raw|fromjson) == [
    {head_sha:("deadbeef"*5),number:7,operation_id:$op7,repo:"Zhachory1/ai-pr-automation",title:"Granted PR",url:"https://github.com/Zhachory1/ai-pr-automation/pull/7"},
    {head_sha:("deadbeef"*5),number:8,operation_id:$op8,repo:"ROKT/ml",title:"Organization PR",url:"https://github.com/ROKT/ml/pull/8"}
  ])' "$tmp/direct.log" >/dev/null || { echo 'FAIL: direct request or argv differs' >&2; cat "$tmp/direct.log" >&2; exit 1; }
echo "$direct_out" | grep -q 'admitted=2 failed=0 skipped=1' || { echo "FAIL: direct summary wrong: $direct_out" >&2; exit 1; }
[[ "$(python3 -c 'import os,stat,sys; print(oct(stat.S_IMODE(os.stat(sys.argv[1]).st_mode)))' "$tmp/work")" == 0o700 ]] || { echo 'FAIL: direct work root mode' >&2; exit 1; }

# Per-item failures continue through the batch; malformed and oversized results fail closed.
rm -f "$tmp/direct.log"
if TEST_MODE=fail-first fail_out="$(direct 2>&1)"; then fail_rc=0; else fail_rc=$?; fi
[[ "$fail_rc" == 1 && "$(wc -l < "$tmp/direct.log" | tr -d ' ')" == 2 ]] \
  || { echo 'FAIL: first direct failure starved later PR' >&2; exit 1; }
echo "$fail_out" | grep -q 'admitted=1 failed=1 skipped=1' || { echo "FAIL: failed direct summary wrong: $fail_out" >&2; exit 1; }
for bad in malformed oversize; do
  rm -f "$tmp/direct.log"
  if TEST_MODE="$bad" direct >/dev/null 2>&1; then echo "FAIL: $bad direct result accepted" >&2; exit 1; fi
done
unset TEST_MODE

direct_maintain() {
  PATH="$tmp/bin:$PATH" TEST_STATE="$tmp" TEST_MODE="${TEST_MODE:-ok}" GH_SCENARIO="${GH_SCENARIO:-maintain}" \
    FEEDBACK_VERSION="${FEEDBACK_VERSION:-none}" FEEDBACK_KIND="${FEEDBACK_KIND:-all}" \
    THREAD_STATE="${THREAD_STATE:-active}" HEAD_SHA="${HEAD_SHA:-deadbeefdeadbeefdeadbeefdeadbeefdeadbeef}" FAIL_API_PR="${FAIL_API_PR:-}" \
    CHECKS_RC="${CHECKS_RC:-0}" PR_MAINTAIN_QUEUE_ENGINE=kanban \
    HERMES_AUTHORITY_FILE="$tmp/authority.yaml" HERMES_AUTHORITY_BIN="$PWD/scripts/hermes-authority.py" \
    HERMES_BIN="$tmp/bin/hermes" HERMES_HOME="$tmp/home" HERMES_PYTHON="$(command -v python3)" \
    HERMES_PR_KANBAN_ENQUEUE="$tmp/support/enqueue.py" HERMES_PR_KANBAN_WORK_ROOT="$tmp/work" \
    bin/hermes-pr-producer maintain
}

# Own feedback and passing checks produce no maintenance round. All feedback reads are paginated,
# bounded by producer timeout, and the authenticated login is fetched once.
rm -f "$tmp/direct.log"; : > "$tmp/psql.log"; : > "$tmp/gh.log"
none_out="$(FEEDBACK_VERSION=none direct_maintain 2>&1)"
[[ ! -e "$tmp/direct.log" && ! -s "$tmp/psql.log" ]] || { echo 'FAIL: no-feedback maintain enqueued' >&2; exit 1; }
echo "$none_out" | grep -q 'admitted=0 failed=0 skipped=1' || { echo "FAIL: no-feedback summary: $none_out" >&2; exit 1; }
[[ "$(grep -c '^api user ' "$tmp/gh.log")" == 1 && "$(grep -c -- '--paginate' "$tmp/gh.log")" == 2 ]] \
  || { echo 'FAIL: maintenance API calls were not login-once/paginated' >&2; cat "$tmp/gh.log" >&2; exit 1; }
! grep -q '/comments?per_page' "$tmp/gh.log" || { echo 'FAIL: REST inline comments were fetched' >&2; exit 1; }

for state in resolved outdated; do
  rm -rf "$tmp/work"/pr-maintain-*; rm -f "$tmp/direct.log"
  state_out="$(FEEDBACK_KIND=thread FEEDBACK_VERSION="$state" THREAD_STATE="$state" direct_maintain 2>&1)"
  [[ ! -e "$tmp/direct.log" ]] || { echo "FAIL: $state thread enqueued" >&2; exit 1; }
  echo "$state_out" | grep -q 'no actionable feedback' || { echo "FAIL: $state thread not empty" >&2; exit 1; }
  FEEDBACK_KIND=thread FEEDBACK_VERSION="$state" direct_maintain >/dev/null 2>&1
  jq -e '(.raw|fromjson|.round) == 1' "$tmp/direct.log" >/dev/null || { echo "FAIL: $state thread consumed a round" >&2; exit 1; }
done
for source in review thread check; do
  rm -rf "$tmp/work"/pr-maintain-*; rm -f "$tmp/direct.log"
  FEEDBACK_KIND="$source" FEEDBACK_VERSION="$source" direct_maintain >/dev/null 2>&1
  [[ "$(wc -l < "$tmp/direct.log" | tr -d ' ')" == 1 ]] || { echo "FAIL: external $source feedback not admitted" >&2; exit 1; }
done
unset FEEDBACK_KIND FEEDBACK_VERSION THREAD_STATE
rm -rf "$tmp/work"/pr-maintain-*; rm -f "$tmp/direct.log" "$tmp/cards.log"

# External top-level review, unresolved thread, and failing check form one canonical digest.
expected_digest="$(python3 - <<'PY'
import hashlib,json
h=lambda value: hashlib.sha256(value.encode()).hexdigest()
check=json.dumps({"name":"ci","bucket":"fail","state":"FAILURE","link":"https://ci/fail"},sort_keys=True,separators=(",",":"))
items=[["review","10",h("review-v1")],["thread","T2","C2",h("thread-v1")],["check",h(check)]]
print(h(json.dumps(sorted(items),separators=(",",":"))))
PY
)"
maintain_out="$(CHECKS_RC=1 FEEDBACK_VERSION=v1 direct_maintain 2>&1)"
[[ "$(wc -l < "$tmp/direct.log" | tr -d ' ')" == 1 && ! -s "$tmp/psql.log" ]] || { echo 'FAIL: external feedback not admitted exactly once' >&2; exit 1; }
maintain_op="$(PYTHONPATH=scripts python3 -c 'from hermes_direct_pr_journal import identity; import sys; print(identity("pr-maintain","Zhachory1/ai-pr-automation",7,"deadbeef"*5,sys.argv[1])["operation_id"])' "$expected_digest")"
jq -e --arg digest "$expected_digest" --arg op "$maintain_op" --arg home "$tmp/home" --arg bin "$tmp/bin/hermes" --arg root "$tmp/work" '
  .argv == ["--kind","pr-maintain","--hermes-home",$home,"--hermes-bin",$bin,"--workspace-root",$root] and
  (.raw|fromjson) == {feedback_digest:$digest,head_sha:("deadbeef"*5),number:7,operation_id:$op,repo:"Zhachory1/ai-pr-automation",round:1,title:"Granted PR",url:"https://github.com/Zhachory1/ai-pr-automation/pull/7"}' \
  "$tmp/direct.log" >/dev/null || { echo 'FAIL: exact maintain request/operation/round differs' >&2; cat "$tmp/direct.log" >&2; exit 1; }
echo "$maintain_out" | grep -q 'admitted=1 failed=0 skipped=0' || { echo "FAIL: maintain summary: $maintain_out" >&2; exit 1; }
grep -Eq 'review-v1|thread-v1|own reply' <<<"$maintain_out" && { echo 'FAIL: feedback body logged' >&2; exit 1; }

# Same digest on a new head replays exact old operation; distinct snapshots use rounds 2 and 3.
HEAD_SHA="$(printf 'a%.0s' {1..40})" FEEDBACK_VERSION=v1 same_out="$(direct_maintain 2>&1)"
[[ "$(wc -l < "$tmp/direct.log" | tr -d ' ')" == 2 ]] || { echo 'FAIL: same digest was not replayed' >&2; exit 1; }
echo "$same_out" | grep -q "reconciled pr-maintain .* via $maintain_op" || { echo 'FAIL: duplicate reconcile not logged' >&2; exit 1; }
jq -se '.[0].raw == .[1].raw and (.[1].raw|fromjson|.round) == 1' "$tmp/direct.log" >/dev/null \
  || { echo 'FAIL: duplicate did not replay exact old request' >&2; exit 1; }
[[ "$(find "$tmp/work" -maxdepth 1 -type d -name 'pr-maintain-*' | wc -l | tr -d ' ')" == 1 ]] || { echo 'FAIL: duplicate created a new operation' >&2; exit 1; }
[[ "$(wc -l < "$tmp/cards.log" | tr -d ' ')" == 1 ]] || { echo 'FAIL: duplicate created a new card' >&2; exit 1; }
FEEDBACK_VERSION=v2 direct_maintain >/dev/null 2>&1
FEEDBACK_VERSION=v3 direct_maintain >/dev/null 2>&1
FEEDBACK_VERSION=v4 capped_out="$(direct_maintain 2>&1)"
[[ "$(wc -l < "$tmp/direct.log" | tr -d ' ')" == 4 ]] || { echo 'FAIL: maintenance round count differs' >&2; exit 1; }
jq -se 'map(.raw|fromjson|select(.number==7)) | map(.round) == [1,1,2,3] and .[0].operation_id == .[1].operation_id' \
  "$tmp/direct.log" >/dev/null || { echo 'FAIL: exact maintenance rounds differ' >&2; cat "$tmp/direct.log" >&2; exit 1; }
echo "$capped_out" | grep -q 'maintenance round cap reached' || { echo 'FAIL: fourth snapshot not capped' >&2; exit 1; }

# Failed initial enqueue persists its request; next cycle replays it without a new round or operation.
rm -rf "$tmp/work"/pr-maintain-*; rm -f "$tmp/direct.log" "$tmp/cards.log"
if TEST_MODE=fail-first FEEDBACK_KIND=thread FEEDBACK_VERSION=retry retry_fail="$(direct_maintain 2>&1)"; then retry_rc=0; else retry_rc=$?; fi
[[ "$retry_rc" == 1 && "$(wc -l < "$tmp/direct.log" | tr -d ' ')" == 1 ]] || { echo 'FAIL: initial maintain failure not recorded' >&2; exit 1; }
TEST_MODE=ok FEEDBACK_KIND=thread FEEDBACK_VERSION=retry retry_out="$(direct_maintain 2>&1)"
[[ "$(wc -l < "$tmp/direct.log" | tr -d ' ')" == 2 ]] || { echo 'FAIL: failed maintain was not retried' >&2; exit 1; }
jq -se '.[0].raw == .[1].raw and (.[1].raw|fromjson|.round) == 1' "$tmp/direct.log" >/dev/null \
  || { echo 'FAIL: retry did not replay original round' >&2; exit 1; }
[[ "$(find "$tmp/work" -maxdepth 1 -type d -name 'pr-maintain-*' | wc -l | tr -d ' ')" == 1 ]] || { echo 'FAIL: retry created a new operation' >&2; exit 1; }
[[ "$(wc -l < "$tmp/cards.log" | tr -d ' ')" == 1 ]] || { echo 'FAIL: retry did not reconcile one card' >&2; exit 1; }
echo "$retry_out" | grep -q 'reconciled pr-maintain' || { echo 'FAIL: retry not reconciled' >&2; exit 1; }

# One PR feedback API failure does not starve another and direct mode exits 1 after the batch.
: > "$tmp/gh.log"; before="$(wc -l < "$tmp/direct.log" | tr -d ' ')"
if GH_SCENARIO=maintain-batch FEEDBACK_VERSION=batch FAIL_API_PR=7 batch_out="$(direct_maintain 2>&1)"; then batch_rc=0; else batch_rc=$?; fi
after="$(wc -l < "$tmp/direct.log" | tr -d ' ')"
[[ "$batch_rc" == 1 && "$after" == $((before+1)) ]] || { echo 'FAIL: API failure starved later PR or wrong exit' >&2; echo "$batch_out" >&2; exit 1; }
echo "$batch_out" | grep -q 'admitted=1 failed=1 skipped=0' || { echo "FAIL: API failure summary: $batch_out" >&2; exit 1; }
[[ "$(grep -c '^api user ' "$tmp/gh.log")" == 1 && ! -s "$tmp/psql.log" ]] || { echo 'FAIL: direct maintain login/psql behavior' >&2; exit 1; }

echo 'PASS: hermes-pr-producer postgres, direct review, and direct maintain modes'
