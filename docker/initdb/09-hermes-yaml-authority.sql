-- Collapse the enrollment/proof authorization model to nothing in the database.
--
-- Rationale (see docs/hermes/DD-authority-and-memory.md): the real security boundary is the
-- hermes-agent OS account + repo-scoped deploy key + read-only API token + GitHub branch protection.
-- Those enforce "which repos" and "what actions" server-side. The enrollment table, proof digests,
-- and 10-minute freshness gate re-proved a wall that already enforces itself — theater. Scope of
-- attention ("which repos should the agent spend effort on") is an operator allowlist that lives in
-- a plain YAML file the enqueue producer reads, not in the database.
--
-- This migration removes the proof parameter and the authorization check from the queue API, and
-- drops the enrollment table and the authorization function. Dedupe, retry cap, stale-head
-- supersede, the three-round pr-maintain cap, lease/heartbeat, and the typed settle paths are
-- unchanged.
BEGIN;

-- Old signatures carry the proof param; a reduced-arg CREATE OR REPLACE would add an overload, so
-- drop the proof-bearing versions first.
DROP FUNCTION IF EXISTS hermes_enqueue_request(TEXT, JSONB, TEXT, TEXT);
DROP FUNCTION IF EXISTS hermes_claim_request(TEXT, TEXT, TEXT, INTEGER);
DROP FUNCTION IF EXISTS hermes_repository_authorized(TEXT, TEXT);

CREATE OR REPLACE FUNCTION hermes_enqueue_request(target_kind TEXT, target_payload JSONB, target_dedupe_key TEXT)
RETURNS BIGINT LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE inserted_id BIGINT;
BEGIN
  IF target_kind NOT IN ('pr-review','pr-maintain','swe-implement','pr-safety-review','doc-write','memory-curate') THEN
    RAISE EXCEPTION 'unsupported queue kind';
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
  INSERT INTO requests(kind,payload,dedupe_key)
  VALUES(target_kind,target_payload,target_dedupe_key)
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
     ORDER BY r.created_at,r.id FOR UPDATE SKIP LOCKED LIMIT 1
  )
  UPDATE requests r SET status='running',started_at=clock_timestamp(),run_id=target_run_id,
         run_nonce=target_nonce,lease_expires_at=clock_timestamp()+make_interval(secs=>lease_seconds)
    FROM candidate c WHERE r.id=c.id RETURNING r.* INTO claimed;
  IF claimed.id IS NULL THEN RETURN NULL; END IF;
  RETURN jsonb_build_object('id',claimed.id,'kind',claimed.kind,'payload',claimed.payload,
                            'dedupe_key',claimed.dedupe_key,'created_at',claimed.created_at);
END;
$$;

-- The enrollment table and the request proof column are no longer read by anything.
ALTER TABLE requests DROP COLUMN IF EXISTS hermes_enrollment_proof;
DROP TABLE IF EXISTS hermes_repository_enrollments;

REVOKE ALL ON FUNCTION hermes_enqueue_request(TEXT,JSONB,TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION hermes_claim_request(TEXT,TEXT,TEXT,INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION hermes_enqueue_request(TEXT,JSONB,TEXT) TO hermes_worker;
GRANT EXECUTE ON FUNCTION hermes_claim_request(TEXT,TEXT,TEXT,INTEGER) TO hermes_worker;

COMMIT;
