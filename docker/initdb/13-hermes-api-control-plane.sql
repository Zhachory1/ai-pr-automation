-- Compose-owned Hermes Runs API control plane: route fence, fixed caps, and durable exact replay.
BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS hermes_kind_routes (
  kind TEXT PRIMARY KEY CHECK (kind IN (
    'pr-review','pr-maintain','swe-implement','doc-write','memory-curate','pr-safety-review')),
  route TEXT NOT NULL CHECK (route IN ('native','api')),
  generation BIGINT NOT NULL CHECK (generation > 0),
  max_concurrent INTEGER NOT NULL CHECK (max_concurrent > 0),
  profile TEXT NOT NULL,
  auth_generation BIGINT NOT NULL CHECK (auth_generation > 0),
  profile_generation TEXT NOT NULL CHECK (profile_generation ~ '^[0-9a-f]{64}$')
);

INSERT INTO hermes_kind_routes(kind,route,generation,max_concurrent,profile,auth_generation,profile_generation)
VALUES
  ('pr-review','api',1,1,'pr-review-v1',1,repeat('0',64)),
  ('pr-maintain','api',1,3,'pr-maintain-v1',1,repeat('0',64)),
  ('swe-implement','api',1,1,'swe-implement-v1',1,repeat('0',64)),
  ('doc-write','api',1,1,'doc-write-v1',1,repeat('0',64)),
  ('memory-curate','api',1,1,'memory-curate-v1',1,repeat('0',64)),
  ('pr-safety-review','api',1,1,'pr-safety-v1',1,repeat('0',64))
ON CONFLICT (kind) DO NOTHING;

CREATE TABLE IF NOT EXISTS hermes_runs (
  request_id BIGINT NOT NULL REFERENCES requests(id),
  attempt_no INTEGER NOT NULL CHECK (attempt_no > 0),
  operation_key TEXT NOT NULL,
  route_generation BIGINT NOT NULL,
  auth_generation BIGINT NOT NULL,
  profile TEXT NOT NULL,
  profile_generation TEXT NOT NULL CHECK (profile_generation ~ '^[0-9a-f]{64}$'),
  idempotency_key TEXT NOT NULL UNIQUE,
  request_bytes BYTEA NOT NULL,
  request_digest TEXT NOT NULL CHECK (request_digest ~ '^[0-9a-f]{64}$'),
  state TEXT NOT NULL CHECK (state IN ('submitting','completed','failed','reconcile')),
  submit_count SMALLINT NOT NULL DEFAULT 0 CHECK (submit_count BETWEEN 0 AND 8),
  first_submit_at TIMESTAMPTZ,
  replay_until TIMESTAMPTZ,
  submit_started_at TIMESTAMPTZ,
  stop_requested_at TIMESTAMPTZ,
  stop_confirmed_at TIMESTAMPTZ,
  run_id TEXT UNIQUE CHECK (run_id IS NULL OR length(run_id) > 0),
  terminal_status TEXT CHECK (terminal_status IS NULL OR terminal_status IN ('completed','failed','cancelled','interrupted')),
  output_digest TEXT CHECK (output_digest IS NULL OR output_digest ~ '^[0-9a-f]{64}$'),
  output_bytes BYTEA,
  reconcile_outcome TEXT CHECK (reconcile_outcome IN ('done','not-done','abandoned')),
  reconcile_reason TEXT,
  reconciled_by TEXT,
  reconciled_at TIMESTAMPTZ,
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(request_id,attempt_no),
  CHECK (request_digest = encode(digest(request_bytes,'sha256'),'hex')),
  CHECK ((first_submit_at IS NULL AND replay_until IS NULL) OR
         (first_submit_at IS NOT NULL AND replay_until = first_submit_at + interval '23 hours')),
  CHECK ((output_bytes IS NULL AND output_digest IS NULL) OR
         output_digest = encode(digest(output_bytes,'sha256'),'hex'))
);

CREATE UNIQUE INDEX IF NOT EXISTS hermes_runs_one_open_request
  ON hermes_runs(request_id) WHERE state='submitting';
CREATE UNIQUE INDEX IF NOT EXISTS hermes_runs_one_unresolved_operation
  ON hermes_runs(operation_key)
  WHERE state='submitting' OR (state='reconcile' AND reconcile_outcome IS NULL);
CREATE INDEX IF NOT EXISTS hermes_runs_open_by_kind
  ON hermes_runs(request_id) WHERE state='submitting';

CREATE OR REPLACE FUNCTION reject_hermes_run_binding_change()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.operation_key IS DISTINCT FROM OLD.operation_key
     OR NEW.route_generation IS DISTINCT FROM OLD.route_generation
     OR NEW.auth_generation IS DISTINCT FROM OLD.auth_generation
     OR NEW.profile IS DISTINCT FROM OLD.profile
     OR NEW.profile_generation IS DISTINCT FROM OLD.profile_generation
     OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
     OR NEW.request_bytes IS DISTINCT FROM OLD.request_bytes
     OR NEW.request_digest IS DISTINCT FROM OLD.request_digest
     OR (OLD.run_id IS NOT NULL AND NEW.run_id IS DISTINCT FROM OLD.run_id)
     OR (OLD.first_submit_at IS NOT NULL AND NEW.first_submit_at IS DISTINCT FROM OLD.first_submit_at)
     OR (OLD.replay_until IS NOT NULL AND NEW.replay_until IS DISTINCT FROM OLD.replay_until)
     OR NEW.submit_count < OLD.submit_count OR NEW.submit_count > OLD.submit_count + 1
     OR (OLD.terminal_status IS NOT NULL AND (
       NEW.terminal_status IS DISTINCT FROM OLD.terminal_status
       OR NEW.output_bytes IS DISTINCT FROM OLD.output_bytes
       OR NEW.output_digest IS DISTINCT FROM OLD.output_digest))
     OR (NEW.state IS DISTINCT FROM OLD.state AND (
       OLD.state <> 'submitting' OR NEW.state NOT IN ('completed','failed','reconcile')))
     OR (NEW.reconcile_outcome IS NOT NULL AND NEW.state <> 'reconcile') THEN
    RAISE EXCEPTION 'invalid Hermes run mutation';
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS hermes_run_binding_immutable ON hermes_runs;
CREATE TRIGGER hermes_run_binding_immutable BEFORE UPDATE ON hermes_runs
FOR EACH ROW EXECUTE FUNCTION reject_hermes_run_binding_change();

-- Preserve old doc evidence before retiring its specialized ledger. Legacy in-flight rows become
-- reconcile because their original exact POST bytes were never stored and therefore cannot replay.
DO $$
BEGIN
  IF to_regclass('public.hermes_doc_runs') IS NOT NULL THEN
    EXECUTE $migration$
      INSERT INTO hermes_runs(
        request_id,attempt_no,operation_key,route_generation,auth_generation,profile,
        profile_generation,idempotency_key,request_bytes,request_digest,state,submit_count,
        first_submit_at,replay_until,run_id,terminal_status,output_digest,reconcile_reason,error,
        created_at,updated_at)
      SELECT d.request_id,
        row_number() OVER (PARTITION BY d.request_id ORDER BY d.created_at,d.phase)::integer,
        'doc:'||d.request_id||':'||d.phase,0,1,'doc-write-v1',d.runtime_generation,
        'legacy-doc:'||d.request_id||':'||d.phase,
        convert_to(jsonb_build_object('legacy_request_digest',d.request_digest,'phase',d.phase)::text,'UTF8'),
        encode(digest(convert_to(jsonb_build_object('legacy_request_digest',d.request_digest,'phase',d.phase)::text,'UTF8'),'sha256'),'hex'),
        CASE WHEN d.state='submitting' THEN 'reconcile' ELSE d.state END,
        LEAST(d.submit_count,8),NULL,NULL,d.hermes_run_id,
        CASE WHEN d.state='completed' THEN 'completed' ELSE NULL END,
        NULL,
        CASE WHEN d.state='submitting' THEN 'legacy doc attempt lacks exact request bytes'
             WHEN d.output_digest IS NOT NULL THEN 'legacy output digest: '||d.output_digest ELSE NULL END,
        d.error,d.created_at,d.updated_at
      FROM hermes_doc_runs d
      ON CONFLICT DO NOTHING
    $migration$;
    UPDATE requests r SET status='reconcile',finished_at=clock_timestamp(),lease_expires_at=NULL,
      fail_response='legacy doc attempt lacks exact request bytes'
    FROM hermes_doc_runs d WHERE d.request_id=r.id AND d.state='submitting'
      AND r.status IN ('queued','running');
    DROP TABLE hermes_doc_runs;
  END IF;
END $$;

CREATE OR REPLACE FUNCTION hermes_configure_api_route(
  target_kind TEXT,target_profile TEXT,target_auth_generation BIGINT,target_profile_generation TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE current_route hermes_kind_routes%ROWTYPE;
BEGIN
  SELECT * INTO current_route FROM hermes_kind_routes WHERE kind=target_kind FOR UPDATE;
  IF current_route.kind IS NULL OR current_route.route<>'api'
     OR current_route.profile<>target_profile OR target_auth_generation<1
     OR target_profile_generation !~ '^[0-9a-f]{64}$' THEN
    RETURN false;
  END IF;
  IF EXISTS (SELECT 1 FROM hermes_runs h JOIN requests r ON r.id=h.request_id
             WHERE r.kind=target_kind AND h.state='submitting'
               AND (h.auth_generation<>target_auth_generation OR h.profile_generation<>target_profile_generation)) THEN
    RETURN false;
  END IF;
  UPDATE hermes_kind_routes SET
    generation=generation+CASE WHEN profile_generation<>repeat('0',64)
      AND (auth_generation<>target_auth_generation OR profile_generation<>target_profile_generation) THEN 1 ELSE 0 END,
    auth_generation=target_auth_generation,profile_generation=target_profile_generation WHERE kind=target_kind;
  RETURN true;
END;
$$;

CREATE OR REPLACE FUNCTION hermes_switch_route(
  target_kind TEXT,target_expected_route TEXT,target_expected_generation BIGINT,target_new_route TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE current_route hermes_kind_routes%ROWTYPE;
BEGIN
  SELECT * INTO current_route FROM hermes_kind_routes WHERE kind=target_kind FOR UPDATE;
  IF current_route.kind IS NULL OR current_route.route<>target_expected_route
     OR current_route.generation<>target_expected_generation OR target_new_route NOT IN ('native','api')
     OR target_new_route=current_route.route THEN RETURN false; END IF;
  IF (current_route.route='api' AND EXISTS (
        SELECT 1 FROM hermes_runs h JOIN requests r ON r.id=h.request_id
        WHERE r.kind=target_kind AND h.state='submitting'))
     OR (current_route.route='native' AND EXISTS (
        SELECT 1 FROM requests WHERE kind=target_kind AND status='running')) THEN RETURN false; END IF;
  UPDATE hermes_kind_routes SET route=target_new_route,generation=generation+1 WHERE kind=target_kind;
  RETURN true;
END;
$$;

CREATE OR REPLACE FUNCTION hermes_api_candidate(target_kind TEXT)
RETURNS JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public,pg_temp AS $$
  SELECT jsonb_build_object('id',r.id,'kind',r.kind,'payload',r.payload,
    'dedupe_key',r.dedupe_key,'created_at',r.created_at)
  FROM requests r JOIN hermes_kind_routes k ON k.kind=r.kind
  WHERE r.kind=target_kind AND r.status='queued' AND k.route='api'
  ORDER BY r.created_at,r.id LIMIT 1;
$$;

-- Retained rollback claimant is route-fenced and cannot overlap the API generation. Normal operation
-- has no host dispatcher or caller for this signature.
CREATE OR REPLACE FUNCTION hermes_claim_request(
  target_kind TEXT,target_run_id TEXT,target_nonce TEXT,lease_seconds INTEGER)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE claimed requests%ROWTYPE; route_row hermes_kind_routes%ROWTYPE;
BEGIN
  IF lease_seconds<30 OR lease_seconds>3600 OR target_nonce !~ '^[0-9a-f]{32}$' THEN
    RAISE EXCEPTION 'invalid native claim';
  END IF;
  SELECT * INTO route_row FROM hermes_kind_routes WHERE kind=target_kind FOR UPDATE;
  IF route_row.kind IS NULL OR route_row.route<>'native' THEN RETURN NULL; END IF;
  UPDATE requests SET status='queued',started_at=NULL,finished_at=NULL,run_id=NULL,run_nonce=NULL,
    lease_expires_at=NULL,fail_response='requeued after expired native lease'
    WHERE kind=target_kind AND status='running' AND lease_expires_at<=clock_timestamp() AND side_effect_at IS NULL;
  UPDATE requests SET status='reconcile',finished_at=clock_timestamp(),lease_expires_at=NULL,
    fail_response='expired native lease after side-effect intent; reconcile remote state'
    WHERE kind=target_kind AND status='running' AND lease_expires_at<=clock_timestamp() AND side_effect_at IS NOT NULL;
  IF (SELECT count(*) FROM requests WHERE kind=target_kind AND status='running') >= route_row.max_concurrent THEN RETURN NULL; END IF;
  WITH candidate AS (
    SELECT id FROM requests WHERE kind=target_kind AND status='queued'
    ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1)
  UPDATE requests r SET status='running',started_at=clock_timestamp(),run_id=target_run_id,
    run_nonce=target_nonce,lease_expires_at=clock_timestamp()+make_interval(secs=>lease_seconds)
    FROM candidate c WHERE r.id=c.id RETURNING r.* INTO claimed;
  IF claimed.id IS NULL THEN RETURN NULL; END IF;
  RETURN jsonb_build_object('id',claimed.id,'kind',claimed.kind,'payload',claimed.payload,
    'dedupe_key',claimed.dedupe_key,'created_at',claimed.created_at);
END;
$$;

CREATE OR REPLACE FUNCTION hermes_claim_request(
  target_kind TEXT,target_expected_route TEXT,target_request_id BIGINT,target_prompt TEXT,
  target_profile TEXT,target_auth_generation BIGINT,target_profile_generation TEXT,
  target_nonce TEXT,lease_seconds INTEGER)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE route_row hermes_kind_routes%ROWTYPE; claimed requests%ROWTYPE; attempt INTEGER;
DECLARE operation TEXT; idem TEXT; body BYTEA; body_digest TEXT;
BEGIN
  IF target_expected_route<>'api' OR target_nonce !~ '^[0-9a-f]{32}$'
     OR lease_seconds<30 OR lease_seconds>3600 OR octet_length(target_prompt)>1048576 THEN
    RAISE EXCEPTION 'invalid API claim';
  END IF;
  SELECT * INTO route_row FROM hermes_kind_routes WHERE kind=target_kind FOR UPDATE;
  IF route_row.kind IS NULL OR route_row.route<>target_expected_route
     OR route_row.profile<>target_profile OR route_row.auth_generation<>target_auth_generation
     OR route_row.profile_generation<>target_profile_generation THEN RETURN NULL; END IF;
  IF (SELECT count(*) FROM hermes_runs h JOIN requests r ON r.id=h.request_id
      WHERE r.kind=target_kind AND h.state='submitting') >= route_row.max_concurrent THEN RETURN NULL; END IF;
  SELECT * INTO claimed FROM requests WHERE id=target_request_id AND kind=target_kind
    AND status='queued' FOR UPDATE SKIP LOCKED;
  IF claimed.id IS NULL THEN RETURN NULL; END IF;
  operation := CASE target_kind
    WHEN 'pr-review' THEN 'review:'||claimed.dedupe_key
    WHEN 'pr-maintain' THEN 'maintain:'||claimed.dedupe_key||':round:'||
      (1+(SELECT count(*) FROM requests x WHERE x.kind='pr-maintain'
          AND split_part(x.dedupe_key,'@',1)=split_part(claimed.dedupe_key,'@',1)
          AND x.id<claimed.id AND x.status<>'superseded'))
    WHEN 'swe-implement' THEN 'swe:'||COALESCE(claimed.payload->>'operation_id',claimed.dedupe_key)
    WHEN 'doc-write' THEN 'doc:'||claimed.id
    WHEN 'pr-safety-review' THEN 'safety:'||COALESCE(claimed.payload->>'operation_id',claimed.dedupe_key)
    ELSE 'memory:'||claimed.dedupe_key END;
  IF EXISTS (SELECT 1 FROM hermes_runs WHERE operation_key=operation
             AND (state='submitting' OR (state='reconcile' AND reconcile_outcome IS NULL))) THEN RETURN NULL; END IF;
  SELECT COALESCE(max(attempt_no),0)+1 INTO attempt FROM hermes_runs WHERE request_id=claimed.id;
  idem := 'request:'||claimed.id||':attempt:'||attempt||':generation:'||route_row.generation;
  body := convert_to(jsonb_build_object('input',target_prompt,
    'session_id','fleet-'||target_kind||'-'||claimed.id||'-'||attempt,
    'instructions','Return only the required typed JSON result for nonce '||target_nonce)::text,'UTF8');
  body_digest := encode(digest(body,'sha256'),'hex');
  UPDATE requests SET status='running',started_at=clock_timestamp(),run_id=idem,run_nonce=target_nonce,
    lease_expires_at=clock_timestamp()+make_interval(secs=>lease_seconds),side_effect_at=NULL,
    fail_response=NULL WHERE id=claimed.id;
  INSERT INTO hermes_runs(request_id,attempt_no,operation_key,route_generation,auth_generation,
    profile,profile_generation,idempotency_key,request_bytes,request_digest,state)
  VALUES(claimed.id,attempt,operation,route_row.generation,route_row.auth_generation,
    route_row.profile,route_row.profile_generation,idem,body,body_digest,'submitting');
  RETURN jsonb_build_object('request_id',claimed.id,'attempt_no',attempt,'kind',target_kind,
    'nonce',target_nonce,'profile',route_row.profile,'route_generation',route_row.generation,
    'idempotency_key',idem,'request_b64',encode(body,'base64'),'request_digest',body_digest,
    'payload',claimed.payload,'dedupe_key',claimed.dedupe_key,'created_at',claimed.created_at);
END;
$$;

CREATE OR REPLACE FUNCTION hermes_api_open_attempts()
RETURNS SETOF JSONB LANGUAGE sql STABLE SECURITY DEFINER SET search_path=public,pg_temp AS $$
  SELECT jsonb_build_object('request_id',h.request_id,'attempt_no',h.attempt_no,'kind',r.kind,
    'nonce',r.run_nonce,'profile',h.profile,'route_generation',h.route_generation,
    'idempotency_key',h.idempotency_key,'request_b64',encode(h.request_bytes,'base64'),
    'request_digest',h.request_digest,'payload',r.payload,'dedupe_key',r.dedupe_key,
    'created_at',r.created_at,'run_id',h.run_id,'first_submit_at',h.first_submit_at,'submit_count',h.submit_count)
  FROM hermes_runs h JOIN requests r ON r.id=h.request_id
  WHERE h.state='submitting' ORDER BY h.created_at;
$$;

CREATE OR REPLACE FUNCTION hermes_api_recover_lease(
  target_id BIGINT,target_attempt INTEGER,target_nonce TEXT,lease_seconds INTEGER)
RETURNS BOOLEAN LANGUAGE sql SECURITY DEFINER SET search_path=public,pg_temp AS $$
  WITH recovered AS (
    UPDATE requests r SET lease_expires_at=clock_timestamp()+make_interval(secs=>lease_seconds)
    FROM hermes_runs h WHERE r.id=target_id AND h.request_id=r.id AND h.attempt_no=target_attempt
      AND h.state='submitting' AND r.status='running' AND r.run_nonce=target_nonce
      AND lease_seconds BETWEEN 30 AND 3600 RETURNING 1)
  SELECT EXISTS(SELECT 1 FROM recovered);
$$;

CREATE OR REPLACE FUNCTION hermes_api_begin_submit(
  target_id BIGINT,target_attempt INTEGER,target_nonce TEXT,target_generation BIGINT)
RETURNS JSONB LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE result JSONB;
BEGIN
  WITH eligible AS (
    SELECT h.request_id,h.attempt_no FROM hermes_runs h
    JOIN requests r ON r.id=h.request_id JOIN hermes_kind_routes k ON k.kind=r.kind
    WHERE h.request_id=target_id AND h.attempt_no=target_attempt AND h.state='submitting'
      AND r.status='running' AND r.run_nonce=target_nonce AND r.lease_expires_at>clock_timestamp()
      AND k.route='api' AND k.generation=target_generation AND h.route_generation=k.generation
      AND h.auth_generation=k.auth_generation AND h.profile=k.profile
      AND h.profile_generation=k.profile_generation AND h.run_id IS NULL AND h.submit_count<8
      AND (h.first_submit_at IS NULL OR
           (clock_timestamp()<h.replay_until AND clock_timestamp()<h.first_submit_at+interval '5 minutes'))
    FOR UPDATE OF h,k,r
  ), changed AS (
    UPDATE hermes_runs h SET first_submit_at=COALESCE(h.first_submit_at,statement_timestamp()),
      replay_until=COALESCE(h.replay_until,COALESCE(h.first_submit_at,statement_timestamp())+interval '23 hours'),
      submit_started_at=clock_timestamp(),submit_count=h.submit_count+1,updated_at=clock_timestamp()
    FROM eligible e WHERE h.request_id=e.request_id AND h.attempt_no=e.attempt_no
    RETURNING h.idempotency_key,encode(h.request_bytes,'base64') request_b64,h.request_digest,h.profile,h.submit_count)
  SELECT to_jsonb(changed) INTO result FROM changed;
  RETURN result;
END;
$$;

CREATE OR REPLACE FUNCTION hermes_api_accept_run(
  target_id BIGINT,target_attempt INTEGER,target_nonce TEXT,target_run_id TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
BEGIN
  IF target_run_id IS NULL OR length(target_run_id)>200 THEN RETURN false; END IF;
  UPDATE hermes_runs h SET run_id=target_run_id,updated_at=clock_timestamp()
  FROM requests r WHERE h.request_id=target_id AND h.attempt_no=target_attempt
    AND h.state='submitting' AND h.run_id IS NULL AND r.id=h.request_id
    AND r.status='running' AND r.run_nonce=target_nonce AND r.lease_expires_at>clock_timestamp();
  IF FOUND THEN UPDATE requests SET run_id=target_run_id WHERE id=target_id AND run_nonce=target_nonce; RETURN true; END IF;
  RETURN EXISTS (SELECT 1 FROM hermes_runs WHERE request_id=target_id AND attempt_no=target_attempt AND run_id=target_run_id);
END;
$$;

CREATE OR REPLACE FUNCTION hermes_api_record_terminal(
  target_id BIGINT,target_attempt INTEGER,target_nonce TEXT,target_status TEXT,target_output BYTEA)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
BEGIN
  IF target_status NOT IN ('completed','failed','cancelled','interrupted') OR octet_length(target_output)>1048576 THEN
    RETURN false;
  END IF;
  UPDATE hermes_runs h SET terminal_status=target_status,output_bytes=target_output,
    output_digest=encode(digest(target_output,'sha256'),'hex'),updated_at=clock_timestamp()
  FROM requests r WHERE h.request_id=target_id AND h.attempt_no=target_attempt
    AND h.state='submitting' AND h.run_id IS NOT NULL AND r.id=h.request_id
    AND r.status='running' AND r.run_nonce=target_nonce AND r.lease_expires_at>clock_timestamp();
  RETURN FOUND;
END;
$$;

CREATE OR REPLACE FUNCTION hermes_api_mark_stop(target_id BIGINT,target_attempt INTEGER,target_confirmed BOOLEAN)
RETURNS VOID LANGUAGE sql SECURITY DEFINER SET search_path=public,pg_temp AS $$
  UPDATE hermes_runs SET stop_requested_at=COALESCE(stop_requested_at,clock_timestamp()),
    stop_confirmed_at=CASE WHEN target_confirmed THEN clock_timestamp() ELSE stop_confirmed_at END,
    updated_at=clock_timestamp() WHERE request_id=target_id AND attempt_no=target_attempt AND state='submitting';
$$;

CREATE OR REPLACE FUNCTION hermes_finish_api_attempt(
  target_id BIGINT,target_attempt INTEGER,target_nonce TEXT,target_generation BIGINT,
  target_status TEXT,target_detail TEXT,target_posted_ref TEXT,target_attempt_state TEXT)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
DECLARE target_kind TEXT;
BEGIN
  IF target_status NOT IN ('done','failed','skipped','reconcile')
     OR target_attempt_state NOT IN ('completed','failed','reconcile') THEN RAISE EXCEPTION 'invalid API settlement'; END IF;
  SELECT r.kind INTO target_kind FROM requests r JOIN hermes_runs h ON h.request_id=r.id
    JOIN hermes_kind_routes k ON k.kind=r.kind
    WHERE r.id=target_id AND h.attempt_no=target_attempt AND h.state='submitting'
      AND h.route_generation=target_generation AND k.generation=target_generation AND k.route='api'
      AND r.status='running' AND r.run_nonce=target_nonce AND r.lease_expires_at>clock_timestamp()
    FOR UPDATE OF r,h,k;
  IF target_kind IS NULL THEN RETURN false; END IF;
  IF target_kind='swe-implement' AND target_status='done' AND
     target_posted_ref !~ '^https://github.com/[^/]+/[^/]+/pull/[1-9][0-9]*$' THEN RAISE EXCEPTION 'invalid SWE result'; END IF;
  UPDATE requests SET status=target_status,finished_at=clock_timestamp(),fail_response=target_detail,
    posted_ref=NULLIF(target_posted_ref,''),lease_expires_at=NULL WHERE id=target_id;
  UPDATE hermes_runs SET state=target_attempt_state,error=CASE WHEN target_attempt_state='completed' THEN NULL ELSE left(target_detail,500) END,
    reconcile_reason=CASE WHEN target_attempt_state='reconcile' THEN left(target_detail,500) END,
    updated_at=clock_timestamp() WHERE request_id=target_id AND attempt_no=target_attempt;
  RETURN true;
END;
$$;

CREATE OR REPLACE FUNCTION hermes_reconcile_api_attempt(
  target_id BIGINT,target_attempt INTEGER,target_outcome TEXT,target_actor TEXT,target_reason TEXT DEFAULT NULL)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
BEGIN
  IF target_outcome NOT IN ('done','not-done','abandoned') OR length(trim(target_actor))<1 THEN RETURN false; END IF;
  UPDATE hermes_runs SET reconcile_outcome=target_outcome,reconciled_by=left(target_actor,200),
    reconciled_at=clock_timestamp(),reconcile_reason=COALESCE(left(target_reason,500),reconcile_reason),
    updated_at=clock_timestamp()
  WHERE request_id=target_id AND attempt_no=target_attempt AND state='reconcile' AND reconcile_outcome IS NULL;
  IF NOT FOUND THEN RETURN false; END IF;
  UPDATE requests SET status=CASE target_outcome WHEN 'done' THEN 'done'
      WHEN 'not-done' THEN 'failed' ELSE 'skipped' END,
    finished_at=clock_timestamp(),lease_expires_at=NULL,
    fail_response=COALESCE(left(target_reason,500),'human reconcile: '||target_outcome)
  WHERE id=target_id AND status='reconcile';
  RETURN true;
END;
$$;

CREATE OR REPLACE FUNCTION hermes_complete_effect_attempt(
  target_id BIGINT,target_attempt INTEGER,target_nonce TEXT,target_state TEXT,target_error TEXT DEFAULT NULL)
RETURNS BOOLEAN LANGUAGE plpgsql SECURITY DEFINER SET search_path=public,pg_temp AS $$
BEGIN
  IF target_state NOT IN ('completed','failed','reconcile') THEN RETURN false; END IF;
  UPDATE hermes_runs h SET state=target_state,error=left(target_error,500),
    reconcile_reason=CASE WHEN target_state='reconcile' THEN left(target_error,500) END,
    updated_at=clock_timestamp()
  FROM requests r WHERE h.request_id=target_id AND h.attempt_no=target_attempt AND h.state='submitting'
    AND r.id=h.request_id AND r.status<>'running' AND r.run_nonce=target_nonce;
  RETURN FOUND;
END;
$$;

REVOKE ALL ON hermes_kind_routes,hermes_runs FROM PUBLIC,hermes_worker;
REVOKE ALL ON FUNCTION hermes_configure_api_route(TEXT,TEXT,BIGINT,TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION hermes_switch_route(TEXT,TEXT,BIGINT,TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION hermes_api_candidate(TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION hermes_claim_request(TEXT,TEXT,TEXT,INTEGER) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION hermes_claim_request(TEXT,TEXT,TEXT,INTEGER) TO hermes_worker;
REVOKE ALL ON FUNCTION hermes_claim_request(TEXT,TEXT,BIGINT,TEXT,TEXT,BIGINT,TEXT,TEXT,INTEGER) FROM PUBLIC;
REVOKE ALL ON FUNCTION hermes_api_open_attempts() FROM PUBLIC;
REVOKE ALL ON FUNCTION hermes_api_recover_lease(BIGINT,INTEGER,TEXT,INTEGER) FROM PUBLIC;
REVOKE ALL ON FUNCTION hermes_api_begin_submit(BIGINT,INTEGER,TEXT,BIGINT) FROM PUBLIC;
REVOKE ALL ON FUNCTION hermes_api_accept_run(BIGINT,INTEGER,TEXT,TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION hermes_api_record_terminal(BIGINT,INTEGER,TEXT,TEXT,BYTEA) FROM PUBLIC;
REVOKE ALL ON FUNCTION hermes_api_mark_stop(BIGINT,INTEGER,BOOLEAN) FROM PUBLIC;
REVOKE ALL ON FUNCTION hermes_finish_api_attempt(BIGINT,INTEGER,TEXT,BIGINT,TEXT,TEXT,TEXT,TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION hermes_reconcile_api_attempt(BIGINT,INTEGER,TEXT,TEXT,TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION hermes_complete_effect_attempt(BIGINT,INTEGER,TEXT,TEXT,TEXT) FROM PUBLIC;
COMMIT;
