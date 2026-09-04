from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from services import public_execution


def test_public_trade_quantity_prefers_reconciled_remaining_quantity():
    assert public_execution._qty({"qty_remaining": 1.25, "quantity": 0.0}) == 1.25


def test_public_trade_quantity_supports_portfolio_quantity():
    assert public_execution._qty({"quantity": "2.5"}) == 2.5


def test_public_buying_power_prefers_buying_power_over_cash():
    assert public_execution._numeric_field({"cash": 0, "buyingPower": "12.00"}, {"cash", "buying_power"}) == 12.0


@pytest.mark.asyncio
async def test_public_execution_uses_fresh_quote_and_submits_order(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.submitted = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def portfolio(self):
            return {"positions": []}

        async def accounts(self):
            return {"accounts": [{"buyingPower": 12.0}]}

        async def quotes(self, symbols):
            row = {"symbol": "AAPL", "ask": 150.25, "quoteTime": datetime.now(timezone.utc).isoformat()}
            return {"quotes": [row]}

        async def submit_equity_order(self, **kwargs):
            self.submitted.append(kwargs)
            return {"preflight": {"outcome": "SUCCESS"}, "order": {"orderId": "order-1"}}

    class FakeCollection:
        async def insert_one(self, _doc):
            return None

    fake_client = FakeClient()
    monkeypatch.setattr(public_execution.public_api, "PublicAPIClient", lambda: fake_client)
    monkeypatch.setattr(public_execution, "enabled", lambda: True)
    monkeypatch.setattr(public_execution.execution_safety, "add_risk_allowed", lambda _scope: _allowed())
    monkeypatch.setattr(public_execution.execution_safety, "claim_execution_intent", lambda **_kwargs: _claimed())
    monkeypatch.setattr(public_execution.execution_safety, "mark_execution_intent", lambda *_args, **_kwargs: _marked())
    monkeypatch.setattr(public_execution, "get_db", lambda: SimpleNamespace(tf_trades=FakeCollection()))
    monkeypatch.setattr(public_execution, "log_activity", _marked)

    result = await public_execution.execute_pm_equity(
        [{"ticker": "AAPL", "action": "STARTER", "allocation_usd": 6, "stop_price": 140}],
        cycle_id="cycle-1",
    )

    assert len(result["executed"]) == 1
    assert result["rejected"] == []
    assert fake_client.submitted[0]["session"] == "TWENTY_FOUR_HOURS"


async def _allowed():
    return True, {"trading_enabled": True}


async def _claimed():
    return {"ok": True}


async def _marked(*_args, **_kwargs):
    return None
