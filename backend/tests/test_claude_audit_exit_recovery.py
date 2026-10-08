"""Offline regressions for CCTT-CRIT-001/002 and CCTT-HIGH-001."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from services import execution_safety, postgres_store, public_api, public_execution, telegram_events


class MemoryCollection:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def find(self, query, *args):
        rows = [dict(r) for r in self.rows if postgres_store._matches(r, query)]
        async def to_list(limit):
            return rows[:limit]
        return SimpleNamespace(to_list=to_list)

    async def find_one(self, query, *args, **kwargs):
        return next((dict(r) for r in self.rows if postgres_store._matches(r, query)), None)

    async def insert_one(self, doc):
        if any(r.get('_id') == doc.get('_id') for r in self.rows):
            return SimpleNamespace(duplicate=True)
        self.rows.append(dict(doc))
        return SimpleNamespace(duplicate=False)

    async def update_one(self, query, update, **kwargs):
        for row in self.rows:
            if postgres_store._matches(row, query):
                row.update(update.get('$set', {}))
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)


@pytest.fixture
def exit_case(monkeypatch):
    trade = dict(ticker='TEST', broker_base='public', status='OPEN', fill_status='FILLED',
                 client_order_id='entry-test', qty_remaining=1.5, current_stop=10)
    db = SimpleNamespace(tf_trades=MemoryCollection([trade]), execution_intents=MemoryCollection(), ll_tickets=MemoryCollection())
    alerts = []

    class Client:
        def __init__(self):
            self.orders = {}
            self.submissions = []
            self.cancels = []
            self.failure = None
            self.bid, self.ask = 9.0, 9.02
            self.quantity = 1.5
            self.cancel_confirmed = True

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def quotes(self, symbols):
            return {'quotes': [{'symbol': 'TEST', 'bid': self.bid, 'ask': self.ask,
                                'quoteTime': datetime.now(timezone.utc).isoformat()}]}

        async def portfolio(self):
            return {'positions': [{'symbol': 'TEST', 'quantity': self.quantity}]}

        async def get_order(self, order_id):
            return self.orders[order_id]

        async def cancel_order(self, order_id):
            self.cancels.append(order_id)
            if self.cancel_confirmed:
                self.orders[order_id] = {**self.orders[order_id], 'status': 'CANCELLED'}
            return {}

        async def submit_equity_order(self, **kwargs):
            self.submissions.append(kwargs)
            if self.failure:
                raise self.failure
            order_id = f'exit-{len(self.submissions)}'
            self.orders[order_id] = {'status': 'NEW'}
            return {'order': {'orderId': order_id}}

    client = Client()
    async def emit(*args, **kwargs):
        alerts.append((args, kwargs))
        return {'sent': True}
    monkeypatch.setattr(public_execution, 'enabled', lambda: True)
    monkeypatch.setattr(public_execution, 'get_db', lambda: db)
    monkeypatch.setattr('services.lottery.get_db', lambda: db)
    monkeypatch.setattr(execution_safety, 'get_db', lambda: db)
    monkeypatch.setattr(public_api, 'PublicAPIClient', lambda **kwargs: client)
    monkeypatch.setattr(public_execution, '_public_session_now', lambda: 'CORE')
    monkeypatch.setattr(telegram_events, 'emit_event', emit)
    return trade, client, db, alerts


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['CANCELLED', 'CANCELED', 'EXPIRED', 'REJECTED', 'FAILED', 'FILLED'])
async def test_terminal_emergency_order_retries_with_new_intent(exit_case, status):
    trade, client, db, _ = exit_case
    await public_execution.process_protective_exits()
    client.orders['exit-1'] = {'status': status}
    client.quantity = 0.5
    result = await public_execution.process_protective_exits()
    assert len(result['submitted']) == 1
    assert client.submissions[1]['quantity'] == 0.5
    assert client.submissions[0]['client_order_id'] != client.submissions[1]['client_order_id']
    assert trade['emergency_exit_history'][0]['order_id'] == 'exit-1'


@pytest.mark.asyncio
@pytest.mark.parametrize('confirmed', [True, False])
async def test_partial_exit_requires_confirmed_cancel_before_replacement(exit_case, confirmed):
    trade, client, db, _ = exit_case
    await public_execution.process_protective_exits()
    client.orders['exit-1'] = {'status': 'PARTIALLY_FILLED', 'filledQuantity': 1.0, 'averagePrice': 9.0}
    client.quantity = 0.5
    client.cancel_confirmed = confirmed
    await public_execution.process_protective_exits()
    assert client.cancels == ['exit-1']
    assert len(client.submissions) == (2 if confirmed else 1)
    if confirmed:
        assert client.submissions[1]['quantity'] == 0.5
    else:
        assert trade['emergency_exit_order_id'] == 'exit-1'


@pytest.mark.asyncio
async def test_failed_preflight_retries_without_waiting_24_hours(exit_case):
    trade, client, db, _ = exit_case
    client.failure = public_api.PublicOrderRejected('offline preflight failure')
    assert not (await public_execution.process_protective_exits())['ok']
    assert trade['emergency_exit_order_id'] is None
    client.failure = None
    assert (await public_execution.process_protective_exits())['submitted']
    assert client.submissions[0]['client_order_id'] != client.submissions[1]['client_order_id']


@pytest.mark.asyncio
async def test_ambiguous_placement_retains_reservation_and_alerts(exit_case):
    trade, client, db, alerts = exit_case
    client.failure = public_api.PublicOrderSubmissionUnknown('offline timeout')
    await public_execution.process_protective_exits()
    pending_id = trade['emergency_exit_order_id']
    assert pending_id and alerts
    client.failure = None
    await public_execution.process_protective_exits()
    assert len(client.submissions) == 1
    assert trade['emergency_exit_order_id'] == pending_id


@pytest.mark.asyncio
async def test_wide_spread_does_not_suppress_fresh_bid_stop_exit(exit_case):
    _, client, _, _ = exit_case
    client.ask = 10.0
    assert (await public_execution.process_protective_exits())['submitted']
    assert client.submissions[0]['limit_price'] == 9.0
    quote, reason = public_execution._execution_quote({'bid': 9, 'ask': 10}, side='BUY')
    assert quote is None and reason == 'public_execution_quote_spread_too_wide'


@pytest.mark.asyncio
async def test_refused_exit_claim_is_not_silently_ignored(exit_case, monkeypatch):
    _, client, _, alerts = exit_case
    async def refused(**kwargs):
        return {'ok': False, 'reason': 'duplicate_execution_intent'}
    monkeypatch.setattr(execution_safety, 'claim_execution_intent', refused)
    result = await public_execution.process_protective_exits()
    assert not result['ok'] and alerts and not client.submissions
