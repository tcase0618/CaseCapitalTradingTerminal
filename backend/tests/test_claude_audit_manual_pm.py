"""Manual entry must resolve a current persisted PM decision, offline only."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import execution_gate, options_desk, safety
from test_claude_audit_exit_recovery import MemoryCollection


@pytest.fixture
def manual_pm(monkeypatch):
    now = datetime.now(timezone.utc).isoformat()
    ticket = {'candidate_id': 'offline', 'ticker': 'TEST', 'cycle_id': 'offline-cycle',
              'scan_finished_at': now, 'pm_action': 'STARTER', 'manual_fire_ready': True}
    scan = {'cycle_id': ticket['cycle_id'], 'finished_at': now, 'full_cycle_finished_at': now,
            'pm_payload': {'scan_finished_at': now, 'recommendations': [
                {'ticker': 'TEST', 'action': 'STARTER', 'option_view': 'CALL_ALLOWED'}]}}
    db = SimpleNamespace(options_desk_candidates=MemoryCollection([ticket]),
                         scan_results=MemoryCollection([scan]))
    monkeypatch.setattr(options_desk, 'get_db', lambda: db)
    monkeypatch.setattr(safety, 'trading_enabled', AsyncMock(return_value=(True, {})))
    gate = AsyncMock(return_value={'ok': False})
    monkeypatch.setattr(execution_gate, 'check', gate)
    return ticket, scan, db, gate


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['missing', 'different-cycle', 'incomplete', 'lock-lost',
    'bad-time', 'stale', 'future', 'ticket-time', 'pm-time', 'missing-pm', 'malformed-pm',
    'missing-ticker', 'watch', 'changed-action', 'stock-only', 'bad-completion',
    'future-completion', 'completion-before-scan', 'duplicate-pm', 'naive-time', 'non-dict-pm'])
async def test_unresolved_manual_authority_stops_before_execution_gate(manual_pm, case):
    ticket, scan, db, gate = manual_pm
    if case == 'missing':
        db.scan_results.rows.clear()
    elif case == 'different-cycle':
        scan['cycle_id'] = 'new-cycle'
    elif case == 'incomplete':
        scan.pop('full_cycle_finished_at')
    elif case == 'lock-lost':
        scan['lock_lost'] = True
    elif case in {'bad-time', 'stale', 'future'}:
        stamp = 'bad' if case == 'bad-time' else (
            datetime.now(timezone.utc) + timedelta(hours=-5 if case == 'stale' else 1)).isoformat()
        scan['finished_at'] = ticket['scan_finished_at'] = scan['pm_payload']['scan_finished_at'] = stamp
    elif case == 'ticket-time':
        ticket['scan_finished_at'] = 'other-time'
    elif case == 'pm-time':
        scan['pm_payload']['scan_finished_at'] = 'other-time'
    elif case == 'missing-pm':
        scan.pop('pm_payload')
    elif case == 'non-dict-pm':
        scan['pm_payload'] = ['bad-docket']
    elif case == 'naive-time':
        stamp = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        scan['finished_at'] = ticket['scan_finished_at'] = scan['pm_payload']['scan_finished_at'] = stamp
    elif case == 'malformed-pm':
        scan['pm_payload']['recommendations'] = 'not-a-docket'
    elif case == 'missing-ticker':
        scan['pm_payload']['recommendations'][0]['ticker'] = 'OTHER'
    elif case == 'watch':
        scan['pm_payload']['recommendations'][0]['action'] = ticket['pm_action'] = 'WATCH'
    elif case == 'changed-action':
        scan['pm_payload']['recommendations'][0]['action'] = 'ACCUMULATE'
    elif case == 'stock-only':
        scan['pm_payload']['recommendations'][0]['option_view'] = 'STOCK_ONLY'
    elif case == 'duplicate-pm':
        scan['pm_payload']['recommendations'] *= 2
    else:
        scan['full_cycle_finished_at'] = 'bad' if case == 'bad-completion' else (
            datetime.now(timezone.utc) + timedelta(hours=1 if case == 'future-completion' else -1)).isoformat()
    result = await options_desk.execute('offline')
    assert result['reason'] == 'manual_pm_cycle_unavailable_or_stale'
    gate.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_manual_pm_reaches_existing_execution_gate(manual_pm):
    _, _, _, gate = manual_pm
    result = await options_desk.execute('offline')
    assert result['reason'] == 'execution_gate_blocked'
    gate.assert_awaited_once()


@pytest.mark.asyncio
async def test_pm_read_error_fails_closed(manual_pm, monkeypatch):
    _, _, db, gate = manual_pm
    monkeypatch.setattr(db.scan_results, 'find_one', AsyncMock(side_effect=RuntimeError('offline read failure')))
    result = await options_desk.execute('offline')
    assert result['reason'] == 'manual_pm_cycle_unavailable_or_stale'
    gate.assert_not_awaited()
