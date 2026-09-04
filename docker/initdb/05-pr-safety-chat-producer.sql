-- Immutable inbound-event ledger for validated Google Chat PR-safety commands.
CREATE TABLE IF NOT EXISTS pr_safety_chat_events (
  provider_message_id TEXT PRIMARY KEY,
  payload_digest      TEXT NOT NULL CHECK (payload_digest ~ '^[0-9a-f]{64}$'),
  received_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
