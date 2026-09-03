#!/usr/bin/env bash
# monitor-build.sh <pipeline> <build-number> [interval-seconds]
# Polls a Buildkite build until terminal, printing job states each cycle.

set -euo pipefail

PIPELINE="${1:?usage: monitor-build.sh <pipeline> <build-number>}"
BUILD="${2:?usage: monitor-build.sh <pipeline> <build-number>}"
INTERVAL="${3:-30}"

while true; do
    data=$(bk api "pipelines/${PIPELINE}/builds/${BUILD}")
    state=$(echo "${data}" | python3 -c "import sys,json; print(json.load(sys.stdin)['state'])")

    echo ""
    echo "=== Build #${BUILD} | ${PIPELINE} | state: ${state} | $(date -u '+%H:%M:%S UTC') ==="
    echo "${data}" | python3 -c "
import sys, json
jobs = json.load(sys.stdin).get('jobs', [])
for j in jobs:
    name = j.get('name', '')
    s = j.get('state', '')
    if not name:
        continue
    icons = {'passed':'✅','failed':'❌','running':'🔄','assigned':'🔄','waiting':'⏳','scheduled':'⏳','blocked':'🔒','broken':'💔'}
    ic = icons.get(s, '  ')
    print(f'  {ic} {s:20} {name}')
"

    case "${state}" in
    passed)
        echo ""
        echo "✅ Build passed."
        exit 0
        ;;
    failed)
        echo ""
        echo "❌ Build failed."
        exit 1
        ;;
    canceled)
        echo ""
        echo "🚫 Build canceled."
        exit 1
        ;;
    blocked)
        echo ""
        echo "🔒 Build blocked — waiting for manual unblock."
        ;;
    skipped)
        echo ""
        echo " Build skipped. Check if this build has been superseded."
        exit 2
        ;;
    not_run)
        echo ""
        echo " Build not run."
        exit 1
        ;;
    *)
        : # still running
        ;;
    esac

    echo "(next poll in ${INTERVAL}s — Ctrl-C to stop)"
    sleep "${INTERVAL}"
done
