-- M1: leased agent-server queue drain. Additive to M0 schema.
BEGIN;

-- Requeue fence: set in the same committed txn immediately BEFORE a side effect.
ALTER TABLE requests ADD COLUMN IF NOT EXISTS side_effect_at TIMESTAMPTZ;

-- Posted-artifact reference so `done` is verifiable without a GitHub round-trip
-- (head-dedup marker or review/comment id). Source of truth for "already posted".
ALTER TABLE requests ADD COLUMN IF NOT EXISTS posted_ref TEXT;

-- Per-run nonce the server issues and the agent must echo in result.json (row/run authenticity).
ALTER TABLE requests ADD COLUMN IF NOT EXISTS run_nonce TEXT;

-- run_id of the attempt currently/last owning the row (for logs + reclaim reconcile).
ALTER TABLE requests ADD COLUMN IF NOT EXISTS run_id TEXT;

-- Renewable ownership lease. run_nonce is the fencing token for every attempt-owned update.
ALTER TABLE requests ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;

-- Ambiguous maintenance attempts (lease expired after side-effect intent) stop for reconciliation.
ALTER TABLE requests DROP CONSTRAINT IF EXISTS requests_status_check;
ALTER TABLE requests ADD CONSTRAINT requests_status_check
  CHECK (status IN ('queued','running','done','failed','skipped','superseded','reconcile'));

-- Upgrade buffer for rows already running when migration starts. Worker rollout remains
-- stop-the-world; replaying this migration does not extend the lease.
UPDATE requests
   SET lease_expires_at = now() + interval '35 minutes'
 WHERE status = 'running' AND lease_expires_at IS NULL;

-- Once this migration lands, legacy workers cannot claim new rows without a lease.
ALTER TABLE requests DROP CONSTRAINT IF EXISTS requests_running_lease_check;
ALTER TABLE requests ADD CONSTRAINT requests_running_lease_check
  CHECK (status <> 'running' OR (run_nonce IS NOT NULL AND lease_expires_at IS NOT NULL));

-- Different heads of one PR share a lineage (the part before @). Only one worker may run a given
-- kind+PR lineage; different PRs and kinds remain fully parallel.
CREATE UNIQUE INDEX IF NOT EXISTS requests_one_running_lineage
  ON requests (kind, (split_part(dedupe_key, '@', 1)))
  WHERE status = 'running';
COMMIT;
