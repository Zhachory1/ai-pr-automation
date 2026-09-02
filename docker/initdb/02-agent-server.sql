-- M1: agent-server serial queue drain. Additive to M0 schema.

-- Requeue fence: set in the same committed txn immediately BEFORE a side effect.
ALTER TABLE requests ADD COLUMN IF NOT EXISTS side_effect_at TIMESTAMPTZ;

-- Posted-artifact reference so `done` is verifiable without a GitHub round-trip
-- (head-dedup marker or review/comment id). Source of truth for "already posted".
ALTER TABLE requests ADD COLUMN IF NOT EXISTS posted_ref TEXT;

-- Per-run nonce the server issues and the agent must echo in result.json (row/run authenticity).
ALTER TABLE requests ADD COLUMN IF NOT EXISTS run_nonce TEXT;

-- run_id of the attempt currently/last owning the row (for logs + reclaim reconcile).
ALTER TABLE requests ADD COLUMN IF NOT EXISTS run_id TEXT;
