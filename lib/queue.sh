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

# Advisory lock key for single-instance enforcement (arbitrary stable int).
AGENT_SERVER_LOCK_KEY="${AGENT_SERVER_LOCK_KEY:-774411}"

_psql() {
  # -qAt: quiet, unaligned, tuples-only. Returns are single JSON objects (claim) or scalars;
  # no multi-column tab-separated output, so no field separator is needed.
  # ON_ERROR_STOP so failures are fatal to callers.
  psql -v ON_ERROR_STOP=1 -qAt \
    -h "$REQUESTS_DB_HOST" -p "$REQUESTS_DB_PORT" \
    -U "$REQUESTS_DB_USER" -d "$REQUESTS_DB_NAME" "$@"
}

# Hold a session advisory lock for the life of THIS psql session. Returns t/f.
# Caller must keep the returned coproc/session alive to hold the lock; see agent-server.
queue_try_single_instance_lock() {
  # session-level try lock; if another live server holds it, returns f.
  _psql <<<"SELECT pg_try_advisory_lock(${AGENT_SERVER_LOCK_KEY});"
}

# Startup reclaim (fail-closed, but uses posted_ref as the 'posted' truth so transient
# pre-post failures are safely retried instead of dead-lettered):
#   posted_ref present            -> genuinely posted; terminal 'done' (no re-run).
#   side_effect intent, not posted -> safe to requeue (head-dedup marker prevents double-post).
#   clean running                 -> requeue.
queue_reclaim_stale() {
  _psql <<'SQL'
UPDATE requests
   SET status = 'done', finished_at = now()
 WHERE status = 'running' AND posted_ref IS NOT NULL;

UPDATE requests
   SET status = 'queued', started_at = NULL, run_id = NULL, run_nonce = NULL, side_effect_at = NULL
 WHERE status = 'running' AND posted_ref IS NULL;
SQL
}

# Claim one queued row -> running, stamping run_id + nonce. Emits ONE json object (avoids
# tab/newline parsing hazards from untrusted dedupe_key/payload). Empty if queue empty.
# FOR UPDATE SKIP LOCKED keeps a future 2nd worker safe.
queue_claim_one() {
  local run_id="$1" nonce="$2"
  _psql -v run_id="$run_id" -v nonce="$nonce" <<'SQL'
WITH claimed AS (
  SELECT id FROM requests
   WHERE status = 'queued'
   ORDER BY created_at
   FOR UPDATE SKIP LOCKED
   LIMIT 1
)
UPDATE requests r
   SET status = 'running', started_at = now(),
       run_id = :'run_id', run_nonce = :'nonce'
  FROM claimed
 WHERE r.id = claimed.id
 RETURNING json_build_object('id', r.id, 'kind', r.kind, 'payload', r.payload, 'dedupe_key', r.dedupe_key)::text;
SQL
}

# Mark a side-effect intent in the SAME txn as the caller expects to act next. Advisory fence.
queue_mark_side_effect() {
  local id="$1"
  _psql -v id="$id" <<<"UPDATE requests SET side_effect_at = now() WHERE id = :'id' AND status='running';"
}

queue_mark_done() {
  local id="$1" posted_ref="${2:-}"
  _psql -v id="$id" -v ref="$posted_ref" \
    <<<"UPDATE requests SET status='done', finished_at=now(), posted_ref = NULLIF(:'ref','') WHERE id = :'id';"
}

queue_mark_failed() {
  local id="$1" reason="$2"
  _psql -v id="$id" -v reason="$reason" \
    <<<"UPDATE requests SET status='failed', finished_at=now(), fail_response = :'reason' WHERE id = :'id';"
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

# Enqueue (used by producers in M2; here for tests). Payload passed as jsonb param.
queue_enqueue() {
  local kind="$1" payload_json="$2" dedupe_key="$3"
  _psql -v kind="$kind" -v payload="$payload_json" -v dk="$dedupe_key" \
    <<<"INSERT INTO requests(kind, payload, dedupe_key) VALUES (:'kind', :'payload'::jsonb, :'dk') ON CONFLICT DO NOTHING;"
}

# Route an agent-authored body to the human batch. NEVER auto-writes shared memory.
pending_decision_insert() {
  local request_id="$1" kind="$2" proposal_json="$3" provenance_json="$4"
  _psql -v rid="$request_id" -v kind="$kind" -v prop="$proposal_json" -v prov="$provenance_json" \
    <<<"INSERT INTO pending_decisions(request_id, kind, proposal, provenance) VALUES (:'rid', :'kind', :'prop'::jsonb, :'prov'::jsonb);"
}
