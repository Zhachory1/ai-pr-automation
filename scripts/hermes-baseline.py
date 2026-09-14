#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
from datetime import datetime, timezone

SQL = r"""
WITH bounds AS (
  SELECT :'start'::timestamptz AS start_at, :'end'::timestamptz AS end_at
),
created AS (
  SELECT id, kind, status, dedupe_key, created_at,
         row_number() OVER (PARTITION BY kind, dedupe_key ORDER BY created_at, id) AS window_attempt
    FROM requests r, bounds b
   WHERE r.created_at >= b.start_at AND r.created_at < b.end_at
),
terminal AS (
  SELECT id, kind, status, created_at, finished_at
    FROM requests r, bounds b
   WHERE r.finished_at >= b.start_at AND r.finished_at < b.end_at
     AND r.status IN ('done','failed','skipped','superseded','reconcile')
),
counts AS (
  SELECT (SELECT count(*) FROM created) AS created_count,
         (SELECT count(*) FROM terminal) AS terminal_count,
         (SELECT count(*) FROM terminal WHERE status = 'failed') AS failed_count,
         (SELECT count(*) FROM created WHERE window_attempt > 1) AS retry_count,
         (SELECT count(*) FROM terminal WHERE status = 'reconcile') AS reconcile_count,
         (SELECT count(*) FROM terminal WHERE status = 'superseded') AS superseded_count
),
request_groups AS (
  SELECT kind, status, count(*) AS count
    FROM created
   GROUP BY kind, status
),
request_group_json AS (
  SELECT coalesce(jsonb_agg(jsonb_build_object(
           'kind', kind, 'status', status, 'count', count
         ) ORDER BY kind, status), '[]'::jsonb) AS value
    FROM request_groups
),
request_percentiles AS (
  SELECT
    (SELECT percentile_disc(ARRAY[0.50,0.95,0.99]) WITHIN GROUP
              (ORDER BY extract(epoch FROM (b.end_at-r.created_at)))
       FROM requests r, bounds b WHERE r.status = 'queued' AND r.created_at < b.end_at) AS queue_pct,
    (SELECT percentile_disc(ARRAY[0.50,0.95,0.99]) WITHIN GROUP
              (ORDER BY extract(epoch FROM (r.started_at-r.created_at)))
       FROM requests r, bounds b WHERE r.started_at >= b.start_at AND r.started_at < b.end_at) AS start_pct,
    (SELECT percentile_disc(ARRAY[0.50,0.95,0.99]) WITHIN GROUP
              (ORDER BY extract(epoch FROM (r.finished_at-r.created_at)))
       FROM terminal r) AS completion_pct
),
human_groups AS (
  SELECT 'maintenance'::text AS queue, state, count(*) AS count
    FROM pending_maintenance_reviews, bounds
   WHERE (state = 'pending' AND created_at < end_at)
      OR (state IN ('reviewed','dismissed') AND reviewed_at >= start_at AND reviewed_at < end_at)
   GROUP BY state
  UNION ALL
  SELECT 'decision'::text AS queue, state, count(*) AS count
    FROM pending_decisions, bounds
   WHERE (state IN ('pending','publishing') AND created_at < end_at)
      OR (state IN ('approved','rejected') AND decided_at >= start_at AND decided_at < end_at)
   GROUP BY state
),
human_group_json AS (
  SELECT coalesce(jsonb_agg(jsonb_build_object(
           'queue', queue, 'state', state, 'count', count
         ) ORDER BY queue, state), '[]'::jsonb) AS value
    FROM human_groups
),
pending AS (
  SELECT created_at FROM pending_maintenance_reviews, bounds
   WHERE state = 'pending' AND created_at < end_at
  UNION ALL
  SELECT created_at FROM pending_decisions, bounds
   WHERE state IN ('pending','publishing') AND created_at < end_at
),
human_pending AS (
  SELECT count(p.created_at) AS count,
         max(extract(epoch FROM (b.end_at-p.created_at))) AS oldest_age
    FROM bounds b LEFT JOIN pending p ON true
)
SELECT jsonb_build_object(
  'schema_version', 1,
  'window', jsonb_build_object(
    'start', to_char(b.start_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    'end', to_char(b.end_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    'seconds', extract(epoch FROM (b.end_at-b.start_at))
  ),
  'percentile_method', 'percentile_disc',
  'snapshot_semantics', 'queue and pending values are current snapshots; end must be within five minutes of collection',
  'metric_semantics', jsonb_build_object(
    'by_kind_status', 'requests created in window, current status at collection',
    'retry', 'second and later same-kind same-dedupe attempts created in window / requests created in window',
    'start_lag', 'requests started in window',
    'throughput_and_completion', 'terminal requests finished in window',
    'terminal_rates', 'matching terminal requests / terminal requests finished in window',
    'human_dispositions', 'reviewed, dismissed, approved, or rejected in window; pending and publishing are current backlog snapshots'
  ),
  'requests', jsonb_build_object(
    'by_kind_status', rg.value,
    'throughput', jsonb_build_object(
      'count', c.terminal_count,
      'denominator_seconds', extract(epoch FROM (b.end_at-b.start_at)),
      'per_hour', round(c.terminal_count::numeric * 3600 / extract(epoch FROM (b.end_at-b.start_at)), 6)
    ),
    'queue_age_seconds', jsonb_build_object('p50', p.queue_pct[1], 'p95', p.queue_pct[2], 'p99', p.queue_pct[3]),
    'start_lag_seconds', jsonb_build_object('p50', p.start_pct[1], 'p95', p.start_pct[2], 'p99', p.start_pct[3]),
    'completion_seconds', jsonb_build_object('p50', p.completion_pct[1], 'p95', p.completion_pct[2], 'p99', p.completion_pct[3]),
    'failure', jsonb_build_object(
      'count', c.failed_count, 'denominator', c.terminal_count,
      'rate', round(c.failed_count::numeric / nullif(c.terminal_count, 0), 6)
    ),
    'retry', jsonb_build_object(
      'count', c.retry_count, 'denominator', c.created_count,
      'rate', round(c.retry_count::numeric / nullif(c.created_count, 0), 6)
    ),
    'reconcile', jsonb_build_object(
      'count', c.reconcile_count, 'denominator', c.terminal_count,
      'rate', round(c.reconcile_count::numeric / nullif(c.terminal_count, 0), 6)
    ),
    'superseded', jsonb_build_object(
      'count', c.superseded_count, 'denominator', c.terminal_count,
      'rate', round(c.superseded_count::numeric / nullif(c.terminal_count, 0), 6)
    )
  ),
  'human', jsonb_build_object(
    'by_queue_state', hg.value,
    'pending_count', hp.count,
    'oldest_pending_age_seconds', hp.oldest_age
  ),
  'target_audit', jsonb_build_object(
    'duplicate_effects', jsonb_build_object('status', 'unavailable', 'count', null),
    'missed_eligible', jsonb_build_object('status', 'unavailable', 'count', null)
  )
)::text
FROM bounds b CROSS JOIN counts c CROSS JOIN request_group_json rg
CROSS JOIN request_percentiles p CROSS JOIN human_group_json hg CROSS JOIN human_pending hp;
"""


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp needs timezone: {value}")
    return parsed.astimezone(timezone.utc)


def iso(value):
    return value.isoformat().replace("+00:00", "Z")


def collect(start, end):
    command = [
        os.environ.get("PSQL_BIN", "psql"), "-X", "-qAt", "-v", "ON_ERROR_STOP=1",
        "-h", os.environ.get("REQUESTS_DB_HOST", "localhost"),
        "-p", os.environ.get("REQUESTS_DB_PORT", "5432"),
        "-U", os.environ.get("REQUESTS_DB_USER", "fleet"),
        "-d", os.environ.get("REQUESTS_DB_NAME", "fleet"),
        "-v", f"start={iso(start)}", "-v", f"end={iso(end)}",
    ]
    result = subprocess.run(command, input=SQL, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "psql failed")
    data = json.loads(result.stdout)
    data["window"]["start"], data["window"]["end"] = iso(start), iso(end)
    return data


def main():
    parser = argparse.ArgumentParser(description="Emit read-only Hermes migration baseline JSON")
    parser.add_argument("--start", required=True, help="inclusive ISO-8601 timestamp")
    parser.add_argument("--end", required=True, help="exclusive ISO-8601 timestamp; collect at this time")
    args = parser.parse_args()
    try:
        start, end = timestamp(args.start), timestamp(args.end)
    except ValueError as error:
        parser.error(str(error))
    if end <= start:
        parser.error("--end must be after --start")
    now = datetime.now(timezone.utc)
    if end > now or (now - end).total_seconds() > 300:
        parser.error("--end must be within five minutes of collection time and not in the future")
    print(json.dumps(collect(start, end), sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
