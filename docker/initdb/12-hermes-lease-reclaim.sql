-- Reclaim native worker leases after process/dispatcher restart. Rows with no side-effect intent are
-- safe to retry; rows that crossed an effect boundary quarantine to reconcile and are never replayed.
BEGIN;

CREATE OR REPLACE FUNCTION hermes_claim_request(
  target_kind TEXT, target_run_id TEXT, target_nonce TEXT, lease_seconds INTEGER)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE claimed requests%ROWTYPE;
BEGIN
  IF target_kind NOT IN ('pr-review','pr-maintain','swe-implement','pr-safety-review','doc-write','memory-curate')
     OR lease_seconds < 30 OR lease_seconds > 3600 OR target_nonce !~ '^[0-9a-f]{32}$' THEN
    RAISE EXCEPTION 'invalid claim';
  END IF;

  -- Safe retry: no remote/filesystem effect boundary was crossed.
  UPDATE requests SET status='queued',started_at=NULL,finished_at=NULL,run_id=NULL,run_nonce=NULL,
         lease_expires_at=NULL,fail_response='requeued after expired native lease'
   WHERE kind=target_kind AND status='running' AND lease_expires_at<=clock_timestamp()
     AND side_effect_at IS NULL;

  -- Unsafe retry: preserve ownership/effect evidence and require explicit reconciliation.
  UPDATE requests SET status='reconcile',finished_at=clock_timestamp(),lease_expires_at=NULL,
         fail_response='expired native lease after side-effect intent; reconcile remote state'
   WHERE kind=target_kind AND status='running' AND lease_expires_at<=clock_timestamp()
     AND side_effect_at IS NOT NULL;

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

REVOKE ALL ON FUNCTION hermes_claim_request(TEXT,TEXT,TEXT,INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION hermes_claim_request(TEXT,TEXT,TEXT,INTEGER) TO hermes_worker;

COMMIT;
