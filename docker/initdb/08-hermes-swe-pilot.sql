BEGIN;
CREATE OR REPLACE FUNCTION hermes_settle_swe_request(
  target_id BIGINT,target_nonce TEXT,target_status TEXT,target_detail TEXT,target_posted_ref TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
BEGIN
  IF target_status NOT IN ('done','skipped','reconcile')
     OR (target_status='done' AND target_posted_ref !~ '^https://github.com/[^/]+/[^/]+/pull/[1-9][0-9]*$')
     OR (target_status<>'done' AND target_posted_ref<>'') THEN
    RAISE EXCEPTION 'invalid SWE terminal result';
  END IF;
  UPDATE requests SET status=target_status,finished_at=clock_timestamp(),fail_response=target_detail,
         posted_ref=NULLIF(target_posted_ref,''),lease_expires_at=NULL
   WHERE id=target_id AND kind='swe-implement' AND status='running' AND run_nonce=target_nonce
     AND lease_expires_at>clock_timestamp();
  RETURN FOUND;
END;
$$;
REVOKE ALL ON FUNCTION hermes_settle_swe_request(BIGINT,TEXT,TEXT,TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION hermes_settle_swe_request(BIGINT,TEXT,TEXT,TEXT,TEXT) TO hermes_worker;
COMMIT;
