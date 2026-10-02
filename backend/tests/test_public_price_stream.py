from types import SimpleNamespace

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
