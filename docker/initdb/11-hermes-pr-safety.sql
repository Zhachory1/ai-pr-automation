-- Native pr-safety-review: least-privilege event-ledger enqueue and incident-only settle.
--
-- hermes_worker has no direct table DML (07-hermes-autonomy.sql REVOKEs it), so the producer and
-- runner reach requests/pr_safety_merged_pr_events only through these SECURITY DEFINER functions,
-- matching hermes_enqueue_request/hermes_claim_request/hermes_settle_request.
BEGIN;

-- Atomically record a validated merged-PR event and enqueue its safety job at most once. Mirrors
-- lib/queue.sh's queue_enqueue_pr_safety_merged_pr_event (the legacy direct-table-write path used by
-- the retired Docker producer), reimplemented as a security-definer function for the restricted
-- hermes_worker role. Event identity is the merge commit SHA; only the canonical payload SHA-256 is
-- stored, never PR text. When a new head enqueues, any still-queued review for the same PR lineage at
-- an older head is superseded; a running review is left to finish and done reviews are history.
CREATE OR REPLACE FUNCTION hermes_enqueue_pr_safety_event(
  target_merge_sha TEXT, target_event_digest TEXT, target_payload JSONB,
  target_dedupe_key TEXT, target_lineage TEXT)
RETURNS BIGINT LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE inserted_id BIGINT; max_attempts CONSTANT INT := 3;
BEGIN
  IF target_merge_sha !~ '^[0-9a-f]{40}$' OR target_event_digest !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'invalid pr-safety event identity';
  END IF;
  INSERT INTO pr_safety_merged_pr_events(merge_sha, payload_digest)
  VALUES (target_merge_sha, target_event_digest)
  ON CONFLICT (merge_sha) DO NOTHING;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;
  IF NOT EXISTS (
       SELECT 1 FROM requests WHERE kind = 'pr-safety-review' AND dedupe_key = target_dedupe_key
         AND status IN ('queued','running','done','reconcile'))
     AND (SELECT count(*) FROM requests
            WHERE kind = 'pr-safety-review' AND dedupe_key = target_dedupe_key AND status = 'failed'
         ) < max_attempts THEN
    INSERT INTO requests(kind, payload, dedupe_key)
    VALUES ('pr-safety-review', target_payload, target_dedupe_key)
    ON CONFLICT DO NOTHING
    RETURNING id INTO inserted_id;
  END IF;
  IF inserted_id IS NOT NULL THEN
    UPDATE requests
       SET status = 'superseded', finished_at = clock_timestamp(),
           fail_response = 'superseded by newer head ' || target_dedupe_key
     WHERE kind = 'pr-safety-review' AND status = 'queued' AND dedupe_key <> target_dedupe_key
       AND split_part(dedupe_key, '@', 1) = target_lineage;
  END IF;
  RETURN inserted_id;
END;
$$;

-- pr-safety-review settle: incident candidates only enter the human-review queue. A clear or
-- non-incident result settles 'done' with no pending row. An incident.candidate=true result inserts
-- pending_maintenance_reviews (state='pending') and settles 'done' in the SAME transaction, so a row
-- can never be marked done while its incident review silently failed to queue.
CREATE OR REPLACE FUNCTION hermes_settle_pr_safety_request(
  target_id BIGINT, target_nonce TEXT, target_status TEXT, target_detail TEXT,
  target_incident_candidate BOOLEAN, target_proposal JSONB, target_provenance JSONB)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE settled BOOLEAN;
BEGIN
  IF target_status NOT IN ('done','failed','superseded','reconcile') THEN
    RAISE EXCEPTION 'invalid pr-safety terminal status';
  END IF;
  IF target_status = 'done' AND target_incident_candidate AND
     (target_proposal IS NULL OR target_provenance IS NULL) THEN
    RAISE EXCEPTION 'incident candidate requires proposal and provenance';
  END IF;
  UPDATE requests SET status = target_status, finished_at = clock_timestamp(),
         fail_response = target_detail, lease_expires_at = NULL
   WHERE id = target_id AND kind = 'pr-safety-review' AND status = 'running'
     AND run_nonce = target_nonce AND lease_expires_at > clock_timestamp();
  settled := FOUND;
  IF settled AND target_status = 'done' AND target_incident_candidate THEN
    INSERT INTO pending_maintenance_reviews(request_id, proposal, provenance)
    VALUES (target_id, target_proposal, target_provenance)
    ON CONFLICT (request_id) DO NOTHING;
  END IF;
  RETURN settled;
END;
$$;

-- Snapshot GC (producer) needs to know whether an operation_id is still queued/running before it
-- deletes the read-only snapshot dir a review might still need. hermes_worker has no direct SELECT
-- on requests, so this mirrors hermes_queue_depth's least-privilege read path.
CREATE OR REPLACE FUNCTION hermes_pr_safety_operation_active(target_operation_id TEXT)
RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public,pg_temp AS $$
  SELECT EXISTS (
    SELECT 1 FROM requests
     WHERE kind = 'pr-safety-review' AND status IN ('queued','running')
       AND payload->>'operation_id' = target_operation_id
  );
$$;

REVOKE ALL ON FUNCTION hermes_enqueue_pr_safety_event(TEXT,TEXT,JSONB,TEXT,TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION hermes_settle_pr_safety_request(BIGINT,TEXT,TEXT,TEXT,BOOLEAN,JSONB,JSONB) FROM PUBLIC;
REVOKE ALL ON FUNCTION hermes_pr_safety_operation_active(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION hermes_enqueue_pr_safety_event(TEXT,TEXT,JSONB,TEXT,TEXT) TO hermes_worker;
GRANT EXECUTE ON FUNCTION hermes_settle_pr_safety_request(BIGINT,TEXT,TEXT,TEXT,BOOLEAN,JSONB,JSONB) TO hermes_worker;
GRANT EXECUTE ON FUNCTION hermes_pr_safety_operation_active(TEXT) TO hermes_worker;

COMMIT;
