from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services import (market_dates, options_desk, pm_ratchet, pnl_tracker,
                      postgres_store, pricer, public_execution, safety, scheduler,
                      telegram_service)
from test_claude_audit_exit_recovery import MemoryCollection
from test_scheduler_overlap_guard import _FakeScheduler


@pytest.mark.asyncio
async def test_option_peak_and_locked_floor_survive_gap_below_entry(monkeypatch):
    policy = {'peak_premium': 1.6, 'locked_floor_pct': 20}
    trade = {'_id': 'trade', 'symbol': 'SPY261218C00600000', 'status': 'active',
             'ticker': 'SPY', 'exit_policy': policy}
    class Collection(MemoryCollection):
        async def update_many(self, *args, **kwargs):
            pass
    db = SimpleNamespace(options_desk_orders=Collection(), options_desk_trades=Collection([trade]),
                         options_desk_risk_checks=Collection())
    async def positions():
        return {'positions': [{'symbol': trade['symbol'], 'qty': 1, 'avg_entry_price': 1}]}
    async def snapshot(symbol):
        return {'ok': True, 'bid': .94, 'ask': .96, 'quote_time': datetime.now(timezone.utc).isoformat()}
    async def no_earnings(ticker):
        return None
    monkeypatch.setattr(options_desk, 'get_db', lambda: db)
    monkeypatch.setattr(options_desk, 'positions', positions)
    monkeypatch.setattr(options_desk, '_option_snapshot', snapshot)
    monkeypatch.setattr(options_desk, '_days_to_next_earnings', no_earnings)
    result = await options_desk.monitor_open_positions(enforce_hard_stop=False)
    ratchet = result['checks'][0]['ratchet']
    assert ratchet['peak_premium'] == 1.6
    assert ratchet['locked_floor_pct'] >= 20
    assert ratchet['exit_triggered'] is True


@pytest.mark.asyncio
async def test_stale_option_quote_cannot_trigger_ratchet_from_position_mark(monkeypatch):
    from services import telegram_events
    alerts = []
    async def positions():
        return {'positions': [{'symbol': 'SPY261218C00600000', 'qty': 1, 'current_price': .1}]}
    async def snapshot(symbol):
        return {'ok': True, 'bid': .1, 'ask': .2,
                'quote_time': (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()}
    async def alert(*args, **kwargs):
        alerts.append(kwargs)
    monkeypatch.setattr(options_desk, 'positions', positions)
    monkeypatch.setattr(options_desk, '_option_snapshot', snapshot)
    monkeypatch.setattr(options_desk, 'get_db', lambda: SimpleNamespace(options_desk_risk_checks=MemoryCollection()))
    monkeypatch.setattr(telegram_events, 'emit_event', alert)
    result = await options_desk.monitor_open_positions()
    assert not result['ok'] and not result['checks'] and not result['closed'] and alerts


@pytest.mark.asyncio
async def test_emergency_option_close_rejects_quote_over_one_minute(monkeypatch):
    async def market():
        return {'is_open': True}
    async def snapshot(symbol):
        return {'ok': True, 'bid': 1, 'ask': 1.1,
                'quote_time': (datetime.now(timezone.utc) - timedelta(seconds=61)).isoformat()}
    monkeypatch.setattr(options_desk, 'options_account_route_guard', lambda: {'ok': True})
    monkeypatch.setattr(options_desk, '_options_market_status', market)
    monkeypatch.setattr(options_desk, '_option_snapshot', snapshot)
    result = await options_desk.close('SPY261218C00600000', qty=1, emergency=True)
    assert not result['ok'] and result['reason'] == 'fresh_quote_stale_for_close'


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
async def test_all_unpriced_public_positions_are_not_reported_ok(monkeypatch):
    trade = dict(ticker='TEST', status='OPEN', fill_status='FILLED', qty_remaining=1,
                 broker_base='public', client_order_id='entry', current_stop=9,
                 filled_avg_price=10, pm_ratchet_plan={'enabled': True})
    async def no_prices(tickers):
        return {}
    monkeypatch.setattr(pm_ratchet, 'get_db', lambda: SimpleNamespace(tf_trades=MemoryCollection([trade])))
    monkeypatch.setattr(pm_ratchet, '_public_prices', no_prices)
    result = await pm_ratchet.process_open_ratchets(broker_base='public')
    assert not result['ok'] and result['monitor_status'] == 'DEGRADED'


@pytest.mark.asyncio
async def test_webhook_requires_secret_and_chat_configuration(monkeypatch):
    from fastapi import HTTPException
    from server import telegram_webhook, position_monitor_latest
    monkeypatch.delenv('TELEGRAM_WEBHOOK_SECRET', raising=False)
    with pytest.raises(HTTPException) as exc:
        await telegram_webhook(SimpleNamespace(headers={}), SimpleNamespace())
    assert exc.value.status_code == 403
    monkeypatch.setattr('server.get_db', lambda: SimpleNamespace(bot_state=MemoryCollection()))
    result = await position_monitor_latest()
    assert result['ok'] is False and result['status'] == 'MISSING' and result['totals'] is None


@pytest.mark.asyncio
async def test_telegram_command_requires_configured_chat(monkeypatch):
    monkeypatch.delenv('TELEGRAM_CHAT_ID', raising=False)
    async def unexpected(*args, **kwargs):
        raise AssertionError('Unconfigured command must not dispatch')
    monkeypatch.setattr(telegram_service, 'log_activity', unexpected)
    await telegram_service.handle_update({'message': {'text': '/calls', 'chat': {'id': 'untrusted'}}})


@pytest.mark.asyncio
async def test_daily_loss_breaker_reads_public_and_halts_on_drawdown(monkeypatch):
    actions = []
    async def portfolio():
        return {'ok': True, 'equity': 960}
    async def status():
        return {'daily_loss_breaker': {'date': safety._today_key(), 'day_start_equity': 1000}}
    async def halt(enabled, reason):
        actions.append((enabled, reason))
    from services import telegram_events
    async def emit(*args, **kwargs):
        pass
    monkeypatch.setattr(public_execution, 'portfolio_state', portfolio)
    monkeypatch.setattr(safety, 'trading_status', status)
    monkeypatch.setattr(safety, 'set_trading', halt)
    monkeypatch.setattr(safety, 'get_db', lambda: SimpleNamespace(bot_state=MemoryCollection()))
    monkeypatch.setattr(telegram_events, 'emit_event', emit)
    result = await safety.check_daily_loss()
    assert result['tripped'] and result['drawdown_pct'] == 4 and actions[0][0] is False


@pytest.mark.asyncio
async def test_unavailable_public_equity_halts_during_session(monkeypatch):
    actions = []
    async def portfolio():
        return {'ok': False}
    async def halt(*args):
        actions.append(args)
    monkeypatch.setattr(public_execution, 'portfolio_state', portfolio)
    monkeypatch.setattr(safety, 'set_trading', halt)
    monkeypatch.setattr(market_dates, 'is_core_session', lambda now: True)
    assert not (await safety.check_daily_loss())['ok']
    assert actions == [(False, 'daily_loss_equity_source_unavailable')]


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


def test_numeric_sql_path_uses_superset_instead_of_wrong_array_filter():
    params = []
    sql, exact = postgres_store._sql_filter({'items.0.value': 'yes'}, params)
    assert not exact and sql == 'true' and params == []


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
