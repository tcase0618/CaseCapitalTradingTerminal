"""Offline halt tests: cancellations below are mocked, never broker calls."""
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_halt_routes_cancellation_to_active_brokers_and_preserves_failure(monkeypatch):
    import server
    from services import safety, telegram_events, trade_floor
    calls = []
    async def halt(*args, **kwargs):
        calls.append('halt')
        return {'enabled': False}
    async def forbidden(*args, **kwargs):
        pytest.fail('Retired blanket cancellation must not run')
    async def event(*args, **kwargs):
        return {}
    monkeypatch.setattr(safety, 'set_trading', halt)
    monkeypatch.setattr(trade_floor, 'cancel_stale_orders', forbidden)
    monkeypatch.setattr(telegram_events, 'emit_event', event)
    # Before repair, this regression reaches the unsafe retired route.
    try:
        from services import halt_orders
    except ImportError:
        halt_orders = None
    if halt_orders:
        async def cancel():
            calls.append('cancel')
            return {'ok': False, 'errors': ['offline broker unavailable']}
        monkeypatch.setattr(halt_orders, 'cancel_open_entry_orders', cancel)
    result = await server.admin_halt('offline fixture')
    assert calls == ['halt', 'cancel']
    assert not result['ok'] and result['status'] == {'enabled': False}


@pytest.fixture
def halt_case(monkeypatch):
    from services import halt_orders, public_execution, public_api, options_desk
    state = SimpleNamespace(public_rows=[], option_rows=[], public_cancel=[], option_cancel=[],
                            public_failed=False, option_failed=False, confirmed=True)
    class Public:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def search_orders(self):
            if state.public_failed:
                raise TimeoutError('offline unavailable')
            return {'orders': state.public_rows}
        async def cancel_order(self, order_id):
            state.public_cancel.append(order_id)
        async def get_order(self, order_id):
            return {'status': 'CANCELLED' if state.confirmed else 'PENDING_CANCEL'}
    def response(code, data):
        return SimpleNamespace(status_code=code, json=lambda: data)
    class Options:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def get(self, url, **kwargs):
            if url.endswith('/orders'):
                return response(503 if state.option_failed else 200, state.option_rows)
            return response(200, {'status': 'canceled' if state.confirmed else 'pending_cancel'})
        async def delete(self, url):
            state.option_cancel.append(url.rsplit('/', 1)[1])
            return response(204, {})
    monkeypatch.setattr(public_execution, 'enabled', lambda: True)
    monkeypatch.setattr(public_api, 'PublicAPIClient', lambda **kwargs: Public())
    monkeypatch.setattr(options_desk, 'options_account_route_guard', lambda: {'ok': True})
    monkeypatch.setattr(options_desk, '_options_headers', lambda: {})
    monkeypatch.setattr(options_desk.httpx, 'AsyncClient', lambda **kwargs: Options())
    return halt_orders, state


@pytest.mark.asyncio
async def test_only_verified_simple_opening_buys_are_cancelled(halt_case):
    module, state = halt_case
    public = {'orderId': 'entry', 'orderSide': 'BUY', 'status': 'NEW',
              'instrument': {'type': 'EQUITY'}, 'openCloseIndicator': 'OPEN', 'orderClass': 'SIMPLE'}
    option = {'id': 'option-entry', 'side': 'buy', 'status': 'new', 'asset_class': 'us_option',
              'position_intent': 'buy_to_open', 'order_class': 'simple'}
    state.public_rows = [public, {**public, 'orderId': 'stop', 'orderSide': 'SELL'},
                         {**public, 'orderId': 'cover', 'openCloseIndicator': 'CLOSE'},
                         {**public, 'orderId': 'nested', 'legs': [{'orderSide': 'SELL'}]},
                         {**public, 'orderId': 'filled', 'status': 'FILLED'}]
    state.option_rows = [option, {**option, 'id': 'sell-exit', 'side': 'sell'},
                         {**option, 'id': 'short-exit', 'position_intent': 'buy_to_close'},
                         {**option, 'id': 'equity', 'asset_class': 'us_equity'},
                         {**option, 'id': 'mixed', 'legs': [{'side': 'sell'}]}]
    result = await module.cancel_open_entry_orders()
    assert state.public_cancel == ['entry'] and state.option_cancel == ['option-entry']
    assert result['public']['confirmed'] == ['entry']
    assert result['options']['confirmed'] == ['option-entry']
    assert not result['ok']  # Mixed/nested buys need manual inspection.


@pytest.mark.asyncio
@pytest.mark.parametrize('public_failed,option_failed', [(True, False), (False, True)])
async def test_one_broker_failure_does_not_skip_the_other(halt_case, public_failed, option_failed):
    module, state = halt_case
    state.public_failed, state.option_failed = public_failed, option_failed
    result = await module.cancel_open_entry_orders()
    assert not result['ok']
    assert result['public' if public_failed else 'options']['errors']
    assert result['options' if public_failed else 'public']['ok']


@pytest.mark.asyncio
async def test_cancel_acknowledgement_does_not_claim_terminal_cancellation(halt_case):
    module, state = halt_case
    state.confirmed = False
    state.option_rows = [{'id': 'entry', 'side': 'buy', 'status': 'new', 'asset_class': 'us_option',
                          'position_intent': 'buy_to_open', 'order_class': 'simple'}]
    result = await module.cancel_open_entry_orders()
    assert result['options']['requested'] == ['entry']
    assert result['options']['confirmed'] == [] and not result['ok']


@pytest.mark.asyncio
async def test_saturated_options_list_does_not_claim_coverage(halt_case):
    module, state = halt_case
    state.option_rows = [{'id': str(i), 'side': 'sell'} for i in range(500)]
    result = await module.cancel_open_entry_orders()
    assert not result['ok'] and not state.option_cancel


@pytest.mark.asyncio
async def test_public_closing_buy_and_missing_intent_are_preserved(halt_case):
    module, state = halt_case
    state.public_rows = [{'orderId': 'unbound', 'orderSide': 'BUY', 'status': 'NEW',
                          'instrument': {'type': 'EQUITY'}}]
    result = await module.cancel_open_entry_orders()
    assert not result['ok'] and not state.public_cancel


@pytest.mark.asyncio
async def test_racing_fill_does_not_claim_cancelled_or_rewrite_holdings(halt_case):
    module, state = halt_case
    # Confirmation unavailable is treated as unresolved, with no ledger writes.
    state.confirmed = False
    state.public_rows = [{'orderId': 'partial', 'orderSide': 'BUY', 'status': 'PARTIALLY_FILLED',
                          'instrument': {'type': 'EQUITY'}, 'openCloseIndicator': 'OPEN'}]
    result = await module.cancel_open_entry_orders()
    assert result['public']['requested'] == ['partial']
    assert not result['public']['confirmed'] and not result['ok']
