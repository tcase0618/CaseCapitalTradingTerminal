BEGIN;
LOCK TABLE cc_collection_snapshots IN SHARE ROW EXCLUSIVE MODE;
CREATE TABLE IF NOT EXISTS cc_kronos_identity_archive (
  collection text NOT NULL,
  old_doc_key text NOT NULL,
  payload jsonb NOT NULL,
  updated_at timestamptz NOT NULL,
  archived_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(collection, old_doc_key)
);
CREATE TEMP TABLE kronos_rekey ON COMMIT DROP AS
SELECT collection, doc_key, payload, updated_at,
  CASE collection
    WHEN 'kronos_forecast_snapshots' THEN payload->>'snapshot_key'
    WHEN 'kronos_pm_disagreements' THEN payload->>'audit_id'
  END AS natural_key
FROM cc_collection_snapshots
WHERE collection IN ('kronos_forecast_snapshots', 'kronos_pm_disagreements');
INSERT INTO cc_kronos_identity_archive(collection, old_doc_key, payload, updated_at)
SELECT collection, doc_key, payload, updated_at FROM kronos_rekey WHERE natural_key IS NOT NULL
ON CONFLICT DO NOTHING;
DELETE FROM cc_collection_snapshots s USING kronos_rekey r
WHERE s.collection = r.collection AND s.doc_key = r.doc_key AND r.natural_key IS NOT NULL;
INSERT INTO cc_collection_snapshots(collection, doc_key, payload, updated_at)
SELECT DISTINCT ON (collection, natural_key) collection, natural_key, payload, updated_at
FROM kronos_rekey WHERE natural_key IS NOT NULL
ORDER BY collection, natural_key, payload->>'generated_at' DESC, updated_at DESC
ON CONFLICT(collection, doc_key) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at;
COMMIT;
