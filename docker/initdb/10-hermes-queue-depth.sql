-- Queue depth for the dispatcher. hermes_worker has no direct table SELECT (least privilege), so the
-- long-running dispatcher reads pending work through this security-definer function instead. It
-- returns how many queued rows of a kind are NOT yet being handled, so the dispatcher only spawns an
-- executor when there is unclaimed work, avoiding a spawn-and-no-op busy loop.
BEGIN;

CREATE OR REPLACE FUNCTION hermes_queue_depth(target_kind TEXT)
RETURNS BIGINT LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public,pg_temp AS $$
  SELECT count(*) FROM requests WHERE kind=target_kind AND status='queued';
$$;

REVOKE ALL ON FUNCTION hermes_queue_depth(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION hermes_queue_depth(TEXT) TO hermes_worker;

COMMIT;
