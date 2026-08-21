"""Mirror critical MongoDB collections into PostgreSQL.

Usage:
  cd backend
  python migrate_mongo_to_postgres.py --limit 5000

This is intentionally non-destructive. Mongo remains untouched; Postgres gets
latest-document snapshots plus append-only event rows for each mirrored doc.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from services.db import get_db  # noqa: E402
from services import postgres_store  # noqa: E402


DEFAULT_SORTS: dict[str, tuple[str, int]] = {
    "activity_log": ("ts", -1),
    "scan_results": ("finished_at", -1),
    "execution_gate_checks": ("generated_at", -1),
    "data_truth_snapshots": ("generated_at", -1),
    "tf_trades": ("created_at", -1),
    "tf_journal": ("created_at", -1),
    "options_desk_orders": ("submitted_at", -1),
    "options_desk_trades": ("opened_at", -1),
    "case_court_sessions": ("generated_at", -1),
    "case_court_trials": ("generated_at", -1),
    "pharma_pdufa": ("pdufa_date", 1),
    "pharma_pm_decisions": ("routed_at", -1),
    "pharma_option_snapshots": ("snapshot_at", -1),
    "pharma_catalyst_shocks": ("detected_at", -1),
    "kronos_forecasts": ("generated_at", -1),
    "telegram_reports": ("created_at", -1),
}


def _collections(raw: str | None) -> list[str]:
    if not raw:
        return list(postgres_store.CRITICAL_COLLECTIONS)
    return [x.strip() for x in raw.split(",") if x.strip()]


async def _mark_run(status: str, collections: dict[str, Any], error: str | None = None, run_id: int | None = None) -> int | None:
    if not await postgres_store.init_schema():
        return None
    pool = postgres_store._pool  # type: ignore[attr-defined]
    if pool is None:
        return None
    async with pool.acquire() as conn:
        if run_id:
            await conn.execute(
                """
                update cc_migration_runs
                set status=$1, completed_at=now(), collections=$2::jsonb, error=$3
                where id=$4
                """,
                status,
                json.dumps(collections),
                error,
                run_id,
            )
            return run_id
        return await conn.fetchval(
            """
            insert into cc_migration_runs(status, collections)
            values($1, $2::jsonb)
            returning id
            """,
            status,
            json.dumps(collections),
        )


async def migrate(limit: int, collections: list[str], dry_run: bool = False) -> dict[str, Any]:
    if dry_run:
        os.environ["POSTGRES_ENABLED"] = "false"
    db = get_db()
    run_id = None if dry_run else await _mark_run("running", {})
    results: dict[str, Any] = {}
    try:
        if not dry_run and not await postgres_store.init_schema():
            raise RuntimeError((await postgres_store.status()).get("last_error") or "Postgres is not ready")
        for name in collections:
            sort = DEFAULT_SORTS.get(name, ("created_at", -1))
            mirrored = 0
            errors = 0
            try:
                cursor = db[name].find({}, {"_id": 0}).sort(sort[0], sort[1]).limit(limit)
                docs = await cursor.to_list(limit)
            except Exception:
                cursor = db[name].find({}, {"_id": 0}).limit(limit)
                docs = await cursor.to_list(limit)
            for doc in docs:
                if dry_run:
                    mirrored += 1
                    continue
                ok = await postgres_store.mirror_document(name, doc, source="mongo-migration")
                if ok:
                    mirrored += 1
                else:
                    errors += 1
            results[name] = {"seen": len(docs), "mirrored": mirrored, "errors": errors}
        if run_id:
            await _mark_run("complete", results, run_id=run_id)
        return {
            "ok": True,
            "dry_run": dry_run,
            "run_id": run_id,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "collections": results,
        }
    except Exception as exc:
        if run_id:
            await _mark_run("failed", results, error=str(exc)[:500], run_id=run_id)
        return {"ok": False, "dry_run": dry_run, "run_id": run_id, "error": str(exc), "collections": results}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--collections", default=None, help="Comma-separated collection list. Defaults to critical collections.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    payload = asyncio.run(migrate(max(1, args.limit), _collections(args.collections), dry_run=args.dry_run))
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
