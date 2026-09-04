-- Durable local queue for PR-maintenance work that the agent explicitly leaves for a human.
-- Separate from pending_decisions: resolving maintenance work must never approve shared-memory data.
CREATE TABLE IF NOT EXISTS pending_maintenance_reviews (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  request_id  BIGINT NOT NULL UNIQUE REFERENCES requests(id),
  proposal    JSONB NOT NULL,
  provenance  JSONB NOT NULL,
  state       TEXT NOT NULL DEFAULT 'pending'
                CHECK (state IN ('pending','reviewed','dismissed')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  reviewed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS pending_maintenance_reviews_open
  ON pending_maintenance_reviews (created_at DESC) WHERE state = 'pending';
