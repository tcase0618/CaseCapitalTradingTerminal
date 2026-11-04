"""Offline DB-outage notices; no real Telegram, database or broker requests."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import database_watchdog as watchdog, scheduler, telegram_service
from test_scheduler_overlap_guard import _FakeScheduler


@pytest.fixture
def notice(monkeypatch):
    notifier = watchdog.OutageNotifier()
    sender = AsyncMock(return_value={'attempted': True, 'sent': True, 'reason': 'sent'})
    monkeypatch.setattr(watchdog, '_notifier', notifier)
    monkeypatch.setattr(watchdog, '_send_outage_alert', sender)
    return notifier, sender


@pytest.mark.asyncio
@pytest.mark.parametrize('failed', ['bot_state', 'claude_cache', 'scan_results', 'watchlist', 'alerts'])
async def test_each_failed_read_alerts_once_without_database_delivery_state(monkeypatch, notice, failed):
    _, sender = notice
    collections = {name: SimpleNamespace(find_one=AsyncMock(return_value=None),
        count_documents=AsyncMock(return_value=0)) for name in
        ['bot_state', 'claude_cache', 'scan_results', 'watchlist', 'alerts']}
    collection = collections[failed]
    method = collection.find_one if failed in {'bot_state', 'scan_results'} else collection.count_documents
    method.side_effect = RuntimeError('fixture-secret')
    monkeypatch.setattr(watchdog, 'get_db', lambda: SimpleNamespace(**collections))
    for _ in range(2):
        result = await watchdog.check()
        assert result['db_available'] is False and result['protection_status'] == 'UNVERIFIED'
        assert 'fixture-secret' not in str(result)
    sender.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_outage_observers_make_one_attempt(notice):
    notifier, sender = notice
    await asyncio.gather(*(notifier.observe(False) for _ in range(10)))
    sender.assert_awaited_once()


@pytest.mark.asyncio
async def test_rearm_requires_two_healthy_probes_spanning_sixty_seconds(notice, monkeypatch):
    notifier, sender = notice
    clock = [0]
    monkeypatch.setattr(watchdog, '_now', lambda: clock[0])
    await notifier.observe(False)
    await notifier.observe(True)
    clock[0] = 59
    await notifier.observe(True)
    await notifier.observe(False)
    assert sender.await_count == 1
    clock[0] = 100
    await notifier.observe(True)
    clock[0] = 160
    await notifier.observe(True)
    await notifier.observe(False)
    assert sender.await_count == 2


@pytest.mark.asyncio
async def test_unknown_delivery_does_not_retry_and_duplicate(notice):
    notifier, sender = notice
    sender.return_value = {'attempted': True, 'sent': False, 'reason': 'delivery_unverified'}
    await notifier.observe(False)
    await notifier.observe(False)
    sender.assert_awaited_once()


@pytest.mark.asyncio
async def test_disabled_or_unconfigured_notice_does_not_consume_attempt(notice):
    notifier, sender = notice
    sender.side_effect = [{'attempted': False, 'sent': False, 'reason': 'not_configured'},
                          {'attempted': True, 'sent': True, 'reason': 'sent'}]
    await notifier.observe(False)
    result = await notifier.observe(False)
    assert result['sent'] and sender.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['sent', 'unknown', 'disabled', 'no-chat'])
async def test_fixed_sender_never_uses_database_guard(monkeypatch, case):
    monkeypatch.setenv('TELEGRAM_EVENTS_ENABLED', 'false' if case == 'disabled' else 'true')
    monkeypatch.setattr(telegram_service, '_has_token', lambda: True)
    monkeypatch.setattr(telegram_service, '_default_chat_id', lambda: '' if case == 'no-chat' else 'offline-chat')
    monkeypatch.setattr(telegram_service, '_api_url', lambda method: 'https://telegram.invalid/offline')
    monkeypatch.setattr(telegram_service, 'get_db', lambda: (_ for _ in ()).throw(AssertionError('DB forbidden')))
    posts = []
    class Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, json):
            posts.append(json)
            if case == 'unknown':
                raise TimeoutError('fixture-secret')
            return SimpleNamespace(status_code=200, json=lambda: {'ok': True})
    monkeypatch.setattr(watchdog.httpx, 'AsyncClient', lambda **kwargs: Client())
    result = await watchdog._send_outage_alert()
    assert result['attempted'] == (case in {'sent', 'unknown'})
    assert result['sent'] == (case == 'sent')
    assert len(posts) == int(case in {'sent', 'unknown'})
    assert 'fixture-secret' not in str(result)
    if posts:
        assert 'UNVERIFIED' in posts[0]['text'] and 'CRITICAL' in posts[0]['text']


def test_scheduler_registers_independent_one_minute_probe(monkeypatch):
    monkeypatch.setattr(scheduler, '_scheduler', None)
    monkeypatch.setattr(scheduler, 'AsyncIOScheduler', _FakeScheduler)
    scheduler.start_scheduler()
    try:
        jobs = {row['id']: row for row in scheduler._scheduler.jobs}
        job = jobs['database_health_watchdog_1m']
        assert job['func'] is watchdog.check
        assert str(job['trigger']) == 'interval[0:01:00]'
    finally:
        scheduler.shutdown_scheduler()


@pytest.mark.asyncio
async def test_ordinary_sender_still_requires_its_database_guard(monkeypatch):
    monkeypatch.setattr(telegram_service, '_has_token', lambda: True)
    monkeypatch.setattr(telegram_service, '_default_chat_id', lambda: 'offline-chat')
    monkeypatch.setattr(telegram_service, '_should_skip_outbound', AsyncMock(side_effect=RuntimeError('offline DB outage')))
    client = AsyncMock(side_effect=AssertionError('ordinary messages must not bypass the guard'))
    monkeypatch.setattr(telegram_service.httpx, 'AsyncClient', client)
    with pytest.raises(RuntimeError, match='offline DB outage'):
        await telegram_service.send_message('CASE CAPITAL | PORTFOLIO TEST')
    client.assert_not_called()
