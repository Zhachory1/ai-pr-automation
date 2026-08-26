#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMP="$(mktemp -d)"
cleanup() {
  if [[ "${KEEP_TEST_TEMP:-false}" == true ]]; then
    echo "kept test temp: $TEMP"
  else
    rm -r "$TEMP"
  fi
}
trap cleanup EXIT

cat > "$TEMP/prs.tsv" <<'TSV'
example/repo	3	https://example.invalid/3	new	false	2026-01-03T00:00:00Z	300	cccccccccccccccccccccccccccccccccccccccc
example/repo	1	https://example.invalid/1	old	false	2026-01-01T00:00:00Z	100	aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
example/repo	2	https://example.invalid/2	middle	false	2026-01-02T00:00:00Z	200	bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
TSV

mkdir -p "$TEMP/state/review"
printf 'example/repo#1\t400\n' > "$TEMP/state/review/attempts.tsv"

output="$(
  PR_AUTOMATION_STATE_ROOT="$TEMP/state" \
  PR_AUTOMATION_LOG_ROOT="$TEMP/logs" \
  PR_AUTOMATION_WORK_ROOT="$TEMP/work" \
  PR_AUTOMATION_INPUT_FILE="$TEMP/prs.tsv" \
  PR_AUTOMATION_REPOSITORIES='example/repo' \
  PR_AUTOMATION_DRY_RUN=true \
  "$ROOT/bin/pr-automation" review
)"

order="$(printf '%s\n' "$output" | awk '/dry run would process/ {print $NF}' | paste -sd ',' -)"
expected='example/repo#2,example/repo#3,example/repo#1'
if [[ "$order" != "$expected" ]]; then
  echo "unexpected fairness order: $order" >&2
  echo "expected: $expected" >&2
  exit 1
fi

cat > "$TEMP/fake-timeout" <<'SH'
#!/usr/bin/env bash
while [[ "${1:-}" == --* ]]; do shift; done
shift
exec "$@"
SH

cat > "$TEMP/fake-runner" <<'SH'
#!/usr/bin/env bash
printf '%s#%s\n' "$PR_REPO" "$PR_NUMBER" >> "$RUNNER_LOG"
SH

chmod +x "$TEMP/fake-timeout" "$TEMP/fake-runner"
export RUNNER_LOG="$TEMP/runner.log"

PR_AUTOMATION_STATE_ROOT="$TEMP/state-maintain" \
PR_AUTOMATION_LOG_ROOT="$TEMP/logs-maintain" \
PR_AUTOMATION_WORK_ROOT="$TEMP/work-maintain" \
PR_AUTOMATION_INPUT_FILE="$TEMP/prs.tsv" \
PR_AUTOMATION_REPOSITORIES='example/repo' \
PR_AUTOMATION_TIMEOUT_BIN="$TEMP/fake-timeout" \
PR_AUTOMATION_RUNNER="$TEMP/fake-runner" \
PR_AUTOMATION_RUN_BUDGET_SECONDS=3300 \
PR_AUTOMATION_KEEP_SUCCESS_WORKSPACES=true \
"$ROOT/bin/pr-automation" maintain >/dev/null

if [[ "$(wc -l < "$TEMP/state-maintain/maintain/attempts.tsv" | tr -d ' ')" != 3 ]]; then
  echo "attempt state did not record all processed PRs" >&2
  exit 1
fi

if [[ "$(paste -sd ',' "$TEMP/runner.log")" != 'example/repo#1,example/repo#2,example/repo#3' ]]; then
  echo "runner order did not use creation time for unseen PRs" >&2
  exit 1
fi

if ! grep -Fq 'status=success' "$TEMP/state-maintain/maintain/health.env"; then
  echo "successful run did not publish health state" >&2
  exit 1
fi

if ! grep -Fq 'Expected head SHA: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' "$TEMP/state-maintain/maintain/prompt-example-repo-1.md"; then
  echo "prompt did not pin expected head SHA" >&2
  exit 1
fi

set +e
PR_AUTOMATION_TIMEOUT_BIN="$TEMP/fake-timeout" \
PR_AUTOMATION_DRY_RUN=true \
"$ROOT/bin/pr-automation" review >/dev/null 2>&1
no_scope_status=$?
set -e
if [[ "$no_scope_status" != 2 ]]; then
  echo "missing repository scope did not fail closed" >&2
  exit 1
fi

scoped_output="$(
  PR_AUTOMATION_STATE_ROOT="$TEMP/state-scope" \
  PR_AUTOMATION_LOG_ROOT="$TEMP/logs-scope" \
  PR_AUTOMATION_WORK_ROOT="$TEMP/work-scope" \
  PR_AUTOMATION_INPUT_FILE="$TEMP/prs.tsv" \
  PR_AUTOMATION_REPOSITORIES='other/repo' \
  PR_AUTOMATION_TIMEOUT_BIN="$TEMP/fake-timeout" \
  PR_AUTOMATION_DRY_RUN=true \
  "$ROOT/bin/pr-automation" review
)"
if grep -Fq 'dry run would process' <<< "$scoped_output"; then
  echo "input fixture bypassed repository scope" >&2
  exit 1
fi

set +e
PR_AUTOMATION_INPUT_FILE="$TEMP/prs.tsv" \
PR_AUTOMATION_ONLY='example/repo#1' \
PR_AUTOMATION_REPOSITORIES='example/repo' \
PR_AUTOMATION_TIMEOUT_BIN="$TEMP/fake-timeout" \
PR_AUTOMATION_DRY_RUN=true \
"$ROOT/bin/pr-automation" review >/dev/null 2>&1
input_target_status=$?
set -e
if [[ "$input_target_status" != 2 ]]; then
  echo "input file plus target did not fail closed" >&2
  exit 1
fi

echo "scheduler tests passed"
