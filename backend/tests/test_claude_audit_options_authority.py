"""Offline options entry authority; execution handlers are harmless mocks."""
from copy import deepcopy
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
    execute.assert_awaited_once_with(ticket['candidate_id'], expected_cycle_id=ticket['cycle_id'],
                                   expected_candidate=ticket)


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
@pytest.mark.parametrize('field,value', [
    ('ticker', 'OTHER'), ('instrument', {'symbol': 'OTHER261218C00100000'}),
    ('risk_budget', 1000), ('contracts', 10), ('pm_action', 'ACCUMULATE'),
    ('route', 'BOTH'), ('strategy', 'DIFFERENT'), ('scan_finished_at', 'other-scan'),
])
async def test_same_cycle_reload_cannot_change_batch_authority(authority_case, monkeypatch, field, value):
    ticket, db, lease, _ = authority_case
    batch_ticket = deepcopy(ticket)
    db.options_desk_candidates.rows[0][field] = value
    gate = AsyncMock(side_effect=lambda **kwargs: {'ok': kwargs.get('ticker') is None})
    monkeypatch.setattr(execution_gate, 'check', gate)
    result = await options_desk._auto_execute_latest_locked(
        candidate_set={'cycle_id': batch_ticket['cycle_id'], 'candidates': [batch_ticket]}, lease=lease)
    assert result['skipped'][0]['reason'] == 'candidate_batch_identity_changed_or_missing'
    assert gate.await_count == 1  # Root gate only; never the reloaded entry's broker checks.


@pytest.mark.asyncio
async def test_batch_is_frozen_before_gate_await(authority_case, monkeypatch):
    ticket, _, lease, _ = authority_case
    ticket['risk_budget'] = 200
    original = deepcopy(ticket)
    async def change_input(**kwargs):
        ticket['risk_budget'] = 1000
        ticket['instrument']['symbol'] = 'OTHER261218C00100000'
        return {'ok': True}
    monkeypatch.setattr(execution_gate, 'check', change_input)
    execute = AsyncMock(return_value={'ok': False, 'reason': 'offline-handler'})
    monkeypatch.setattr(options_desk, 'execute', execute)
    await options_desk._auto_execute_latest_locked(
        candidate_set={'cycle_id': ticket['cycle_id'], 'candidates': [ticket]}, lease=lease)
    assert execute.call_args.kwargs['expected_candidate'] == original


@pytest.mark.asyncio
@pytest.mark.parametrize('supplied', [True, False])
async def test_automatic_entry_requires_matching_full_snapshot(authority_case, monkeypatch, supplied):
    ticket, _, _, _ = authority_case
    gate = AsyncMock(return_value={'ok': False})
    monkeypatch.setattr(execution_gate, 'check', gate)
    result = await options_desk.execute(ticket['candidate_id'], expected_cycle_id=ticket['cycle_id'],
                                       expected_candidate=deepcopy(ticket) if supplied else None)
    assert result['reason'] == ('execution_gate_blocked' if supplied else 'candidate_batch_identity_changed_or_missing')
    assert gate.await_count == int(supplied)
