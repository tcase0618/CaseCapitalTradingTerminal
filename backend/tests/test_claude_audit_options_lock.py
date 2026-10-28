"""Offline regression for the legacy read-before-write acquisition race."""
import asyncio
from types import SimpleNamespace

import pytest

from services import options_desk


@pytest.fixture
def sessions(monkeypatch):
    from services import postgres_store
    holders, connections = {}, []
    class Connection:
        key = None
        closed = False
        fail_close = False
        fail_query = False
        async def fetchval(self, sql, *args, **kwargs):
            await asyncio.sleep(0)
            if self.closed or self.fail_query:
                raise ConnectionError('offline session lost')
            if sql == 'select 1':
                return 1
            assert sql == 'select pg_try_advisory_lock($1::bigint)'
            key = args[0]
            if key in holders:
                return False
            holders[key] = self
            self.key = key
            return True
        def is_closed(self):
            return self.closed
        def terminate(self):
            self.closed = True
            if holders.get(self.key) is self:
                holders.pop(self.key)
        async def close(self, **kwargs):
            if self.fail_close:
                raise TimeoutError('offline close timeout')
            self.terminate()
    async def connect(*args, **kwargs):
        connection = Connection()
        connections.append(connection)
        return connection
    monkeypatch.setattr(postgres_store, 'enabled', lambda: True)
    monkeypatch.setattr(postgres_store, 'dsn', lambda: 'offline-session-fixture')
    monkeypatch.setattr(postgres_store, 'asyncpg', SimpleNamespace(connect=connect))
    yield holders, connections
    for connection in connections:
        connection.terminate()


@pytest.mark.asyncio
async def test_two_legacy_acquires_cannot_both_claim_empty_lock(monkeypatch, sessions):
    reads = 0
    both_read = asyncio.Event()
    class State:
        async def find_one(self, *args):
            nonlocal reads
            reads += 1
            if reads == 2:
                both_read.set()
            await both_read.wait()
            return None
        async def update_one(self, *args, **kwargs):
            return None
    monkeypatch.setattr(options_desk, 'get_db', lambda: SimpleNamespace(bot_state=State()))
    results = await asyncio.gather(options_desk._acquire_auto_execute_lock(), options_desk._acquire_auto_execute_lock())
    assert sum(bool(result.get('ok')) for result in results) == 1


@pytest.mark.asyncio
async def test_stale_release_cannot_clear_new_holder(sessions):
    first = await options_desk._acquire_auto_execute_lock()
    await options_desk._release_auto_execute_lock(first)
    second = await options_desk._acquire_auto_execute_lock()
    await options_desk._release_auto_execute_lock(first)
    assert await second['lease'].healthy()
    assert not (await options_desk._acquire_auto_execute_lock())['ok']
    await options_desk._release_auto_execute_lock(second)
    assert (await options_desk._acquire_auto_execute_lock())['ok']


@pytest.mark.asyncio
async def test_session_loss_marks_lock_unhealthy(sessions):
    _, connections = sessions
    lock = await options_desk._acquire_auto_execute_lock()
    connections[0].fail_query = True
    assert not await lock['lease'].healthy()


@pytest.mark.asyncio
async def test_failed_close_terminates_exact_owner_session(sessions):
    holders, connections = sessions
    lock = await options_desk._acquire_auto_execute_lock()
    connections[0].fail_close = True
    await options_desk._release_auto_execute_lock(lock)
    assert connections[0].closed and not holders


@pytest.mark.asyncio
async def test_unavailable_database_fails_closed(monkeypatch, sessions):
    from services import postgres_store
    monkeypatch.setattr(postgres_store, 'enabled', lambda: False)
    result = await options_desk._acquire_auto_execute_lock()
    assert not result['ok'] and result['reason'] == 'options_auto_execute_lock_unavailable'


@pytest.mark.asyncio
async def test_wrapper_releases_session_on_failure_and_cancellation(monkeypatch, sessions):
    holders, _ = sessions
    async def failed(**kwargs):
        assert await kwargs['lease'].healthy()
        raise asyncio.CancelledError()
    monkeypatch.setattr(options_desk, '_auto_execute_latest_locked', failed)
    with pytest.raises(asyncio.CancelledError):
        await options_desk.auto_execute_latest()
    assert not holders


@pytest.mark.asyncio
async def test_lost_session_blocks_order_submission(monkeypatch, sessions):
    from services import execution_gate, safety
    async def trading(**kwargs):
        return True, {}
    async def gate(**kwargs):
        return {'ok': True}
    async def activity(*args):
        pass
    async def today():
        return 0
    async def missing(*args):
        return None
    async def rows(_limit):
        return []
    cursor = SimpleNamespace(to_list=rows)
    cursor.sort = lambda *args: cursor
    db = SimpleNamespace(options_desk_candidates=SimpleNamespace(find=lambda *args: cursor),
                         options_desk_orders=SimpleNamespace(find_one=missing))
    async def forbidden(*args):
        pytest.fail('lost lease must never reach mocked execution')
    monkeypatch.setattr(options_desk, 'get_db', lambda: db)
    monkeypatch.setattr(options_desk, 'OPTIONS_EXECUTION_ENABLED', True)
    monkeypatch.setattr(safety, 'trading_enabled', trading)
    monkeypatch.setattr(execution_gate, 'check', gate)
    monkeypatch.setattr(options_desk, '_auto_orders_submitted_today', today)
    monkeypatch.setattr(options_desk, 'log_activity', activity)
    monkeypatch.setattr(options_desk, 'execute', forbidden)
    lock = await options_desk._acquire_auto_execute_lock()
    sessions[1][0].fail_query = True
    result = await options_desk._auto_execute_latest_locked(lease=lock['lease'], candidate_set={
        'cycle_id': 'offline-cycle',
        'candidates': [{'candidate_id': 'offline', 'ticker': 'TEST', 'manual_fire_ready': True, 'cycle_id': 'offline-cycle',
                        'instrument': {'symbol': 'TEST261218C00100000'}}]})
    assert not result['submitted'] and not result['ok']
    assert result['skipped'][0]['reason'] == 'options_auto_execute_lock_lost'


@pytest.mark.asyncio
async def test_verified_isolated_postgres_session_exclusion(monkeypatch):
    """Opt-in: lock-only SQL on independently verified isolated PostgreSQL."""
    import os
    import uuid
    from services import advisory_locks, postgres_store
    test_dsn = os.environ.get('POSTGRES_TEST_DSN')
    if not test_dsn or os.environ.get('POSTGRES_TEST_ISOLATED') != 'true':
        pytest.skip('explicit verified isolated PostgreSQL DSN required; no runtime DSN fallback')
    monkeypatch.setattr(postgres_store, 'enabled', lambda: True)
    monkeypatch.setattr(postgres_store, 'dsn', lambda: test_dsn)
    namespace = 'isolated-options-lock-test:' + uuid.uuid4().hex
    first = second = None
    try:
        first = await advisory_locks.try_acquire(namespace)
        assert first is not None
        assert await advisory_locks.try_acquire(namespace) is None
        await first.release()
        second = await advisory_locks.try_acquire(namespace)
        assert second is not None
        await first.release()  # Stale release cannot close the new session.
        assert await advisory_locks.try_acquire(namespace) is None
    finally:
        if first is not None:
            await first.release()
        if second is not None:
            await second.release()
