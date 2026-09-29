from types import SimpleNamespace

import pytest

from services import pm_ratchet


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
    assert updates[0]["current_stop"] == 7.2
    assert updates[0]["protection_state"] == "MONITORED_EXIT_ONLY"
    assert events == []
