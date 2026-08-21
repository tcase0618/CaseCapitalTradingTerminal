# Case Capital Postgres Migration

MongoDB remains the active primary store until an explicit cutover. Postgres is
introduced as a durable, SQL-queryable mirror for the records that matter most:
scans, execution gate checks, data truth, trade/order records, options records,
Case Court, pharma, Kronos, scheduler/QC, and Telegram reports.

## VPS Setup

```bash
cd /opt/case-capital/stock-intel
git pull origin codex/desktop-checkpoint
POSTGRES_PASSWORD='CHANGE_ME_STRONG' sudo -E bash deploy/vps/setup-postgres.sh
```

Then verify:

```bash
curl -s http://127.0.0.1:8001/api/postgres/status | python3 -m json.tool
```

Expected:

```json
{
  "enabled": true,
  "ready": true,
  "dsn_configured": true
}
```

## Non-Destructive Migration

Dry run:

```bash
cd /opt/case-capital/stock-intel/backend
sudo -u casecapital .venv/bin/python migrate_mongo_to_postgres.py --dry-run --limit 5000
```

Mirror critical collections:

```bash
cd /opt/case-capital/stock-intel/backend
sudo -u casecapital .venv/bin/python migrate_mongo_to_postgres.py --limit 5000
```

The migration writes to:

- `cc_collection_snapshots`: latest document by collection/doc key.
- `cc_events`: append-only event stream.
- `cc_migration_runs`: run status and per-collection counts.

Mongo is not deleted or mutated.

## Cutover Rule

Do not switch read paths away from Mongo until:

1. Mongo writes are unblocked.
2. Postgres `/api/postgres/status` is ready.
3. Migration counts are reviewed.
4. Dual writes run through at least one full market session.
5. Trading records, scans, PM decisions, options, QC, and pharma counts match expectations.
