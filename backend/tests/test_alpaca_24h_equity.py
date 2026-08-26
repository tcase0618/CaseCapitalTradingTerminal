from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from services import trade_floor


ET = ZoneInfo("America/New_York")


def test_equity_order_session_classifies_24h_windows(monkeypatch):
    monkeypatch.setattr(trade_floor, "ALPACA_24H_EQUITY_ENABLED", True)

    assert trade_floor.equity_order_session(datetime(2026, 8, 25, 10, 0, tzinfo=ET))["label"] == "regular"
    assert trade_floor.equity_order_session(datetime(2026, 8, 25, 17, 0, tzinfo=ET))["label"] == "afterhours"
    assert trade_floor.equity_order_session(datetime(2026, 8, 25, 21, 0, tzinfo=ET))["label"] == "overnight"
    assert trade_floor.equity_order_session(datetime(2026, 8, 25, 5, 0, tzinfo=ET))["label"] == "premarket"
    assert trade_floor.equity_order_session(datetime(2026, 8, 22, 12, 0, tzinfo=ET))["tradable_now"] is False


def test_equity_order_session_can_disable_24h(monkeypatch):
    monkeypatch.setattr(trade_floor, "ALPACA_24H_EQUITY_ENABLED", False)

    session = trade_floor.equity_order_session(datetime(2026, 8, 25, 21, 0, tzinfo=ET))
    assert session["label"] == "closed"
    assert session["tradable_now"] is False
    assert session["extended_hours"] is False


def test_extended_asset_gate_blocks_non_overnight_tradable(monkeypatch):
    async def fake_get_asset(ticker):
        return {
            "symbol": ticker,
            "tradable": True,
            "fractionable": True,
            "overnight_tradable": False,
            "overnight_halted": False,
        }

    monkeypatch.setattr(trade_floor, "get_asset", fake_get_asset)
    session = {"extended_hours": True}

    import asyncio

    ok, status = asyncio.run(trade_floor._extended_order_asset_gate("XYZ", session))
    assert ok is False
    assert status["reason"] == "asset_not_overnight_tradable"


def test_asset_status_accepts_alpaca_overnight_attribute_shape(monkeypatch):
    async def fake_get_asset(ticker):
        return {
            "symbol": ticker,
            "tradable": True,
            "fractionable": True,
            "attributes": ["fractional_eh_enabled", "overnight_tradable"],
        }

    monkeypatch.setattr(trade_floor, "get_asset", fake_get_asset)

    import asyncio

    status = asyncio.run(trade_floor.equity_24h_asset_status("SPY"))
    assert status["ok"] is True
    assert status["reason"] == "ok"
    assert status["overnight_tradable"] is True


def test_extended_submit_rejects_before_order_post(monkeypatch):
    async def fake_trading_enabled(scope="system"):
        return True, {"ok": True}

    async def fake_asset_gate(ticker, session):
        return False, {"ok": False, "reason": "asset_overnight_halted"}

    async def fake_submit(*args, **kwargs):
        raise AssertionError("order should not be posted after failed 24h asset gate")

    async def fake_log(*args, **kwargs):
        return None

    from services import safety

    monkeypatch.setattr(safety, "trading_enabled", fake_trading_enabled)
    monkeypatch.setattr(trade_floor, "_alpaca_ready", lambda: True)
    monkeypatch.setattr(trade_floor, "paper_only", lambda: True)
    monkeypatch.setattr(trade_floor, "equity_order_session", lambda: {"tradable_now": True, "extended_hours": True})
    monkeypatch.setattr(trade_floor, "_extended_order_asset_gate", fake_asset_gate)
    monkeypatch.setattr(trade_floor, "_submit_fractional_limit_buy_now", fake_submit)
    monkeypatch.setattr(trade_floor, "log_activity", fake_log)

    import asyncio

    result = asyncio.run(trade_floor.submit_fractional_limit_buy("XYZ", 25, 10.12, client_order_id="test"))
    assert result is None


def test_extended_order_payload_sets_extended_hours(monkeypatch):
    posted = {}

    class Response:
        status_code = 201

        @staticmethod
        def json():
            return {"id": "ord_1", "symbol": "AAPL", "status": "accepted"}

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json):
            posted["url"] = url
            posted["payload"] = json
            return Response()

    monkeypatch.setattr(trade_floor.httpx, "AsyncClient", Client)

    import asyncio

    order = asyncio.run(trade_floor._submit_fractional_limit_buy_now(
        "AAPL",
        25,
        229.12,
        client_order_id="case-test",
        session={"extended_hours": True},
    ))
    assert order["id"] == "ord_1"
    assert posted["payload"]["type"] == "limit"
    assert posted["payload"]["time_in_force"] == "day"
    assert posted["payload"]["extended_hours"] is True


def test_execution_quote_falls_back_to_boats_feed(monkeypatch):
    calls = []

    class Response:
        def __init__(self, status_code, payload):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params):
            feed = params.get("feed")
            calls.append(feed)
            if feed == "overnight":
                return Response(403, {})
            if feed == "boats":
                return Response(200, {"quote": {"ap": 101.25, "bp": 101.2, "t": "2026-08-25T12:00:00Z"}})
            raise AssertionError("fresh BOATS quote should short-circuit")

    monkeypatch.setattr(trade_floor, "_alpaca_ready", lambda: True)
    monkeypatch.setattr(trade_floor, "ALPACA_EXECUTION_QUOTE_FEEDS", "overnight,boats,sip,iex,")
    monkeypatch.setattr(trade_floor.httpx, "AsyncClient", Client)

    import asyncio

    meta = asyncio.run(trade_floor.get_latest_ask_meta("AAPL"))
    assert calls == ["overnight", "boats"]
    assert meta["price"] == 101.25
    assert meta["source"] == "alpaca_boats_latest_quote"
