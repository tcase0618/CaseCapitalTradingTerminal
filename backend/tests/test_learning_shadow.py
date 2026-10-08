"""Offline, full-cycle regression tests for the active-weight write boundary."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import pytest

from services import db as db_module
from services import learning_engine as engine


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, limit):
        return deepcopy(self.rows[:limit])


class Collection:
    def __init__(self, rows=(), *, read_only=False, append_only=False):
        self.rows = deepcopy(list(rows))
        self.read_only = read_only
        self.append_only = append_only
        self.writes = []

    def find(self, query, projection):
        rows = self.rows
        for key, value in query.items():
            if isinstance(value, dict):
                assert value == {"$ne": None}
                rows = [row for row in rows if row.get(key) is not None]
            else:
                rows = [row for row in rows if row.get(key) == value]
        return Cursor([{k: v for k, v in row.items() if k != "_id"} for row in rows])

    async def count_documents(self, query):
        assert query == {}
        return len(self.rows)

    async def insert_one(self, doc):
        assert not self.read_only, "unexpected write to active state"
        self.writes.append(("insert", deepcopy(doc)))
        self.rows.append(deepcopy(doc))

    async def insert_many(self, docs):
        for doc in docs:
            await self.insert_one(doc)

    async def update_one(self, query, update, upsert=False):
        assert not self.read_only, "unexpected write to active state"
        assert not self.append_only, "proposal reports must be append-only"
        assert set(update) == {"$set"}
        self.writes.append(("update", deepcopy(update)))
        row = next((r for r in self.rows if all(r.get(k) == v for k, v in query.items())), None)
        if row is not None:
            row.update(deepcopy(update["$set"]))
            return SimpleNamespace(modified_count=1, upserted_id=None)
        assert upsert
        self.rows.append({**query, **deepcopy(update["$set"])})
        return SimpleNamespace(modified_count=0, upserted_id="fake-id")


def encoded(rows):
    return json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")


def active_weights():
    rows = []
    for key in ("insider_cluster_buy", "CALL_SWEEP", "DAY2_CONTINUATION"):
        cfg = engine.DEFAULT_WEIGHTS[key]
        rows.append({
            "_id": key, "weight_key": key, "current_value": cfg["value"] + 0.12345,
            "default_value": cfg["value"], "min_value": cfg["min"], "max_value": cfg["max"],
            "sample_count": 7, "win_rate": 0.57, "avg_return": 1.2,
            "confidence": 0.14, "last_updated": "unchanged", "operator_note": "preserve",
        })
    return rows


@pytest.fixture
def fake_db(monkeypatch):
    db = SimpleNamespace(
        learning_weights=Collection(active_weights(), read_only=True),
        learning_weight_history=Collection([{"weight_key": "CALL_SWEEP", "ts": "old"}], read_only=True),
        signal_first_seen=Collection(), signal_performance=Collection(),
        combo_stats=Collection(), learning_runs=Collection(append_only=True),
        activity_log=Collection(append_only=True),
    )
    monkeypatch.setattr(engine, "get_db", lambda: db)
    monkeypatch.setattr(db_module, "get_db", lambda: db)

    async def closes(tickers):
        # PENDING is intentionally unpriced; it must not become a loss or a sample.
        return {ticker: (90.0 if ticker.startswith("LOSS") else 110.0)
                for ticker in tickers if ticker != "PENDING"}

    monkeypatch.setattr(engine.pricer, "batch_latest_closes", closes)
    return db


def add_samples(db, count, *, matured=False, live=True):
    date = datetime.now(timezone.utc).date().isoformat()
    for i in range(count):
        for prefix, signal, ret in (("WIN", "insider_cluster_buy", 10.0), ("LOSS", "CALL_SWEEP", -10.0)):
            ticker = f"{prefix}{i}"
            signals = [signal, "UNTRACKED_COMBO_SIGNAL"]
            if live:
                db.signal_first_seen.rows.append({
                    "ticker": ticker, "first_seen_price": 100.0, "first_seen_date": date,
                    "first_signals": signals, "first_strategy_lanes": ["DAY2_CONTINUATION"],
                })
            if matured:
                db.signal_performance.rows.append({
                    "ticker": ticker, "date": date, "signals": signals,
                    "strategy_lanes": ["DAY2_CONTINUATION"], "return_30d": ret,
                })
    db.signal_first_seen.rows.extend([
        {"ticker": "PENDING", "first_seen_price": 100.0, "first_seen_date": date,
         "first_signals": ["CALL_SWEEP"]},
        {"ticker": "INVALID", "first_seen_price": 0, "first_seen_date": date},
        {"ticker": "BAD_DATE", "first_seen_price": 100.0, "first_seen_date": "invalid"},
    ])
    db.signal_performance.rows.extend([
        {"ticker": "PENDING", "date": date, "signals": ["CALL_SWEEP"], "return_30d": None},
        {"ticker": "UNRESOLVED", "date": date, "signals": ["CALL_SWEEP"]},
    ])


@pytest.mark.parametrize("count,matured", [(10, False), (50, False), (10, True), (1000, True)])
def test_full_cycle_only_appends_proposals_at_every_sample_size(fake_db, count, matured):
    add_samples(fake_db, count, matured=matured)
    before = encoded(fake_db.learning_weights.rows)
    history_before = encoded(fake_db.learning_weight_history.rows)
    defaults_before = encoded(engine.DEFAULT_WEIGHTS)
    scanner_before = asyncio.run(engine.get_weights())

    result = asyncio.run(engine.run_learning_cycle())
    report = fake_db.learning_runs.rows[-1]
    assert result["mode"] == report["mode"] == "SHADOW"
    assert result["applied"] is report["applied"] is False
    assert result["changes"] == 0
    assert result["proposed_change_count"] == 2
    assert report["weights_changed"] == {}
    assert report["trades_live"] == count * 2
    assert report["trades_30d"] == (count * 2 if matured else 0)
    assert result["trades"] == count * 2
    assert report["proposed_changes"]["insider_cluster_buy"]["delta"] > 0
    assert report["proposed_changes"]["CALL_SWEEP"]["delta"] < 0
    assert report["proposed_changes"]["CALL_SWEEP"]["sample_count"] == count
    assert report["proposed_changes"]["CALL_SWEEP"]["basis"] == ("30d" if matured else "live")
    assert report["signal_stats"]["DAY2_CONTINUATION"]["count"] == count * 2
    assert report["weights_snapshot"] == {r["weight_key"]: r["current_value"] for r in fake_db.learning_weights.rows}
    assert report["proposed_weights_snapshot"] != report["weights_snapshot"]
    assert all("weight reduced" not in insight for insight in report["insights"])
    assert fake_db.combo_stats.writes  # The reporting refresh still runs.

    first_report = encoded(fake_db.learning_runs.rows)
    asyncio.run(engine.run_learning_cycle())
    assert len(fake_db.learning_runs.rows) == 2
    assert encoded(fake_db.learning_runs.rows[:1]) == first_report
    assert fake_db.learning_runs.rows[1]["proposed_changes"] == report["proposed_changes"]
    assert encoded(fake_db.learning_weights.rows) == before
    assert encoded(fake_db.learning_weight_history.rows) == history_before
    assert encoded(engine.DEFAULT_WEIGHTS) == defaults_before
    assert asyncio.run(engine.get_weights()) == scanner_before
    assert not fake_db.learning_weights.writes
    assert not fake_db.learning_weight_history.writes


def test_matured_observations_can_be_analyzed_without_live_marks(fake_db):
    add_samples(fake_db, 10, matured=True, live=False)
    result = asyncio.run(engine.run_learning_cycle())
    assert result["proposed_change_count"] == 2
    assert result["applied"] is False
    assert fake_db.learning_runs.rows[-1]["trades_live"] == 0


def test_empty_active_collection_is_not_implicitly_seeded(fake_db):
    fake_db.learning_weights.rows.clear()
    add_samples(fake_db, 10)
    result = asyncio.run(engine.run_learning_cycle())
    assert result["proposed_change_count"] == 0
    assert fake_db.learning_weights.rows == []
    assert not fake_db.learning_weights.writes
    assert fake_db.learning_runs.rows[-1]["weights_snapshot"] == {}
    assert asyncio.run(engine.get_weights()) == {k: cfg["value"] for k, cfg in engine.DEFAULT_WEIGHTS.items()}


@pytest.mark.parametrize("count", [0, 4])
def test_small_or_pending_only_samples_append_skipped_shadow_report(fake_db, count):
    add_samples(fake_db, count)
    before = encoded(fake_db.learning_weights.rows)
    result = asyncio.run(engine.run_learning_cycle())
    assert result["skipped"] is True
    assert result["mode"] == "SHADOW"
    assert result["applied"] is False
    assert result["changes"] == result["proposed_change_count"] == 0
    assert result["trades"] == count * 2
    assert len(fake_db.learning_runs.rows) == 1
    assert fake_db.learning_runs.rows[0]["weights_changed"] == {}
    assert encoded(fake_db.learning_weights.rows) == before
    assert not fake_db.combo_stats.writes


def test_manual_reset_remains_an_explicit_active_write(fake_db):
    fake_db.learning_weights.read_only = False
    result = asyncio.run(engine.reset_weights())
    assert result == len(engine.DEFAULT_WEIGHTS)
    assert asyncio.run(engine.get_weights()) == {k: cfg["value"] for k, cfg in engine.DEFAULT_WEIGHTS.items()}
    assert fake_db.learning_weights.writes
    assert not fake_db.learning_runs.rows
