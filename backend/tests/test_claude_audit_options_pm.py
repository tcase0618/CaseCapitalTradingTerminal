"""Offline regression: a scheduled options sweep cannot invent a PM docket."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import options_desk
from test_claude_audit_exit_recovery import MemoryCollection


@pytest.fixture
def pm_case(monkeypatch):
    now = datetime.now(timezone.utc).isoformat()
    row = {'finished_at': now, 'full_cycle_finished_at': now, 'cycle_id': 'offline-pm-cycle',
           'pm_payload': {'scan_finished_at': now, 'recommendations': [{'ticker': 'TEST', 'action': 'STARTER'}]}}
    db = SimpleNamespace(scan_results=MemoryCollection([row]))
    monkeypatch.setattr(options_desk, 'get_db', lambda: db)
    monkeypatch.setattr(options_desk, 'sync_fills', AsyncMock(return_value={}))
    monkeypatch.setattr(options_desk, 'monitor_open_positions', AsyncMock(return_value={}))
    build = AsyncMock(return_value={'candidates': [], 'cycle_id': 'offline-pm-cycle'})
    auto = AsyncMock(return_value={'ok': True, 'submitted': []})
    monkeypatch.setattr(options_desk, 'build_candidates', build)
    monkeypatch.setattr(options_desk, 'auto_execute_latest', auto)
    return row, db, build, auto


@pytest.mark.asyncio
async def test_scheduled_options_uses_frozen_pm_docket(pm_case):
    row, _, build, auto = pm_case
    result = await options_desk.refresh_and_auto_execute_latest()
    assert result['ok']
    assert build.call_args.kwargs['scan'] == row
    assert build.call_args.kwargs['pm_recommendations'] == row['pm_payload']['recommendations']
    assert auto.call_args.kwargs['candidate_set']['cycle_id'] == row['cycle_id']


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['missing', 'incomplete', 'mismatch', 'stale', 'future', 'degraded', 'malformed'])
async def test_unverified_pm_docket_skips_entries(pm_case, case):
    row, db, build, auto = pm_case
    if case == 'missing':
        db.scan_results.rows.clear()
    elif case == 'incomplete':
        row.pop('full_cycle_finished_at')
    elif case == 'mismatch':
        row['pm_payload']['scan_finished_at'] = 'another-scan'
    elif case in {'stale', 'future'}:
        row['finished_at'] = (datetime.now(timezone.utc) + timedelta(hours=-5 if case == 'stale' else 1)).isoformat()
        row['pm_payload']['scan_finished_at'] = row['finished_at']
    elif case == 'degraded':
        row['lock_lost'] = True
    else:
        row['pm_payload']['recommendations'] = 'not-a-docket'
    result = await options_desk.refresh_and_auto_execute_latest()
    assert not result['ok'] and result.get('skipped')
    build.assert_not_awaited()
    auto.assert_not_awaited()


@pytest.mark.asyncio
async def test_authoritative_candidate_retains_cycle_without_recomputing_pm(monkeypatch):
    from services import portfolio_manager, options_contract_learning
    db = SimpleNamespace(options_lane_throttle=MemoryCollection())
    monkeypatch.setattr(options_desk, 'get_db', lambda: db)
    recompute = AsyncMock(side_effect=AssertionError('must not recompute PM'))
    monkeypatch.setattr(portfolio_manager, 'evaluate_rows', recompute)
    monkeypatch.setattr(options_contract_learning, 'record_selection', AsyncMock(return_value=None))
    scan = {'cycle_id': 'frozen-cycle', 'finished_at': '2026-10-19T12:00:00+00:00', 'results': []}
    result = await options_desk.build_candidates(persist=False, scan=scan,
        pm_recommendations=[{'ticker': 'TEST', 'action': 'WATCH', 'pm_score': 10}])
    assert result['cycle_id'] == 'frozen-cycle'
    assert result['candidates'][0]['cycle_id'] == 'frozen-cycle'
    recompute.assert_not_called()


@pytest.mark.asyncio
async def test_option_entry_record_retains_candidate_cycle(monkeypatch):
    from services import safety, execution_gate
    now = datetime.now(timezone.utc).isoformat()
    ticket = {'candidate_id': 'offline-entry', 'ticker': 'TEST', 'cycle_id': 'frozen-cycle',
              'scan_finished_at': now,
              'manual_fire_ready': True, 'pm_action': 'STARTER', 'contracts': 1, 'risk_budget': 200,
              'data_provider': 'ALPACA_OPTIONS', 'data_quality': 'REALTIME',
              'instrument': {'kind': 'single_leg', 'symbol': 'TEST261218C00100000'}}
    scan = {'cycle_id': 'frozen-cycle', 'finished_at': now, 'full_cycle_finished_at': now,
            'pm_payload': {'scan_finished_at': now, 'recommendations': [{'ticker': 'TEST', 'action': 'STARTER'}]}}
    db = SimpleNamespace(options_desk_candidates=MemoryCollection([ticket]), options_desk_orders=MemoryCollection(),
                         scan_results=MemoryCollection([scan]))
    monkeypatch.setattr(options_desk, 'get_db', lambda: db)
    monkeypatch.setattr(safety, 'trading_enabled', AsyncMock(return_value=(True, {})))
    monkeypatch.setattr(execution_gate, 'check', AsyncMock(return_value={'ok': True}))
    monkeypatch.setattr(options_desk, 'options_account_route_guard', lambda: {'ok': True})
    monkeypatch.setattr(options_desk, 'OPTIONS_EXECUTION_ENABLED', True)
    monkeypatch.setattr(options_desk, 'paper_only', lambda: True)
    monkeypatch.setattr(options_desk, '_options_market_status', AsyncMock(return_value={'is_open': True}))
    monkeypatch.setattr(options_desk, '_execution_grade_allowed', lambda *_args: True)
    monkeypatch.setattr(options_desk, '_fresh_execution_preflight', AsyncMock(return_value={
        'ok': True, 'symbol': ticket['instrument']['symbol'], 'instrument': ticket['instrument']}))
    monkeypatch.setattr(options_desk, '_execution_limit_from_quote', lambda *args, **kwargs: ({'limit_price': 1}, None))
    monkeypatch.setattr(options_desk, 'daily_premium_used', AsyncMock(return_value=0))
    monkeypatch.setattr(options_desk, '_symbol_already_exposed', AsyncMock(return_value={'blocked': False}))
    monkeypatch.setattr(options_desk, 'orders', AsyncMock(return_value={'orders': []}))
    monkeypatch.setattr(options_desk, '_options_headers', lambda: {})
    class Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, *args, **kwargs):
            return SimpleNamespace(status_code=201, json=lambda: {'id': 'offline-order'})
    monkeypatch.setattr(options_desk.httpx, 'AsyncClient', lambda **kwargs: Client())
    assert (await options_desk.execute(ticket['candidate_id']))['ok']
    assert db.options_desk_orders.rows[0]['cycle_id'] == 'frozen-cycle'
