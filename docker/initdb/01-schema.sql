-- Agent fleet request tracking. Loaded on first Postgres boot via
-- /docker-entrypoint-initdb.d/, AND re-run idempotently by the schema-migrate service on every up
-- (so an existing DB never drifts from a fresh one). Every statement here must be idempotent.

CREATE TABLE IF NOT EXISTS requests (
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  kind          TEXT NOT NULL,                       -- 'pr-review', 'comment-handler', ...
  payload       JSONB NOT NULL,                      -- identifiers only, never secrets
  dedupe_key    TEXT NOT NULL,                       -- e.g. 'owner/repo#123@sha'
  status        TEXT NOT NULL DEFAULT 'queued'
                  CHECK (status IN ('queued','running','done','failed','skipped','superseded','reconcile')),
  fail_response TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at    TIMESTAMPTZ,
  finished_at   TIMESTAMPTZ
);

-- A cron producer re-enqueuing the same work while it is still queued or running is a no-op.
CREATE UNIQUE INDEX IF NOT EXISTS requests_dedupe_active
  ON requests (kind, dedupe_key)
  WHERE status IN ('queued','running');

-- Decision-shaped writes wait here for the daily human batch before entering shared memory.
-- Operational facts do NOT pass through this table; they auto-write in M1.
CREATE TABLE IF NOT EXISTS pending_decisions (
  id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  request_id  BIGINT NOT NULL REFERENCES requests(id),
  kind        TEXT NOT NULL,
  proposal    JSONB NOT NULL,                        -- typed structured output from the agent
  provenance  JSONB NOT NULL,                        -- run_id, repo#PR@sha, status, written_by
  state       TEXT NOT NULL DEFAULT 'pending'
                  CHECK (state IN ('pending','approved','rejected')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS pending_decisions_open ON pending_decisions (state) WHERE state = 'pending';
