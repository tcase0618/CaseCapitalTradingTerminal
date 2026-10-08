"""Offline broker lifecycle tests: persisted state survives successive polls."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services import lottery, public_execution, public_api, execution_safety
from test_claude_audit_exit_recovery import MemoryCollection


@pytest.fixture
def lifecycle(monkeypatch):
    trade = dict(client_order_id="entry-client", public_order_id="entry", ticker="TEST",
                 broker_base="public", status="OPEN", fill_status="FILLED",
                 qty_total=2.0, qty_remaining=2.0, filled_avg_price=10,
                 current_stop=9, pm_active_stop=9)
    ticket = dict(ticket_id="ticket", broker="public", broker_order_id="entry",
                  status="OPEN", quantity=2, quantity_remaining=2, entry_fill_price=10)
    db = SimpleNamespace(tf_trades=MemoryCollection([trade]), ll_tickets=MemoryCollection([ticket]),
                         bot_state=MemoryCollection(), execution_intents=MemoryCollection())

    class Client:
        def __init__(self):
            self.orders = {"entry": {"status": "FILLED", "filledQuantity": 2, "averagePrice": 10}}
            self.quantity = 2
            self.submitted = []
            self.cancel_fills = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def portfolio(self):
            return {"positions": [{"symbol": "TEST", "quantity": self.quantity}]}

        async def get_order(self, order_id):
            return dict(self.orders[order_id])

        async def cancel_order(self, order_id):
            self.orders[order_id]["status"] = "CANCELLED"
            if self.cancel_fills is not None:
                self.orders[order_id]["filledQuantity"] = self.cancel_fills
            return {"status": "CANCELLED"}

        async def submit_equity_order(self, **kwargs):
            self.submitted.append(kwargs)
            order_id = f"stop-{len(self.submitted)}"
            self.orders[order_id] = {"status": "NEW"}
            return {"order": {"orderId": order_id}}

    client = Client()
    async def quiet(*args, **kwargs):
        return {}
    async def zero(*args, **kwargs):
        return 0
    async def broker_orders(*args):
        return {}, {}
    async def phase(*args):
        return {"updates": 0, "poll_errors": 0}
    monkeypatch.setattr(public_api, "PublicAPIClient", lambda **kwargs: client)
    monkeypatch.setattr(public_execution, "enabled", lambda: True)
    monkeypatch.setattr(public_execution, "get_db", lambda: db)
    monkeypatch.setattr(execution_safety, "get_db", lambda: db)
    monkeypatch.setattr(lottery, "get_db", lambda: db)
    monkeypatch.setattr(public_execution, "_broker_orders_with_search", broker_orders)
    monkeypatch.setattr(public_execution, "_import_unmanaged_broker_positions", quiet)
    monkeypatch.setattr(public_execution, "_reconcile_public_phase_exits", phase)
    monkeypatch.setattr(public_execution, "_reconcile_closed_broker_history", quiet)
    monkeypatch.setattr(public_execution, "_import_unattributed_closed_broker_history", quiet)
    monkeypatch.setattr(public_execution, "label_legacy_unattributed_positions", zero)
    monkeypatch.setattr(public_execution, "sync_public_learning_ledger", quiet)
    monkeypatch.setattr(lottery, "record_filled_lottery_entry", quiet)
    return trade, ticket, client, db


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["CANCELLED", "CANCELED", "EXPIRED", "REJECTED", "FAILED"])
async def test_terminal_partial_entry_has_protection(lifecycle, status):
    trade, _, client, _ = lifecycle
    trade.update(fill_status="PENDING", qty_total=0, qty_remaining=0)
    client.orders["entry"].update(status=status, filledQuantity=0.5)
    client.quantity = 0.5
    assert (await public_execution.reconcile())["ok"]
    assert client.submitted[0]["quantity"] == 0.5
    assert trade["protective_order_id"] == "stop-1"
    assert trade["qty_remaining"] == 0.5


@pytest.mark.asyncio
async def test_ttl_cancelled_partial_entry_protected_same_pass(lifecycle):
    trade, _, client, _ = lifecycle
    trade.update(fill_status="PENDING", qty_total=0, qty_remaining=0,
                 submitted_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
    client.orders["entry"].update(status="PARTIALLY_FILLED", filledQuantity=0.5)
    client.quantity = 0.5
    assert (await public_execution.reconcile())["ok"]
    assert len(client.submitted) == 1 and client.submitted[0]["quantity"] == 0.5


@pytest.mark.asyncio
async def test_replacement_resets_order_fill_counter_and_counts_new_fills(lifecycle):
    trade, ticket, client, _ = lifecycle
    trade.update(qty_remaining=1.6, protective_order_id="old", protective_order_qty=2,
                 protective_stop_price=8, protective_filled_qty=0.4)
    ticket["quantity_remaining"] = 1.6
    client.quantity = 1.6
    client.orders["old"] = {"status": "PARTIALLY_FILLED", "filledQuantity": 0.4, "averagePrice": 11}
    await public_execution.reconcile()
    assert client.submitted[0]["quantity"] == 1.6
    assert trade["protective_filled_qty"] == 0
    client.orders["stop-1"] = {"status": "PARTIALLY_FILLED", "filledQuantity": 0.2, "averagePrice": 12}
    client.quantity = 1.4
    await public_execution.reconcile()
    assert trade["qty_remaining"] == pytest.approx(1.4)
    assert ticket["quantity_remaining"] == pytest.approx(1.4)


@pytest.mark.asyncio
async def test_cancellation_racing_fill_deferred_before_replacement(lifecycle):
    trade, ticket, client, _ = lifecycle
    trade.update(protective_order_id="old", protective_order_qty=2, protective_stop_price=8)
    client.orders["old"] = {"status": "NEW", "filledQuantity": 0, "averagePrice": 11}
    client.cancel_fills = 0.5
    await public_execution.reconcile()
    assert client.submitted == []
    assert ticket["quantity_remaining"] == 1.5
    assert trade["protective_order_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [False, True])
async def test_emergency_cumulative_fill_not_counted_twice(lifecycle, terminal):
    trade, ticket, client, _ = lifecycle
    trade.update(emergency_exit_order_id="sell", emergency_exit_order_qty=2)
    client.orders["sell"] = {"status": "CANCELLED" if terminal else "PARTIALLY_FILLED",
                             "filledQuantity": 0.5, "averagePrice": 11}
    client.quantity = 1.5
    await public_execution.reconcile()
    await public_execution.reconcile()
    assert ticket["quantity_remaining"] == 1.5
    assert trade["qty_remaining"] == 1.5
    assert len(ticket["exit_order_fills"]) == 1


@pytest.mark.asyncio
async def test_lottery_cumulative_fill_prices_only_new_slice(lifecycle):
    _, ticket, _, _ = lifecycle
    async def record(quantity, price):
        return await lottery.close_filled_lottery_entry(broker="public", entry_order_id="entry",
            exit_order_id="sell", exit_quantity=quantity, exit_price=price, reason="test")
    await record(0.5, 10)
    await record(0.5, 10)
    await record(1, 11)
    assert ticket["quantity_remaining"] == 1
    assert ticket["exit_quantity"] == 0.5
    assert ticket["exit_fill_price"] == 12
    assert (await record(0.5, 10))["reason"] == "exit_fill_already_accounted"
