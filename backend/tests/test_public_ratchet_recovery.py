from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import pm_ratchet, public_execution, public_price_stream, scheduler


def delayed_sides(last=106.0, age=0):
    now = datetime.now(timezone.utc)
    return {"symbol": "TEST", "bid": 99.0, "ask": 100.0,
            "bidTimestamp": (now - timedelta(minutes=15)).isoformat(),
            "askTimestamp": (now - timedelta(minutes=15)).isoformat(),
            "last": last, "lastTimestamp": (now - timedelta(seconds=age)).isoformat()}


def test_current_trade_is_not_discarded_because_nbbo_is_delayed():
    row = delayed_sides(106.001)
    quote, reason = public_execution._protective_exit_quote(row)
    assert reason is None
    assert quote["price_basis"] == "PUBLIC_FRESH_LAST_TRADE_LIMIT"
    assert quote["bid"] is None and quote["ask"] is None
    assert quote["limit_price"] == 106.01
    assert quote["reference_price"] == 106.001
    assert quote["fill_guaranteed"] is False
    mark = pm_ratchet._public_ratchet_mark(row)
    assert mark["price"] == 106.001
    assert pm_ratchet._fresh_public_execution_mark(row) is None


@pytest.mark.parametrize("last,age", [(106, 300), (float('nan'), 0), (float('inf'), 0), (-1, 0)])
def test_bad_or_stale_last_does_not_rescue_stale_sides(last, age):
    quote, reason = public_execution._protective_exit_quote(delayed_sides(last, age))
    assert quote is None and reason


def test_last_trade_requires_its_own_timestamp_and_success():
    row = delayed_sides()
    row.pop("lastTimestamp")
    row["quoteTime"] = datetime.now(timezone.utc).isoformat()
    assert public_execution._protective_exit_quote(row)[0] is None
    row = delayed_sides()
    row["outcome"] = "FAILED"
    assert public_execution._protective_exit_quote(row)[0] is None
    row["bidTimestamp"] = row["askTimestamp"] = datetime.now(timezone.utc).isoformat()
    assert public_execution._protective_exit_quote(row)[0] is None


def test_fresh_bid_remains_preferred():
    row = delayed_sides(last=110)
    row["bidTimestamp"] = row["askTimestamp"] = datetime.now(timezone.utc).isoformat()
    quote, _ = public_execution._protective_exit_quote(row)
    assert quote["price_basis"] == "PUBLIC_FRESH_BID"
    assert quote["reference_price"] == 99


def test_fractional_premarket_cannot_be_reported_fully_protected():
    now = datetime(2026, 11, 9, 13, 0, tzinfo=timezone.utc)
    row = {"qty_remaining": 0.01, "pm_active_stop": 105,
           "pm_ratchet_plan": {"enabled": True}, "protection_state": "MONITORED_EXIT_ONLY",
           "pm_last_ratchet_check": now.isoformat()}
    assert public_execution._verified_monitored_exit(row, now=now)[0] is False
    row["qty_remaining"] = 1.01
    assert public_execution._verified_monitored_exit(row, now=now)[0] is False
    row["qty_remaining"] = 1
    assert public_execution._verified_monitored_exit(row, now=now)[0] is True


@pytest.mark.asyncio
async def test_actual_ratchet_to_exit_pipeline_latches_breach_and_waits_for_legal_session(monkeypatch):
    trade = {"ticker": "TEST", "client_order_id": "recovery", "broker_base": "public",
             "status": "OPEN", "fill_status": "FILLED", "qty_remaining": 0.05,
             "filled_avg_price": 100, "current_stop": 90, "pm_active_stop": 90,
             "pm_ratchet_plan": {"enabled": True}}
    maxima = []

    class Cursor:
        async def to_list(self, _limit): return [dict(trade)]

    class Trades:
        def find(self, *_args): return Cursor()
        async def update_one(self, _query, update):
            trade.update(update.get("$set", {}))
            maxima.append(update.get("$max", {}))
            for key, value in update.get("$max", {}).items():
                trade[key] = max(trade.get(key, value), value)

    db = SimpleNamespace(tf_trades=Trades(), pm_ratchet_events=SimpleNamespace(insert_one=AsyncMock()))
    monkeypatch.setattr(pm_ratchet, "get_db", lambda: db)
    monkeypatch.setattr(public_execution, "get_db", lambda: db)
    monkeypatch.setattr(public_execution, "enabled", lambda: True)
    submissions = []
    last = [106.0]
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): pass
        async def quotes(self, _symbols): return {"quotes": [delayed_sides(last[0])]}
        async def portfolio(self): return {"positions": [{"symbol": "TEST", "quantity": 0.05}]}
        async def submit_equity_order(self, **kwargs):
            submissions.append(kwargs)
            return {"order": {"orderId": "exit-fixture"}}
    monkeypatch.setattr(public_execution.public_api, "PublicAPIClient", lambda **_kwargs: Client())
    monkeypatch.setattr(public_execution.execution_safety, "claim_execution_intent", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(public_execution.execution_safety, "mark_execution_intent", AsyncMock())
    # The REST production path consumes real normalized quote rows, not a
    # hand-built set of downstream trade keys.
    await pm_ratchet.process_open_ratchets(broker_base="public")
    assert trade["current_stop"] == 105
    assert trade["pm_last_ratchet_price_basis"] == "PUBLIC_FRESH_LAST_TRADE_LIMIT"
    assert maxima[-1]["current_stop"] == 105
    last[0] = 104
    monkeypatch.setattr(public_execution, "_public_session_now", lambda *_args: "TWENTY_FOUR_HOURS")
    result = await public_execution.process_protective_exits()
    assert result["deferred"][0]["reason"] == "public_24h_exit_requires_whole_share"
    assert trade["emergency_exit_trigger_stop"] == 105
    assert not submissions
    # Once triggered, a recovery above the floor must not erase the sell.
    last[0] = 106
    monkeypatch.setattr(public_execution, "_public_session_now", lambda *_args: "CORE")
    result = await public_execution.process_protective_exits()
    assert len(result["submitted"]) == 1
    assert submissions[0]["quantity"] == 0.05
    assert submissions[0]["limit_price"] == 106
    assert submissions[0]["side"] == "SELL"
    assert trade["emergency_exit_quote"]["bid"] is None
    await public_execution.process_protective_exits()
    assert len(submissions) == 1


@pytest.mark.asyncio
async def test_stale_ratchet_input_uses_monotone_database_operators(monkeypatch):
    state = {"current_stop": 135, "pm_active_stop": 135, "peak_price_since_entry": 145, "pm_ratchet_level": 40}
    stale = {"ticker": "TEST", "client_order_id": "overlap", "filled_avg_price": 100,
             "current_stop": 90, "pm_ratchet_plan": {"enabled": True}}
    class Trades:
        async def update_one(self, _query, update):
            assert not set(state).intersection(update["$set"])
            for key, value in update["$max"].items(): state[key] = max(state[key], value)
    monkeypatch.setattr(pm_ratchet, "get_db", lambda: SimpleNamespace(tf_trades=Trades(), pm_ratchet_events=SimpleNamespace(insert_one=AsyncMock())))
    await pm_ratchet._apply_public_ratchet_marks([stale], {"TEST": 106}, source="overlapping_callback")
    assert state == {"current_stop": 135, "pm_active_stop": 135, "peak_price_since_entry": 145, "pm_ratchet_level": 40}


@pytest.mark.asyncio
async def test_reconcile_exception_does_not_skip_exits_in_scheduled_monitor(monkeypatch):
    class FakeScheduler:
        def __init__(self, **_kwargs):
            self.jobs = []
            self.running = False
        def add_job(self, func, _trigger, **kwargs): self.jobs.append({"func": func, **kwargs})
        def start(self): self.running = True
        def shutdown(self, **_kwargs): self.running = False

    monkeypatch.setattr(scheduler, "_scheduler", None)
    monkeypatch.setattr(scheduler, "AsyncIOScheduler", FakeScheduler)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    state = SimpleNamespace(find_one=AsyncMock(return_value={}), update_one=AsyncMock())
    monkeypatch.setattr(scheduler, "get_db", lambda: SimpleNamespace(bot_state=state))
    monkeypatch.setattr(scheduler, "log_activity", AsyncMock())
    snapshot = {"snapshot_at": datetime.now(timezone.utc).isoformat(), "totals": {"positions": 1, "open_orders": 0, "unrealized_pl": 0}}
    persist = AsyncMock(return_value=snapshot)
    monkeypatch.setattr(scheduler, "persist_live_position_snapshot", persist)
    monkeypatch.setattr(public_execution, "enabled", lambda: True)
    monkeypatch.setattr(public_execution, "reconcile", AsyncMock(side_effect=RuntimeError("offline fixture")))
    exits = AsyncMock(return_value={"ok": True, "checked": 1, "deferred": []})
    monkeypatch.setattr(public_execution, "process_protective_exits", exits)
    monkeypatch.setattr(public_execution, "protection_coverage", AsyncMock(return_value={}))
    monkeypatch.setattr(pm_ratchet, "process_open_ratchets", AsyncMock(return_value={"ok": True}))
    scheduler.start_scheduler()
    try:
        job = next(job for job in scheduler._scheduler.jobs if job["id"] == "position_monitor")
        await job["func"]()
        exits.assert_awaited_once()
        result = persist.call_args.kwargs["management"]["legacy_position_monitor"]
        assert result["ok"] is False
        assert result["failures"][0]["stage"] == "public_reconciliation"
        assert result["protective_exits"]["checked"] == 1
    finally:
        scheduler.shutdown_scheduler()
