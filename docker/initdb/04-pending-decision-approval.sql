-- Human-approved pending decisions are synchronously retained to the shared memory bank.
-- Replay-safe for both initdb and existing request database volumes.
BEGIN;

ALTER TABLE pending_decisions ADD COLUMN IF NOT EXISTS publish_started_at TIMESTAMPTZ;
ALTER TABLE pending_decisions ADD COLUMN IF NOT EXISTS publish_error TEXT;

ALTER TABLE pending_decisions DROP CONSTRAINT IF EXISTS pending_decisions_state_check;
ALTER TABLE pending_decisions ADD CONSTRAINT pending_decisions_state_check
  CHECK (state IN ('pending','publishing','approved','rejected'));
COMMIT;
