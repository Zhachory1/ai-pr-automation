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

# Advisory lock key for single-instance enforcement. Per-KIND so one serial worker runs per kind:
# a review container and a comment-handler container each hold their own lock; a duplicate of the
# SAME kind refuses to start. Derived from the kind via hashtext() at lock time (see
# queue_try_single_instance_lock), with this constant as a namespace base for standalone/legacy use.
AGENT_SERVER_LOCK_KEY="${AGENT_SERVER_LOCK_KEY:-774411}"

_psql() {
  # -qAt: quiet, unaligned, tuples-only. Returns are single JSON objects (claim) or scalars;
  # no multi-column tab-separated output, so no field separator is needed.
  # ON_ERROR_STOP so failures are fatal to callers.
  psql -v ON_ERROR_STOP=1 -qAt \
    -h "$REQUESTS_DB_HOST" -p "$REQUESTS_DB_PORT" \
    -U "$REQUESTS_DB_USER" -d "$REQUESTS_DB_NAME" "$@"
}

# Hold a session advisory lock for the life of THIS psql session, scoped to a KIND. Returns t/f.
# Two-int advisory lock: (namespace base, hashtext(kind)) so different kinds never collide and the
# same kind always maps to the same key. Caller keeps the session alive to hold the lock.
queue_try_single_instance_lock() {
  local kind="${1:?kind}"
  _psql -v base="$AGENT_SERVER_LOCK_KEY" -v kind="$kind" \
    <<<"SELECT pg_try_advisory_lock(:'base'::int, hashtext(:'kind'));"
}

# Startup reclaim for a KIND (fail-closed, uses posted_ref as the 'posted' truth so transient
# pre-post failures are safely retried instead of dead-lettered):
#   posted_ref present            -> genuinely posted; terminal 'done' (no re-run).
#   side_effect intent, not posted -> safe to requeue (head-dedup marker prevents double-post).
#   clean running                 -> requeue.
# Scoped to KIND so a review worker never reclaims a comment-handler's rows.
queue_reclaim_stale() {
  local kind="${1:?kind}"
  _psql -v kind="$kind" <<'SQL'
UPDATE requests
   SET status = 'done', finished_at = now()
 WHERE status = 'running' AND kind = :'kind' AND posted_ref IS NOT NULL;

UPDATE requests
   SET status = 'queued', started_at = NULL, run_id = NULL, run_nonce = NULL, side_effect_at = NULL
 WHERE status = 'running' AND kind = :'kind' AND posted_ref IS NULL;
SQL
}

# Claim one queued row OF THIS KIND -> running, stamping run_id + nonce. Emits ONE json object
# (avoids tab/newline parsing hazards from untrusted dedupe_key/payload). Empty if none queued.
# FOR UPDATE SKIP LOCKED keeps a future 2nd worker safe.
queue_claim_one() {
  local kind="${1:?kind}" run_id="$2" nonce="$3"
  _psql -v kind="$kind" -v run_id="$run_id" -v nonce="$nonce" <<'SQL'
WITH claimed AS (
  SELECT id FROM requests
   WHERE status = 'queued' AND kind = :'kind'
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

queue_mark_superseded() {
  local id="$1" reason="$2"
  _psql -v id="$id" -v reason="$reason" \
    <<<"UPDATE requests SET status='superseded', finished_at=now(), fail_response = :'reason' WHERE id = :'id';"
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
# Atomically records a validated Chat event and enqueues its safety job at most once. Chat text is
# deliberately not stored: only its canonical JSON digest and immutable provider message name remain.
queue_enqueue_pr_safety_chat_event() {
  local event_id="$1" event_digest="$2" payload_json="$3" dedupe_key="$4"
  local max_attempts="${PR_PRODUCER_MAX_ATTEMPTS:-3}"
  _psql -v event_id="$event_id" -v event_digest="$event_digest" -v payload="$payload_json" -v dk="$dedupe_key" -v maxatt="$max_attempts" <<'SQL'
WITH event AS (
  INSERT INTO pr_safety_chat_events(provider_message_id, payload_digest)
  VALUES (:'event_id', :'event_digest')
  ON CONFLICT (provider_message_id) DO NOTHING
  RETURNING 1
), request AS (
  INSERT INTO requests(kind, payload, dedupe_key)
  SELECT 'pr-safety-review', :'payload'::jsonb, :'dk'
   WHERE EXISTS (SELECT 1 FROM event)
     AND NOT EXISTS (
       SELECT 1 FROM requests
        WHERE kind = 'pr-safety-review' AND dedupe_key = :'dk'
          AND status IN ('queued','running','done')
     )
     AND (
       :'maxatt' = '0'
       OR (SELECT count(*) FROM requests
             WHERE kind = 'pr-safety-review' AND dedupe_key = :'dk' AND status = 'failed') < :'maxatt'::int
     )
  ON CONFLICT DO NOTHING
  RETURNING 1
)
SELECT CASE WHEN EXISTS (SELECT 1 FROM request) THEN '1' ELSE '' END;
SQL
}

queue_enqueue() {
  local kind="$1" payload_json="$2" dedupe_key="$3"
  # Cap failed retries: a head that has already FAILED >= max_attempts times is a poison PR
  # (persistent checkout/tool error). Stop re-enqueuing it so it can't retry-loop forever and
  # burn tokens unattended. Default 3; 0 disables the cap. Dedup of queued/running/done is
  # unchanged — an unchanged head is still never re-reviewed.
  local max_attempts="${PR_PRODUCER_MAX_ATTEMPTS:-3}"
  _psql -v kind="$kind" -v payload="$payload_json" -v dk="$dedupe_key" -v maxatt="$max_attempts" <<'SQL'
INSERT INTO requests(kind, payload, dedupe_key)
SELECT :'kind', :'payload'::jsonb, :'dk'
WHERE NOT EXISTS (
  SELECT 1 FROM requests
   WHERE kind = :'kind' AND dedupe_key = :'dk'
     AND status IN ('queued','running','done')
)
AND (
  :'maxatt' = '0'
  OR (SELECT count(*) FROM requests
        WHERE kind = :'kind' AND dedupe_key = :'dk' AND status = 'failed') < :'maxatt'::int
)
ON CONFLICT DO NOTHING
RETURNING 1;
SQL
}

# A durable decision is an explicit reusable rule, never a run summary or finding.
valid_memory_decisions() {
  jq -e 'type == "array" and length > 0 and all(.[]; type == "object" and (keys | sort) == ["rationale","rule","scope"] and (.scope == "repository" or .scope == "fleet") and (.rule | type == "string" and test("\\S")) and (.rationale | type == "string" and test("\\S")))' >/dev/null 2>&1
}

# Route an agent-authored memory proposal to its human batch. NEVER auto-writes shared memory.
pending_decision_insert() {
  local request_id="$1" kind="$2" proposal_json="$3" provenance_json="$4"
  _psql -v rid="$request_id" -v kind="$kind" -v prop="$proposal_json" -v prov="$provenance_json" \
    <<<"INSERT INTO pending_decisions(request_id, kind, proposal, provenance) VALUES (:'rid', :'kind', :'prop'::jsonb, :'prov'::jsonb);"
}

# Blocked PR maintenance waits in a separate queue: completing it cannot approve a memory proposal.
pending_maintenance_review_insert() {
  local request_id="$1" proposal_json="$2" provenance_json="$3"
  _psql -v rid="$request_id" -v prop="$proposal_json" -v prov="$provenance_json" \
    <<<"INSERT INTO pending_maintenance_reviews(request_id, proposal, provenance) VALUES (:'rid', :'prop'::jsonb, :'prov'::jsonb) ON CONFLICT (request_id) DO NOTHING;"
}

pending_maintenance_review_operation_exists() {
  local operation_id="$1"
  _psql -v op="$operation_id" \
    <<<"SELECT EXISTS (SELECT 1 FROM pending_maintenance_reviews WHERE provenance->>'operation_id' = :'op');" \
    | grep -qx t
}
