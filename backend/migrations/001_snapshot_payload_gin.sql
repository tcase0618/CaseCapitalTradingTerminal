-- Accelerates JSONB predicates used by scans, journals, and replay views.
-- This must remain a standalone statement: CONCURRENTLY cannot run in a
-- transaction, which is why migrations are run explicitly during deploy.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cc_snapshots_payload_gin
  ON cc_collection_snapshots USING GIN (payload jsonb_path_ops);
