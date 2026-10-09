import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services import execution_safety, options_desk
from test_claude_audit_exit_recovery import MemoryCollection


@pytest.fixture
def option_case(monkeypatch):
    symbol = 'SPY261218C00600000'
    state = SimpleNamespace(history=[], quantity=2, posts=[], deletes=[], confirmed=True,
                            response_status=200, submission_unknown=False)
    db = SimpleNamespace(execution_intents=MemoryCollection())
    def response(code, data):
        return SimpleNamespace(status_code=code, json=lambda: data, text='offline fixture')
    class Client:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def get(self, url, **kwargs):
            if url.endswith('/orders'):
                return response(state.response_status, state.history)
            if '/positions/' in url:
                return response(200, {'qty': str(state.quantity)})
            return response(200, {**state.history[0], 'status': 'canceled' if state.confirmed else 'pending_cancel'})
        async def delete(self, url):
            state.deletes.append(url)
            return response(204, {})
        async def post(self, url, json):
            state.posts.append(dict(json))
            if state.submission_unknown:
                raise TimeoutError('offline timeout')
            return response(201, {'id': 'new-exit', 'status': 'new', **json})
    async def market():
        return {'is_open': True}
    async def snapshot(symbol):
        return {'ok': True, 'bid': 1, 'ask': 1.1, 'quote_time': datetime.now(timezone.utc).isoformat()}
    monkeypatch.setattr(options_desk, 'options_account_route_guard', lambda: {'ok': True})
    monkeypatch.setattr(options_desk, '_options_market_status', market)
    monkeypatch.setattr(options_desk, '_option_snapshot', snapshot)
    monkeypatch.setattr(options_desk, '_options_headers', lambda: {})
    monkeypatch.setattr(options_desk.httpx, 'AsyncClient', lambda **kwargs: Client())
    monkeypatch.setattr(options_desk, 'get_db', lambda: db)
    monkeypatch.setattr(execution_safety, 'get_db', lambda: db)
    return symbol, state, db


@pytest.mark.asyncio
async def test_working_option_sell_is_reused_without_second_submission(option_case):
    symbol, state, _ = option_case
    state.history = [{'id': 'working', 'symbol': symbol, 'side': 'sell', 'status': 'new', 'limit_price': '1'}]
    result = await options_desk.close(symbol, qty=2, emergency=True)
    assert result['ok'] and result['already_working'] and not state.posts and not state.deletes


@pytest.mark.asyncio
@pytest.mark.parametrize('confirmed', [True, False])
async def test_stale_option_sell_requires_terminal_cancel_and_current_quantity(option_case, confirmed):
    symbol, state, _ = option_case
    state.history = [{'id': 'working', 'symbol': symbol, 'side': 'sell', 'status': 'partially_filled',
                      'limit_price': '1.5', 'submitted_at': (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()}]
    state.quantity, state.confirmed = 1, confirmed
    result = await options_desk.close(symbol, qty=2, emergency=True)
    assert len(state.deletes) == 1
    if confirmed:
        assert result['ok'] and len(state.posts) == 1 and state.posts[0]['qty'] == '1'
        assert state.posts[0]['client_order_id']
    else:
        assert not result['ok'] and not state.posts


@pytest.mark.asyncio
async def test_delayed_broker_visibility_and_concurrent_closes_submit_once(option_case):
    symbol, state, _ = option_case
    results = await asyncio.gather(*(options_desk.close(symbol, qty=2, emergency=True) for _ in range(3)))
    assert sum(bool(r['ok']) for r in results) == 1 and len(state.posts) == 1


@pytest.mark.asyncio
async def test_unknown_option_submission_retains_reservation_even_if_other_terminal_order_appears(option_case):
    symbol, state, _ = option_case
    state.submission_unknown = True
    assert not (await options_desk.close(symbol, qty=2, emergency=True))['ok']
    state.history = [{'id': 'unrelated-old-sell', 'symbol': symbol, 'side': 'sell', 'status': 'filled'}]
    state.submission_unknown = False
    assert not (await options_desk.close(symbol, qty=2, emergency=True))['ok']
    assert len(state.posts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('unavailable,quantity', [(True, 2), (False, 0), (False, -1)])
async def test_option_close_fails_closed_on_unavailable_orders_or_no_long_position(option_case, unavailable, quantity):
    symbol, state, _ = option_case
    state.response_status = 503 if unavailable else 200
    state.quantity = quantity
    assert not (await options_desk.close(symbol, qty=2, emergency=True))['ok']
    assert not state.posts
