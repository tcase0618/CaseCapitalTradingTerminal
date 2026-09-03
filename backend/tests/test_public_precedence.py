import asyncio

from services import options_engine, pricer, public_api


def test_public_is_first_price_source(monkeypatch):
    monkeypatch.setattr(public_api, "configured", lambda: True)
    monkeypatch.setattr(pricer, "_alpaca_trade_meta", lambda *_a, **_k: asyncio.sleep(0, result=None))
    monkeypatch.setattr(pricer, "_finnhub_quote", lambda *_a, **_k: asyncio.sleep(0, result=99.0))

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def quotes(self, _symbols):
            return {"quotes": {"AAPL": {"lastPrice": 123.45}}}

    monkeypatch.setattr(public_api, "PublicAPIClient", lambda: Client())
    result = asyncio.run(pricer.live_price_meta("AAPL"))
    assert result["price"] == 123.45
    assert result["source"] == "public_quote"


def test_source_label_identifies_public_first(monkeypatch):
    monkeypatch.setattr(public_api, "configured", lambda: True)
    assert pricer.source_label().startswith("public+")


def test_public_research_source_does_not_authorize_execution(monkeypatch):
    monkeypatch.setattr(public_api, "configured", lambda: True)
    monkeypatch.setattr(pricer, "ALPACA_KEY", "")
    monkeypatch.setattr(pricer, "ALPACA_SECRET", "")
    assert pricer.source_label().startswith("public+")
    assert pricer.execution_source_label() == "unavailable"
