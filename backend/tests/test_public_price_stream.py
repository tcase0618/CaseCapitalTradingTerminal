from types import SimpleNamespace

import pytest

from services import public_price_stream


def test_price_stream_polling_is_bounded(monkeypatch):
    monkeypatch.setenv("PUBLIC_PRICE_STREAM_POLL_SECONDS", "0.01")
    monkeypatch.setenv("PUBLIC_PRICE_STREAM_SYMBOL_REFRESH_SECONDS", "1000")

    assert public_price_stream._poll_seconds() == 0.5
    assert public_price_stream._refresh_seconds() == 300.0


def test_price_stream_can_be_explicitly_disabled(monkeypatch):
    monkeypatch.setenv("PUBLIC_PRICE_STREAM_ENABLED", "false")

    assert public_price_stream.enabled() is False


def test_stream_quote_payload_preserves_sdk_timestamp_fallback():
    class Quote:
        ask_timestamp = None
        bid_timestamp = None
        last_timestamp = None

        def model_dump(self, **_kwargs):
            return {
                "instrument": {"symbol": "AAPL"},
                "bid": "99.90",
                "ask": "100.10",
                "last": "100.00",
            }

    symbol, row = public_price_stream._stream_quote_payload(
        SimpleNamespace(instrument=SimpleNamespace(symbol="AAPL"), new_quote=Quote())
    )
    assert symbol == "AAPL"
    assert row["bid"] == "99.90"
    assert row["ask"] == "100.10"


@pytest.mark.asyncio
async def test_stream_keeps_subscription_open_until_held_symbols_change(monkeypatch):
    import sys
    import types

    events = []
    held_sets = iter([["AAPL"], ["AAPL"], ["MSFT"]])

    class Stream:
        async def subscribe(self, **_kwargs):
            events.append("subscribe")
            return "subscription-1"

        async def unsubscribe(self, _subscription_id):
            events.append("unsubscribe")

    class Client:
        def __init__(self, **_kwargs):
            self.price_stream = Stream()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    sdk = types.SimpleNamespace(
        ApiKeyAuthConfig=lambda **_kwargs: object(),
        AsyncPublicApiClient=Client,
        AsyncPublicApiClientConfiguration=lambda **_kwargs: object(),
        InstrumentType=types.SimpleNamespace(EQUITY="EQUITY"),
        OrderInstrument=lambda **kwargs: kwargs,
        SubscriptionConfig=lambda **kwargs: kwargs,
    )
    monkeypatch.setitem(sys.modules, "public_api_sdk", sdk)
    monkeypatch.setattr(public_price_stream, "_held_symbols", lambda: _next(held_sets))
    monkeypatch.setattr(public_price_stream, "_refresh_seconds", lambda: 0.0)
    monkeypatch.setattr(public_price_stream, "_persist_state", _noop_async)
    monkeypatch.setattr(public_price_stream, "_poll_seconds", lambda: 1.0)
    monkeypatch.setattr("services.public_api.config", lambda: types.SimpleNamespace(secret="secret", sdk_token_validity_minutes=5, account_id="acct", api_base="https://example.test"))

    await public_price_stream._run_subscription(["AAPL"])

    assert events == ["subscribe", "unsubscribe"]


async def _next(values):
    return next(values)


async def _noop_async(**_kwargs):
    return None
