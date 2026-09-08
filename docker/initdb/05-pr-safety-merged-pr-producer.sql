-- Immutable inbound-event ledger for merged PRs picked up by the PR-safety merged-PR producer.
-- Event identity is the merge commit SHA (stable and unique per landed change), so each merged PR
-- is reviewed at most once. Replaces the retired Google Chat event ledger.
CREATE TABLE IF NOT EXISTS pr_safety_merged_pr_events (
  merge_sha      TEXT PRIMARY KEY CHECK (merge_sha ~ '^[0-9a-f]{40}$'),
  payload_digest TEXT NOT NULL CHECK (payload_digest ~ '^[0-9a-f]{64}$'),
  received_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
