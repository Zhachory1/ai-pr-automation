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
example/repo	3	https://example.invalid/3	new	false	2026-01-03T00:00:00Z	3000	300	cccccccccccccccccccccccccccccccccccccccc
example/repo	1	https://example.invalid/1	old	false	2026-01-01T00:00:00Z	1000	100	aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
example/repo	2	https://example.invalid/2	middle	false	2026-01-02T00:00:00Z	2000	200	bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
TSV

mkdir -p "$TEMP/state/review"
printf 'example/repo#1\t400\n' > "$TEMP/state/review/attempts.tsv"

output="$(
  PR_AUTOMATION_STATE_ROOT="$TEMP/state" \
  PR_AUTOMATION_LOG_ROOT="$TEMP/logs" \
  PR_AUTOMATION_WORK_ROOT="$TEMP/work" \
  PR_AUTOMATION_INPUT_FILE="$TEMP/prs.tsv" \
  PR_AUTOMATION_REPOSITORIES='example/repo' \
  PR_AUTOMATION_ORDER=least-attempted \
  PR_AUTOMATION_MAX_UPDATED_AGE_DAYS=0 \
  PR_AUTOMATION_MAX_PR_AGE_DAYS=0 \
  PR_AUTOMATION_SKIP_REVIEWED_HEADS=false \
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

updated_output="$(
  PR_AUTOMATION_STATE_ROOT="$TEMP/state-updated" \
  PR_AUTOMATION_LOG_ROOT="$TEMP/logs-updated" \
  PR_AUTOMATION_WORK_ROOT="$TEMP/work-updated" \
  PR_AUTOMATION_INPUT_FILE="$TEMP/prs.tsv" \
  PR_AUTOMATION_REPOSITORIES='example/repo' \
  PR_AUTOMATION_ORDER=updated \
  PR_AUTOMATION_MAX_UPDATED_AGE_DAYS=0 \
  PR_AUTOMATION_MAX_PR_AGE_DAYS=0 \
  PR_AUTOMATION_SKIP_REVIEWED_HEADS=false \
  PR_AUTOMATION_DRY_RUN=true \
  "$ROOT/bin/pr-automation" review
)"
updated_order="$(printf '%s\n' "$updated_output" | awk '/dry run would process/ {print $NF}' | paste -sd ',' -)"
if [[ "$updated_order" != 'example/repo#3,example/repo#2,example/repo#1' ]]; then
  echo "review queue did not prioritize newest activity: $updated_order" >&2
  exit 1
fi

mkdir -p "$TEMP/state-hybrid/review"
now="$(date +%s)"
printf 'example/repo#1\t%s\nexample/repo#2\t%s\nexample/repo#3\t%s\n' "$((now - 10000))" "$((now - 100))" "$((now - 50))" > "$TEMP/state-hybrid/review/attempts.tsv"
hybrid_output="$(
  PR_AUTOMATION_STATE_ROOT="$TEMP/state-hybrid" \
  PR_AUTOMATION_LOG_ROOT="$TEMP/logs-hybrid" \
  PR_AUTOMATION_WORK_ROOT="$TEMP/work-hybrid" \
  PR_AUTOMATION_INPUT_FILE="$TEMP/prs.tsv" \
  PR_AUTOMATION_REPOSITORIES='example/repo' \
  PR_AUTOMATION_ORDER=hybrid \
  PR_AUTOMATION_MAX_UPDATED_AGE_DAYS=0 \
  PR_AUTOMATION_MAX_PR_AGE_DAYS=0 \
  PR_AUTOMATION_SKIP_REVIEWED_HEADS=false \
  PR_AUTOMATION_DRY_RUN=true \
  "$ROOT/bin/pr-automation" review
)"
hybrid_order="$(printf '%s\n' "$hybrid_output" | awk '/dry run would process/ {print $NF}' | paste -sd ',' -)"
if [[ "$hybrid_order" != 'example/repo#1,example/repo#3,example/repo#2' ]]; then
  echo "hybrid queue did not age overdue work forward: $hybrid_order" >&2
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

cat > "$TEMP/gh" <<'SH'
#!/usr/bin/env bash
if [[ "${1:-}" == api ]]; then
  for sha in \
    aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
    bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb \
    cccccccccccccccccccccccccccccccccccccccc; do
    printf '%s\t<!-- ai-pr-automation head=%s -->\n' "${FAKE_GH_LOGIN:-fixture-user}" "$sha"
  done
  exit 0
fi
exit 1
SH

chmod +x "$TEMP/fake-timeout" "$TEMP/fake-runner" "$TEMP/gh"
export RUNNER_LOG="$TEMP/runner.log"

marker_output="$(
  PATH="$TEMP:$PATH" \
  FAKE_GH_LOGIN=fixture-user \
  PR_AUTOMATION_STATE_ROOT="$TEMP/state-marker" \
  PR_AUTOMATION_LOG_ROOT="$TEMP/logs-marker" \
  PR_AUTOMATION_WORK_ROOT="$TEMP/work-marker" \
  PR_AUTOMATION_INPUT_FILE="$TEMP/prs.tsv" \
  PR_AUTOMATION_REPOSITORIES='example/repo' \
  PR_AUTOMATION_ORDER=updated \
  PR_AUTOMATION_MAX_UPDATED_AGE_DAYS=0 \
  PR_AUTOMATION_MAX_PR_AGE_DAYS=0 \
  PR_AUTOMATION_TIMEOUT_BIN="$TEMP/fake-timeout" \
  PR_AUTOMATION_DRY_RUN=true \
  "$ROOT/bin/pr-automation" review
)"
if [[ "$(grep -c 'exact review marker already exists' <<< "$marker_output")" != 3 ]]; then
  echo "own exact-head markers did not pre-skip reviews" >&2
  exit 1
fi

foreign_marker_output="$(
  PATH="$TEMP:$PATH" \
  FAKE_GH_LOGIN=other-user \
  PR_AUTOMATION_STATE_ROOT="$TEMP/state-foreign-marker" \
  PR_AUTOMATION_LOG_ROOT="$TEMP/logs-foreign-marker" \
  PR_AUTOMATION_WORK_ROOT="$TEMP/work-foreign-marker" \
  PR_AUTOMATION_INPUT_FILE="$TEMP/prs.tsv" \
  PR_AUTOMATION_REPOSITORIES='example/repo' \
  PR_AUTOMATION_ORDER=updated \
  PR_AUTOMATION_MAX_UPDATED_AGE_DAYS=0 \
  PR_AUTOMATION_MAX_PR_AGE_DAYS=0 \
  PR_AUTOMATION_TIMEOUT_BIN="$TEMP/fake-timeout" \
  PR_AUTOMATION_DRY_RUN=true \
  "$ROOT/bin/pr-automation" review
)"
if [[ "$(grep -c 'dry run would process' <<< "$foreign_marker_output")" != 3 ]]; then
  echo "foreign markers incorrectly suppressed reviews" >&2
  exit 1
fi

PR_AUTOMATION_STATE_ROOT="$TEMP/state-maintain" \
PR_AUTOMATION_LOG_ROOT="$TEMP/logs-maintain" \
PR_AUTOMATION_WORK_ROOT="$TEMP/work-maintain" \
PR_AUTOMATION_INPUT_FILE="$TEMP/prs.tsv" \
PR_AUTOMATION_REPOSITORIES='example/repo' \
PR_AUTOMATION_TIMEOUT_BIN="$TEMP/fake-timeout" \
PR_AUTOMATION_RUNNER="$TEMP/fake-runner" \
PR_AUTOMATION_RUN_BUDGET_SECONDS=3300 \
PR_AUTOMATION_MAX_UPDATED_AGE_DAYS=0 \
PR_AUTOMATION_MAX_PR_AGE_DAYS=0 \
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
  PR_AUTOMATION_MAX_UPDATED_AGE_DAYS=0 \
  PR_AUTOMATION_MAX_PR_AGE_DAYS=0 \
  PR_AUTOMATION_SKIP_REVIEWED_HEADS=false \
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
