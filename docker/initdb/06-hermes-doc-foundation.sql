-- M2a: durable Hermes doc-run identity and exact-byte publication approval state.
BEGIN;

CREATE TABLE IF NOT EXISTS hermes_doc_runs (
  request_id          BIGINT NOT NULL REFERENCES requests(id),
  phase               TEXT NOT NULL CHECK (phase IN ('draft','council')),
  request_digest      TEXT NOT NULL CHECK (request_digest ~ '^[0-9a-f]{64}$'),
  runtime_generation  TEXT NOT NULL CHECK (runtime_generation ~ '^[0-9a-f]{64}$'),
  state               TEXT NOT NULL CHECK (state IN ('submitting','completed','failed','reconcile')),
  submit_count        SMALLINT NOT NULL DEFAULT 0 CHECK (submit_count BETWEEN 0 AND 2),
  hermes_run_id       TEXT UNIQUE CHECK (hermes_run_id IS NULL OR length(hermes_run_id) > 0),
  raw_status          TEXT,
  replay_until        TIMESTAMPTZ NOT NULL,
  output_digest       TEXT CHECK (output_digest IS NULL OR output_digest ~ '^[0-9a-f]{64}$'),
  usage               JSONB CHECK (usage IS NULL OR jsonb_typeof(usage) = 'object'),
  error               TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (request_id, phase),
  CHECK (
    (state = 'completed' AND hermes_run_id IS NOT NULL AND output_digest IS NOT NULL) OR
    (state = 'submitting' AND output_digest IS NULL) OR
    (state IN ('failed','reconcile') AND output_digest IS NULL)
  )
);

CREATE INDEX IF NOT EXISTS hermes_doc_runs_open
  ON hermes_doc_runs(replay_until)
  WHERE state = 'submitting';

CREATE UNIQUE INDEX IF NOT EXISTS hermes_doc_runs_one_open_per_request
  ON hermes_doc_runs(request_id)
  WHERE state = 'submitting';

CREATE TABLE IF NOT EXISTS doc_publications (
  request_id          BIGINT PRIMARY KEY REFERENCES requests(id),
  state               TEXT NOT NULL CHECK (state IN (
                        'awaiting_approval','approved','prepared','published',
                        'reconcile','invalid','dismissed')),
  staged_path         TEXT NOT NULL,
  target_path         TEXT NOT NULL UNIQUE,
  content_digest      TEXT NOT NULL CHECK (content_digest ~ '^[0-9a-f]{64}$'),
  document_generation TEXT NOT NULL CHECK (document_generation ~ '^(legacy|hermes):[0-9a-f]{64}$'),
  approved_at         TIMESTAMPTZ,
  published_at        TIMESTAMPTZ,
  error               TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (staged_path = 'requests/' || request_id || '/publish.md'),
  CHECK (target_path ~ '^(seprd|mlprd|launchpad|dd)-[0-9]{4}-[0-9]{2}-[0-9]{2}-[a-z0-9]+([a-z0-9-]*[a-z0-9])?(-[0-9]+)?\.md$'),
  CHECK (
    (state = 'awaiting_approval' AND approved_at IS NULL AND published_at IS NULL) OR
    (state IN ('dismissed','invalid') AND approved_at IS NULL AND published_at IS NULL) OR
    (state IN ('approved','prepared','reconcile') AND approved_at IS NOT NULL AND published_at IS NULL) OR
    (state = 'published' AND approved_at IS NOT NULL AND published_at IS NOT NULL)
  )
);

CREATE OR REPLACE FUNCTION reject_doc_publication_binding_change()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.staged_path IS DISTINCT FROM OLD.staged_path
     OR NEW.target_path IS DISTINCT FROM OLD.target_path
     OR NEW.content_digest IS DISTINCT FROM OLD.content_digest
     OR NEW.document_generation IS DISTINCT FROM OLD.document_generation THEN
    RAISE EXCEPTION 'doc publication binding is immutable';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS doc_publication_binding_immutable ON doc_publications;
CREATE TRIGGER doc_publication_binding_immutable
BEFORE UPDATE ON doc_publications
FOR EACH ROW EXECUTE FUNCTION reject_doc_publication_binding_change();

COMMIT;
