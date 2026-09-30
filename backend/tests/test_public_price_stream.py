from services import public_price_stream


def test_price_stream_polling_is_bounded(monkeypatch):
    monkeypatch.setenv("PUBLIC_PRICE_STREAM_POLL_SECONDS", "0.01")
    monkeypatch.setenv("PUBLIC_PRICE_STREAM_SYMBOL_REFRESH_SECONDS", "1000")

    assert public_price_stream._poll_seconds() == 0.5
    assert public_price_stream._refresh_seconds() == 300.0


def test_price_stream_can_be_explicitly_disabled(monkeypatch):
    monkeypatch.setenv("PUBLIC_PRICE_STREAM_ENABLED", "false")

    assert public_price_stream.enabled() is False
