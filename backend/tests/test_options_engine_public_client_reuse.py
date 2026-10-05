import pytest

from services import options_engine, public_api


@pytest.mark.asyncio
async def test_public_option_chain_reuses_supplied_sdk_client(monkeypatch):
    class Client:
        def __init__(self):
            self.calls = []

        async def option_expirations(self, ticker):
            self.calls.append(("expirations", ticker))
            return {"expirations": ["2026-11-20"]}

        async def option_chain(self, ticker, *, expiration):
            self.calls.append(("chain", ticker, expiration))
            return {
                "options": [
                    {
                        "symbol": "AAPL261120C00200000",
                        "type": "C",
                        "strike": 200,
                        "bid": 4.9,
                        "ask": 5.1,
                        "last": 5.0,
                        "impliedVolatility": 0.3,
                        "openInterest": 100,
                        "volume": 10,
                        "delta": 0.5,
                        "expiration": "2026-11-20",
                    }
                ]
            }

        async def quotes(self, tickers):
            self.calls.append(("quotes", tickers))
            return {"quotes": [{"lastPrice": 200.0}]}

    client = Client()
    monkeypatch.setattr(public_api, "configured", lambda: True)
    monkeypatch.setattr(public_api, "PublicAPIClient", lambda: (_ for _ in ()).throw(AssertionError("should reuse caller client")))

    chain = await options_engine._fetch_public_options_data("AAPL", public_client=client)

    assert chain is not None
    assert chain["ticker"] == "AAPL"
    assert [call[0] for call in client.calls] == ["expirations", "chain", "quotes"]
