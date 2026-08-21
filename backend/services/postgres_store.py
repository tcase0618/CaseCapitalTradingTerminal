"""Optional PostgreSQL durability layer.

MongoDB remains the active application store until an explicit subsystem
cutover. This module gives the terminal a conservative Postgres foundation:
schema creation, append-only events, latest-document snapshots, and migration
helpers. All calls are no-op unless POSTGRES_ENABLED=true and POSTGRES_DSN is
configured.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

try:
    import asyncpg
except Exception:  # pragma: no cover - optional dependency at import time
    asyncpg = None  # type: ignore


_pool: Any | None = None
_last_error: str | None = None
_schema_ready = False


CRITICAL_COLLECTIONS: tuple[str, ...] = (
    "activity_log",
    "scan_results",
    "bot_state",
    "execution_gate_checks",
    "data_truth_snapshots",
    "qc_events",
    "schedule_control_runs",
    "pm_decisions",
    "tf_queued_orders",
    "tf_trades",
    "tf_journal",
    "tf_phase_outcomes",
    "options_desk_candidates",
    "options_desk_orders",
    "options_desk_trades",
    "options_desk_risk_checks",
    "options_mark_audits",
    "earnings_pm_decisions",
    "case_court_sessions",
    "case_court_trials",
    "pharma_pdufa",
    "pharma_pm_decisions",
    "pharma_option_snapshots",
    "pharma_catalyst_shocks",
    "pharma_active_plays",
    "pharma_track_record",
    "kronos_forecasts",
    "kronos_prediction_audits",
    "telegram_reports",
)


def enabled() -> bool:
    return os.environ.get("POSTGRES_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def dsn() -> str:
    return os.environ.get("POSTGRES_DSN", "").strip()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    # ObjectId and other provider objects stringify cleanly enough for audit.
    return str(value)


def _sanitize_json(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _sanitize_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_json(v) for v in value]
    return value


def normalize_json(value: Any) -> Any:
    serialized = json.dumps(value, default=_json_default, ensure_ascii=False)
    return _sanitize_json(json.loads(serialized))


def doc_key(collection: str, doc: dict[str, Any]) -> str:
    """Stable best-effort natural key for mirrored Mongo documents."""
    for key in (
        "_id",
        "id",
        "order_id",
        "client_order_id",
        "session_id",
        "trial_id",
        "symbol",
        "ticker",
    ):
        if doc.get(key) not in (None, ""):
            if key == "ticker" and doc.get("pdufa_date"):
                return f"{doc[key]}:{doc['pdufa_date']}"
            return str(doc[key])
    for key in ("finished_at", "generated_at", "snapshot_at", "created_at", "ts"):
        if doc.get(key):
            return str(doc[key])
    payload = normalize_json(doc)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{collection}:{raw}".encode("utf-8")).hexdigest()


def occurred_at(doc: dict[str, Any]) -> datetime:
    for key in ("created_at", "finished_at", "generated_at", "snapshot_at", "ts", "routed_at"):
        value = doc.get(key)
        if not value:
            continue
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
    return _now()


async def init_pool() -> bool:
    global _pool, _last_error
    if not enabled():
        return False
    if _pool is not None:
        return True
    if asyncpg is None:
        _last_error = "asyncpg is not installed"
        return False
    if not dsn():
        _last_error = "POSTGRES_DSN is empty"
        return False
    try:
        _pool = await asyncpg.create_pool(dsn(), min_size=1, max_size=int(os.environ.get("POSTGRES_POOL_MAX", "5")))
        _last_error = None
        return True
    except Exception as exc:
        _last_error = str(exc)[:500]
        _pool = None
        return False


async def init_schema() -> bool:
    global _schema_ready, _last_error
    if _schema_ready:
        return True
    if not await init_pool():
        return False
    assert _pool is not None
    sql = """
    create table if not exists cc_events (
      id bigserial primary key,
      collection text not null,
      natural_key text,
      event_type text not null default 'upsert',
      source text not null default 'case-capital',
      occurred_at timestamptz not null default now(),
      payload jsonb not null,
      created_at timestamptz not null default now()
    );
    create index if not exists idx_cc_events_collection_occurred
      on cc_events(collection, occurred_at desc);
    create index if not exists idx_cc_events_natural_key
      on cc_events(collection, natural_key);

    create table if not exists cc_collection_snapshots (
      collection text not null,
      doc_key text not null,
      payload jsonb not null,
      updated_at timestamptz not null default now(),
      primary key(collection, doc_key)
    );
    create index if not exists idx_cc_snapshots_updated
      on cc_collection_snapshots(collection, updated_at desc);

    create table if not exists cc_migration_runs (
      id bigserial primary key,
      started_at timestamptz not null default now(),
      completed_at timestamptz,
      status text not null,
      collections jsonb not null default '{}'::jsonb,
      error text
    );
    """
    try:
        async with _pool.acquire() as conn:
            await conn.execute(sql)
        _schema_ready = True
        _last_error = None
        return True
    except Exception as exc:
        _last_error = str(exc)[:500]
        return False


async def close_pool() -> None:
    global _pool, _schema_ready
    if _pool is not None:
        await _pool.close()
    _pool = None
    _schema_ready = False


async def write_event(
    collection: str,
    payload: dict[str, Any],
    *,
    natural_key: str | None = None,
    event_type: str = "upsert",
    source: str = "case-capital",
    at: datetime | None = None,
) -> bool:
    global _last_error
    if not await init_schema():
        return False
    assert _pool is not None
    clean = normalize_json(payload)
    try:
        async with _pool.acquire() as conn:
            await conn.execute(
                """
                insert into cc_events(collection, natural_key, event_type, source, occurred_at, payload)
                values($1, $2, $3, $4, $5, $6::jsonb)
                """,
                collection,
                natural_key,
                event_type,
                source,
                at or occurred_at(payload),
                json.dumps(clean, default=_json_default),
            )
        _last_error = None
        return True
    except Exception as exc:
        _last_error = str(exc)[:500]
        return False


async def upsert_snapshot(
    collection: str,
    key: str,
    payload: dict[str, Any],
    *,
    source: str = "case-capital",
    event_type: str = "upsert",
) -> bool:
    global _last_error
    if not await init_schema():
        return False
    assert _pool is not None
    clean = normalize_json(payload)
    raw = json.dumps(clean, default=_json_default)
    try:
        async with _pool.acquire() as conn:
            await conn.execute(
                """
                insert into cc_collection_snapshots(collection, doc_key, payload, updated_at)
                values($1, $2, $3::jsonb, now())
                on conflict(collection, doc_key) do update
                set payload = excluded.payload, updated_at = excluded.updated_at
                """,
                collection,
                key,
                raw,
            )
        await write_event(collection, clean, natural_key=key, event_type=event_type, source=source)
        _last_error = None
        return True
    except Exception as exc:
        _last_error = str(exc)[:500]
        return False


async def mirror_document(collection: str, doc: dict[str, Any], *, source: str = "mongo-dual-write") -> bool:
    return await upsert_snapshot(collection, doc_key(collection, doc), doc, source=source)


async def status() -> dict[str, Any]:
    ready = await init_schema() if enabled() else False
    counts: dict[str, int] = {}
    if ready and _pool is not None:
        try:
            async with _pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    select collection, count(*)::int as count
                    from cc_collection_snapshots
                    group by collection
                    order by collection
                    """
                )
            counts = {r["collection"]: r["count"] for r in rows}
        except Exception as exc:
            global _last_error
            _last_error = str(exc)[:500]
            ready = False
    return {
        "enabled": enabled(),
        "ready": ready,
        "dsn_configured": bool(dsn()),
        "schema_ready": _schema_ready,
        "last_error": _last_error,
        "mirrored_counts": counts,
        "critical_collections": list(CRITICAL_COLLECTIONS),
    }
