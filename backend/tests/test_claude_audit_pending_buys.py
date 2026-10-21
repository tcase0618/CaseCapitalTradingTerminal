"""Offline LOW-002 reservations; pending entries are not filled holdings."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import public_execution, portfolio_manager
from test_claude_audit_exit_recovery import MemoryCollection


@pytest.mark.asyncio
async def test_pending_ledger_and_broker_buys_reserve_symbols(monkeypatch):
    rows = [dict(broker_base='public', status='OPEN', fill_status='PENDING', ticker='LOCAL'),
            dict(broker_base='public', status='CLOSED', fill_status='FILLED', ticker='CLOSED'),
            dict(broker_base='other', status='OPEN', fill_status='PENDING', ticker='OTHER')]
    monkeypatch.setattr(public_execution, 'get_db', lambda: SimpleNamespace(tf_trades=MemoryCollection(rows)))
    result = await public_execution._pending_buy_symbols({'orders': [
        {'orderSide': 'BUY', 'instrument': {'symbol': 'BROKER'}, 'status': 'NEW'},
        {'side': 'BUY', 'symbol': 'CANCEL', 'status': 'PENDING_CANCEL'},
        {'side': 'BUY', 'symbol': 'PARTIAL', 'status': 'PARTIALLY_FILLED'},
        {'side': 'SELL', 'symbol': 'EXIT', 'status': 'NEW'},
        {'side': 'BUY', 'symbol': 'DEAD', 'status': 'CANCELLED'}]})
    assert result == {'LOCAL', 'BROKER', 'CANCEL', 'PARTIAL'}


def test_pm_does_not_reapprove_pending_buy():
    rows = [{'ticker': 'TEST', 'action': 'STARTER', 'allocation_usd': 6}]
    portfolio_manager._apply_equity_book_constraints(rows, {
        'broker': 'public', 'positions': [], 'pending_buy_symbols': ['TEST'], 'cash_buying_power': 100})
    assert rows[0]['action'] == 'WATCH' and rows[0]['allocation_usd'] == 0


@pytest.mark.asyncio
async def test_pm_context_keeps_pending_reservations(monkeypatch):
    monkeypatch.setattr(public_execution, 'enabled', lambda: True)
    monkeypatch.setattr(public_execution, 'portfolio_state', AsyncMock(return_value={
        'ok': True, 'equity': 100, 'cash_buying_power': 100, 'positions': [], 'pending_buy_symbols': ['TEST']}))
    assert (await portfolio_manager._equity_book_context())['pending_buy_symbols'] == ['TEST']


@pytest.mark.asyncio
@pytest.mark.parametrize('malformed', [True, False])
async def test_pending_coverage_fails_closed(monkeypatch, malformed):
    rows = [] if malformed else [dict(broker_base='public', status='OPEN', fill_status='PENDING', ticker=f'T{i}') for i in range(500)]
    monkeypatch.setattr(public_execution, 'get_db', lambda: SimpleNamespace(tf_trades=MemoryCollection(rows)))
    with pytest.raises(RuntimeError):
        await public_execution._pending_buy_symbols({'orders': {} if malformed else []})
