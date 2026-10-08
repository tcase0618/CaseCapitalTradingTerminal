"""Real PostgreSQL contract tests use a connection-local TEMP table only.

Set POSTGRES_TEST_DSN explicitly. No broker clients or permanent schema writes.
"""
import json
import os
from contextlib import asynccontextmanager

import pytest
from services import postgres_store as store


DOCS = [
    {"id": "a", "status": "OPEN", "broker": {"account": "public"}, "qty": 2, "ts": "2026-10-01T10:00:00Z"},
    {"id": "b", "status": ["OPEN", "WATCH"], "qty": 0, "ts": "2026-10-02T10:00:00Z", "nullable": None},
    {"id": "c", "status": "CLOSED", "qty": True, "ts": "2026-10-03T10:00:00Z", "nullable": [None]},
    {"id": "d", "qty": "2", "nullable": "value"},
    {"id": "e", "qty": 1.5, "status": "Ω", "nullable": []},
]
QUERIES = [
    {}, {"status": "OPEN"}, {"status": {"$in": ["OPEN", "WATCH"]}},
    {"status": {"$nin": ["OPEN"]}}, {"status": {"$ne": "OPEN"}},
    {"nullable": None}, {"nullable": {"$ne": None}},
    {"nullable": {"$exists": True}}, {"nullable": {"$exists": False}},
    {"broker.account": "public"}, {"qty": {"$gt": 0}}, {"qty": 1},
    {"qty": {"$in": [1, "2"]}}, {"qty": {"$nin": [True]}},
    {"ts": {"$gte": "2026-10-02", "$lt": "2026-10-04"}},
    {"$or": [{"qty": 2}, {"status": "CLOSED"}]},
    {"$and": [{"status": "OPEN"}, {"qty": {"$gt": 0}}]},
    {"status": {"$regex": "OP"}}, {"status": {"$in": []}},
    {"status": "'; DROP TABLE cc_events; --"},
]


@pytest.mark.asyncio
async def test_real_postgres_reads_match_reference_and_release_pool(monkeypatch):
    dsn = os.environ.get("POSTGRES_TEST_DSN")
    if not dsn:
        pytest.skip("explicit POSTGRES_TEST_DSN required; TEMP-table contract test")
    asyncpg = pytest.importorskip("asyncpg")
    connection = await asyncpg.connect(dsn)
    class Pool:
        held = False
        @asynccontextmanager
        async def acquire(self):
            assert not self.held, "iteration must release connection before consumer writes"
            self.held = True
            try:
                yield connection
            finally:
                self.held = False
    pool = Pool()
    try:
        await connection.execute("create temp table cc_collection_snapshots(collection text, doc_key text, payload jsonb, primary key(collection,doc_key))")
        await connection.executemany("insert into pg_temp.cc_collection_snapshots values('test',$1,$2::jsonb)", [(d["id"], json.dumps(d)) for d in DOCS])
        monkeypatch.setattr(store, "_pool", pool)
        monkeypatch.setattr(store, "_schema_ready", True)
        collection = store.PostgresCollection("test")
        for query in QUERIES:
            expected = [d for d in DOCS if store._matches(d, query)]
            actual = await collection.find(query).to_list(None)
            assert sorted(d["id"] for d in actual) == sorted(d["id"] for d in expected), query
            assert await collection.count_documents(query) == len(expected), query
            page = await collection.find(query).skip(1).to_list(2)
            assert len(page) == min(2, max(0, len(expected) - 1)), query
        ordered = await collection.find({"ts": {"$exists": True}}).sort("ts", -1).skip(1).to_list(1)
        assert ordered[0]["id"] == "b"
        async for doc in collection.find({"qty": {"$gt": 0}}):
            assert not pool.held
            async with pool.acquire():
                assert await connection.fetchval("select 1") == 1
        # Cross the batch boundary and prove no duplicates or omitted records.
        await connection.executemany("insert into pg_temp.cc_collection_snapshots values('bulk',$1,$2::jsonb)", [(f"{i:04}", json.dumps({"id": i})) for i in range(101)])
        assert len(await store.PostgresCollection("bulk").find({}).to_list(None)) == 101
        grouped = await store.PostgresCollection("bulk").aggregate([
            {"$group": {"_id": "all", "n": {"$sum": 1}, "low": {"$min": "$id"}, "high": {"$max": "$id"}}},
        ]).to_list(None)
        assert grouped == [{"_id": "all", "n": 101, "low": 0, "high": 100}]
    finally:
        await connection.close()
