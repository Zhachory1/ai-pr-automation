#!/usr/bin/env bash
# Agent-fleet queue access layer. All request-table SQL lives here, parameterized.
# COUNCIL INVARIANT: request data is NEVER string-interpolated into SQL. Every value is passed
# via psql -v with a quoted binding. Tested by tests/test-queue-injection.sh (';DROP fixture).
set -euo pipefail

: "${REQUESTS_DB_USER:=fleet}"
: "${REQUESTS_DB_NAME:=fleet}"
: "${REQUESTS_DB_HOST:=localhost}"
: "${REQUESTS_DB_PORT:=5432}"
# PGPASSWORD expected in env (from .env / launchd keychain), never on the command line.

QUEUE_LEASE_SECONDS="${QUEUE_LEASE_SECONDS:-120}"

_psql() {
  # -qAt: quiet, unaligned, tuples-only. Returns are single JSON objects (claim) or scalars;
  # no multi-column tab-separated output, so no field separator is needed.
  # ON_ERROR_STOP so failures are fatal to callers.
  psql -v ON_ERROR_STOP=1 -qAt \
    -h "$REQUESTS_DB_HOST" -p "$REQUESTS_DB_PORT" \
    -U "$REQUESTS_DB_USER" -d "$REQUESTS_DB_NAME" "$@"
}

# Reclaim only expired leases for this kind. Posted work is terminal; an expired older head is
# superseded when a newer head is already queued; otherwise it becomes claimable again.
queue_reclaim_stale() {
  local kind="${1:?kind}"
  _psql -v kind="$kind" <<'SQL'
BEGIN;
UPDATE requests
   SET status = 'done', finished_at = clock_timestamp(), lease_expires_at = NULL
 WHERE status = 'running' AND kind = :'kind'
   AND (lease_expires_at IS NULL OR lease_expires_at <= clock_timestamp())
   AND posted_ref IS NOT NULL;

UPDATE requests
   SET status = 'reconcile', finished_at = clock_timestamp(), lease_expires_at = NULL,
       fail_response = 'lease expired after maintenance side-effect intent; reconcile before retry'
 WHERE status = 'running' AND kind = 'pr-maintain'
   AND (lease_expires_at IS NULL OR lease_expires_at <= clock_timestamp())
   AND posted_ref IS NULL AND side_effect_at IS NOT NULL;

UPDATE requests r
   SET status = 'superseded', finished_at = clock_timestamp(), lease_expires_at = NULL,
       fail_response = 'expired lease superseded by newer queued head'
 WHERE status = 'running' AND kind = :'kind'
   AND (lease_expires_at IS NULL OR lease_expires_at <= clock_timestamp())
   AND posted_ref IS NULL
   AND EXISTS (
     SELECT 1 FROM requests q
      WHERE q.kind = r.kind AND q.status = 'queued'
        AND split_part(q.dedupe_key, '@', 1) = split_part(r.dedupe_key, '@', 1)
   );

UPDATE requests r
   SET status = 'queued', started_at = NULL, run_id = NULL, run_nonce = NULL,
       side_effect_at = NULL, lease_expires_at = NULL
 WHERE status = 'running' AND kind = :'kind'
   AND (lease_expires_at IS NULL OR lease_expires_at <= clock_timestamp())
   AND posted_ref IS NULL
   AND NOT EXISTS (
     SELECT 1 FROM requests q
      WHERE q.kind = r.kind AND q.status = 'queued'
        AND split_part(q.dedupe_key, '@', 1) = split_part(r.dedupe_key, '@', 1)
   );
COMMIT;
SQL
}

# Reclaim expired attempts, then atomically claim one queued row for this kind. The newest queued
# head is canonical within each PR lineage; SKIP LOCKED distributes different lineages across
# workers. The partial unique index is the final same-lineage concurrency guard.
queue_claim_one() {
  local kind="${1:?kind}" run_id="$2" nonce="$3" lease_seconds="${4:-$QUEUE_LEASE_SECONDS}"
  queue_reclaim_stale "$kind" >/dev/null
  _psql -v kind="$kind" -v run_id="$run_id" -v nonce="$nonce" -v lease="$lease_seconds" <<'SQL'
WITH candidate AS (
  SELECT r.id, split_part(r.dedupe_key, '@', 1) AS lineage
    FROM requests r
   WHERE r.status = 'queued' AND r.kind = :'kind'
     AND NOT EXISTS (
       SELECT 1 FROM requests active
        WHERE active.kind = r.kind AND active.status = 'running'
          AND split_part(active.dedupe_key, '@', 1) = split_part(r.dedupe_key, '@', 1)
     )
     AND NOT EXISTS (
       SELECT 1 FROM requests newer
        WHERE newer.kind = r.kind AND newer.status = 'queued'
          AND split_part(newer.dedupe_key, '@', 1) = split_part(r.dedupe_key, '@', 1)
          AND (newer.created_at, newer.id) > (r.created_at, r.id)
     )
   ORDER BY r.created_at, r.id
   FOR UPDATE OF r SKIP LOCKED
   LIMIT 1
), stale AS (
  UPDATE requests r
     SET status = 'superseded', finished_at = clock_timestamp(),
         fail_response = 'superseded by newer queued head'
    FROM candidate c
   WHERE r.kind = :'kind' AND r.status = 'queued' AND r.id <> c.id
     AND split_part(r.dedupe_key, '@', 1) = c.lineage
), claimed AS (
  UPDATE requests r
     SET status = 'running', started_at = clock_timestamp(),
         run_id = :'run_id', run_nonce = :'nonce',
         lease_expires_at = clock_timestamp() + make_interval(secs => :'lease'::int)
    FROM candidate c
   WHERE r.id = c.id
  RETURNING r.*
)
SELECT json_build_object('id', id, 'kind', kind, 'payload', payload, 'dedupe_key', dedupe_key)::text
  FROM claimed;
SQL
}

# Returns renewed, finished, or lost. A nonce may never revive an already-expired lease.
queue_renew_lease() {
  local id="$1" nonce="$2" lease_seconds="${3:-$QUEUE_LEASE_SECONDS}"
  _psql -v id="$id" -v nonce="$nonce" -v lease="$lease_seconds" <<'SQL'
WITH renewed AS (
  UPDATE requests
     SET lease_expires_at = clock_timestamp() + make_interval(secs => :'lease'::int)
   WHERE id = :'id' AND status = 'running' AND run_nonce = :'nonce'
     AND lease_expires_at > clock_timestamp()
  RETURNING 1
)
SELECT CASE
  WHEN EXISTS (SELECT 1 FROM renewed) THEN 'renewed'
  WHEN EXISTS (SELECT 1 FROM requests WHERE id = :'id' AND status <> 'running') THEN 'finished'
  ELSE 'lost'
END;
SQL
}

# Every attempt-owned transition is fenced by nonce and an unexpired lease. Prints 1 on success.
queue_mark_side_effect() {
  local id="$1" nonce="$2"
  _psql -v id="$id" -v nonce="$nonce" \
    <<<"UPDATE requests SET side_effect_at=clock_timestamp() WHERE id=:'id' AND status='running' AND run_nonce=:'nonce' AND lease_expires_at>clock_timestamp() RETURNING 1;"
}

queue_mark_done() {
  local id="$1" posted_ref="${2:-}" nonce="$3"
  _psql -v id="$id" -v ref="$posted_ref" -v nonce="$nonce" \
    <<<"UPDATE requests SET status='done', finished_at=clock_timestamp(), posted_ref=NULLIF(:'ref',''), lease_expires_at=NULL WHERE id=:'id' AND status='running' AND run_nonce=:'nonce' AND lease_expires_at>clock_timestamp() RETURNING 1;"
}

queue_mark_failed() {
  local id="$1" reason="$2" nonce="$3"
  _psql -v id="$id" -v reason="$reason" -v nonce="$nonce" \
    <<<"UPDATE requests SET status='failed', finished_at=clock_timestamp(), fail_response=:'reason', lease_expires_at=NULL WHERE id=:'id' AND status='running' AND run_nonce=:'nonce' AND lease_expires_at>clock_timestamp() RETURNING 1;"
}

queue_mark_reconcile() {
  local id="$1" reason="$2" nonce="$3"
  _psql -v id="$id" -v reason="$reason" -v nonce="$nonce" \
    <<<"UPDATE requests SET status='reconcile', finished_at=clock_timestamp(), fail_response=:'reason', lease_expires_at=NULL WHERE id=:'id' AND status='running' AND run_nonce=:'nonce' AND lease_expires_at>clock_timestamp() RETURNING 1;"
}

queue_mark_superseded() {
  local id="$1" reason="$2" nonce="$3"
  _psql -v id="$id" -v reason="$reason" -v nonce="$nonce" \
    <<<"UPDATE requests SET status='superseded', finished_at=clock_timestamp(), fail_response=:'reason', lease_expires_at=NULL WHERE id=:'id' AND status='running' AND run_nonce=:'nonce' AND lease_expires_at>clock_timestamp() RETURNING 1;"
}

# DB-side record that a review was posted (self-describing row; verifiable without a GitHub call).
# NOTE: this is NOT the idempotency gate — that is github_already_reviewed (crash-proof marker on
# GitHub itself). posted_ref can be NULL after a crash-between-post-and-mark_done; do not rely on it
# to prevent double-posting.
queue_already_posted() {
  local kind="$1" dedupe_key="$2"
  local n
  n="$(_psql -v kind="$kind" -v dk="$dedupe_key" \
    <<<"SELECT count(*) FROM requests WHERE kind = :'kind' AND dedupe_key = :'dk' AND posted_ref IS NOT NULL;")"
  [[ "${n:-0}" != "0" ]]
}

# Enqueue (used by producers). Payload passed as jsonb param. Prints '1' if a row was inserted,
# empty if suppressed (so callers can distinguish real enqueues from dedupe no-ops).
# Dedupe is two-layered: the partial unique index blocks a second active (queued|running) row, and
# the NOT EXISTS guard skips re-enqueuing a key that is already queued/running/DONE (same head
# already handled) so producers don't create churn rows. A 'failed' head IS allowed to re-enqueue
# (transient agent/network error should be retried; permanent poison-PR protection is a future
# max_attempts concern, not a permanent dead-letter here). Re-review on a NEW head still works
# because a new head = a new dedupe_key.
# Atomically records a validated merged-PR event and enqueues its safety job at most once. The event
# identity is the merge commit SHA; only its canonical payload digest is stored, not PR text.
# dedupe_key is "<repo>#<pr>@<merge_sha>"; lineage is "<repo>#<pr>" (the part before '@', which neither
# repo nor PR number can contain). When a new head enqueues, any still-QUEUED review for the same PR
# at an older head is superseded (stale head: no point reviewing code a newer merge already replaced).
# A RUNNING review is left alone (mid-analysis; killing it would waste the in-flight work), and DONE
# reviews are history. Matched via split_part on '@' to avoid LIKE-wildcard hazards in repo names.
queue_enqueue_pr_safety_merged_pr_event() {
  local merge_sha="$1" event_digest="$2" payload_json="$3" dedupe_key="$4" lineage="$5"
  local max_attempts="${PR_PRODUCER_MAX_ATTEMPTS:-3}"
  _psql -v merge_sha="$merge_sha" -v event_digest="$event_digest" -v payload="$payload_json" -v dk="$dedupe_key" -v lineage="$lineage" -v maxatt="$max_attempts" <<'SQL'
WITH event AS (
  INSERT INTO pr_safety_merged_pr_events(merge_sha, payload_digest)
  VALUES (:'merge_sha', :'event_digest')
  ON CONFLICT (merge_sha) DO NOTHING
  RETURNING 1
), request AS (
  INSERT INTO requests(kind, payload, dedupe_key)
  SELECT 'pr-safety-review', :'payload'::jsonb, :'dk'
   WHERE EXISTS (SELECT 1 FROM event)
     AND NOT EXISTS (
       SELECT 1 FROM requests
        WHERE kind = 'pr-safety-review' AND dedupe_key = :'dk'
          AND status IN ('queued','running','done','reconcile')
     )
     AND (
       :'maxatt' = '0'
       OR (SELECT count(*) FROM requests
             WHERE kind = 'pr-safety-review' AND dedupe_key = :'dk' AND status = 'failed') < :'maxatt'::int
     )
  ON CONFLICT DO NOTHING
  RETURNING 1
), superseded AS (
  UPDATE requests
     SET status = 'superseded', finished_at = now(),
         fail_response = 'superseded by newer head ' || :'dk'
   WHERE EXISTS (SELECT 1 FROM request)
     AND kind = 'pr-safety-review'
     AND status = 'queued'
     AND dedupe_key <> :'dk'
     AND split_part(dedupe_key, '@', 1) = :'lineage'
  RETURNING 1
)
SELECT CASE WHEN EXISTS (SELECT 1 FROM request) THEN '1' ELSE '' END;
SQL
}

# Prints '1' if a review for this operation_id is still queued or running (in-flight), else empty.
# Used by snapshot GC to avoid deleting a snapshot an active analysis still needs.
queue_pr_safety_operation_active() {
  local op="$1"
  _psql -v op="$op" <<'SQL'
SELECT CASE WHEN EXISTS (
  SELECT 1 FROM requests
   WHERE kind = 'pr-safety-review'
     AND status IN ('queued','running')
     AND payload->>'operation_id' = :'op'
) THEN '1' ELSE '' END;
SQL
}

# Enqueue a swe-implement request (kind='swe-implement'). payload_json carries the harness inputs
# (source + handoff_path/issue/prompt+repo + optional no_pr). dedupe_key identifies the task so the
# same task cannot sit queued/running twice. Prints '1' if a row was inserted, empty if suppressed
# (a matching task already queued/running). Unlike PR-safety, a done/failed task CAN be re-enqueued
# (operator may deliberately retry), so only active rows suppress.
queue_enqueue_swe_implement() {
  local payload_json="$1" dedupe_key="$2"
  _psql -v payload="$payload_json" -v dk="$dedupe_key" <<'SQL'
INSERT INTO requests(kind, payload, dedupe_key)
SELECT 'swe-implement', :'payload'::jsonb, :'dk'
WHERE NOT EXISTS (
  SELECT 1 FROM requests
   WHERE kind = 'swe-implement' AND dedupe_key = :'dk' AND status IN ('queued','running')
)
ON CONFLICT DO NOTHING
RETURNING 1;
SQL
}

queue_enqueue() {
  local kind="$1" payload_json="$2" dedupe_key="$3"
  # Cap failed retries: a head that has already FAILED >= max_attempts times is a poison PR
  # (persistent checkout/tool error). Stop re-enqueuing it so it can't retry-loop forever and
  # burn tokens unattended. Default 3; 0 disables the cap. Dedup of queued/running/done is
  # unchanged — an unchanged head is still never re-reviewed.
  local max_attempts="${PR_PRODUCER_MAX_ATTEMPTS:-3}"
  # When a new head enqueues, supersede any still-QUEUED row for the SAME kind+PR lineage at an
  # older head (stale head: no point reviewing code a newer commit already replaced). A RUNNING
  # row is left alone (mid-analysis). Lineage is the part before '@' (repo#pr); neither a repo nor
  # a PR number can contain '@', so split_part is safe. This mirrors the merged-PR producer and
  # stops maintain/review PRs whose branch head advances each cycle from stacking duplicate rows.
  _psql -v kind="$kind" -v payload="$payload_json" -v dk="$dedupe_key" -v maxatt="$max_attempts" <<'SQL'
WITH request AS (
  INSERT INTO requests(kind, payload, dedupe_key)
  SELECT :'kind', :'payload'::jsonb, :'dk'
  WHERE NOT EXISTS (
    SELECT 1 FROM requests
     WHERE kind = :'kind' AND dedupe_key = :'dk'
       AND status IN ('queued','running','done','reconcile')
  )
  AND (
    :'maxatt' = '0'
    OR (SELECT count(*) FROM requests
          WHERE kind = :'kind' AND dedupe_key = :'dk' AND status = 'failed') < :'maxatt'::int
  )
  ON CONFLICT DO NOTHING
  RETURNING 1
), superseded AS (
  UPDATE requests
     SET status = 'superseded', finished_at = now(),
         fail_response = 'superseded by newer head ' || :'dk'
   WHERE EXISTS (SELECT 1 FROM request)
     AND kind = :'kind'
     AND status = 'queued'
     AND dedupe_key <> :'dk'
     AND split_part(dedupe_key, '@', 1) = split_part(:'dk', '@', 1)
  RETURNING 1
)
SELECT CASE WHEN EXISTS (SELECT 1 FROM request) THEN '1' ELSE '' END;
SQL
}

# Blocked PR maintenance waits in a separate queue: completing it cannot approve a memory proposal.
pending_maintenance_review_insert() {
  local request_id="$1" proposal_json="$2" provenance_json="$3" nonce="$4"
  _psql -v rid="$request_id" -v prop="$proposal_json" -v prov="$provenance_json" -v nonce="$nonce" <<'SQL' \
    | grep -qx 1
WITH eligible AS (
  SELECT 1 FROM requests
   WHERE id = :'rid' AND status = 'running' AND run_nonce = :'nonce'
     AND lease_expires_at > clock_timestamp()
   FOR UPDATE
), inserted AS (
  INSERT INTO pending_maintenance_reviews(request_id, proposal, provenance)
  SELECT :'rid', :'prop'::jsonb, :'prov'::jsonb FROM eligible
  ON CONFLICT (request_id) DO NOTHING
  RETURNING 1
)
SELECT CASE WHEN EXISTS (SELECT 1 FROM eligible) THEN 1 ELSE 0 END;
SQL
}

pending_maintenance_review_operation_exists() {
  local operation_id="$1"
  _psql -v op="$operation_id" \
    <<<"SELECT EXISTS (SELECT 1 FROM pending_maintenance_reviews WHERE provenance->>'operation_id' = :'op');" \
    | grep -qx t
}
