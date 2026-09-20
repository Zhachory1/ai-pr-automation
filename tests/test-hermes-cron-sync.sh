#!/usr/bin/env bash
# hermes-cron-sync registers one PAUSED no-agent executor job per role, idempotently, using the
# installed executor as the job script. Uses a fake `hermes` launcher (no real install).
set -euo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/hermes/scripts"

# Fake hermes launcher: `cron list --all` prints the jobs recorded so far; `cron create` appends
# a job (asserting --no-agent and --paused are present); anything else exits 0.
cat > "$tmp/bin/hermes" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
[[ "${1:-}" == cron ]] || { echo "unexpected: $*" >&2; exit 2; }
shift
store="$FAKE_CRON_STORE"
case "${1:-}" in
  list)
    echo "Scheduled Jobs"
    [[ -f "$store" ]] && while IFS= read -r n; do echo "    Name:      $n"; done < "$store"
    ;;
  create)
    grep -q -- '--no-agent' <<<"$*" || { echo 'FAIL: create without --no-agent' >&2; exit 3; }
    grep -q -- '--paused' <<<"$*" || { echo 'FAIL: create without --paused' >&2; exit 3; }
    name=""; while (($#)); do [[ "$1" != --name ]] || { name="$2"; }; shift; done
    echo "$name" >> "$store"
    echo "Created job: $name (paused)"
    ;;
  *) exit 0 ;;
esac
SH
chmod +x "$tmp/bin/hermes"
store="$tmp/jobs.txt"; : > "$store"

run() { HERMES_BIN="$tmp/bin/hermes" HERMES_HOME="$tmp/hermes" FAKE_CRON_STORE="$store" \
        scripts/hermes-cron-sync.py "$@"; }

# Dry run creates nothing.
out="$(run)"
[[ "$(jq -r .mode <<<"$out")" == dry-run ]]
[[ "$(jq -r '.created|length' <<<"$out")" == 0 ]]
[[ "$(jq -r '.planned|length' <<<"$out")" == 3 ]]
[[ -s "$store" ]] && { echo 'FAIL: dry-run created jobs' >&2; exit 1; }

# Apply creates the three paused jobs.
out="$(run --apply)"
[[ "$(jq -r '.created|sort|join(",")' <<<"$out")" == \
   "ai-pr-automation-memory-curate,ai-pr-automation-pr-maintain,ai-pr-automation-pr-review" ]] \
  || { echo "FAIL: wrong created set: $out" >&2; exit 1; }
# wrapper scripts written and exec the installed executor
grep -q 'hermes-queue-runner pr-review' "$tmp/hermes/scripts/ai-pr-automation-pr-review.sh"
grep -q 'hermes-memory-curate' "$tmp/hermes/scripts/ai-pr-automation-memory-curate.sh"
# self-triggering role enqueues against the sentinel before claiming; repo roles do not
grep -q 'hermes_enqueue_local' "$tmp/hermes/scripts/ai-pr-automation-memory-curate.sh"
if grep -q 'hermes_enqueue_local' "$tmp/hermes/scripts/ai-pr-automation-pr-review.sh"; then
  echo 'FAIL: repo role should not self-enqueue' >&2; exit 1
fi

# Idempotent: second apply creates nothing (all exist) and does not rewrite unchanged wrappers.
out="$(run --apply)"
[[ "$(jq -r '.created|length' <<<"$out")" == 0 ]] || { echo 'FAIL: not idempotent' >&2; exit 1; }
[[ "$(jq -r '.existing|length' <<<"$out")" == 3 ]]
[[ "$(jq -r '.script_refreshed|length' <<<"$out")" == 0 ]] || { echo 'FAIL: rewrote unchanged wrapper' >&2; exit 1; }
[[ "$(wc -l < "$store" | tr -d ' ')" == 3 ]] || { echo 'FAIL: duplicate jobs created' >&2; exit 1; }

# A stale wrapper for an EXISTING job is refreshed in place (no new job created).
echo 'stale contents' > "$tmp/hermes/scripts/ai-pr-automation-memory-curate.sh"
out="$(run --apply)"
[[ "$(jq -r '.created|length' <<<"$out")" == 0 ]] || { echo 'FAIL: refresh created a job' >&2; exit 1; }
[[ "$(jq -r '.script_refreshed|join(",")' <<<"$out")" == 'ai-pr-automation-memory-curate' ]] \
  || { echo "FAIL: stale wrapper not refreshed: $out" >&2; exit 1; }
grep -q 'hermes_enqueue_local' "$tmp/hermes/scripts/ai-pr-automation-memory-curate.sh" \
  || { echo 'FAIL: refreshed wrapper missing self-enqueue' >&2; exit 1; }

echo 'PASS: hermes-cron-sync creates paused executor jobs idempotently'
