"""CRIT-003: database errors must not fabricate coverage or disclose secrets."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import server
from services import db as database, system_health, postgres_store


@pytest.mark.asyncio
@pytest.mark.parametrize('failed', ['bot_state', 'claude_cache', 'scan_results', 'watchlist', 'alerts'])
async def test_status_reports_each_failed_read_without_raw_error(monkeypatch, failed):
    collections = {name: SimpleNamespace(find_one=AsyncMock(return_value=None),
                   count_documents=AsyncMock(return_value=0))
                   for name in ['bot_state', 'claude_cache', 'scan_results', 'watchlist', 'alerts']}
    collection = collections[failed]
    method = collection.find_one if failed in {'bot_state', 'scan_results'} else collection.count_documents
    method.side_effect = RuntimeError('postgresql://offline-user:fixture-secret@private-host/db')
    monkeypatch.setattr(server, 'get_db', lambda: SimpleNamespace(**collections))
    result = await server.status()
    assert result['bot']['db_available'] is False
    assert result['bot']['db_reason'] == 'database_read_failed'
    assert result['bot']['db_failed_read'] == failed
    assert result['bot']['protection_status'] == 'UNVERIFIED'
    assert all(value is None for value in result['stats'].values())
    assert 'fixture-secret' not in str(result)


@pytest.mark.asyncio
async def test_monitor_returns_unknown_coverage_on_db_failure(monkeypatch):
    read = AsyncMock(side_effect=TimeoutError('fixture-secret'))
    monkeypatch.setattr(server, 'get_db', lambda: SimpleNamespace(bot_state=SimpleNamespace(find_one=read)))
    result = await server.position_monitor_latest()
    assert result['ok'] is False and result['protection_unverified'] is True
    assert result['status'] == 'UNVERIFIED' and result['db_reason'] == 'database_timeout'
    assert result['totals'] is None and result['snapshot_at'] is None
    assert 'fixture-secret' not in str(result)


@pytest.mark.asyncio
async def test_health_marks_protection_unknown_and_sanitizes_database_errors(monkeypatch):
    monkeypatch.setattr(system_health, '_db_counts', AsyncMock(side_effect=RuntimeError('fixture-secret')))
    monkeypatch.setattr(system_health, '_alpaca_probe', AsyncMock(return_value={'ok': False, 'reason': 'offline'}))
    monkeypatch.setattr(postgres_store, 'status', AsyncMock(return_value={'ready': False, 'last_error': 'fixture-secret'}))
    result = await system_health.overview()
    assert result['protection_status'] == 'UNVERIFIED'
    assert result['database']['reason'] == 'database_read_failed'
    assert result['ready_for_trade_floor'] is False
    assert 'fixture-secret' not in str(result)


@pytest.mark.asyncio
async def test_healthy_status_preserves_real_counts(monkeypatch):
    row = {'last_scan_at': 'offline-scan'}
    db = SimpleNamespace(**{name: SimpleNamespace(find_one=AsyncMock(return_value=row if name == 'bot_state' else {}),
        count_documents=AsyncMock(return_value=7)) for name in ['bot_state', 'claude_cache', 'scan_results', 'watchlist', 'alerts']})
    monkeypatch.setattr(server, 'get_db', lambda: db)
    result = await server.status()
    assert result['bot']['db_available'] is True and result['bot']['db_reason'] is None
    assert result['bot']['protection_status'] == 'NOT_ASSESSED'
    assert result['stats']['watchlist_count'] == 7 and result['last_scan_at'] == 'offline-scan'


@pytest.mark.parametrize('sqlstate,expected', [('28000', 'database_authentication_failed'),
    ('08006', 'database_connection_failed'), ('53300', 'database_capacity_unavailable'),
    ('42P01', 'database_schema_unavailable'), ('untrusted-secret', 'database_read_failed')])
def test_failure_categories_never_echo_sqlstate_or_message(sqlstate, expected):
    error = RuntimeError('fixture-secret')
    error.sqlstate = sqlstate
    assert database.database_failure_reason(error) == expected


@pytest.mark.asyncio
async def test_connection_failure_before_first_read_is_sanitized(monkeypatch):
    def unavailable():
        raise OSError('fixture-secret')
    monkeypatch.setattr(server, 'get_db', unavailable)
    result = await server.status()
    assert result['bot']['db_reason'] == 'database_connection_failed'
    assert result['bot']['db_failed_read'] == 'connection'
    assert 'fixture-secret' not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize('sqlstate,expected', [('28000', 'database_authentication_failed'),
    ('08006', 'database_connection_failed'), ('53300', 'database_capacity_unavailable'),
    ('42P01', 'database_schema_unavailable'), (None, 'database_read_failed')])
async def test_health_preserves_classified_postgres_probe_failure(monkeypatch, sqlstate, expected):
    error = RuntimeError('fixture-secret')
    error.sqlstate = sqlstate
    monkeypatch.setattr(system_health, '_db_counts', AsyncMock(return_value={'ok': False, 'counts': {}}))
    monkeypatch.setattr(system_health, '_alpaca_probe', AsyncMock(return_value={'ok': False, 'reason': 'offline'}))
    monkeypatch.setattr(postgres_store, 'status', AsyncMock(side_effect=error))
    result = await system_health.overview()
    assert result['postgres']['last_error'] == expected
    assert result['protection_status'] == 'UNVERIFIED'
    assert 'fixture-secret' not in str(result)


@pytest.mark.asyncio
@pytest.mark.parametrize('reason,expected', [('database_timeout', 'database_timeout'),
    ('postgresql://fixture-secret@private-host/db', 'database_unavailable'), (['bad-schema'], 'database_unavailable')])
async def test_health_keeps_only_allowlisted_postgres_reasons(monkeypatch, reason, expected):
    monkeypatch.setattr(system_health, '_db_counts', AsyncMock(return_value={'ok': False, 'counts': {}}))
    monkeypatch.setattr(system_health, '_alpaca_probe', AsyncMock(return_value={'ok': False, 'reason': 'offline'}))
    monkeypatch.setattr(postgres_store, 'status', AsyncMock(return_value={'ready': False, 'last_error': reason}))
    result = await system_health.overview()
    assert result['postgres']['last_error'] == expected
    assert 'fixture-secret' not in str(result)
