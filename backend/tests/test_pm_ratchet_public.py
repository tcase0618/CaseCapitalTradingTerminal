from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from services import pm_ratchet


@pytest.mark.parametrize(
    "peak,previous,expected,armed",
    [(10.0, 8.0, 9.0, False), (10.49, 9.0, 9.0, False),
     (10.5, 9.0, 10.5, True), (12.0, 11.0, 11.0, True),
     (10.0, 9.2, 9.2, False)],
)
def test_public_stop_policy_locks_five_percent_without_loosening(peak, previous, expected, armed):
    policy = pm_ratchet.public_stop_policy(10.0, peak, previous)
    assert policy["active_stop"] == expected
    assert policy["profit_floor_armed"] is armed


@pytest.mark.asyncio
async def test_public_profit_floor_persists_across_reversal_and_restart(monkeypatch):
    trade = {
        "client_order_id": "profit-floor", "ticker": "WIN",
        "filled_avg_price": 10.0, "current_stop": 9.0,
        "pm_active_stop": 9.0, "pm_ratchet_level": 0,
        "pm_ratchet_plan": {"enabled": True, "no_capped_tp": True,
                            "initial_stop_pct": 10, "max_ratchets": 20},
    }

    class Trades:
        async def update_one(self, query, update):
            assert query["$and"][1] == {"broker_base": "public"}
            trade.update(update["$set"])

    class Events:
        async def insert_one(self, event):
            assert event["broker_base"] == "public"

    monkeypatch.setattr(pm_ratchet, "get_db", lambda: SimpleNamespace(tf_trades=Trades(), pm_ratchet_events=Events()))
    await pm_ratchet._apply_public_ratchet_marks([dict(trade)], {"WIN": 10.5}, source="test")
    assert trade["current_stop"] == 10.5
    assert trade["public_stop_policy"]["profit_floor_armed"] is True
    await pm_ratchet._apply_public_ratchet_marks([dict(trade)], {"WIN": 10.3}, source="test_restart")
    assert trade["current_stop"] == 10.5
    assert trade["pm_active_stop"] == 10.5
    assert trade["public_stop_policy"]["profit_floor_armed"] is True


def test_public_ratchet_mark_requires_a_fresh_executable_quote(monkeypatch):
    monkeypatch.setenv("PUBLIC_MAX_EQUITY_SPREAD_BPS", "300")
    now = datetime.now(timezone.utc).isoformat()
    valid = pm_ratchet._fresh_public_execution_mark({"bid": 10.00, "ask": 10.04, "quoteTime": now})
    wide = pm_ratchet._fresh_public_execution_mark({"bid": 10.00, "ask": 11.00, "quoteTime": now})
    stale = pm_ratchet._fresh_public_execution_mark({"bid": 10.00, "ask": 10.04, "quoteTime": "2020-01-01T00:00:00Z"})

    assert valid == 10.0
    assert wide is None
    assert stale is None


@pytest.mark.asyncio
async def test_public_ratchet_uses_fresh_public_mark_and_persists_raised_stop(monkeypatch):
    trade = {
        "client_order_id": "public-runner-1",
        "broker_base": "public",
        "status": "OPEN",
        "fill_status": "FILLED",
        "qty_remaining": 1,
        "ticker": "RUN",
        "filled_avg_price": 10.0,
        "current_stop": 9.0,
        "pm_ratchet_level": 0,
        "pm_ratchet_plan": {
            "enabled": True,
            "trigger_step_pct": 10.0,
            "max_ratchets": 4,
            "initial_stop_pct": 10.0,
            "stop_raise_pct": 7.5,
            "initial_target_pct": 25.0,
            "target_raise_pct": 18.0,
            "profile": "RUNNER",
        },
    }
    updates = []
    events = []

    class Cursor:
        async def to_list(self, _limit):
            return [dict(trade)]

    class Trades:
        def find(self, query, *_args, **_kwargs):
            assert query["$and"][1] == {"broker_base": "public"}
            return Cursor()

        async def update_one(self, _query, update):
            updates.append(update["$set"])

    class Events:
        async def insert_one(self, event):
            events.append(event)

    monkeypatch.setattr(pm_ratchet, "get_db", lambda: SimpleNamespace(tf_trades=Trades(), pm_ratchet_events=Events()))

    async def fresh_marks(_tickers):
        return {"RUN": 22.0}

    monkeypatch.setattr(pm_ratchet, "_public_prices", fresh_marks)

    result = await pm_ratchet.process_open_ratchets(broker_base="public")

    assert result["broker_base"] == "public"
    assert result["checked"] == 1
    assert result["ratcheted"] == 1
    assert updates[0]["pm_ratchet_level"] == 4
    assert updates[0]["peak_price_since_entry"] == 22.0
    assert updates[0]["current_stop"] == 12.0
    assert events[0]["broker_base"] == "public"


@pytest.mark.asyncio
async def test_public_ratchet_initializes_account_protection_for_unplanned_position(monkeypatch):
    trade = {
        "client_order_id": "public-imported-1",
        "broker_base": "public",
        "status": "OPEN",
        "fill_status": "FILLED",
        "qty_remaining": 1,
        "ticker": "LEGACY",
        "filled_avg_price": 10.0,
        "current_stop": 0.0,
        "pm_ratchet_plan": {"enabled": False},
    }
    updates = []
    events = []

    class Cursor:
        async def to_list(self, _limit):
            return [dict(trade)]

    class Trades:
        def find(self, query, *_args, **_kwargs):
            assert query["$and"][1] == {"broker_base": "public"}
            assert "pm_ratchet_plan.enabled" not in query["$and"][0]
            return Cursor()

        async def update_one(self, _query, update):
            updates.append(update["$set"])

    class Events:
        async def insert_one(self, event):
            events.append(event)

    monkeypatch.setattr(pm_ratchet, "get_db", lambda: SimpleNamespace(tf_trades=Trades(), pm_ratchet_events=Events()))

    async def fresh_marks(_tickers):
        return {"LEGACY": 8.0}

    monkeypatch.setattr(pm_ratchet, "_public_prices", fresh_marks)

    result = await pm_ratchet.process_open_ratchets(broker_base="public")

    assert result["checked"] == 1
    assert result["coverage_initialized"] == 1
    assert result["coverage_initialized_tickers"] == ["LEGACY"]
    assert updates[0]["pm_ratchet_plan"]["profile"] == "ACCOUNT_PROTECTION"
    assert updates[0]["pm_ratchet_plan"]["anchor_price"] == 8.0
    assert updates[0]["current_stop"] == 9.0
    assert updates[0]["protection_state"] == "MONITORED_EXIT_ONLY"
    assert events[0]["active_stop"] == 9.0


@pytest.mark.asyncio
async def test_public_ratchet_quote_gap_is_degraded_not_a_monitor_failure(monkeypatch):
    trade = {
        "client_order_id": "public-unpriced-1",
        "broker_base": "public",
        "status": "OPEN",
        "fill_status": "FILLED",
        "qty_remaining": 1,
        "ticker": "NOQUOTE",
        "filled_avg_price": 10.0,
        "current_stop": 9.0,
        "pm_ratchet_plan": {"enabled": True},
    }

    class Cursor:
        async def to_list(self, _limit):
            return [dict(trade)]

    class Trades:
        def find(self, _query, *_args, **_kwargs):
            return Cursor()

    monkeypatch.setattr(pm_ratchet, "get_db", lambda: SimpleNamespace(tf_trades=Trades(), pm_ratchet_events=SimpleNamespace()))

    async def no_marks(_tickers):
        return {}

    monkeypatch.setattr(pm_ratchet, "_public_prices", no_marks)

    result = await pm_ratchet.process_open_ratchets(broker_base="public")

    assert result["ok"] is True
    assert result["degraded"] is True
    assert result["reason"] == "public_ratchet_quotes_unavailable"
    assert result["quote_coverage_status"] == "unavailable"


@pytest.mark.asyncio
async def test_sdk_stream_mark_uses_the_same_public_ratchet_ledger(monkeypatch):
    trade = {
        "client_order_id": "public-stream-1",
        "broker_base": "public",
        "status": "OPEN",
        "fill_status": "FILLED",
        "qty_remaining": 1,
        "ticker": "STREAM",
        "filled_avg_price": 10.0,
        "current_stop": 9.0,
        "pm_ratchet_level": 0,
        "pm_ratchet_plan": {
            "enabled": True,
            "trigger_step_pct": 10.0,
            "max_ratchets": 4,
            "initial_stop_pct": 10.0,
            "stop_raise_pct": 7.5,
            "no_capped_tp": True,
        },
    }
    updates = []

    class Cursor:
        async def to_list(self, _limit):
            return [dict(trade)]

    class Trades:
        def find(self, query, *_args, **_kwargs):
            assert query["broker_base"] == "public"
            assert query["ticker"] == {"$in": ["STREAM"]}
            return Cursor()

        async def update_one(self, _query, update):
            updates.append(update["$set"])

    class Events:
        async def insert_one(self, _event):
            return None

    monkeypatch.setattr(pm_ratchet, "get_db", lambda: SimpleNamespace(tf_trades=Trades(), pm_ratchet_events=Events()))

    result = await pm_ratchet.process_public_ratchet_marks({"STREAM": 22.0})

    assert result["source"] == "public_price_stream"
    assert result["ratcheted"] == 1
    assert updates[0]["pm_last_ratchet_source"] == "public_price_stream"
    assert updates[0]["current_stop"] == 12.0
