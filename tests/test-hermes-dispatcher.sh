#!/usr/bin/env bash
# The dispatcher spawns executors as work arrives, never exceeding the per-kind cap, and only when
# there is unclaimed depth. Fake psql reports a large depth; fake executors block on a fifo so we can
# observe the peak concurrency. Assert pr-maintain peaks at exactly its cap (3), review/swe/doc at 1.
set -euo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"; [[ -n "${disp:-}" ]] && kill "$disp" 2>/dev/null || true' EXIT
mkdir -p "$tmp/bin" "$tmp/libexec" "$tmp/run"

# Fake psql: hermes_queue_depth('<kind>') -> 99 for maintain/review/swe/doc, 0 for memory-curate.
cat > "$tmp/bin/psql" <<'SH'
#!/usr/bin/env bash
args="$*"
if [[ "$args" == *"hermes_queue_depth('pr-maintain')"* ]]; then echo 99
elif [[ "$args" == *"hermes_queue_depth('pr-review')"* ]]; then echo 99
elif [[ "$args" == *"hermes_queue_depth('swe-implement')"* ]]; then echo 99
elif [[ "$args" == *"hermes_queue_depth('doc-write')"* ]]; then echo 99
else echo 0; fi
SH
chmod +x "$tmp/bin/psql"

# Fake executor: record start, block until the fifo is fed, record stop. One script, symlinked as
# the queue runner; it derives its "kind" from argv so all kinds share it.
cat > "$tmp/libexec/hermes-queue-runner" <<SH
#!/usr/bin/env bash
kind="\$1"
[[ -n "\${HERMES_WORK_ROOT:-}" ]] || { echo 'missing HERMES_WORK_ROOT' >> "$tmp/run/log"; exit 2; }
echo "start \$kind \$\$" >> "$tmp/run/log"
# block so the dispatcher sees us as active; released when the test writes to the gate file
while [[ ! -e "$tmp/run/release" ]]; do sleep 0.1; done
echo "stop \$kind \$\$" >> "$tmp/run/log"
SH
cp "$tmp/libexec/hermes-queue-runner" "$tmp/libexec/hermes-memory-curate"
cat > "$tmp/libexec/hermes-doc-write-runner" <<SH
#!/usr/bin/env bash
echo "start doc-write \$\$" >> "$tmp/run/log"
while [[ ! -e "$tmp/run/release" ]]; do sleep 0.1; done
echo "stop doc-write \$\$" >> "$tmp/run/log"
SH
chmod +x "$tmp/libexec/hermes-queue-runner" "$tmp/libexec/hermes-memory-curate" "$tmp/libexec/hermes-doc-write-runner"

PATH="$tmp/bin:$PATH" HERMES_NATIVE_SUPPORT_ROOT="$tmp/libexec" \
  HERMES_MAINTENANCE_FILE="$tmp/nope" HERMES_DISPATCH_POLL_SECONDS=1 \
  REQUESTS_DB_USER=x PGPASSWORD=x \
  bin/hermes-dispatcher >/dev/null 2>&1 &
disp=$!

# Let it fill slots (blocked executors hold their slots).
sleep 4

count_starts() { local n; n="$(grep -c "start $1 " "$tmp/run/log" 2>/dev/null || true)"; printf '%s' "${n:-0}"; }
peak_maintain="$(count_starts pr-maintain)"; peak_maintain="${peak_maintain:-0}"
peak_review="$(count_starts pr-review)"; peak_review="${peak_review:-0}"
peak_swe="$(count_starts swe-implement)"; peak_swe="${peak_swe:-0}"
peak_doc="$(count_starts doc-write)"; peak_doc="${peak_doc:-0}"
peak_mem="$(count_starts memory-curate)"; peak_mem="${peak_mem:-0}"

# Release the blocked executors and stop the dispatcher.
touch "$tmp/run/release"; sleep 1; kill "$disp" 2>/dev/null || true; wait "$disp" 2>/dev/null || true

fail=0
[[ "$peak_maintain" == 3 ]] || { echo "FAIL: pr-maintain peaked at $peak_maintain, cap 3" >&2; fail=1; }
[[ "$peak_review" == 1 ]] || { echo "FAIL: pr-review peaked at $peak_review, cap 1" >&2; fail=1; }
[[ "$peak_swe" == 1 ]] || { echo "FAIL: swe-implement peaked at $peak_swe, cap 1" >&2; fail=1; }
[[ "$peak_doc" == 1 ]] || { echo "FAIL: doc-write peaked at $peak_doc, cap 1" >&2; fail=1; }
[[ "$peak_mem" == 0 ]] || { echo "FAIL: memory-curate spawned $peak_mem with depth 0" >&2; fail=1; }
if grep -q 'missing HERMES_WORK_ROOT' "$tmp/run/log"; then echo 'FAIL: dispatcher omitted executor work root' >&2; fail=1; fi
(( fail == 0 )) || exit 1

echo 'PASS: dispatcher honors per-kind caps and only spawns on unclaimed depth'
