-- Durable Hermes doc state and least-privilege document settlement API.
BEGIN;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='hermes_worker') THEN
    CREATE ROLE hermes_worker NOLOGIN;
  END IF;
END $$;

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
  ON hermes_doc_runs(replay_until) WHERE state = 'submitting';
CREATE UNIQUE INDEX IF NOT EXISTS hermes_doc_runs_one_open_per_request
  ON hermes_doc_runs(request_id) WHERE state = 'submitting';

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
RETURNS trigger LANGUAGE plpgsql AS $$
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

CREATE OR REPLACE FUNCTION hermes_settle_doc_questions(
  target_id BIGINT, target_nonce TEXT, target_proposal JSONB, target_provenance JSONB)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
BEGIN
  IF jsonb_typeof(target_proposal) <> 'object'
     OR target_proposal->>'kind' <> 'doc-open-questions'
     OR target_proposal->>'draft_path' <> 'requests/'||target_id||'/draft.md'
     OR jsonb_typeof(target_proposal->'open_questions') <> 'array'
     OR jsonb_array_length(target_proposal->'open_questions') < 1
     OR jsonb_typeof(target_provenance) <> 'object' THEN
    RAISE EXCEPTION 'invalid document questions result';
  END IF;
  WITH eligible AS (
    SELECT id FROM requests WHERE id=target_id AND kind='doc-write' AND status='running'
      AND run_nonce=target_nonce AND lease_expires_at>clock_timestamp() FOR UPDATE
  ), review AS (
    INSERT INTO pending_maintenance_reviews(request_id,proposal,provenance)
    SELECT id,target_proposal,target_provenance FROM eligible ON CONFLICT DO NOTHING RETURNING request_id
  )
  UPDATE requests r SET status='done',finished_at=clock_timestamp(),
    posted_ref='queued: awaiting document answers',lease_expires_at=NULL
  FROM review h WHERE r.id=h.request_id;
  RETURN FOUND;
END;
$$;

CREATE OR REPLACE FUNCTION hermes_stage_doc_publication(
  target_id BIGINT, target_nonce TEXT, target_staged_path TEXT, target_path TEXT,
  target_digest TEXT, target_generation TEXT, target_proposal JSONB, target_provenance JSONB)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
BEGIN
  IF target_staged_path <> 'requests/'||target_id||'/publish.md'
     OR target_digest !~ '^[0-9a-f]{64}$'
     OR target_generation !~ '^hermes:[0-9a-f]{64}$'
     OR jsonb_typeof(target_proposal) <> 'object'
     OR target_proposal->>'kind' <> 'doc-publication-approval'
     OR target_proposal->>'staged_path' IS DISTINCT FROM target_staged_path
     OR target_proposal->>'target_path' IS DISTINCT FROM target_path
     OR target_proposal->>'content_digest' IS DISTINCT FROM target_digest
     OR target_proposal->>'document_generation' IS DISTINCT FROM target_generation
     OR jsonb_typeof(target_provenance) <> 'object' THEN
    RAISE EXCEPTION 'invalid document publication result';
  END IF;
  WITH eligible AS (
    SELECT id FROM requests WHERE id=target_id AND kind='doc-write' AND status='running'
      AND run_nonce=target_nonce AND lease_expires_at>clock_timestamp() FOR UPDATE
  ), publication AS (
    INSERT INTO doc_publications(request_id,state,staged_path,target_path,content_digest,document_generation)
    SELECT id,'awaiting_approval',target_staged_path,target_path,target_digest,target_generation
    FROM eligible ON CONFLICT DO NOTHING RETURNING request_id
  ), review AS (
    INSERT INTO pending_maintenance_reviews(request_id,proposal,provenance)
    SELECT request_id,target_proposal,target_provenance FROM publication
    ON CONFLICT DO NOTHING RETURNING request_id
  )
  UPDATE requests r SET status='done',finished_at=clock_timestamp(),
    posted_ref='queued: awaiting exact publication approval',lease_expires_at=NULL
  FROM review h WHERE r.id=h.request_id;
  RETURN FOUND;
END;
$$;

CREATE OR REPLACE FUNCTION hermes_doc_publication_claimed(target_id BIGINT,target_nonce TEXT)
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public,pg_temp AS $$
  SELECT jsonb_build_object('staged_path',p.staged_path,'target_path',p.target_path,
    'content_digest',p.content_digest,'document_generation',p.document_generation)
  FROM doc_publications p JOIN requests r ON r.id=p.request_id
  WHERE p.request_id=target_id AND p.state='approved' AND p.approved_at IS NOT NULL
    AND r.kind='doc-write' AND r.status='running' AND r.run_nonce=target_nonce
    AND r.lease_expires_at>clock_timestamp() AND r.payload->>'publication_only'='true';
$$;

CREATE OR REPLACE FUNCTION hermes_prepare_doc_publication(target_id BIGINT,target_nonce TEXT)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE prepared JSONB;
BEGIN
  WITH eligible AS (
    SELECT p.request_id,p.staged_path,p.target_path,p.content_digest,p.document_generation
    FROM doc_publications p JOIN requests r ON r.id=p.request_id
    WHERE p.request_id=target_id AND p.state='approved' AND p.approved_at IS NOT NULL
      AND r.kind='doc-write' AND r.status='running' AND r.run_nonce=target_nonce
      AND r.lease_expires_at>clock_timestamp() AND r.payload->>'publication_only'='true'
    FOR UPDATE OF p,r
  ), publication AS (
    UPDATE doc_publications p SET state='prepared',updated_at=clock_timestamp()
    FROM eligible e WHERE p.request_id=e.request_id RETURNING e.*
  ), quarantined AS (
    UPDATE requests r SET status='reconcile',side_effect_at=clock_timestamp(),
      finished_at=clock_timestamp(),fail_response='doc publication prepared; verify exact target',
      lease_expires_at=NULL
    FROM publication p WHERE r.id=p.request_id RETURNING p.*
  )
  SELECT jsonb_build_object('staged_path',staged_path,'target_path',target_path,
    'content_digest',content_digest,'document_generation',document_generation)
  INTO prepared FROM quarantined;
  RETURN prepared;
END;
$$;

CREATE OR REPLACE FUNCTION hermes_reconcile_doc_publication(
  target_id BIGINT,target_nonce TEXT,target_error TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
BEGIN
  WITH eligible AS (
    SELECT p.request_id FROM doc_publications p JOIN requests r ON r.id=p.request_id
    WHERE p.request_id=target_id AND p.state='approved' AND p.approved_at IS NOT NULL
      AND r.status='running' AND r.run_nonce=target_nonce AND r.lease_expires_at>clock_timestamp()
    FOR UPDATE OF p,r
  ), publication AS (
    UPDATE doc_publications p SET state='reconcile',error=left(target_error,500),updated_at=clock_timestamp()
    FROM eligible e WHERE p.request_id=e.request_id RETURNING p.request_id
  )
  UPDATE requests r SET status='reconcile',finished_at=clock_timestamp(),
    fail_response=left(target_error,500),lease_expires_at=NULL
  FROM publication p WHERE r.id=p.request_id;
  RETURN FOUND;
END;
$$;

CREATE OR REPLACE FUNCTION hermes_mark_doc_published(
  target_id BIGINT,target_target_path TEXT,target_digest TEXT,target_generation TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
BEGIN
  WITH eligible AS (
    SELECT p.request_id FROM doc_publications p JOIN requests r ON r.id=p.request_id
    WHERE p.request_id=target_id AND p.state='prepared' AND p.approved_at IS NOT NULL
      AND p.target_path=target_target_path AND p.content_digest=target_digest
      AND p.document_generation=target_generation AND r.status='reconcile'
    FOR UPDATE OF p,r
  ), publication AS (
    UPDATE doc_publications p SET state='published',published_at=clock_timestamp(),
      updated_at=clock_timestamp(),error=NULL
    FROM eligible e WHERE p.request_id=e.request_id RETURNING p.request_id
  )
  UPDATE requests r SET status='done',posted_ref=target_target_path,finished_at=clock_timestamp(),fail_response=NULL
  FROM publication p WHERE r.id=p.request_id;
  RETURN FOUND;
END;
$$;

REVOKE ALL ON hermes_doc_runs,doc_publications,pending_maintenance_reviews,requests FROM hermes_worker;
REVOKE ALL ON FUNCTION hermes_settle_doc_questions(BIGINT,TEXT,JSONB,JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION hermes_stage_doc_publication(BIGINT,TEXT,TEXT,TEXT,TEXT,TEXT,JSONB,JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION hermes_doc_publication_claimed(BIGINT,TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION hermes_prepare_doc_publication(BIGINT,TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION hermes_reconcile_doc_publication(BIGINT,TEXT,TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION hermes_mark_doc_published(BIGINT,TEXT,TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION hermes_settle_doc_questions(BIGINT,TEXT,JSONB,JSONB) TO hermes_worker;
GRANT EXECUTE ON FUNCTION hermes_stage_doc_publication(BIGINT,TEXT,TEXT,TEXT,TEXT,TEXT,JSONB,JSONB) TO hermes_worker;
GRANT EXECUTE ON FUNCTION hermes_doc_publication_claimed(BIGINT,TEXT) TO hermes_worker;
GRANT EXECUTE ON FUNCTION hermes_prepare_doc_publication(BIGINT,TEXT) TO hermes_worker;
GRANT EXECUTE ON FUNCTION hermes_reconcile_doc_publication(BIGINT,TEXT,TEXT) TO hermes_worker;
GRANT EXECUTE ON FUNCTION hermes_mark_doc_published(BIGINT,TEXT,TEXT,TEXT) TO hermes_worker;

COMMIT;
