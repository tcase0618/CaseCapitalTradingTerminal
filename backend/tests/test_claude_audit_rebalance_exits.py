"""Offline regressions for CCTT-MED-004; no broker connectivity."""
from datetime import datetime, timezone

import pytest

from services import pm_rebalance, public_api, public_execution
from test_claude_audit_exit_recovery import MemoryCollection, exit_case  # noqa: F401


@pytest.fixture
def rebalance_case(exit_case, monkeypatch):
    trade, client, db, alerts = exit_case
    intent = {'intent_id': 'offline-rotation', 'sell_ticker': 'TEST', 'status': 'PLANNED',
              'broker_base': 'public', 'candidate': {'ticker': 'NEXT'}}
    db.pm_rebalance_intents = MemoryCollection([intent])
    monkeypatch.setattr(pm_rebalance, 'get_db', lambda: db)
    # Start above the floor so this is a discretionary release of capital.
    client.bid, client.ask = 11, 11.02
    return trade, client, db, alerts, intent


@pytest.mark.asyncio
async def test_rebalance_sale_is_reserved_in_exit_ledger_before_post(rebalance_case):
    trade, client, _, _, intent = rebalance_case
    original = client.submit_equity_order
    async def post(**kwargs):
        assert trade.get('emergency_exit_order_id')
        assert trade.get('emergency_exit_status') == 'SUBMIT_UNKNOWN'
        assert trade.get('emergency_exit_request') == kwargs
        return await original(**kwargs)
    client.submit_equity_order = post
    result = await pm_rebalance._submit_rebalance_exit(intent)
    assert result['submitted']
    assert intent['sell_order_id'] == trade['emergency_exit_order_id'] == 'exit-1'
    assert intent['status'] == 'SELL_SUBMITTED'
    await public_execution.process_protective_exits()
    assert len(client.submissions) == 1 and client.cancels == []


@pytest.mark.asyncio
@pytest.mark.parametrize('confirmed', [True, False])
async def test_breached_rebalance_requires_terminal_cancel_then_remaining_quantity(rebalance_case, confirmed):
    trade, client, _, _, intent = rebalance_case
    assert (await pm_rebalance._submit_rebalance_exit(intent))['submitted']
    client.bid, client.ask = 9, 9.02
    client.cancel_confirmed = confirmed
    client.quantity = .5
    client.orders['exit-1'] = {'status': 'NEW', 'filledQuantity': 1, 'averagePrice': 11}
    await public_execution.process_protective_exits()
    assert client.cancels == ['exit-1']
    assert len(client.submissions) == (2 if confirmed else 1)
    if confirmed:
        assert client.submissions[1]['quantity'] == .5
        assert client.submissions[1]['client_order_id'] != client.submissions[0]['client_order_id']
    else:
        assert trade['emergency_exit_order_id'] == 'exit-1'


@pytest.mark.asyncio
async def test_unknown_rebalance_preserves_reservation_and_does_not_unlock_replacement(rebalance_case):
    trade, client, _, _, intent = rebalance_case
    client.failure = public_api.PublicOrderSubmissionUnknown('offline timeout')
    result = await pm_rebalance._submit_rebalance_exit(intent)
    assert not result['submitted'] and intent['status'] == 'SELL_UNKNOWN'
    assert intent['sell_order_id'] == trade['emergency_exit_order_id']
    result = await pm_rebalance._reconcile_intents(client)
    assert result['errors'] and not result['replacement_submitted']
    assert len(client.submissions) == 1


@pytest.mark.asyncio
async def test_legacy_unlinked_rebalance_blocks_duplicate_emergency_sell(rebalance_case):
    _, client, _, alerts, intent = rebalance_case
    intent.update(status='SELL_SUBMITTED', sell_order_id='legacy-sell',
                  sell_submitted_at=datetime.now(timezone.utc).isoformat())
    client.bid, client.ask = 9, 9.02
    result = await public_execution.process_protective_exits()
    assert not result['ok'] and alerts
    assert result['errors'][0]['reason'] == 'unlinked_rebalance_exit_requires_reconciliation'
    assert not client.submissions and not client.cancels


@pytest.mark.asyncio
async def test_missing_open_ledger_does_not_submit_rebalance(rebalance_case):
    _, client, db, _, intent = rebalance_case
    db.tf_trades.rows.clear()
    result = await pm_rebalance._submit_rebalance_exit(intent)
    assert not result['submitted'] and result['reason'] == 'public_open_trade_ledger_missing'
    assert not client.submissions and intent['status'] == 'PLANNED'


@pytest.mark.asyncio
async def test_full_rotation_cycle_uses_monitored_exit_ledger(rebalance_case, monkeypatch):
    trade, client, db, _, intent = rebalance_case
    db.portfolio_manager_history = MemoryCollection([{'generated_at': datetime.now(timezone.utc).isoformat()}])
    db.bot_state = MemoryCollection()
    action = {'ticker': 'TEST', 'action': 'EXIT_AND_REPLACE', 'quantity': 1.5,
              'replacement': 'NEXT', 'reason': 'offline capital rotation'}
    monkeypatch.setattr(pm_rebalance, '_enabled', lambda: True)
    monkeypatch.setattr(pm_rebalance, 'build_rebalance_plan', lambda *args: {'actions': [action]})
    async def state():
        return {'ok': True, 'positions': [{'ticker': 'TEST', 'quantity': 1.5}]}
    async def persist(*args):
        return dict(intent)
    async def protective():
        return set()
    monkeypatch.setattr(public_execution, 'portfolio_state', state)
    monkeypatch.setattr(pm_rebalance, '_persist_action', persist)
    monkeypatch.setattr(pm_rebalance, '_open_protective_tickers', protective)
    result = await pm_rebalance.run_rebalance_cycle()
    assert len(result['submitted']) == 1
    assert trade.get('emergency_exit_order_id') == intent['sell_order_id'] == 'exit-1'
    assert trade.get('emergency_exit_request') == client.submissions[0]


@pytest.mark.asyncio
async def test_rotation_record_write_failure_recovers_without_another_sell(rebalance_case):
    trade, client, db, _, intent = rebalance_case
    original = db.pm_rebalance_intents.update_one
    async def fail(*args, **kwargs):
        raise RuntimeError('offline rotation record failure')
    db.pm_rebalance_intents.update_one = fail
    with pytest.raises(RuntimeError, match='rotation record failure'):
        await pm_rebalance._submit_rebalance_exit(intent)
    assert trade['emergency_exit_order_id'] == 'exit-1'
    db.pm_rebalance_intents.update_one = original
    await pm_rebalance._submit_rebalance_exit(intent)
    assert intent['sell_order_id'] == 'exit-1'
    assert len(client.submissions) == 1


@pytest.mark.asyncio
async def test_stop_cancelled_rotation_cannot_unlock_replacement(rebalance_case, monkeypatch):
    _, client, _, _, intent = rebalance_case
    await pm_rebalance._submit_rebalance_exit(intent)
    client.bid, client.ask = 9, 9.02
    await public_execution.process_protective_exits()
    async def forbidden_buy(*args, **kwargs):
        pytest.fail('A cancelled rotation cannot unlock a replacement buy')
    monkeypatch.setattr(public_execution, 'execute_pm_equity', forbidden_buy)
    await pm_rebalance._reconcile_intents(client)
    await pm_rebalance._reconcile_intents(client)
    assert intent['status'] == 'SELL_TERMINAL'


@pytest.mark.asyncio
async def test_rebalance_coverage_read_failure_blocks_sell(rebalance_case):
    _, client, db, _, _ = rebalance_case
    def fail(*args, **kwargs):
        raise RuntimeError('offline coverage unavailable')
    db.pm_rebalance_intents.find = fail
    client.bid, client.ask = 9, 9.02
    result = await public_execution.process_protective_exits()
    assert not result['ok'] and not client.submissions
