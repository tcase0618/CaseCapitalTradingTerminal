"""Repair historical snapshot keys after the Mongo-to-Postgres cutover.

Run with ``--execute`` only after a database backup. The migration derives
keys from each event payload where available, so it can recover records that
were overwritten by an old ticker-only snapshot key.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from services import postgres_store


COLLECTIONS = (
    "pm_company_observations",
    "pm_decision_ledger",
    "pm_decision_outcomes",
    "pm_portfolio_snapshots",
    "pm_policy_reports",
    "pm_rebalance_intents",
    "signal_performance",
    "pm_ratchet_events",
)


async def repair(*, execute: bool) -> dict[str, Any]:
    if not await postgres_store.init_schema() or postgres_store._pool is None:
        raise RuntimeError(postgres_store._last_error or "Postgres unavailable")
    summary: dict[str, Any] = {"execute": execute, "collections": {}}
    async with postgres_store._pool.acquire() as conn:
        for collection in COLLECTIONS:
            rows = await conn.fetch(
                "select doc_key, payload, updated_at from cc_collection_snapshots where collection=$1 order by updated_at asc",
                collection,
            )
            reconstructed: dict[str, dict[str, Any]] = {}
            events = await conn.fetch(
                "select payload from cc_events where collection=$1 order by occurred_at asc, id asc",
                collection,
            )
            for row in [*rows, *events]:
                payload = row["payload"]
                if isinstance(payload, str):
                    payload = json.loads(payload)
                if not isinstance(payload, dict):
                    continue
                reconstructed[postgres_store.doc_key(collection, payload)] = postgres_store.normalize_json(payload)
            def snapshot_key(row: Any) -> str:
                payload = row["payload"]
                if isinstance(payload, str):
                    payload = json.loads(payload)
                return postgres_store.doc_key(collection, payload) if isinstance(payload, dict) else str(row["doc_key"])

            old_keys = {str(row["doc_key"]) for row in rows}
            desired_keys = set(reconstructed)
            summary["collections"][collection] = {
                "snapshot_rows": len(rows),
                "event_rows": len(events),
                "reconstructed_rows": len(reconstructed),
                "rekey_required": sum(1 for row in rows if snapshot_key(row) != str(row["doc_key"])),
                "stale_snapshot_keys": len(old_keys - desired_keys),
            }
            if not execute:
                continue
            for key, payload in reconstructed.items():
                await conn.execute(
                    """
                    insert into cc_collection_snapshots(collection, doc_key, payload, updated_at)
                    values($1, $2, $3::jsonb, now())
                    on conflict(collection, doc_key) do update
                    set payload=excluded.payload, updated_at=excluded.updated_at
                    """,
                    collection, key, json.dumps(payload, default=postgres_store._json_default),
                )
            # Only remove keys whose payload has been materialized under a
            # correct natural key. Events remain immutable audit history.
            for old_key in old_keys - desired_keys:
                await conn.execute(
                    "delete from cc_collection_snapshots where collection=$1 and doc_key=$2",
                    collection, old_key,
                )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="write repaired snapshot keys")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(repair(execute=args.execute)), indent=2, default=str))


if __name__ == "__main__":
    main()
