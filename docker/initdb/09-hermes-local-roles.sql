-- Host-native Hermes local (non-repo-scoped) roles.
-- doc-write and memory-curate are not tied to a GitHub repository, but must still authorize through
-- the same least-privilege enrollment path. Option 1: a reserved sentinel enrollment (repo
-- 'local/fleet') gates them, so the audited repo-scoped enqueue/claim functions stay unchanged in
-- shape and the Hermes role never gains direct table DML.
BEGIN;

CREATE OR REPLACE FUNCTION hermes_enqueue_request(
  target_kind TEXT, target_payload JSONB, target_dedupe_key TEXT, target_proof TEXT)
RETURNS BIGINT LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE inserted_id BIGINT; target_repo TEXT := target_payload->>'repo';
BEGIN
  IF target_kind NOT IN ('pr-review','pr-maintain','swe-implement','pr-safety-review','doc-write','memory-curate')
     OR target_repo IS NULL OR NOT hermes_repository_authorized(target_repo,target_proof) THEN
    RAISE EXCEPTION 'repository is not actively enrolled';
  END IF;
  IF (target_kind IN ('doc-write','memory-curate')) <> (target_repo = 'local/fleet') THEN
    RAISE EXCEPTION 'local roles require the local/fleet sentinel and repo-scoped kinds forbid it';
  END IF;
  IF target_kind='pr-maintain' THEN
    PERFORM pg_advisory_xact_lock(hashtextextended('pr-maintain:'||split_part(target_dedupe_key,'@',1),0));
  END IF;
  IF EXISTS (SELECT 1 FROM requests WHERE kind=target_kind AND dedupe_key=target_dedupe_key
               AND (status IN ('queued','running','reconcile') OR (target_kind<>'swe-implement' AND status='done')))
     OR (target_kind<>'swe-implement' AND
         (SELECT count(*) FROM requests WHERE kind=target_kind AND dedupe_key=target_dedupe_key AND status='failed') >= 3)
     OR (target_kind='pr-maintain' AND
         (SELECT count(*) FROM requests WHERE kind='pr-maintain' AND status<>'superseded'
            AND split_part(dedupe_key,'@',1)=split_part(target_dedupe_key,'@',1)) >= 3) THEN
    RETURN NULL;
  END IF;
  INSERT INTO requests(kind,payload,dedupe_key,hermes_enrollment_proof)
  VALUES(target_kind,target_payload,target_dedupe_key,target_proof)
  ON CONFLICT DO NOTHING RETURNING id INTO inserted_id;
  IF inserted_id IS NOT NULL AND target_kind IN ('pr-review','pr-maintain') THEN
    UPDATE requests SET status='superseded',finished_at=clock_timestamp(),
           fail_response='superseded by newer head '||target_dedupe_key
     WHERE kind=target_kind AND status='queued' AND id<>inserted_id
       AND split_part(dedupe_key,'@',1)=split_part(target_dedupe_key,'@',1);
  END IF;
  RETURN inserted_id;
END;
$$;

CREATE OR REPLACE FUNCTION hermes_claim_request(
  target_kind TEXT, target_run_id TEXT, target_nonce TEXT, lease_seconds INTEGER)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE claimed requests%ROWTYPE;
BEGIN
  IF target_kind NOT IN ('pr-review','pr-maintain','swe-implement','pr-safety-review','doc-write','memory-curate')
     OR lease_seconds < 30 OR lease_seconds > 3600 OR target_nonce !~ '^[0-9a-f]{32}$' THEN
    RAISE EXCEPTION 'invalid claim';
  END IF;
  WITH candidate AS (
    SELECT r.id FROM requests r
     WHERE r.kind=target_kind AND r.status='queued'
       AND hermes_repository_authorized(r.payload->>'repo',r.hermes_enrollment_proof)
     ORDER BY r.created_at,r.id FOR UPDATE SKIP LOCKED LIMIT 1
  )
  UPDATE requests r SET status='running',started_at=clock_timestamp(),run_id=target_run_id,
         run_nonce=target_nonce,lease_expires_at=clock_timestamp()+make_interval(secs=>lease_seconds)
    FROM candidate c WHERE r.id=c.id RETURNING r.* INTO claimed;
  IF claimed.id IS NULL THEN RETURN NULL; END IF;
  RETURN jsonb_build_object('id',claimed.id,'kind',claimed.kind,'payload',claimed.payload,
                            'dedupe_key',claimed.dedupe_key,'created_at',claimed.created_at,
                            'enrollment_proof',claimed.hermes_enrollment_proof);
END;
$$;

REVOKE ALL ON FUNCTION hermes_enqueue_request(TEXT,JSONB,TEXT,TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION hermes_claim_request(TEXT,TEXT,TEXT,INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION hermes_enqueue_request(TEXT,JSONB,TEXT,TEXT) TO hermes_worker;
GRANT EXECUTE ON FUNCTION hermes_claim_request(TEXT,TEXT,TEXT,INTEGER) TO hermes_worker;

COMMIT;
