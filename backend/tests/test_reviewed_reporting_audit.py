from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from services import market_dates, pnl_tracker, postgres_store, pricer, scheduler, telegram_service
from test_scheduler_overlap_guard import _FakeScheduler


class MemoryCollection:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def find(self, query, *args):
        rows = [dict(r) for r in self.rows if postgres_store._matches(r, query)]
        async def to_list(limit):
            return rows[:limit]
        return SimpleNamespace(to_list=to_list)

    async def find_one(self, query, *args, **kwargs):
        return next((dict(r) for r in self.rows if postgres_store._matches(r, query)), None)

    async def update_one(self, query, update, **kwargs):
        for row in self.rows:
            if postgres_store._matches(row, query):
                row.update(update.get("$set", {}))
                return SimpleNamespace(matched_count=1)
        if kwargs.get("upsert"):
            self.rows.append({**query, **update.get("$set", {})})
        return SimpleNamespace(matched_count=0)

@pytest.mark.parametrize('title', ['MONITOR FAILURE', 'PROTECTION DEGRADED', 'MONITOR RECOVERED'])
def test_portfolio_operations_alerts_bypass_scan_only_policy(monkeypatch, title):
    monkeypatch.setenv('TELEGRAM_SINGLE_CONSOLIDATED_SCAN_ONLY', 'true')
    kind = telegram_service._outbound_kind(f'CASE CAPITAL | PORTFOLIO {title}')
    assert kind == 'ops_alert' and telegram_service._standalone_delivery_allowed(kind)



@pytest.mark.asyncio
@pytest.mark.parametrize('recovery', [False, True])
async def test_failed_protection_alert_is_retried(monkeypatch, recovery):
    monkeypatch.setattr(scheduler, '_scheduler', None)
    monkeypatch.setattr(scheduler, 'AsyncIOScheduler', _FakeScheduler)
    monkeypatch.setenv('TELEGRAM_CHAT_ID', 'offline-test-chat')
    state = MemoryCollection([{'_id': 'position_monitor_alert:monitor', 'active': True, 'incident': 'OLD'}] if recovery else [])
    calls = []
    async def send(*args, **kwargs):
        calls.append(args)
        return False
    monkeypatch.setattr(scheduler, 'get_db', lambda: SimpleNamespace(bot_state=state))
    monkeypatch.setattr(telegram_service, 'send_message', send)
    scheduler.start_scheduler()
    try:
        monitor = next(j['func'] for j in _FakeScheduler.instances[-1].jobs if j['id'] == 'position_monitor')
        closure = dict(zip(monitor.__code__.co_freevars, (c.cell_contents for c in monitor.__closure__)))
        failures = [{'stage': 'public_protection_coverage', 'reason': 'unpriced'}]
        if recovery:
            await closure['_clear_position_monitor_failure']('monitor')
            await closure['_clear_position_monitor_failure']('monitor')
            assert state.rows[0]['active']
        else:
            await closure['_send_position_monitor_failure'](failures)
            await closure['_send_position_monitor_failure'](failures)
            assert not any(row.get('active') for row in state.rows)
        assert len(calls) == 2
    finally:
        scheduler.shutdown_scheduler()



@pytest.mark.asyncio
async def test_telegram_command_requires_configured_chat(monkeypatch):
    monkeypatch.delenv('TELEGRAM_CHAT_ID', raising=False)
    async def unexpected(*args, **kwargs):
        raise AssertionError('Unconfigured command must not dispatch')
    monkeypatch.setattr(telegram_service, 'log_activity', unexpected)
    await telegram_service.handle_update({'message': {'text': '/calls', 'chat': {'id': 'untrusted'}}})



def test_trading_dates_skip_holidays_and_respect_early_close():
    assert market_dates.add_trading_days(date(2026, 11, 25), 1) == date(2026, 11, 27)
    assert not market_dates.is_core_session(datetime(2026, 11, 27, 18, 1, tzinfo=timezone.utc))



@pytest.mark.asyncio
async def test_outcome_waits_until_target_session_is_completed(monkeypatch):
    row = {'ticker': 'TEST', 'observation_id': 'o1', 'observed_at': '2026-10-07T14:00:00Z', 'entry_price': 100}
    collection = MemoryCollection([row])
    calls = []
    async def close(ticker, day, **kwargs):
        calls.append((day, kwargs))
        return 101
    monkeypatch.setattr(pnl_tracker, 'get_db', lambda: SimpleNamespace(strategy_observations=collection))
    monkeypatch.setattr(pnl_tracker, '_today_et', lambda: date(2026, 10, 8))
    monkeypatch.setattr(pricer, 'get_close_on_date', close)
    result = await pnl_tracker.refresh_due_strategy_observations()
    assert result['r1'] == 0 and calls == []
    monkeypatch.setattr(pnl_tracker, '_today_et', lambda: date(2026, 10, 9))
    await pnl_tracker.refresh_due_strategy_observations()
    assert row['return_1d_date'] == '2026-10-08' and calls[0][1] == {'exact': True}



@pytest.mark.asyncio
async def test_exact_outcome_price_does_not_walk_back(monkeypatch):
    async def history(*args, **kwargs):
        return {'2026-10-07': 100}
    monkeypatch.setattr(pricer, 'MASSIVE_KEY', '')
    monkeypatch.setattr(pricer, 'get_history_range', history)
    assert await pricer.get_close_on_date('TEST', '2026-10-08', exact=True) is None



@pytest.mark.asyncio
async def test_daily_pnl_curve_with_measured_gain_does_not_raise(monkeypatch):
    day = pnl_tracker._today_iso()
    collection = MemoryCollection([{'ticker': 'TEST', 'first_seen_date': day, 'first_seen_price': 100}])
    async def history(*args, **kwargs):
        return {'TEST': {day: 105}}
    monkeypatch.setattr(pnl_tracker, 'get_db', lambda: SimpleNamespace(signal_first_seen=collection))
    monkeypatch.setattr(pricer, 'batch_history', history)
    result = await pnl_tracker.daily_pnl_curve()
    assert result[0]['avg_gain_pct'] == 5 and result[0]['is_today']



@pytest.mark.asyncio
async def test_stale_retry_scheduler_tag_reaches_terminal_cycle(monkeypatch):
    from services import terminal_cycle
    calls = []
    async def market_day():
        return True, ''
    async def scan(**kwargs):
        calls.append(kwargs)
        return {}
    async def log(*args, **kwargs):
        pass
    monkeypatch.setattr(scheduler, '_stock_scan_market_day_now', market_day)
    monkeypatch.setattr(terminal_cycle, 'run_full_terminal_scan', scan)
    monkeypatch.setattr(scheduler, 'log_activity', log)
    await scheduler._daily_scan_job(triggered_by='stale_retry_scan_1030')
    assert calls == [{'triggered_by': 'stale_retry_scan_1030'}]
