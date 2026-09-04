from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from services import portfolio_manager, public_execution


def test_public_trade_quantity_prefers_reconciled_remaining_quantity():
    assert public_execution._qty({"qty_remaining": 1.25, "quantity": 0.0}) == 1.25


def test_public_trade_quantity_supports_portfolio_quantity():
    assert public_execution._qty({"quantity": "2.5"}) == 2.5


def test_public_buying_power_prefers_buying_power_over_cash():
    assert public_execution._numeric_field({"cash": 0, "buyingPower": "12.00"}, {"cash", "buying_power"}) == 12.0


@pytest.mark.asyncio
async def test_public_reconciliation_health_fails_closed_without_success_marker(monkeypatch):
    class FakeCollection:
        async def find_one(self, *_args, **_kwargs):
            return None

    monkeypatch.setattr(public_execution, "get_db", lambda: SimpleNamespace(bot_state=FakeCollection()))
    result = await public_execution.reconciliation_health()
    assert result == {"ok": False, "reason": "public_reconciliation_not_initialized"}


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
        def __init__(self):
            self.docs = []

        async def insert_one(self, _doc):
            self.docs.append(_doc)
            return None

    fake_client = FakeClient()
    fake_trades = FakeCollection()
    monkeypatch.setattr(public_execution.public_api, "PublicAPIClient", lambda: fake_client)
    monkeypatch.setattr(public_execution, "enabled", lambda: True)
    monkeypatch.setattr(public_execution, "reconciliation_health", lambda: _healthy())
    monkeypatch.setattr(public_execution.execution_safety, "add_risk_allowed", lambda _scope: _allowed())
    monkeypatch.setattr(public_execution.execution_safety, "claim_execution_intent", lambda **_kwargs: _claimed())
    monkeypatch.setattr(public_execution.execution_safety, "mark_execution_intent", lambda *_args, **_kwargs: _marked())
    monkeypatch.setattr(public_execution, "get_db", lambda: SimpleNamespace(tf_trades=fake_trades))
    monkeypatch.setattr(public_execution, "log_activity", _marked)
    monkeypatch.setattr("services.trading_halts.fetch_halts", lambda: _clear_halts())

    pm_row = portfolio_manager.evaluate_rows([{
        "ticker": "AAPL",
        "price": 150.25,
        "target_blended": 180,
        "stop_loss": 140,
        "source_scan": "lottery_gap",
        "scanner_family": "LOTTERY",
        "signals": ["GAP/SURGE", "RVOL"],
        "strategy_views": [{"screener_id": "lottery_gap", "family": "LOTTERY", "lane": "DAY2_CONTINUATION"}],
    }], equity=1000, mode="BALANCED")[0]
    pm_row.update({"action": "STARTER", "allocation_usd": 6})
    result = await public_execution.execute_pm_equity(
        [pm_row],
        cycle_id="cycle-1",
    )

    assert len(result["executed"]) == 1
    assert result["rejected"] == []
    assert fake_client.submitted[0]["session"] == "TWENTY_FOUR_HOURS"
    assert fake_trades.docs[0]["strategy_id"] == "lottery_gap"
    assert fake_trades.docs[0]["screener_id"] == "lottery_gap"
    assert fake_trades.docs[0]["scanner_family"] == "LOTTERY"
    assert fake_trades.docs[0]["strategy_lanes"] == ["DAY2_CONTINUATION"]
    assert fake_trades.docs[0]["strategy_attribution"]["strategy_id"] == "lottery_gap"
    assert fake_trades.docs[0]["current_stop"] == 140.0
    assert fake_trades.docs[0]["pm_active_stop"] == 140.0


async def _allowed():
    return True, {"trading_enabled": True}


async def _claimed():
    return {"ok": True}


async def _marked(*_args, **_kwargs):
    return None


async def _healthy():
    return {"ok": True, "age_seconds": 1}


async def _clear_halts():
    return {"ok": True, "halts": []}
