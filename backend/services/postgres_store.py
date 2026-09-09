"""PostgreSQL persistence and the collection-style async API.

The collection-style API lets existing terminal services use PostgreSQL
without a simultaneous rewrite of every strategy module.
"""
from __future__ import annotations

import hashlib
import asyncio
import json
import math
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

try:
    import asyncpg
except Exception:  # pragma: no cover - optional dependency at import time
    asyncpg = None  # type: ignore


_pool: Any | None = None
_last_error: str | None = None
_schema_ready = False


class PostgresResult:
    def __init__(self, **values: Any):
        self.__dict__.update(values)


def _path_get(doc: dict[str, Any], path: str, default: Any = None) -> Any:
    value: Any = doc
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return default
        value = value[part]
    return value


def _path_set(doc: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    target = doc
    for part in parts[:-1]:
        current = target.get(part)
        if not isinstance(current, dict):
            current = {}
            target[part] = current
        target = current
    target[parts[-1]] = value


def _path_delete(doc: dict[str, Any], path: str) -> None:
    parts = path.split(".")
    target: Any = doc
    for part in parts[:-1]:
        if not isinstance(target, dict):
            return
        target = target.get(part)
    if isinstance(target, dict):
        target.pop(parts[-1], None)


def _value_equal(actual: Any, expected: Any) -> bool:
    if isinstance(actual, list) and not isinstance(expected, list):
        return expected in actual
    return actual == expected


def _match_value(actual: Any, condition: Any) -> bool:
    if not isinstance(condition, dict) or not any(str(k).startswith("$") for k in condition):
        return _value_equal(actual, condition)
    for op, expected in condition.items():
        if op == "$exists":
            if bool(expected) != (actual is not None):
                return False
        elif op == "$in":
            values = actual if isinstance(actual, list) else [actual]
            if not any(any(_value_equal(v, e) for e in expected) for v in values):
                return False
        elif op == "$nin":
            values = actual if isinstance(actual, list) else [actual]
            if any(any(_value_equal(v, e) for e in expected) for v in values):
                return False
        elif op == "$ne" and _value_equal(actual, expected):
            return False
        elif op in {"$gt", "$gte", "$lt", "$lte"}:
            if actual is None:
                return False
            try:
                if op == "$gt" and not actual > expected: return False
                if op == "$gte" and not actual >= expected: return False
                if op == "$lt" and not actual < expected: return False
                if op == "$lte" and not actual <= expected: return False
            except TypeError:
                return False
        elif op == "$regex":
            import re
            if actual is None or re.search(str(expected), str(actual)) is None:
                return False
        elif op == "$elemMatch":
            if not isinstance(actual, list) or not any(_matches(item, expected) if isinstance(item, dict) else _match_value(item, expected) for item in actual):
                return False
        else:
            return False
    return True


def _matches(doc: dict[str, Any], query: dict[str, Any] | None) -> bool:
    if not query:
        return True
    for key, condition in query.items():
        if key == "$or":
            if not any(_matches(doc, item) for item in condition):
                return False
        elif key == "$and":
            if not all(_matches(doc, item) for item in condition):
                return False
        elif not _match_value(_path_get(doc, key), condition):
            return False
    return True


def _project(doc: dict[str, Any], projection: dict[str, Any] | None) -> dict[str, Any]:
    if not projection:
        return dict(doc)
    include = {k for k, v in projection.items() if v and k != "_id"}
    exclude = {k for k, v in projection.items() if not v}
    if include:
        out = {k: _path_get(doc, k) for k in include if _path_get(doc, k) is not None}
        if projection.get("_id", 1) and "_id" in doc:
            out["_id"] = doc["_id"]
        return out
    out = dict(doc)
    for key in exclude:
        _path_delete(out, key)
    return out


class PostgresCursor:
    def __init__(self, collection: "PostgresCollection", query: dict[str, Any] | None, projection: dict[str, Any] | None):
        self.collection, self.query, self.projection = collection, query or {}, projection
        self._sort: list[tuple[str, int]] = []
        self._limit: int | None = None
        self._skip = 0

    def sort(self, key_or_list: Any, direction: int | None = None):
        self._sort = [(key_or_list, direction or 1)] if isinstance(key_or_list, str) else list(key_or_list)
        return self

    def limit(self, value: int):
        self._limit = value
        return self

    def skip(self, value: int):
        self._skip = value
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        rows = await self.collection._read(self.query)
        for key, direction in reversed(self._sort):
            rows.sort(key=lambda row: (_path_get(row, key) is None, _path_get(row, key)), reverse=direction < 0)
        rows = rows[self._skip:]
        if self._limit is not None:
            rows = rows[:self._limit]
        if length is not None:
            rows = rows[:length]
        return [_project(row, self.projection) for row in rows]

    def __aiter__(self):
        async def gen():
            for row in await self.to_list(None):
                yield row
        return gen()


class PostgresCollection:
    def __init__(self, name: str):
        self.name = name

    async def _read(self, query: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if not await init_schema() or _pool is None:
            raise RuntimeError(_last_error or "Postgres is unavailable")
        async with _pool.acquire() as conn:
            rows = await conn.fetch("select payload from cc_collection_snapshots where collection=$1", self.name)
        documents: list[dict[str, Any]] = []
        for row in rows:
            payload = row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            if isinstance(payload, dict) and _matches(payload, query):
                documents.append(payload)
        return documents

    def find(self, query: dict[str, Any] | None = None, projection: dict[str, Any] | None = None, **kwargs: Any) -> PostgresCursor:
        return PostgresCursor(self, query, projection)

    async def find_one(self, query: dict[str, Any] | None = None, projection: dict[str, Any] | None = None, sort: Any = None, **kwargs: Any) -> dict[str, Any] | None:
        cursor = self.find(query, projection)
        if sort:
            cursor.sort(sort)
        rows = await cursor.to_list(1)
        return rows[0] if rows else None

    async def insert_one(self, doc: dict[str, Any]) -> PostgresResult:
        global _last_error
        clean = normalize_json(doc)
        key = doc_key(self.name, clean)
        if not await init_schema() or _pool is None:
            raise RuntimeError(_last_error or "Postgres insert failed")
        try:
            async with _pool.acquire() as conn:
                result = await conn.execute(
                    """
                    insert into cc_collection_snapshots(collection, doc_key, payload, updated_at)
                    values($1, $2, $3::jsonb, now())
                    on conflict(collection, doc_key) do nothing
                    """,
                    self.name, key, json.dumps(clean, default=_json_default),
                )
            if not result.endswith("1"):
                return PostgresResult(inserted_id=None, duplicate=True)
            await write_event(self.name, clean, natural_key=key, event_type="insert")
            return PostgresResult(inserted_id=clean.get("_id", key), duplicate=False)
        except Exception as exc:
            _last_error = str(exc)[:500]
            raise RuntimeError(_last_error) from exc

    async def insert_many(self, docs: Iterable[dict[str, Any]]) -> PostgresResult:
        ids = []
        for doc in docs:
            result = await self.insert_one(doc)
            ids.append(result.inserted_id)
        return PostgresResult(inserted_ids=ids)

    async def update_one(self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False, **kwargs: Any) -> PostgresResult:
        existing = await self.find_one(query)
        inserted = False
        if existing is None:
            if not upsert:
                return PostgresResult(matched_count=0, modified_count=0, upserted_id=None)
            existing = {k: v for k, v in query.items() if not k.startswith("$") and not isinstance(v, dict)}
            inserted = True
        original = dict(existing)
        for op, values in update.items():
            if op == "$set" or (op == "$setOnInsert" and inserted):
                for key, value in values.items(): _path_set(existing, key, normalize_json(value))
            elif op == "$unset":
                for key in values: _path_delete(existing, key)
            elif op == "$inc":
                for key, value in values.items(): _path_set(existing, key, (_path_get(existing, key, 0) or 0) + value)
            elif op in {"$max", "$min"}:
                for key, value in values.items():
                    old = _path_get(existing, key)
                    if old is None or (op == "$max" and value > old) or (op == "$min" and value < old): _path_set(existing, key, value)
            elif op in {"$push", "$addToSet"}:
                for key, value in values.items():
                    arr = list(_path_get(existing, key, []) or [])
                    additions = value.get("$each", []) if isinstance(value, dict) and "$each" in value else [value]
                    for item in additions:
                        if op == "$push" or item not in arr: arr.append(item)
                    _path_set(existing, key, arr)
        key = doc_key(self.name, existing)
        if not await upsert_snapshot(self.name, key, existing):
            raise RuntimeError(_last_error or "Postgres update failed")
        return PostgresResult(matched_count=0 if inserted else 1, modified_count=1 if existing != original else 0, upserted_id=key if inserted else None)

    async def update_many(self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False, **kwargs: Any) -> PostgresResult:
        rows = await self._read(query)
        if not rows and upsert:
            return await self.update_one(query, update, upsert=True)
        modified = 0
        for row in rows:
            result = await self.update_one({"_id": row.get("_id")} if row.get("_id") else {"_key": doc_key(self.name, row)}, update)
            modified += result.modified_count
        return PostgresResult(matched_count=len(rows), modified_count=modified)

    async def find_one_and_update(self, query: dict[str, Any], update: dict[str, Any], *, upsert: bool = False, return_document: Any = None, projection: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any] | None:
        before = await self.find_one(query, projection)
        await self.update_one(query, update, upsert=upsert)
        if return_document or before is None:
            return await self.find_one(query, projection)
        return before

    async def find_one_and_delete(self, query: dict[str, Any], projection: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any] | None:
        row = await self.find_one(query, projection)
        if row is not None:
            await self.delete_one(query)
        return row

    async def delete_one(self, query: dict[str, Any], **kwargs: Any) -> PostgresResult:
        rows = await self._read(query)
        if not rows: return PostgresResult(deleted_count=0)
        return await self._delete_doc(rows[0])

    async def delete_many(self, query: dict[str, Any], **kwargs: Any) -> PostgresResult:
        rows = await self._read(query)
        deleted = 0
        for row in rows: deleted += (await self._delete_doc(row)).deleted_count
        return PostgresResult(deleted_count=deleted)

    async def _delete_doc(self, doc: dict[str, Any]) -> PostgresResult:
        if _pool is None or not await init_schema(): raise RuntimeError(_last_error or "Postgres unavailable")
        async with _pool.acquire() as conn:
            result = await conn.execute("delete from cc_collection_snapshots where collection=$1 and doc_key=$2", self.name, doc_key(self.name, doc))
        return PostgresResult(deleted_count=1 if result.endswith("1") else 0)

    async def count_documents(self, query: dict[str, Any] | None = None, **kwargs: Any) -> int:
        return len(await self._read(query))

    async def distinct(self, key: str, query: dict[str, Any] | None = None, **kwargs: Any) -> list[Any]:
        values = {_path_get(row, key) for row in await self._read(query)}
        return list(values)

    async def create_index(self, *args: Any, **kwargs: Any) -> str:
        return "postgres_jsonb_compat"

    def aggregate(self, pipeline: list[dict[str, Any]], **kwargs: Any) -> PostgresCursor:
        query: dict[str, Any] = {}
        cursor = PostgresCursor(self, query, None)
        for stage in pipeline:
            if "$match" in stage: query.update(stage["$match"]); cursor.query = query
            elif "$sort" in stage: cursor.sort(list(stage["$sort"].items()))
            elif "$skip" in stage: cursor.skip(stage["$skip"])
            elif "$limit" in stage: cursor.limit(stage["$limit"])
        return cursor


class PostgresDatabase:
    def __getitem__(self, name: str) -> PostgresCollection:
        return PostgresCollection(name)

    def __getattr__(self, name: str) -> PostgresCollection:
        return PostgresCollection(name)


_database = PostgresDatabase()


def get_database() -> PostgresDatabase:
    return _database


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
    "options_desk_candidate_history",
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
    return os.environ.get("POSTGRES_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


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
    """Stable best-effort natural key for collection-style documents."""
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
    global _pool, _last_error, _schema_ready
    if not enabled():
        return False
    if _pool is not None:
        # asyncpg pools are bound to the event loop that created them. Test
        # runners and short-lived maintenance jobs may create a new loop;
        # never hand an old-loop pool to it.
        try:
            current_loop = asyncio.get_running_loop()
            pool_loop = getattr(_pool, "_loop", None)
            if pool_loop is current_loop and not current_loop.is_closed():
                return True
            if pool_loop is not None and not pool_loop.is_closed() and pool_loop is not current_loop:
                _last_error = "Postgres pool belongs to another event loop"
                return False
            _pool = None
            _schema_ready = False
        except RuntimeError:
            return False
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


async def mirror_document(collection: str, doc: dict[str, Any], *, source: str = "postgres-write") -> bool:
    return await upsert_snapshot(collection, doc_key(collection, doc), doc, source=source)


async def upsert_snapshots_bulk(collection: str, docs: list[dict[str, Any]], *, source: str = "postgres-migration") -> int:
    """Write a bounded migration batch without retaining the whole collection."""
    if not docs or not await init_schema() or _pool is None:
        return 0
    rows = [(collection, doc_key(collection, normalize_json(doc)), json.dumps(normalize_json(doc), default=_json_default)) for doc in docs]
    async with _pool.acquire() as conn:
        await conn.executemany(
            """
            insert into cc_collection_snapshots(collection, doc_key, payload, updated_at)
            values($1, $2, $3::jsonb, now())
            on conflict(collection, doc_key) do update
            set payload = excluded.payload, updated_at = excluded.updated_at
            """,
            rows,
        )
    return len(rows)


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
