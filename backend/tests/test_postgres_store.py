from datetime import datetime, timezone

import pytest

from services import postgres_store


def test_postgres_disabled_without_env(monkeypatch):
    monkeypatch.setenv("POSTGRES_ENABLED", "false")
    monkeypatch.delenv("POSTGRES_DSN", raising=False)
    assert postgres_store.enabled() is False


def test_postgres_doc_key_prefers_business_keys():
    assert postgres_store.doc_key("scan_results", {"finished_at": "2026-08-21T12:00:00Z"}) == "2026-08-21T12:00:00Z"
    assert postgres_store.doc_key("pharma_pdufa", {"ticker": "MRNA", "pdufa_date": "2026-09-01"}) == "MRNA:2026-09-01"
    assert postgres_store.doc_key("options_desk_orders", {"order_id": "abc"}) == "abc"
    assert postgres_store.doc_key("pm_company_observations", {"observation_id": "decision-1:AI"}) == "decision-1:AI"
    assert postgres_store.doc_key("options_performance", {"observation_id": "cycle-1:ABC:contract"}) == "cycle-1:ABC:contract"
    assert postgres_store.doc_key("signal_performance", {"ticker": "AAPL", "date": "2026-09-16", "screener_id": "lottery_gap"}) == "AAPL:2026-09-16:lottery_gap"


def test_postgres_matching_handles_missing_fields_ne_and_exists():
    assert postgres_store._matches({"status": "OPEN"}, {"status": {"$ne": "CLOSED"}})
    assert postgres_store._matches({"value": None}, {"value": {"$exists": True}})
    assert not postgres_store._matches({}, {"value": {"$exists": True}})
    assert postgres_store._matches({}, {"value": {"$exists": False}})


def test_postgres_projection_preserves_nested_document_shape():
    assert postgres_store._project({"judge": {"advisory_posture": "COURT_SUPPORTS_PM"}}, {"judge.advisory_posture": 1, "_id": 0}) == {
        "judge": {"advisory_posture": "COURT_SUPPORTS_PM"}
    }


@pytest.mark.asyncio
async def test_postgres_aggregate_supports_first_last_sum_and_max():
    class Collection:
        async def _read(self, _query):
            return [
                {"ticker": "AAPL", "date": "2026-09-14", "price": 10},
                {"ticker": "AAPL", "date": "2026-09-15", "price": 12},
                {"ticker": "MSFT", "date": "2026-09-15", "price": 8},
            ], False

    cursor = postgres_store.PostgresAggregateCursor(Collection(), [
        {"$sort": {"ticker": 1, "date": 1}},
        {"$group": {"_id": "$ticker", "first": {"$first": "$price"}, "last": {"$last": "$price"}, "n": {"$sum": 1}, "high": {"$max": "$price"}}},
    ])
    rows = await cursor.to_list(None)
    assert rows == [
        {"_id": "AAPL", "first": 10, "last": 12, "n": 2, "high": 12},
        {"_id": "MSFT", "first": 8, "last": 8, "n": 1, "high": 8},
    ]


def test_postgres_json_normalization_handles_dates_and_unknown_objects():
    class Odd:
        def __str__(self):
            return "odd-object"

    payload = postgres_store.normalize_json({
        "ts": datetime(2026, 8, 21, tzinfo=timezone.utc),
        "odd": Odd(),
    })
    assert payload["ts"] == "2026-08-21T00:00:00+00:00"
    assert payload["odd"] == "odd-object"


def test_postgres_json_normalization_nulls_nonfinite_numbers():
    payload = postgres_store.normalize_json({"bad": float("nan"), "nested": [float("inf"), -float("inf"), 1.5]})
    assert payload == {"bad": None, "nested": [None, None, 1.5]}


def test_postgres_update_events_skip_snapshot_telemetry_and_trade_poll_noise():
    assert not postgres_store._record_update_event("bot_state", {"status": "old"}, {"status": "new"})
    before = {"status": "OPEN", "pm_last_ratchet_check": "2026-10-05T10:00:00Z"}
    after = {"status": "OPEN", "pm_last_ratchet_check": "2026-10-05T10:01:00Z"}
    assert not postgres_store._record_update_event("tf_trades", before, after)
    assert postgres_store._record_update_event("tf_trades", before, {**after, "current_stop": 9.5})


def test_postgres_cursor_allow_disk_use_is_chainable():
    collection = postgres_store.PostgresCollection("scan_results")
    cursor = collection.find({}, {"_id": 0}).sort("finished_at", -1).allow_disk_use(True).limit(10)
    assert isinstance(cursor, postgres_store.PostgresCursor)
    assert cursor._limit == 10


@pytest.mark.asyncio
async def test_postgres_status_disabled(monkeypatch):
    monkeypatch.setenv("POSTGRES_ENABLED", "false")
    status = await postgres_store.status()
    assert status["enabled"] is False
    assert status["ready"] is False


@pytest.mark.asyncio
async def test_postgres_cursor_uses_sql_window_for_latest_dashboard_reads(monkeypatch):
    class Connection:
        def __init__(self):
            self.sql = ""
            self.params = ()

        async def fetch(self, sql, *params):
            self.sql, self.params = sql, params
            return [{"payload": {"_id": "latest", "generated_at": "2026-09-14T12:00:00Z"}}]

    class Acquire:
        def __init__(self, connection):
            self.connection = connection

        async def __aenter__(self):
            return self.connection

        async def __aexit__(self, *_args):
            return None

    class Pool:
        def __init__(self, connection):
            self.connection = connection

        def acquire(self):
            return Acquire(self.connection)

    connection = Connection()
    monkeypatch.setattr(postgres_store, "_pool", Pool(connection))
    monkeypatch.setattr(postgres_store, "init_schema", lambda: _schema_ready())
    rows = await postgres_store.PostgresCollection("candidate_ledgers").find({"_id": "latest"}).sort("generated_at", -1).skip(0).to_list(1)
    assert rows == [{"_id": "latest", "generated_at": "2026-09-14T12:00:00Z"}]
    assert "payload #>" in connection.sql
    assert "order by payload #>>" in connection.sql
    assert connection.params[-2:] == (0, 1)


async def _schema_ready():
    return True


def test_sql_filter_never_negates_an_inexact_predicate():
    params = ["test"]
    sql, exact = postgres_store._sql_filter({"qty": {"$ne": 1}, "status": "OPEN"}, params)
    assert not exact
    assert "not coalesce(true" not in sql
    assert "OPEN" not in sql  # Values are bound, including suspicious input.
    sql, exact = postgres_store._sql_filter({"$or": [{"qty": 1}, {"status": "OPEN"}]}, ["test"])
    assert not exact and "true" in sql


@pytest.mark.asyncio
async def test_fallback_window_applies_after_matching_without_loading_collection(monkeypatch):
    collection = postgres_store.PostgresCollection("test")
    examined = []
    async def stream(query):
        for index in range(10000):
            examined.append(index)
            if index % 2:
                yield {"qty": index}
    monkeypatch.setattr(postgres_store, "_pool", object())
    monkeypatch.setattr(postgres_store, "init_schema", _schema_ready)
    monkeypatch.setattr(collection, "_iter_read", stream)
    result = await collection.find({"qty": {"$gt": 0}}).skip(1).to_list(2)
    assert result == [{"qty": 3}, {"qty": 5}]
    assert len(examined) == 6


@pytest.mark.asyncio
async def test_aggregate_pushes_leading_match_before_materialization():
    class Collection:
        async def _read(self, query):
            assert query == {"strategy": "LOTTERY"}
            return [{"strategy": "LOTTERY", "qty": 2}], False
    result = await postgres_store.PostgresAggregateCursor(Collection(), [
        {"$match": {"strategy": "LOTTERY"}},
        {"$group": {"_id": "$strategy", "n": {"$sum": 1}}},
    ]).to_list(None)
    assert result == [{"_id": "LOTTERY", "n": 1}]


@pytest.mark.asyncio
async def test_group_first_aggregation_streams_without_retaining_input():
    class Collection:
        async def _iter_read(self, query):
            assert query == {"family": "LOTTERY"}
            for index in range(1000):
                yield {"ticker": "ABC", "value": index, "signals": "RVOL"}
        async def _read(self, query):
            raise AssertionError("must not materialize input collection")
    rows = await postgres_store.PostgresAggregateCursor(Collection(), [
        {"$match": {"family": "LOTTERY"}},
        {"$group": {"_id": "$ticker", "n": {"$sum": 1}, "first": {"$first": "$value"}, "last": {"$last": "$value"}, "lo": {"$min": "$value"}, "hi": {"$max": "$value"}, "signals": {"$addToSet": "$signals"}}},
    ]).to_list(None)
    assert rows == [{"_id": "ABC", "n": 1000, "first": 0, "last": 999, "lo": 0, "hi": 999, "signals": ["RVOL"]}]
