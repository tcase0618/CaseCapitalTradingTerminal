"""Mock independent process mutexes sharing PostgreSQL lock sessions."""
import asyncio

import pytest

from services import terminal_cycle
from test_claude_audit_options_lock import sessions  # noqa: F401


@pytest.mark.asyncio
async def test_independent_process_mutexes_do_not_run_duplicate_cycle(monkeypatch, sessions):
    entered, finish = asyncio.Event(), asyncio.Event()
    calls = []
    async def scan(**kwargs):
        calls.append(kwargs)
        if len(calls) > 1:
            return {'ok': True, 'duplicate': True}
        entered.set()
        await finish.wait()
        return {'ok': True}
    monkeypatch.setattr(terminal_cycle, '_FULL_SCAN_LOCK', asyncio.Lock())
    monkeypatch.setattr(terminal_cycle, '_run_full_terminal_scan', scan)
    first = asyncio.create_task(terminal_cycle.run_full_terminal_scan('offline-one'))
    try:
        await entered.wait()
        # Emulate a second process's separate in-memory mutex.
        monkeypatch.setattr(terminal_cycle, '_FULL_SCAN_LOCK', asyncio.Lock())
        second = await terminal_cycle.run_full_terminal_scan('offline-two')
        assert second.get('skipped') and not second.get('ok')
        assert len(calls) == 1
    finally:
        finish.set()
        await first
    assert not sessions[0]


@pytest.mark.asyncio
async def test_unavailable_lock_never_starts_scan(monkeypatch, sessions):
    from services import postgres_store
    monkeypatch.setattr(postgres_store, 'enabled', lambda: False)
    monkeypatch.setattr(terminal_cycle, '_FULL_SCAN_LOCK', asyncio.Lock())
    async def forbidden(**kwargs):
        pytest.fail('No execution cycle without a shared lock')
    monkeypatch.setattr(terminal_cycle, '_run_full_terminal_scan', forbidden)
    result = await terminal_cycle.run_full_terminal_scan('offline')
    assert result.get('skipped') and result['reason'] == 'full_terminal_scan_lock_unavailable'


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [RuntimeError, asyncio.CancelledError])
async def test_failed_or_cancelled_cycle_releases_exact_session(monkeypatch, sessions, error):
    monkeypatch.setattr(terminal_cycle, '_FULL_SCAN_LOCK', asyncio.Lock())
    async def failed(**kwargs):
        assert await kwargs['cycle_lease'].healthy()
        raise error()
    monkeypatch.setattr(terminal_cycle, '_run_full_terminal_scan', failed)
    with pytest.raises(error):
        await terminal_cycle.run_full_terminal_scan('offline')
    assert not sessions[0] and not terminal_cycle._FULL_SCAN_LOCK.locked()


@pytest.mark.asyncio
async def test_unhealthy_session_never_starts_scan(monkeypatch, sessions):
    from services import advisory_locks
    original = advisory_locks.try_acquire
    async def acquire(namespace):
        lease = await original(namespace)
        sessions[1][-1].fail_query = True
        return lease
    monkeypatch.setattr(advisory_locks, 'try_acquire', acquire)
    monkeypatch.setattr(terminal_cycle, '_FULL_SCAN_LOCK', asyncio.Lock())
    async def forbidden(**kwargs):
        pytest.fail('No execution pipeline with a lost session')
    monkeypatch.setattr(terminal_cycle, '_run_full_terminal_scan', forbidden)
    result = await terminal_cycle.run_full_terminal_scan('offline')
    assert result.get('skipped') and result['reason'] == 'full_terminal_scan_lock_lost'
    assert not sessions[0]
