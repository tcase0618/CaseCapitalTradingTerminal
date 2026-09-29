from types import SimpleNamespace

import pytest

from services import public_execution


@pytest.mark.asyncio
async def test_public_phase_one_submits_a_fractional_limit_trim(monkeypatch):
    trade = {
        "client_order_id": "public-entry-1",
        "broker_base": "public",
        "ticker": "RUN",
        "status": "OPEN",
        "fill_status": "FILLED",
        "qty_total": 10.0,
        "qty_remaining": 10.0,
        "filled_avg_price": 10.0,
        "current_stop": 9.0,
        "phase": 1,
        "public_phase_plan": {
            "enabled": True,
            "phase1_target": 12.0,
            "phase2_target": 13.0,
            "phase1_close_pct": 0.40,
            "phase2_close_pct": 0.30,
            "phase3_trail_pct": 0.50,
        },
    }
    updates = []

    class Cursor:
        async def to_list(self, _limit):
            return [dict(trade)]

    class Trades:
        def find(self, *_args, **_kwargs):
            return Cursor()

        async def update_one(self, _query, update):
            updates.append(update["$set"])

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def quotes(self, _tickers):
            return {"quotes": [{"symbol": "RUN", "bid": 12.0, "ask": 12.0, "quoteTime": "2026-09-29T16:00:00+00:00"}]}

        async def submit_equity_order(self, **kwargs):
            self.submitted = kwargs
            return {"preflight": {"estimatedCost": 1}, "order": {"orderId": "phase-order-1"}}

    client = Client()

    async def claim(**_kwargs):
        return {"ok": True}

    async def marked(*_args, **_kwargs):
        return None

    monkeypatch.setattr(public_execution, "enabled", lambda: True)
    monkeypatch.setattr(public_execution, "get_db", lambda: SimpleNamespace(tf_trades=Trades()))
    monkeypatch.setattr(public_execution.public_api, "PublicAPIClient", lambda **_kwargs: client)
    monkeypatch.setattr(public_execution.safety, "quote_is_fresh", lambda _meta: (True, 0))
    monkeypatch.setattr(public_execution.execution_safety, "claim_execution_intent", claim)
    monkeypatch.setattr(public_execution.execution_safety, "mark_execution_intent", marked)
    monkeypatch.setattr(public_execution, "_public_session_now", lambda *_args: "CORE")

    result = await public_execution.process_public_phase_exits()

    assert result["submitted"] == [{"ticker": "RUN", "phase": 1, "order_id": "phase-order-1", "qty": 4.0, "limit_price": 11.94}]
    assert client.submitted["quantity"] == 4.0
    assert client.submitted["session"] == "CORE"
    assert updates[0]["public_phase_order_id"] == "phase-order-1"


def test_no_cap_lottery_plan_never_creates_a_partial_profit_target():
    plan = public_execution._public_phase_plan(10.0, 16.0, no_capped_tp=True, proxy_target=False)

    assert plan == {"enabled": False, "reason": "no_verified_capped_target"}


@pytest.mark.asyncio
async def test_public_phase_fill_advances_phase_and_raises_remaining_stop(monkeypatch):
    trade = {
        "client_order_id": "public-entry-2",
        "broker_base": "public",
        "ticker": "RUN",
        "status": "OPEN",
        "filled_avg_price": 10.0,
        "current_stop": 9.0,
        "phase": 1,
        "public_phase_order_id": "phase-order-2",
        "public_phase_order_phase": 1,
        "public_phase_filled_qty": 0.0,
        "public_phase_trigger_price": 12.0,
        "phases_hit": {},
        "public_phase_plan": {"enabled": True},
    }
    updates = []
    exits = []

    class Cursor:
        async def to_list(self, _limit):
            return [dict(trade)]

    class Trades:
        def find(self, *_args, **_kwargs):
            return Cursor()

        async def update_one(self, _query, update):
            updates.append(update["$set"])

    class Exits:
        async def insert_one(self, row):
            exits.append(row)

    class Client:
        async def get_order(self, _order_id):
            return {"status": "FILLED", "filledQuantity": 4.0, "averagePrice": 12.1}

    monkeypatch.setattr(
        public_execution,
        "get_db",
        lambda: SimpleNamespace(tf_trades=Trades(), public_phase_exits=Exits()),
    )

    result = await public_execution._reconcile_public_phase_exits(Client(), {})

    assert result == {"updates": 1, "poll_errors": 0}
    assert exits[0]["phase"] == 1
    assert exits[0]["qty"] == 4.0
    assert updates[0]["phase"] == 2
    assert updates[0]["current_stop"] == 10.0
    assert updates[0]["public_phase_order_id"] is None
