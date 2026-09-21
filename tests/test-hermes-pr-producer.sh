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

echo 'PASS: hermes-pr-producer discovers, scopes to grant, resolves head, enqueues per-commit'
