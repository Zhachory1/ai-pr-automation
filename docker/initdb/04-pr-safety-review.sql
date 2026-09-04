-- PR safety review terminal state: an immutable snapshot changed before analysis.
ALTER TABLE requests DROP CONSTRAINT IF EXISTS requests_status_check;
ALTER TABLE requests ADD CONSTRAINT requests_status_check
  CHECK (status IN ('queued','running','done','failed','superseded'));
