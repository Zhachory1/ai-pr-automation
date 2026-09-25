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

# Fake gh: search returns exact, organization-wide, and ungranted PRs; pr view resolves a head sha.
cat > "$tmp/bin/gh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == search && "$2" == prs ]]; then
  printf 'Zhachory1/ai-pr-automation\t7\thttps://x/7\tGranted PR\t1700000000\n'
  printf 'ROKT/ml\t8\thttps://x/8\tOrganization PR\t1700000000\n'
  printf 'other/repo\t9\thttps://x/9\tUngranted PR\t1700000000\n'
elif [[ "$1" == pr && "$2" == view ]]; then
  printf 'deadbeefdeadbeefdeadbeefdeadbeefdeadbeef\n'
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
with pathlib.Path(os.environ["TEST_STATE"], "direct.log").open("a") as stream:
    stream.write(json.dumps({"argv":sys.argv[1:],"raw":raw},separators=(",",":"))+"\n")
if mode == "fail-first" and request["number"] == 7: raise SystemExit(1)
if mode == "malformed": print("{}")
elif mode == "oversize": print("x"*5000)
else:
    print(json.dumps({"kind":"pr-review","board":"pr-review","operation_id":request["operation_id"],
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

if PR_REVIEW_QUEUE_ENGINE=kanban bin/hermes-pr-producer maintain >/dev/null 2>"$tmp/maintain.err"; then
  echo 'FAIL: Kanban maintain accepted' >&2; exit 1
fi
grep -q 'unsupported for maintain' "$tmp/maintain.err" || { echo 'FAIL: Kanban maintain was not loud' >&2; exit 1; }

echo 'PASS: hermes-pr-producer postgres and direct review modes'
