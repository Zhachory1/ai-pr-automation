#!/usr/bin/env bash
# Interval loop for the containerized memory curator (compose has no cron). Runs the curator, then
# sleeps MEMORY_CURATOR_INTERVAL seconds, forever. Each run is bounded and watermarked, so a missed
# or failed run just retries its source window next time.
set -uo pipefail

INTERVAL="${MEMORY_CURATOR_INTERVAL:-21600}"   # default 6h
HERE="$(cd "$(dirname "$0")/.." && pwd)"
CURATOR="$HERE/bin/memory-curator"

log() { printf '%s memory-curator-loop %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*"; }
trap 'log "shutting down"; exit 0' TERM INT

: "${OPENAI_API_KEY:?set OPENAI_API_KEY}"
: "${HINDSIGHT_URL:?set HINDSIGHT_URL (e.g. http://hindsight:8888)}"

# Wait for hindsight before the first run so a cold start does not fail on connection refused.
for _ in $(seq 1 60); do
  curl -fsS "$HINDSIGHT_URL/health/ready" >/dev/null 2>&1 && break
  log "waiting for hindsight at $HINDSIGHT_URL"; sleep 3
done

log "curator loop up; interval ${INTERVAL}s"
while true; do
  log "starting curator run"
  "$CURATOR" || log "curator run exited non-zero (continuing)"
  log "run done; sleeping ${INTERVAL}s"
  sleep "$INTERVAL"
done
