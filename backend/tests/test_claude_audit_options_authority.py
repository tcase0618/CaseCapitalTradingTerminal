"""Offline options entry authority; execution handlers are harmless mocks."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import execution_gate, options_desk, safety
from test_strategy_evidence import Collection


@pytest.fixture
def authority_case(monkeypatch):
    ticket = {'candidate_id': 'offline-candidate', 'ticker': 'TEST', 'manual_fire_ready': True,
              'cycle_id': 'offline-cycle', 'scan_finished_at': 'offline-scan',
              'instrument': {'symbol': 'TEST261218C00100000'}}
    db = SimpleNamespace(options_desk_candidates=Collection([ticket]), options_desk_orders=Collection())
    monkeypatch.setattr(options_desk, 'get_db', lambda: db)
    monkeypatch.setattr(safety, 'trading_enabled', AsyncMock(return_value=(True, {})))
    monkeypatch.setattr(execution_gate, 'check', AsyncMock(return_value={'ok': True}))
    monkeypatch.setattr(options_desk, 'OPTIONS_EXECUTION_ENABLED', True)
    monkeypatch.setattr(options_desk, '_auto_orders_submitted_today', AsyncMock(return_value=0))
    monkeypatch.setattr(options_desk, 'log_activity', AsyncMock(return_value=None))
    monkeypatch.setattr(options_desk, '_cached_candidates_stale', AsyncMock(return_value=False))
    build = AsyncMock(side_effect=AssertionError('independent PM rebuild is forbidden'))
    monkeypatch.setattr(options_desk, 'build_candidates', build)
    return ticket, db, SimpleNamespace(healthy=AsyncMock(return_value=True)), build


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['missing', 'empty-cycle', 'mismatched-ticket', 'missing-ticket-cycle'])
async def test_auto_refuses_uncoordinated_candidate_sets(authority_case, monkeypatch, case):
    ticket, _, lease, build = authority_case
    execute = AsyncMock(return_value={'ok': False, 'reason': 'offline-handler'})
    monkeypatch.setattr(options_desk, 'execute', execute)
    batch = {'cycle_id': ticket['cycle_id'], 'candidates': [dict(ticket)]}
    if case == 'missing':
        batch = None
    elif case == 'empty-cycle':
        batch['cycle_id'] = ''
    elif case == 'mismatched-ticket':
        batch['candidates'][0]['cycle_id'] = 'different-cycle'
    else:
        batch['candidates'][0].pop('cycle_id')
    result = await options_desk._auto_execute_latest_locked(candidate_set=batch, lease=lease)
    assert result['ok'] is False
    assert result['reason'] == 'coordinated_pm_candidate_set_required'
    execute.assert_not_awaited()
    build.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_passes_frozen_cycle_identity_to_entry_handler(authority_case, monkeypatch):
    ticket, _, lease, _ = authority_case
    execute = AsyncMock(return_value={'ok': False, 'reason': 'offline-handler'})
    monkeypatch.setattr(options_desk, 'execute', execute)
    await options_desk._auto_execute_latest_locked(
        candidate_set={'cycle_id': ticket['cycle_id'], 'candidates': [ticket]}, lease=lease,
    )
    execute.assert_awaited_once_with(ticket['candidate_id'], expected_cycle_id=ticket['cycle_id'])


@pytest.mark.asyncio
@pytest.mark.parametrize('cycle', [None, '', '   ', 0])
async def test_manual_entry_without_cycle_stops_before_execution_gate(authority_case, monkeypatch, cycle):
    _, db, _, _ = authority_case
    db.options_desk_candidates.rows[0]['cycle_id'] = cycle
    gate = AsyncMock(side_effect=AssertionError('entry gate must not run without cycle attribution'))
    monkeypatch.setattr(execution_gate, 'check', gate)
    result = await options_desk.execute('offline-candidate')
    assert result['ok'] is False
    assert result['reason'] == 'candidate_pm_cycle_missing_or_changed'
    gate.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_persisted_cycle_stops_before_preflight(authority_case, monkeypatch):
    gate = AsyncMock(side_effect=AssertionError('changed cycle must stop before broker checks'))
    monkeypatch.setattr(execution_gate, 'check', gate)
    result = await options_desk.execute('offline-candidate', expected_cycle_id='previous-cycle')
    assert result['ok'] is False
    assert result['reason'] == 'candidate_pm_cycle_missing_or_changed'
    gate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('allowed', [True, False])
async def test_auto_api_uses_persisted_pm_workflow_not_unattributed_cache(monkeypatch, allowed):
    import server
    from services import execution_safety

    monkeypatch.setattr(execution_safety, 'add_risk_allowed', AsyncMock(return_value=(allowed, {})))
    refresh = AsyncMock(return_value={'ok': True, 'submitted': []})
    monkeypatch.setattr(options_desk, 'refresh_and_auto_execute_latest', refresh)
    raw_auto = AsyncMock(side_effect=AssertionError('API must not execute an unattributed cache'))
    monkeypatch.setattr(options_desk, 'auto_execute_latest', raw_auto)
    result = await server.options_desk_auto_execute_latest(limit=2)
    if allowed:
        refresh.assert_awaited_once_with(limit=2)
        assert result['ok']
    else:
        refresh.assert_not_awaited()
        assert result['reason'] == 'safety_halt'
    raw_auto.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_persisted_pm_build_flows_through_batch_validator(monkeypatch):
    from datetime import datetime, timezone
    from services import options_contract_learning, portfolio_manager, telegram_events

    class Storage(Collection):
        async def delete_many(self, query):
            self.rows.clear()

        async def insert_many(self, rows):
            self.rows.extend(rows)

    now = datetime.now(timezone.utc).isoformat()
    scan = {'finished_at': now, 'full_cycle_finished_at': now, 'cycle_id': 'pipeline-cycle',
            'results': [], 'pm_payload': {'scan_finished_at': now, 'recommendations': [
                {'ticker': 'TEST', 'action': 'WATCH', 'pm_score': 10}]}}
    class Scans(Collection):
        async def find_one(self, query, projection=None, **kwargs):
            return scan

    db = SimpleNamespace(scan_results=Scans(), options_lane_throttle=Storage(),
                         options_desk_candidates=Storage(), options_desk_candidate_history=Storage(),
                         options_desk_orders=Storage())
    monkeypatch.setattr(options_desk, 'get_db', lambda: db)
    monkeypatch.setattr(options_desk, 'OPTIONS_EXECUTION_ENABLED', True)
    monkeypatch.setattr(safety, 'trading_enabled', AsyncMock(return_value=(True, {})))
    monkeypatch.setattr(execution_gate, 'check', AsyncMock(return_value={'ok': True}))
    for name in ['sync_fills', 'monitor_open_positions', 'log_activity', '_release_auto_execute_lock']:
        monkeypatch.setattr(options_desk, name, AsyncMock(return_value={}))
    lease = SimpleNamespace(healthy=AsyncMock(return_value=True))
    monkeypatch.setattr(options_desk, '_acquire_auto_execute_lock', AsyncMock(return_value={'ok': True, 'lease': lease}))
    monkeypatch.setattr(options_desk, '_auto_orders_submitted_today', AsyncMock(return_value=0))
    monkeypatch.setattr(options_contract_learning, 'record_selection', AsyncMock(return_value=None))
    monkeypatch.setattr(telegram_events, 'dispatch_options_execution_report', AsyncMock())
    recompute = AsyncMock(side_effect=AssertionError('PM authority must not be recomputed'))
    monkeypatch.setattr(portfolio_manager, 'evaluate_rows', recompute)
    execute = AsyncMock(side_effect=AssertionError('WATCH is not an order'))
    monkeypatch.setattr(options_desk, 'execute', execute)
    result = await options_desk.refresh_and_auto_execute_latest()
    assert result['ok']
    assert result.get('reason') != 'coordinated_pm_candidate_set_required'
    assert db.options_desk_candidates.rows[0]['cycle_id'] == 'pipeline-cycle'
    assert result['submitted'] == []
    execute.assert_not_awaited()
    recompute.assert_not_called()
