"""Offline scheduler/cycle evidence regressions for MED-018 and MED-019."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import scheduler, terminal_cycle, telegram_events
from test_claude_audit_exit_recovery import MemoryCollection


@pytest.mark.asyncio
@pytest.mark.parametrize('reason', ['full_terminal_scan_lock_unavailable', 'full_terminal_scan_already_running'])
async def test_skipped_scheduled_cycle_is_recorded_and_not_complete(monkeypatch, reason):
    db = SimpleNamespace(scheduler_runs=MemoryCollection())
    activity, event = AsyncMock(), AsyncMock()
    monkeypatch.setattr(scheduler, 'get_db', lambda: db)
    monkeypatch.setattr(scheduler, 'log_activity', activity)
    monkeypatch.setattr(scheduler, '_stock_scan_market_day_now', AsyncMock(return_value=(True, 'offline session')))
    monkeypatch.setattr(terminal_cycle, 'run_full_terminal_scan', AsyncMock(return_value={'ok': False, 'skipped': True, 'reason': reason}))
    monkeypatch.setattr(telegram_events, 'emit_event', event)
    await scheduler._daily_scan_job('offline-scheduled')
    assert len(db.scheduler_runs.rows) == 1
    assert db.scheduler_runs.rows[0]['status'] == 'SKIPPED'
    assert db.scheduler_runs.rows[0]['reason'] == reason
    assert not any('complete' in str(call.args[0]).lower() for call in activity.call_args_list)
    assert event.await_count == (0 if reason.endswith('already_running') else 1)


@pytest.fixture
def cycle_case(monkeypatch):
    from services import (accountant_research, candidate_ledger, lottery, options_desk, pharma,
                          pm_brain, portfolio_manager, scanner, strategy_contracts, strategy_screeners,
                          public_execution, pnl_tracker)
    scan = {'finished_at': '2026-10-15T08:00:00+00:00', 'cycle_id': 'offline-cycle', 'results': []}
    db = SimpleNamespace(scan_results=MemoryCollection([scan]))
    monkeypatch.setattr(terminal_cycle, 'get_db', lambda: db)
    monkeypatch.setattr(terminal_cycle, 'log_activity', AsyncMock())
    for module, name, result in [
        (scanner, 'run_scan', dict(scan)), (lottery, 'run_dedicated_lottery_scan', {}),
        (pharma, 'run_pharma_scan', {}), (pharma, 'run_catalyst_shock_scan', {}),
        (strategy_screeners, 'run_all', {}), (accountant_research, 'enrich_scan_candidates', {}),
        (pnl_tracker, 'record_scan_picks', {}),
        (portfolio_manager, 'latest_portfolio_plan', {'scan_finished_at': scan['finished_at'], 'recommendations': []}),
        (pm_brain, 'record_pm_cycle', {}), (options_desk, 'build_candidates', {}),
        (candidate_ledger, 'build_from_scan', {}), (strategy_contracts, 'build_shadow_contracts', {}),
        (public_execution, 'refresh_execution_freshness', {'ok': True}),
    ]:
        monkeypatch.setattr(module, name, AsyncMock(return_value=result))
    equity = AsyncMock(return_value={'executed': [{'order_id': 'offline-order'}], 'rejected': []})
    options = AsyncMock(return_value={'submitted': []})
    funnel, retry, report = AsyncMock(return_value={'summary': {}}), AsyncMock(return_value={'queued': 0}), AsyncMock(return_value={'sent': True})
    monkeypatch.setattr(public_execution, 'execute_pm_equity', equity)
    monkeypatch.setattr(public_execution, 'enabled', lambda: True)
    monkeypatch.setattr(options_desk, 'options_execution_enabled', lambda: True)
    monkeypatch.setattr(options_desk, 'auto_execute_latest', options)
    monkeypatch.setattr(terminal_cycle, '_persist_execution_funnel', funnel)
    monkeypatch.setattr(terminal_cycle, '_persist_stale_execution_retries', retry)
    monkeypatch.setattr(telegram_events, 'dispatch_scan_report', report)
    # Fixture-only switches/identities; network is blocked by the test runner.
    monkeypatch.setenv('ENABLE_TRADE_EXECUTION', 'true')
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'offline-dummy')
    monkeypatch.setenv('TELEGRAM_CHAT_ID', 'offline-dummy')
    return db, equity, options, funnel, retry, report


@pytest.mark.asyncio
async def test_lost_lock_after_equity_keeps_funnel_cycle_and_report(cycle_case):
    db, equity, options, funnel, retry, report = cycle_case
    lease = SimpleNamespace(healthy=AsyncMock(side_effect=[True, True, False]))
    result = await terminal_cycle._run_full_terminal_scan('offline', cycle_lease=lease)
    equity.assert_awaited_once()
    options.assert_not_awaited()
    funnel.assert_awaited_once()
    retry.assert_awaited_once()
    report.assert_awaited_once()
    persisted = db.scan_results.rows[0]
    assert persisted['lock_lost'] and persisted['execution_summary']['equity_executed'] == 1
    assert report.call_args.args[0]['lock_lost']
    assert result['lock_lost'] and not result['ok'] and not result.get('skipped')


@pytest.mark.asyncio
@pytest.mark.parametrize('health', [[False], [True, False]])
async def test_lock_lost_before_equity_blocks_execution_but_keeps_cycle(cycle_case, health):
    db, equity, options, funnel, retry, report = cycle_case
    lease = SimpleNamespace(healthy=AsyncMock(side_effect=health))
    result = await terminal_cycle._run_full_terminal_scan('offline', cycle_lease=lease)
    equity.assert_not_awaited()
    options.assert_not_awaited()
    funnel.assert_awaited_once()
    retry.assert_awaited_once()
    report.assert_awaited_once()
    assert db.scan_results.rows[0]['lock_lost'] and not result['ok']


@pytest.mark.asyncio
async def test_healthy_cycle_preserves_normal_execution_and_report(cycle_case):
    db, equity, options, funnel, retry, report = cycle_case
    result = await terminal_cycle._run_full_terminal_scan('offline', cycle_lease=SimpleNamespace(healthy=AsyncMock(return_value=True)))
    assert result['ok'] and not result['lock_lost']
    equity.assert_awaited_once()
    options.assert_awaited_once()
    funnel.assert_awaited_once()
    report.assert_awaited_once()
    assert not db.scan_results.rows[0]['lock_lost']


@pytest.mark.asyncio
async def test_failed_scheduler_persistence_still_alerts(monkeypatch):
    failing = AsyncMock(side_effect=RuntimeError('offline storage failure'))
    monkeypatch.setattr(scheduler, 'get_db', lambda: SimpleNamespace(scheduler_runs=SimpleNamespace(insert_one=failing)))
    monkeypatch.setattr(scheduler, 'log_activity', AsyncMock())
    monkeypatch.setattr(scheduler, '_stock_scan_market_day_now', AsyncMock(return_value=(True, 'offline session')))
    monkeypatch.setattr(terminal_cycle, 'run_full_terminal_scan', AsyncMock(return_value={'ok': False, 'skipped': True, 'reason': 'full_terminal_scan_lock_unavailable'}))
    event = AsyncMock()
    monkeypatch.setattr(telegram_events, 'emit_event', event)
    result = await scheduler._daily_scan_job('offline')
    assert result['skipped']
    event.assert_awaited_once()


def test_consolidated_report_visibly_flags_lost_execution_lock():
    text = telegram_events._consolidated_scan_report_text(
        {'lock_lost': True}, results=[], lottery={}, pharma={}, shocks={}, screener_summary={},
        new_scan={}, pm={}, pm_rows=[], routes={'EQUITY': 0, 'OPTION': 0, 'BOTH': 0, 'WATCH': 0, 'REJECT': 0},
        pm_actions={'ACCUMULATE': 0, 'STARTER': 0, 'WATCH': 0, 'REJECT': 0},
        opt_summary={}, gate={}, qc={}, edge={}, blockers=0, execution_summary={})
    assert 'DEGRADED: execution lock lost' in text


@pytest.mark.asyncio
@pytest.mark.parametrize('health', [[False], [True, False], [True, True, False]])
async def test_1030_lock_loss_preserves_pending_retry(cycle_case, monkeypatch, health):
    from services import portfolio_manager
    db, equity, options, _, _, report = cycle_case
    pending = {'retry_key': 'offline-retry', 'ticker': 'TEST', 'status': 'PENDING_1030_FULL_RESCAN'}
    db.stale_execution_retries = MemoryCollection([pending])
    monkeypatch.setattr(portfolio_manager, 'latest_portfolio_plan', AsyncMock(return_value={
        'scan_finished_at': '2026-10-15T08:00:00+00:00',
        'recommendations': [{'ticker': 'TEST', 'action': 'STARTER'}]}))
    result = await terminal_cycle._run_full_terminal_scan(
        'stale_retry_scan_1030', cycle_lease=SimpleNamespace(healthy=AsyncMock(side_effect=health)))
    assert pending['status'] == 'PENDING_1030_FULL_RESCAN'
    assert result['stale_execution_retry_resolution']['retried'] == 0
    assert result['stale_execution_retry_resolution']['reason'] == 'full_terminal_scan_lock_lost'
    report.assert_awaited_once()


@pytest.mark.asyncio
async def test_retry_resolver_itself_does_not_consume_skipped_execution(monkeypatch):
    pending = {'retry_key': 'offline-retry', 'ticker': 'TEST', 'status': 'PENDING_1030_FULL_RESCAN'}
    db = SimpleNamespace(stale_execution_retries=MemoryCollection([pending]))
    monkeypatch.setattr(terminal_cycle, 'get_db', lambda: db)
    result = await terminal_cycle._resolve_stale_execution_retries(
        pm_rows=[{'ticker': 'TEST', 'action': 'STARTER'}],
        equity_execution={'skipped': True, 'reason': 'full_terminal_scan_lock_lost', 'executed': [], 'rejected': []},
        cycle_id='offline-cycle')
    assert pending['status'] == 'PENDING_1030_FULL_RESCAN'
    assert result['retried'] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('executed,reason,status', [
    (True, None, 'EXECUTED_1030'),
    (False, 'public_execution_quote_stale_or_unverifiable', 'STALE_AGAIN_1030'),
    (False, 'safety_halt', 'REJECTED_1030'),
])
async def test_attempted_retry_still_records_actual_outcome(monkeypatch, executed, reason, status):
    pending = {'retry_key': '2026-10-18:TEST:CORE', 'ticker': 'TEST', 'status': 'PENDING_1030_FULL_RESCAN'}
    monkeypatch.setattr(terminal_cycle, 'get_db', lambda: SimpleNamespace(stale_execution_retries=MemoryCollection([pending])))
    result = await terminal_cycle._resolve_stale_execution_retries(
        pm_rows=[{'ticker': 'TEST', 'action': 'STARTER'}],
        equity_execution={'executed': [{'ticker': 'TEST'}] if executed else [],
                          'rejected': [] if executed else [{'ticker': 'TEST', 'reason': reason}]},
        cycle_id='offline-cycle', observed_at='2026-10-18T14:30:00+00:00')
    assert pending['status'] == status
    assert result['retried'] == 1 and result['executed'] == int(executed)


@pytest.mark.asyncio
@pytest.mark.parametrize('skipped', [False, True])
async def test_prior_day_retries_cannot_borrow_later_execution(monkeypatch, skipped):
    rows = [dict(retry_key=f'{day}:TEST:CORE', ticker='TEST', status='PENDING_1030_FULL_RESCAN')
            for day in ['2026-10-17', '2026-10-18', '2026-10-19']]
    monkeypatch.setattr(terminal_cycle, 'get_db', lambda: SimpleNamespace(stale_execution_retries=MemoryCollection(rows)))
    result = await terminal_cycle._resolve_stale_execution_retries(
        pm_rows=[{'ticker': 'TEST', 'action': 'STARTER'}],
        equity_execution={'skipped': skipped, 'reason': 'pm_scan_mismatch', 'executed': [] if skipped else [{'ticker': 'TEST'}]},
        cycle_id='day2', observed_at='2026-10-18T14:30:00+00:00')
    assert rows[0]['status'] == 'EXPIRED_UNRESOLVED'
    assert 'retry_cycle_id' not in rows[0]
    assert rows[1]['status'] == ('PENDING_1030_FULL_RESCAN' if skipped else 'EXECUTED_1030')
    assert rows[2]['status'] == 'PENDING_1030_FULL_RESCAN'
    assert result['retried'] == (0 if skipped else 1)


@pytest.mark.asyncio
async def test_old_retry_backlog_does_not_starve_today(monkeypatch):
    rows = [dict(retry_key=f'2026-10-17:OLD{i}:CORE', ticker=f'OLD{i}', status='PENDING_1030_FULL_RESCAN') for i in range(501)]
    today = dict(retry_key='2026-10-18:TEST:CORE', ticker='TEST', status='PENDING_1030_FULL_RESCAN')
    rows.append(today)
    monkeypatch.setattr(terminal_cycle, 'get_db', lambda: SimpleNamespace(stale_execution_retries=MemoryCollection(rows)))
    result = await terminal_cycle._resolve_stale_execution_retries(pm_rows=[{'ticker': 'TEST', 'action': 'STARTER'}],
        equity_execution={'executed': [{'ticker': 'TEST'}]}, cycle_id='day2', observed_at='2026-10-18T14:30:00+00:00')
    assert today['status'] == 'EXECUTED_1030' and result['retried'] == 1
