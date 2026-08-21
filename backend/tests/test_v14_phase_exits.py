"""Validation harness for the three-phase exit system.

Drops a synthetic FILLED trade into tf_trades with controlled price/peak,
then calls process_phase_exits and asserts the right phase fires. After
each assertion the trade is cleaned up so the harness is idempotent.

Run with:  cd /app/backend && set -a && source .env && set +a && \
              python3 -m pytest tests/test_v14_phase_exits.py -v
"""
from __future__ import annotations
import asyncio
import os
import sys
import pytest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.db import get_db, stamped  # noqa: E402
from services import trade_floor_phases as phases  # noqa: E402


@pytest.fixture(autouse=True)
def _disable_live_alpaca_qty(monkeypatch):
    monkeypatch.setattr(phases, "_alpaca_available_qty", lambda ticker: _const(None))


def _recent_iso():
    return datetime.now(timezone.utc).isoformat()


@pytest.mark.asyncio
async def test_phase1_trigger_moves_stop_to_breakeven(monkeypatch):
    db = get_db()
    cli = "tf-phase-test1"
    await db.tf_trades.delete_many({"client_order_id": cli})
    await db.tf_phase_exits.delete_many({"parent_client_order_id": cli})
    await db.tf_trades.insert_one(stamped({
        "client_order_id": cli, "order_id": "alp-test-1", "ticker": "TEST",
        "signal_combo": ["CONGRESSIONAL_BUY", "UNUSUAL_FLOW"],
        "instrument": "fractional", "notional": 30.0, "limit_price": 100.0,
        "entry_price_ref": 100.0, "filled_avg_price": 100.0,
        "stop_price": 88.0, "current_stop": 88.0, "stop_pct": 0.12,
        "axiom_target": 120.0, "phase1_target": 120.0, "phase2_target": 130.0,
        "phase": 1, "phases_hit": {},
        "qty_total": 0.3, "qty_remaining": 0.3,
        "peak_price_since_entry": 100.0,
        "hold_window_days": 30, "sector": "industrials",
        "status": "OPEN", "fill_status": "FILLED",
        "submitted_at": _recent_iso(),
        "filled_at": _recent_iso(),
    }))
    # Stub Alpaca sell + price
    monkeypatch.setattr(phases, "_alpaca_market_sell",
                          lambda t, q, client_order_id: _fake_order(t, q))
    monkeypatch.setattr(phases, "_current_price", lambda t: _const(122.0))
    monkeypatch.setattr(phases, "_send_telegram", lambda text: _noop())

    res = await phases.process_phase_exits()
    assert res["checked"] >= 1
    doc = await db.tf_trades.find_one({"client_order_id": cli}, {"_id": 0})
    assert doc["phase"] == 2, f"expected phase=2 got {doc['phase']}"
    assert doc["current_stop"] == 100.0, f"stop should move to entry: {doc['current_stop']}"
    assert "1" in doc["phases_hit"]
    assert abs(doc["qty_remaining"] - 0.18) < 1e-6, doc["qty_remaining"]
    await db.tf_trades.delete_many({"client_order_id": cli})
    await db.tf_phase_exits.delete_many({"parent_client_order_id": cli})


@pytest.mark.asyncio
async def test_phase2_moves_stop_to_phase1_exit_price(monkeypatch):
    db = get_db()
    cli = "tf-phase-test2"
    await db.tf_trades.delete_many({"client_order_id": cli})
    await db.tf_phase_exits.delete_many({"parent_client_order_id": cli})
    # Trade already in phase 2 (phase 1 completed at $121)
    await db.tf_trades.insert_one(stamped({
        "client_order_id": cli, "order_id": "alp-test-2", "ticker": "TEST",
        "signal_combo": ["INSIDER", "UNUSUAL_FLOW"],
        "instrument": "fractional", "notional": 30.0, "limit_price": 100.0,
        "entry_price_ref": 100.0, "filled_avg_price": 100.0,
        "stop_price": 88.0, "current_stop": 100.0,  # already at breakeven from phase 1
        "stop_pct": 0.12,
        "axiom_target": 120.0, "phase1_target": 120.0, "phase2_target": 130.0,
        "phase": 2,
        "phases_hit": {"1": {"hit_at": _recent_iso(),
                                  "exit_price": 121.0, "qty_sold": 0.12}},
        "qty_total": 0.3, "qty_remaining": 0.18,
        "peak_price_since_entry": 121.0,
        "hold_window_days": 30, "sector": "industrials",
        "status": "OPEN", "fill_status": "FILLED",
        "submitted_at": _recent_iso(),
        "filled_at": _recent_iso(),
    }))
    monkeypatch.setattr(phases, "_alpaca_market_sell",
                          lambda t, q, client_order_id: _fake_order(t, q))
    monkeypatch.setattr(phases, "_current_price", lambda t: _const(131.0))
    monkeypatch.setattr(phases, "_send_telegram", lambda text: _noop())

    await phases.process_phase_exits()
    doc = await db.tf_trades.find_one({"client_order_id": cli}, {"_id": 0})
    assert doc["phase"] == 3, doc["phase"]
    # Stop should move to phase-1 exit price ($121), not stay at $100
    assert doc["current_stop"] == 121.0, doc["current_stop"]
    assert "2" in doc["phases_hit"]
    assert abs(doc["qty_remaining"] - 0.09) < 1e-6, doc["qty_remaining"]
    await db.tf_trades.delete_many({"client_order_id": cli})
    await db.tf_phase_exits.delete_many({"parent_client_order_id": cli})


@pytest.mark.asyncio
async def test_phase_exit_caps_sell_to_live_alpaca_available_qty(monkeypatch):
    db = get_db()
    cli = "tf-phase-test-live-qty-cap"
    sold = {}
    await db.tf_trades.delete_many({"client_order_id": cli})
    await db.tf_phase_exits.delete_many({"parent_client_order_id": cli})
    await db.tf_trades.insert_one(stamped({
        "client_order_id": cli, "order_id": "alp-test-live-qty", "ticker": "TEST",
        "signal_combo": ["INSIDER"], "instrument": "fractional",
        "notional": 100.0, "limit_price": 100.0,
        "entry_price_ref": 100.0, "filled_avg_price": 100.0,
        "stop_price": 88.0, "current_stop": 88.0, "stop_pct": 0.12,
        "axiom_target": 120.0, "phase1_target": 120.0, "phase2_target": 130.0,
        "phase": 1, "phases_hit": {},
        "qty_total": 1.0,
        # Stale ledger says 70% remains, but Alpaca only has 25% available.
        "qty_remaining": 0.7,
        "peak_price_since_entry": 100.0,
        "hold_window_days": 30, "sector": "technology",
        "status": "OPEN", "fill_status": "FILLED",
        "submitted_at": _recent_iso(),
        "filled_at": _recent_iso(),
    }))

    async def fake_sell(ticker, qty, client_order_id):
        sold["qty"] = qty
        return {"id": f"fake-{ticker}-{qty}", "filled_avg_price": None}

    monkeypatch.setattr(phases, "_alpaca_available_qty", lambda ticker: _const(0.25))
    monkeypatch.setattr(phases, "_alpaca_market_sell", fake_sell)
    monkeypatch.setattr(phases, "_current_price", lambda t: _const(122.0))
    monkeypatch.setattr(phases, "_send_telegram", lambda text: _noop())

    await phases.process_phase_exits()
    doc = await db.tf_trades.find_one({"client_order_id": cli}, {"_id": 0})
    assert sold["qty"] == 0.25
    assert doc["qty_available_source"] == "alpaca_live"
    assert doc["qty_remaining"] == 0
    assert doc["phases_hit"]["1"]["qty_sold"] == 0.25
    await db.tf_trades.delete_many({"client_order_id": cli})
    await db.tf_phase_exits.delete_many({"parent_client_order_id": cli})


@pytest.mark.asyncio
async def test_phase3_trailing_stop_closes_position(monkeypatch):
    db = get_db()
    cli = "tf-phase-test3"
    await db.tf_trades.delete_many({"client_order_id": cli})
    await db.tf_phase_outcomes.delete_many({"parent_client_order_id": cli})
    await db.tf_phase_exits.delete_many({"parent_client_order_id": cli})
    # Trade in phase 3, peak $140 (40% gain), trail = 50% of peak gain = 20%,
    # so trail_stop = 100 * (1 + 0.4 * 0.5) = 120. Current price 119 → trigger.
    await db.tf_trades.insert_one(stamped({
        "client_order_id": cli, "order_id": "alp-test-3", "ticker": "TEST",
        "signal_combo": ["INSIDER"],
        "instrument": "fractional", "notional": 30.0, "limit_price": 100.0,
        "entry_price_ref": 100.0, "filled_avg_price": 100.0,
        "stop_price": 88.0, "current_stop": 121.0, "stop_pct": 0.12,
        "axiom_target": 120.0, "phase1_target": 120.0, "phase2_target": 130.0,
        "phase": 3,
        "phases_hit": {"1": {"exit_price": 121.0, "qty_sold": 0.12},
                          "2": {"exit_price": 131.0, "qty_sold": 0.09}},
        "qty_total": 0.3, "qty_remaining": 0.09,
        "peak_price_since_entry": 140.0,
        "hold_window_days": 30, "sector": "technology",
        "status": "OPEN", "fill_status": "FILLED",
        "submitted_at": _recent_iso(),
        "filled_at": _recent_iso(),
    }))
    monkeypatch.setattr(phases, "_alpaca_market_sell",
                          lambda t, q, client_order_id: _fake_order(t, q))
    monkeypatch.setattr(phases, "_current_price", lambda t: _const(119.0))
    monkeypatch.setattr(phases, "_send_telegram", lambda text: _noop())

    await phases.process_phase_exits()
    doc = await db.tf_trades.find_one({"client_order_id": cli}, {"_id": 0})
    assert doc["status"] == "CLOSED", doc["status"]
    assert doc["close_reason"] == "phase3_trailing_stop", doc["close_reason"]
    assert doc["qty_remaining"] == 0
    outcomes = await db.tf_phase_outcomes.count_documents({"parent_client_order_id": cli})
    assert outcomes == 1
    await db.tf_trades.delete_many({"client_order_id": cli})
    await db.tf_phase_outcomes.delete_many({"parent_client_order_id": cli})
    await db.tf_phase_exits.delete_many({"parent_client_order_id": cli})


@pytest.mark.asyncio
async def test_hard_stop_hit_closes_at_market(monkeypatch):
    db = get_db()
    cli = "tf-phase-test-stop"
    await db.tf_trades.delete_many({"client_order_id": cli})
    await db.tf_phase_outcomes.delete_many({"parent_client_order_id": cli})
    await db.tf_trades.insert_one(stamped({
        "client_order_id": cli, "order_id": "alp-test-stop", "ticker": "TEST",
        "signal_combo": ["INSIDER"], "instrument": "fractional",
        "notional": 30.0, "limit_price": 100.0,
        "entry_price_ref": 100.0, "filled_avg_price": 100.0,
        "stop_price": 88.0, "current_stop": 88.0, "stop_pct": 0.12,
        "axiom_target": 120.0, "phase1_target": 120.0, "phase2_target": 130.0,
        "phase": 1, "phases_hit": {},
        "qty_total": 0.3, "qty_remaining": 0.3, "peak_price_since_entry": 100.0,
        "hold_window_days": 30, "sector": "biotechnology",
        "status": "OPEN", "fill_status": "FILLED",
        "submitted_at": _recent_iso(),
        "filled_at": _recent_iso(),
    }))
    monkeypatch.setattr(phases, "_alpaca_market_sell",
                          lambda t, q, client_order_id: _fake_order(t, q))
    monkeypatch.setattr(phases, "_current_price", lambda t: _const(87.0))
    monkeypatch.setattr(phases, "_send_telegram", lambda text: _noop())
    await phases.process_phase_exits()
    doc = await db.tf_trades.find_one({"client_order_id": cli}, {"_id": 0})
    assert doc["status"] == "CLOSED" and doc["close_reason"] == "hard_stop"
    await db.tf_trades.delete_many({"client_order_id": cli})
    await db.tf_phase_outcomes.delete_many({"parent_client_order_id": cli})


# ── Helpers ──
async def _fake_order(ticker, qty):
    return {"id": f"fake-{ticker}-{qty}", "filled_avg_price": None}


async def _const(value):
    return value


async def _noop():
    return None
