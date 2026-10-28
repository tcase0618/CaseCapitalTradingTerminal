from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services import market_regime as regime, portfolio_manager as pm, pricer, trade_floor
from test_strategy_evidence import Collection


@pytest.fixture
def case(monkeypatch):
    now = datetime(2026, 10, 28, 14, 0, tzinfo=timezone.utc)
    db = SimpleNamespace(bot_state=Collection())
    monkeypatch.setattr(regime, '_now', lambda: now)
    monkeypatch.setattr(regime, 'get_db', lambda: db)
    return SimpleNamespace(now=now, db=db)


def snapshot(case, status='green', age=60):
    return {'status': status, 'spy_last': 110, 'spy_ema200': 100,
            'spy_day_change_pct': -5 if status == 'doomsday' else 1,
            'halt_new_entries': status == 'doomsday',
            'reason': 'doomsday_trigger' if status == 'doomsday' else 'risk_on',
            'checked_at': (case.now - timedelta(seconds=age)).isoformat()}


def save(case, result):
    case.db.bot_state.rows.append({'_id': regime.SNAPSHOT_ID, 'snapshot': result})


async def outage(**kwargs):
    raise TimeoutError('offline fixture')


@pytest.mark.asyncio
async def test_real_history_retry_then_pm_uses_valid_regime(case, monkeypatch):
    calls = []

    async def history(symbol, days, force):
        calls.append((symbol, force))
        return {'2026-10-27': 100, '2026-10-28': 101} if force else {}

    monkeypatch.setattr(pricer, 'get_history', history)
    result = await trade_floor.regime_status()
    assert calls == [('SPY', False), ('SPY', True)]
    assert result['status'] == 'green'
    assert result['halt_new_entries'] is False
    assert pm._mode_from_regime(result) == 'BALANCED'
    assert pm._regime_adjusted_action('STARTER', 80, 2, [], {}, result)[0] == 'STARTER'
    assert case.db.bot_state.rows[0]['snapshot']['data_state'] == 'AVAILABLE'


@pytest.mark.asyncio
async def test_transient_outage_retains_recent_real_assessment(case):
    good = snapshot(case)
    save(case, good)
    result = await regime.resolve(outage)
    assert result['data_state'] == 'LAST_GOOD'
    assert result['checked_at'] == good['checked_at']
    assert result['data_age_seconds'] == 60
    assert pm._mode_from_regime(result) == 'BALANCED'
    assert not result['halt_new_entries']
    assert case.db.bot_state.rows[0]['snapshot'] == good


@pytest.mark.asyncio
async def test_cached_crash_never_cleared_by_outage(case):
    save(case, snapshot(case, 'doomsday'))
    result = await regime.resolve(outage)
    assert result['halt_new_entries']
    assert pm._regime_adjusted_action('STARTER', 90, 3, [], {}, result)[0] == 'WATCH'
    assert 'MARKET-RISK ENTRY RESTRICTION' in regime.alert_text(result, None)


@pytest.mark.asyncio
async def test_current_crash_overrides_cached_green(case):
    save(case, snapshot(case))

    async def fetch(**kwargs):
        return snapshot(case, 'doomsday', age=0)

    result = await regime.resolve(fetch)
    assert result['halt_new_entries']
    assert result['data_state'] == 'AVAILABLE'


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['expired', 'future', 'naive', 'missing', 'invalid', 'unknown'])
async def test_unverifiable_fallback_does_not_authorize_risk(case, mutation):
    good = snapshot(case)
    if mutation == 'expired':
        good = snapshot(case, age=901)
    elif mutation == 'future':
        good = snapshot(case, age=-1)
    elif mutation == 'naive':
        good['checked_at'] = '2026-10-28T13:59:00'
    elif mutation == 'missing':
        good.pop('checked_at')
    elif mutation == 'invalid':
        good['spy_last'] = float('nan')
    else:
        good['status'] = 'unknown'
    save(case, good)
    result = await regime.resolve(outage)
    assert result['data_state'] == 'UNAVAILABLE'
    assert result['halt_new_entries']
    text = regime.alert_text(result, None)
    assert 'REGIME DATA UNAVAILABLE' in text
    assert 'HALT NEW ENTRIES' not in text
    assert 'None' not in text


@pytest.mark.asyncio
async def test_real_crash_history_still_blocks_pm(case, monkeypatch):
    async def history(*args, **kwargs):
        return {'2026-10-27': 100, '2026-10-28': 95}

    monkeypatch.setattr(pricer, 'get_history', history)
    result = await trade_floor.regime_status()
    assert result['reason'] == 'doomsday_trigger'
    assert result['halt_new_entries']
    assert pm._mode_from_regime(result) == 'RISK_OFF'


@pytest.mark.parametrize('history', [
    {'2026-10-27': 100, '2026-10-28': float('inf')},
    {'2026-10-27': 100, '2026-10-28': -1},
    {'2026-10-27': 100, '2026-10-29': 101},
    {'2026-10-20': 100, '2026-10-21': 101},
    {},
])
def test_invalid_history_not_a_valid_regime(case, history):
    assert regime.history_values(history) == []


@pytest.mark.asyncio
async def test_alert_change_and_recovery_sent_once(case, monkeypatch):
    from services import telegram_service
    messages = []

    async def send(text):
        messages.append(text)
        return True

    monkeypatch.setattr(telegram_service, 'send_message', send)
    unknown = await regime.resolve(outage)
    await regime.notify_transition(unknown)
    await regime.notify_transition(unknown)
    recovered = {**snapshot(case), 'data_state': 'AVAILABLE'}
    await regime.notify_transition(recovered)
    await regime.notify_transition(recovered)
    assert len(messages) == 2
    assert 'DATA UNAVAILABLE' in messages[0]
    assert 'REGIME CHECK RECOVERED' in messages[1]


@pytest.mark.asyncio
async def test_suppressed_alert_not_claimed_delivered(case, monkeypatch):
    from services import telegram_service

    async def suppressed(text):
        return False

    monkeypatch.setattr(telegram_service, 'send_message', suppressed)
    await regime.notify_transition(await regime.resolve(outage))
    assert await case.db.bot_state.find_one({'_id': regime.ALERT_ID}) is None


@pytest.mark.asyncio
async def test_hung_fetch_bounded_and_recent_fallback_usable(case, monkeypatch):
    import asyncio
    monkeypatch.setattr(regime, 'FETCH_TIMEOUT_SECONDS', .01)
    save(case, snapshot(case))

    async def hung(**kwargs):
        await asyncio.sleep(10)

    result = await asyncio.wait_for(regime.resolve(hung), timeout=1)
    assert result['data_state'] == 'LAST_GOOD'


@pytest.mark.asyncio
async def test_cache_write_failure_does_not_discard_valid_fetch(case, monkeypatch):
    class Broken(Collection):
        async def update_one(self, *args, **kwargs):
            raise RuntimeError('offline write failure')

    monkeypatch.setattr(regime, 'get_db', lambda: SimpleNamespace(bot_state=Broken()))
    good = deepcopy(snapshot(case))

    async def fetch(**kwargs):
        return good

    result = await regime.resolve(fetch)
    assert result['data_state'] == 'AVAILABLE'
    assert not result['halt_new_entries']
