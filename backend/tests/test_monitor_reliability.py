from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import database_watchdog, pm_ratchet, public_execution, scheduler


def test_coverage_incident_is_stable_as_quote_coverage_and_position_count_change():
    partial = [{"stage": "public_protection_coverage", "reason": "3_positions_without_current_exit_coverage"}]
    unavailable = partial + [{"stage": "public_pm_ratchet", "reason": "no_fresh_position_marks"}]
    other_count = [{"stage": "public_protection_coverage", "reason": "4_positions_without_current_exit_coverage"}]
    assert scheduler._monitor_incident_key(partial) == scheduler._monitor_incident_key(unavailable) == scheduler._monitor_incident_key(other_count)
    old = "PUBLIC_QUOTE_COVERAGE_DEGRADED|public_protection_coverage:3_positions_without_current_exit_coverage"
    assert scheduler._canonical_monitor_incident(old) == scheduler._monitor_incident_key(partial)
    exception = partial + [{"stage": "public_pm_ratchet", "reason": "RuntimeError"}]
    assert scheduler._monitor_incident_key(exception) != scheduler._monitor_incident_key(partial)


@pytest.mark.parametrize("age,expected", [(15, True), (900, False), (-60, False)])
def test_observation_cache_keeps_original_provider_timestamp(age, expected):
    timestamp = (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat()
    quote = public_execution._cached_protective_exit_quote({"pm_last_ratchet_reference": {
        "price": 105.001, "price_basis": "PUBLIC_FRESH_LAST_TRADE_LIMIT", "timestamp": timestamp,
    }})
    assert bool(quote) is expected
    if quote:
        assert quote["quote_time"] == timestamp
        assert quote["limit_price"] == 105.01
        assert quote["bid"] is None
        assert quote["price_basis"] == "PUBLIC_RECENT_OBSERVATION_LIMIT"


@pytest.mark.parametrize("change", [{"price": float('nan')}, {"timestamp": None}, {"price_basis": "PORTFOLIO_MARK"}])
def test_cache_does_not_authorize_unverified_marks(change):
    mark = {"price": 100, "price_basis": "PUBLIC_FRESH_BID", "timestamp": datetime.now(timezone.utc).isoformat()}
    assert public_execution._cached_protective_exit_quote({"pm_last_ratchet_reference": {**mark, **change}}) is None


@pytest.mark.asyncio
async def test_critical_database_probe_does_not_fetch_or_sort_large_scan_history(monkeypatch):
    state = AsyncMock(return_value=None)
    trades = AsyncMock(return_value=None)
    # No research/display collections exist on this fixture: touching one fails.
    monkeypatch.setattr(database_watchdog, "get_db", lambda: SimpleNamespace(
        bot_state=SimpleNamespace(find_one=state), tf_trades=SimpleNamespace(find_one=trades)))
    monkeypatch.setattr(database_watchdog, "_notifier", SimpleNamespace(observe=AsyncMock(return_value={})))
    result = await database_watchdog.check()
    assert result["db_available"] is True
    assert result["failed_stage"] is None
    assert set(result["stage_timings_ms"]) == {"bot_state", "tf_trades"}
    assert result["protection_status"] == "NOT_ASSESSED"


@pytest.mark.asyncio
async def test_ledger_timeout_identifies_stage_without_claiming_server_crash(monkeypatch):
    monkeypatch.setattr(database_watchdog, "get_db", lambda: SimpleNamespace(
        bot_state=SimpleNamespace(find_one=AsyncMock(return_value={})),
        tf_trades=SimpleNamespace(find_one=AsyncMock(side_effect=TimeoutError('fixture-secret')))))
    monkeypatch.setattr(database_watchdog, "_notifier", SimpleNamespace(observe=AsyncMock(return_value={})))
    result = await database_watchdog.check()
    assert result["db_available"] is False
    assert result["failed_stage"] == "tf_trades"
    assert result["db_reason"] == "database_timeout"
    assert result["failure_scope"] == "critical_ledger_read"
    assert "fixture-secret" not in str(result)


@pytest.mark.asyncio
async def test_exit_uses_newer_sdk_observation_without_replaying_an_older_rest_reversal(monkeypatch):
    now = datetime.now(timezone.utc)
    trade = {"ticker": "TEST", "client_order_id": "entry", "qty_remaining": 1, "pm_active_stop": 105,
             "pm_last_ratchet_reference": {"price": 106, "price_basis": "PUBLIC_FRESH_LAST_TRADE_LIMIT",
                                            "timestamp": (now - timedelta(seconds=10)).isoformat()}}
    api_last_age = [60]
    submissions = []
    class Cursor:
        async def to_list(self, _limit): return [dict(trade)]
    class Trades:
        def find(self, *_args): return Cursor()
        async def update_one(self, _query, update): trade.update(update["$set"])
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): pass
        async def portfolio(self): return {"positions": [{"symbol": "TEST", "quantity": 1}]}
        async def quotes(self, _symbols): return {"quotes": [{"symbol": "TEST", "last": 104,
            "lastTimestamp": (now - timedelta(seconds=api_last_age[0])).isoformat()}]}
        async def submit_equity_order(self, **kwargs):
            submissions.append(kwargs)
            return {"order": {"orderId": "offline-exit"}}
    monkeypatch.setattr(public_execution, "enabled", lambda: True)
    monkeypatch.setattr(public_execution, "get_db", lambda: SimpleNamespace(tf_trades=Trades()))
    monkeypatch.setattr(public_execution.public_api, "PublicAPIClient", lambda **_kwargs: Client())
    monkeypatch.setattr(public_execution, "_public_session_now", lambda *_args: "CORE")
    claim = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(public_execution.execution_safety, "claim_execution_intent", claim)
    monkeypatch.setattr(public_execution.execution_safety, "mark_execution_intent", AsyncMock())
    await public_execution.process_protective_exits()
    assert not submissions
    claim.assert_not_awaited()
    # A later cached observation of a breach is usable even if REST stalls.
    trade["pm_last_ratchet_reference"]["price"] = 104
    trade["pm_last_ratchet_reference"]["timestamp"] = now.isoformat()
    api_last_age[0] = 900
    result = await public_execution.process_protective_exits()
    assert len(result["submitted"]) == 1
    assert submissions[0]["limit_price"] == 104
    assert submissions[0]["side"] == "SELL"
    assert trade["emergency_exit_quote"]["price_basis"] == "PUBLIC_RECENT_OBSERVATION_LIMIT"
    assert trade["emergency_exit_quote"]["quote_time"] == now.isoformat()


@pytest.mark.asyncio
async def test_two_scheduled_quote_incidents_send_one_degradation_notice_and_real_exception_escalates(monkeypatch):
    class FakeScheduler:
        def __init__(self, **_kwargs): self.jobs = []; self.running = False
        def add_job(self, func, _trigger, **kwargs): self.jobs.append({"func": func, **kwargs})
        def start(self): self.running = True
        def shutdown(self, **_kwargs): self.running = False

    documents = {}
    class State:
        async def find_one(self, query, *_args): return dict(documents.get(query["_id"], {}))
        async def update_one(self, query, update, **_kwargs): documents.setdefault(query["_id"], {}).update(update["$set"])

    clock = [datetime(2026, 11, 11, 11, 53, tzinfo=timezone.utc)]
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return clock[0].astimezone(tz or timezone.utc)

    monkeypatch.setattr(scheduler, "datetime", Clock)
    monkeypatch.setattr(scheduler, "_scheduler", None)
    monkeypatch.setattr(scheduler, "AsyncIOScheduler", FakeScheduler)
    monkeypatch.setattr(scheduler, "get_db", lambda: SimpleNamespace(bot_state=State()))
    monkeypatch.setattr(scheduler, "log_activity", AsyncMock())
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "offline-chat")
    sender = AsyncMock(return_value=True)
    monkeypatch.setattr(scheduler.telegram_service, "send_message", sender)
    monkeypatch.setattr(scheduler, "persist_live_position_snapshot", AsyncMock(return_value={"snapshot_at": clock[0].isoformat(), "totals": {"positions": 3, "open_orders": 0, "unrealized_pl": 0}}))
    monkeypatch.setattr(public_execution, "enabled", lambda: True)
    monkeypatch.setattr(public_execution, "reconcile", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(public_execution, "process_protective_exits", AsyncMock(return_value={"ok": True}))
    coverage = AsyncMock(return_value={"monitored_exit_only_open": 3})
    monkeypatch.setattr(public_execution, "protection_coverage", coverage)
    ratchet = AsyncMock(return_value={"ok": True, "degraded": True, "quote_coverage_status": "partial"})
    monkeypatch.setattr(pm_ratchet, "process_open_ratchets", ratchet)
    scheduler.start_scheduler()
    try:
        job = next(job for job in scheduler._scheduler.jobs if job["id"] == "position_monitor")["func"]
        await job()
        ratchet.return_value = {"ok": True, "degraded": True, "quote_coverage_status": "unavailable"}
        clock[0] += timedelta(minutes=2)
        await job()
        assert sender.await_count == 1
        assert "PROTECTION DEGRADED" in sender.call_args.args[0]
        assert "MONITOR FAILURE" not in sender.call_args.args[0]
        ratchet.side_effect = RuntimeError("fixture")
        await job()
        assert sender.await_count == 2
        assert "MONITOR FAILURE" in sender.call_args.args[0]
        # One good minute must not clear an incident, then immediately re-alert.
        ratchet.side_effect = None
        ratchet.return_value = {"ok": True}
        coverage.return_value = {}
        await job()
        clock[0] += timedelta(seconds=60)
        await job()
        assert sender.await_count == 2
        clock[0] += timedelta(seconds=60)
        await job()
        assert sender.await_count == 3
        assert "RECOVERED" in sender.call_args.args[0]
    finally:
        scheduler.shutdown_scheduler()
