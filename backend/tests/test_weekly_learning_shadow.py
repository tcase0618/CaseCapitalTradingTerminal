"""Full offline weekly-cycle tests: active config writes are forbidden."""
import asyncio
from copy import deepcopy
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import db as db_module
from services import lottery_grader as lottery
from services import trade_floor_learning as floor


def matches(row, query):
    for key, expected in query.items():
        if key == "$and":
            if not all(matches(row, clause) for clause in expected):
                return False
        elif key == "$or":
            if not any(matches(row, clause) for clause in expected):
                return False
        elif isinstance(expected, dict):
            for op, value in expected.items():
                if op == "$ne" and row.get(key) == value:
                    return False
                if op == "$exists" and (key in row) != value:
                    return False
                assert op in {"$ne", "$exists"}
        elif row.get(key) != expected:
            return False
    return True


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, key, direction):
        self.rows = sorted(self.rows, key=lambda row: row.get(key, ""), reverse=direction < 0)
        return self

    async def to_list(self, limit):
        return self.rows[:limit]


class Collection:
    def __init__(self, rows=(), *, active=False, append_only=False):
        self.rows = deepcopy(list(rows))
        self.active = active
        self.append_only = append_only
        self.writes = []

    def find(self, query, projection=None):
        # Return nested references deliberately: tests catch in-memory mutations too.
        return Cursor([{k: v for k, v in row.items() if not projection or projection.get(k, 1)}
                       for row in self.rows if matches(row, query)])

    async def find_one(self, query, projection=None, sort=None):
        cursor = self.find(query, projection)
        if sort:
            cursor.sort(*sort[0])
        rows = await cursor.to_list(1)
        return rows[0] if rows else None

    async def count_documents(self, query):
        return sum(matches(row, query) for row in self.rows)

    async def insert_one(self, doc):
        assert not self.active, "active config insertion forbidden"
        self.rows.append(deepcopy(doc))
        self.writes.append("insert")

    async def update_one(self, query, update, upsert=False):
        assert not self.append_only, "reports must be append-only"
        row = next((r for r in self.rows if matches(r, query)), None)
        if row is not None and "$setOnInsert" in update:
            return SimpleNamespace(modified_count=0, upserted_id=None)
        assert not self.active, "active config update forbidden"
        if row is None:
            assert upsert
            row = dict(query)
            self.rows.append(row)
        row.update(deepcopy(update.get("$set", update.get("$setOnInsert", {}))))
        self.writes.append("update")
        return SimpleNamespace(modified_count=1, upserted_id=None)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


ACTIVE = ("tf_weights", "tf_risk_tiers", "tf_stop_engine", "tf_entry_engine", "tf_phase_engine", "ll_learned_config")


@pytest.fixture
def db(monkeypatch):
    current = lambda **fields: [{"_id": "current", "updated_at": "preserve", **fields}]
    db = SimpleNamespace(
        tf_weights=Collection(current(weights={"SIGNAL": 0.75}, adjusted_combos=["old"]), active=True),
        tf_risk_tiers=Collection(current(tiers={"equity": {"20-24": 0.04}}), active=True),
        tf_stop_engine=Collection(current(coefficients={"sector_delta": {"tech": 0.01}, "score_tier_delta": {"20-24": 0.01}}), active=True),
        tf_entry_engine=Collection(current(offsets_by_combo={"SIGNAL": {"20-24": {"offset_bps": 5}}}), active=True),
        tf_phase_engine=Collection(current(params_by_combo={"SIGNAL": {"phase2_multiplier": 1.4, "trail_pct_normal": 0.4}}), active=True),
        ll_learned_config=Collection(current(version="operator", min_ticket_score=65, retired_variants=[], preferred_segments=[], penalized_segments=[], ladder_bias="operator", status="OPERATOR", custom="preserve"), active=True),
        tf_trades=Collection(), tf_phase_outcomes=Collection(), tf_combo_stats=Collection(),
        tf_recalibration_log=Collection(append_only=True), ll_tickets=Collection(),
        ll_grades=Collection(), ll_learning_runs=Collection(append_only=True),
        activity_log=Collection(append_only=True),
    )
    for module in (floor, lottery, db_module):
        monkeypatch.setattr(module, "get_db", lambda: db)
    monkeypatch.setenv("APCA_API_BASE_URL", "https://paper-api.alpaca.markets")
    return db


def active_bytes(db):
    return encoded({name: getattr(db, name).rows for name in ACTIVE})


def floor_samples(db, count):
    for i in range(count):
        db.tf_trades.rows.append({
            "ticker": f"TEST{i}", "status": "CLOSED", "signal_combo": ["SIGNAL"],
            "realized_pct": -20, "limit_price": 100, "trade_score": 22,
            "fill_status": "FILLED", "fill_seconds": 5,
            "stop_breakdown": {"sector": "tech", "score_tier": "20-24"},
            "entry_price_ref": 100, "lowest_price_reached": 80, "stop_pct": 0.05,
        })
        db.tf_phase_outcomes.rows.append({
            "signal_combo": ["SIGNAL"], "phase2_hit": True,
            "final_realized_pct": 30, "peak_gain_pct": 100,
            "phase3": {"realized_pct_on_slice": 20},
        })
    db.tf_trades.rows.extend([
        {"status": "OPEN", "signal_combo": ["SIGNAL"], "realized_pct": 1000},
        {"status": "CLOSED", "learning_excluded": True, "signal_combo": ["SIGNAL"], "realized_pct": 1000},
        {"status": "CLOSED", "broker_base": "https://api.alpaca.markets", "signal_combo": ["SIGNAL"], "realized_pct": 1000},
    ])


@pytest.mark.parametrize("count", [0, 4, 5, 30, 300])
def test_floor_weekly_cycle_never_mutates_any_active_subsystem(db, monkeypatch, count):
    floor_samples(db, count)
    before = active_bytes(db)
    # Tripwires ensure no legacy mutation helper is invoked, even if reintroduced.
    for name in ("_recalibrate_stop_engine", "_recalibrate_entry_price", "_recalibrate_phase_engine", "initialize_from_signal_engine"):
        monkeypatch.setattr(floor, name, AsyncMock(side_effect=AssertionError("live helper called")), raising=False)
    result = asyncio.run(floor.recalibrate())
    report = db.tf_recalibration_log.rows[-1]
    assert result["mode"] == report["mode"] == "SHADOW"
    assert result["applied"] is report["applied"] is False
    assert result["changes"] == 0
    assert report["changes"] == []
    assert report["closed_trades"] == count
    if count >= 5:
        assert result["proposed_change_count"] > 0
        assert result["proposal_errors"] == {}
        assert report["proposals"]["weights"]["active"] == {"SIGNAL": 0.75}
    if count >= 30:
        assert set(report["proposals"]) == {"weights", "risk_tiers", "stop_engine", "entry_price", "phase_engine"}
        assert all(p["active"] != p["proposed"] for p in report["proposals"].values())
    first = encoded(db.tf_recalibration_log.rows)
    asyncio.run(floor.recalibrate())
    assert len(db.tf_recalibration_log.rows) == 2
    assert encoded(db.tf_recalibration_log.rows[:1]) == first
    assert active_bytes(db) == before
    assert all(not getattr(db, name).writes for name in ACTIVE)
    for name in ("_recalibrate_stop_engine", "_recalibrate_entry_price", "_recalibrate_phase_engine", "initialize_from_signal_engine"):
        getattr(floor, name).assert_not_called()


@pytest.mark.parametrize("legacy", [False, True])
def test_initialize_preserves_existing_current_or_legacy_weights(db, monkeypatch, legacy):
    if legacy:
        db.tf_weights.rows[0]["_id"] = "legacy-active"
    before = active_bytes(db)
    from services import learning_engine
    get_weights = AsyncMock(side_effect=AssertionError("must not re-inherit active weights"))
    monkeypatch.setattr(learning_engine, "get_weights", get_weights)
    assert asyncio.run(floor.initialize_from_signal_engine()) is False
    assert active_bytes(db) == before
    get_weights.assert_not_called()


def test_initialize_empty_weights_only_seeds_once_and_preserves_existing_risk(db, monkeypatch):
    from services import learning_engine
    db.tf_weights.rows.clear()
    db.tf_weights.active = False
    risk_before = encoded(db.tf_risk_tiers.rows)
    monkeypatch.setattr(learning_engine, "get_weights", AsyncMock(return_value={"INHERITED": 0.8}))
    monkeypatch.setitem(sys.modules, "services.trade_floor", SimpleNamespace(DEFAULT_RISK_TIERS={"equity": {(20, 24): 0.02}}))
    assert asyncio.run(floor.initialize_from_signal_engine()) is True
    weights_before = encoded(db.tf_weights.rows)
    assert asyncio.run(floor.initialize_from_signal_engine()) is False
    assert encoded(db.tf_weights.rows) == weights_before
    assert encoded(db.tf_risk_tiers.rows) == risk_before


def test_floor_missing_active_configs_are_not_initialized_by_weekly_cycle(db):
    for name in ACTIVE:
        getattr(db, name).rows.clear()
    floor_samples(db, 30)
    before = active_bytes(db)
    result = asyncio.run(floor.recalibrate())
    assert result["mode"] == "SHADOW"
    assert result["applied"] is False
    assert result["proposal_errors"] == {}
    assert result["proposals"]["weights"]["active"] == {}
    assert active_bytes(db) == before
    assert all(not getattr(db, name).writes for name in ACTIVE)


def test_proposal_failure_is_reported_without_fallback_to_live_helpers(db, monkeypatch):
    floor_samples(db, 30)
    before = active_bytes(db)
    monkeypatch.setattr(floor, "_propose_stop_engine", AsyncMock(side_effect=ValueError("invalid coefficient input")))
    result = asyncio.run(floor.recalibrate())
    assert result["proposal_errors"] == {"stop_engine": "invalid coefficient input"}
    assert db.tf_recalibration_log.rows[-1]["proposal_errors"] == result["proposal_errors"]
    assert result["applied"] is False
    assert active_bytes(db) == before


@pytest.mark.parametrize("count,exit_price", [(0, 70), (10, 70), (60, 70), (160, 70), (300, 150)])
def test_lottery_full_cycle_only_appends_config_proposals(db, count, exit_price):
    for i in range(count):
        db.ll_tickets.rows.append({
            "ticket_id": str(i), "ticker": f"TEST{i}", "status": "CLOSED",
            "entry_fill_price": 100, "exit_fill_price": exit_price, "peak_price": 220,
            "score": 65, "variant": "V1_DAY2_CONTINUATION",
            "opened_at": "2026-09-01T12:00:00Z", "closed_at": "2026-09-03T12:00:00Z",
        })
    db.ll_tickets.rows.extend([
        {"ticket_id": "open", "status": "OPEN", "entry_fill_price": 100, "exit_fill_price": 1000},
        {"ticket_id": "pending-fill", "status": "CLOSED", "entry_fill_price": 100},
    ])
    before = active_bytes(db)
    result = asyncio.run(lottery.run_learning_cycle(triggered_by="test"))
    assert result["mode"] == "SHADOW"
    assert result["applied"] is False
    assert result["changes"] == []
    assert result["sample_count"] == count
    assert result["learned_config"]["min_ticket_score"] == 65
    proposed = result["proposed_learned_config"]
    assert proposed["custom"] == "preserve"
    if count >= 10 and exit_price == 70:
        assert proposed["min_ticket_score"] == 70
        assert result["proposed_changes"]
    if count >= 160:
        assert proposed["status"] == ("RETIRE_LEAGUE" if exit_price == 70 else "VALIDATED_POSITIVE")
    first = encoded(db.ll_learning_runs.rows)
    asyncio.run(lottery.run_learning_cycle())
    assert len(db.ll_learning_runs.rows) == 2
    assert encoded(db.ll_learning_runs.rows[:1]) == first
    assert active_bytes(db) == before
    assert all(not getattr(db, name).writes for name in ACTIVE)


def test_lottery_without_active_config_does_not_seed_defaults(db):
    db.ll_learned_config.rows.clear()
    result = asyncio.run(lottery.run_learning_cycle())
    assert result["mode"] == "SHADOW"
    assert result["applied"] is False
    assert db.ll_learned_config.rows == []
    assert not db.ll_learned_config.writes
