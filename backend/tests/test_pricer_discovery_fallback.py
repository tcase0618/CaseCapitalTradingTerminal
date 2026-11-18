import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import pricer, public_api


NOW = datetime(2026, 11, 18, 15, tzinfo=timezone.utc)


def trade(age=0, *, delayed=False, timestamp=True):
    return {
        "price": 101.0,
        "source": "alpaca_latest_trade",
        "provider_ts": (NOW - timedelta(seconds=age)).isoformat() if timestamp else None,
        "age_seconds": age if timestamp else None,
        "fresh": True,
        "delayed": delayed,
        "execution_eligible": not delayed,
    }


@pytest.fixture
def providers(monkeypatch):
    monkeypatch.setattr(pricer, "_now", lambda: NOW)
    monkeypatch.setenv("SCANNER_LIVE_PRICE_MAX_AGE_SECONDS", "1800")
    monkeypatch.delenv("ALPACA_STOCK_FEED", raising=False)
    monkeypatch.setattr(pricer, "_scanner_alpaca_feeds", lambda: ["sip", "iex"])
    monkeypatch.setattr(pricer, "_public_configured", lambda: True)
    quote = {"symbol": "TEST", "lastPrice": 100.0}

    class Client:
        def __init__(self, **kwargs):
            assert kwargs == {"use_sdk": False}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def quotes(self, symbols):
            return {"quotes": [quote]}

    monkeypatch.setattr(public_api, "PublicAPIClient", Client)
    alpaca = AsyncMock(return_value=None)
    finnhub = AsyncMock(return_value=99.0)
    monkeypatch.setattr(pricer, "_alpaca_trade_meta", alpaca)
    monkeypatch.setattr(pricer, "_finnhub_quote", finnhub)
    return SimpleNamespace(quote=quote, alpaca=alpaca, finnhub=finnhub)


@pytest.mark.parametrize("age", [0, 89, 90])
def test_public_within_caller_budget_stays_preferred(providers, age):
    providers.quote["lastTimestamp"] = (NOW - timedelta(seconds=age)).isoformat()
    result = asyncio.run(pricer.live_price_meta("test", max_age_seconds=90))
    assert result["source"] == "public_quote"
    assert result["fresh"] is True
    providers.alpaca.assert_not_awaited()


@pytest.mark.parametrize("age", [None, 91, 120, 1800, 2000, -10])
def test_invalid_public_uses_fresh_live_alternative(providers, age):
    if age is not None:
        providers.quote["lastTimestamp"] = (NOW - timedelta(seconds=age)).isoformat()
    providers.alpaca.return_value = trade()
    result = asyncio.run(pricer.live_price_meta("TEST", max_age_seconds=90))
    assert result["source"] == "alpaca_latest_trade"
    assert result["fresh"] is True
    assert result["execution_eligible"] is True
    providers.alpaca.assert_awaited_once_with("TEST", feed="sip")


@pytest.mark.parametrize("age", [None, 120, 2000, -10])
def test_failed_fallback_preserves_public_diagnostics(providers, age):
    if age is not None:
        providers.quote["lastTimestamp"] = (NOW - timedelta(seconds=age)).isoformat()
    providers.alpaca.return_value = trade(2001)
    result = asyncio.run(pricer.live_price_meta("TEST", max_age_seconds=90))
    assert result["source"] == "public_quote"
    assert result["price"] == 100
    assert result["provider_ts"] == providers.quote.get("lastTimestamp")
    assert result["raw"] == providers.quote
    assert result["fresh"] is False
    assert result["execution_eligible"] is False
    assert result["warning"]
    providers.finnhub.assert_not_awaited()


def test_default_budget_preserves_fresh_public_precedence(providers):
    providers.quote["lastTimestamp"] = (NOW - timedelta(seconds=120)).isoformat()
    result = asyncio.run(pricer.live_price_meta("TEST"))
    assert result["source"] == "public_quote"
    assert result["fresh"] is True
    providers.alpaca.assert_not_awaited()


def test_default_budget_still_falls_back_from_stale_public(providers):
    providers.quote["lastTimestamp"] = (NOW - timedelta(seconds=1801)).isoformat()
    providers.alpaca.return_value = trade()
    assert asyncio.run(pricer.live_price_meta("TEST"))["source"] == "alpaca_latest_trade"


@pytest.mark.parametrize("first", [trade(120), trade(-10), trade(timestamp=False), trade(delayed=True)])
def test_unusable_first_feed_does_not_hide_fresh_second_feed(providers, first):
    providers.alpaca.side_effect = [first, trade()]
    result = asyncio.run(pricer.live_price_meta("TEST", max_age_seconds=90))
    assert result["fresh"] is True
    assert result["delayed"] is False
    assert providers.alpaca.await_count == 2


def test_delayed_feed_name_cannot_be_promoted(monkeypatch, providers):
    monkeypatch.setattr(pricer, "_scanner_alpaca_feeds", lambda: ["delayed_sip", "sip"])
    providers.alpaca.side_effect = [trade(), trade()]
    result = asyncio.run(pricer.live_price_meta("TEST", max_age_seconds=90))
    assert result["delayed"] is False
    assert providers.alpaca.await_count == 2


def test_only_delayed_alternative_preserves_stale_public(providers):
    providers.alpaca.return_value = trade(delayed=True)
    result = asyncio.run(pricer.live_price_meta("TEST", max_age_seconds=90))
    assert result["source"] == "public_quote"
    assert result["fresh"] is False


def test_delayed_only_without_public_is_diagnostic(monkeypatch, providers):
    monkeypatch.setattr(pricer, "_public_configured", lambda: False)
    providers.alpaca.return_value = trade(delayed=True)
    result = asyncio.run(pricer.live_price_meta("TEST", max_age_seconds=90))
    assert result["delayed"] is True
    assert result["fresh"] is False
    assert result["execution_eligible"] is False
    assert result["premarket_confirmed"] is False


def test_feed_exception_allows_remaining_fallback(providers):
    providers.alpaca.side_effect = [RuntimeError("unavailable"), trade()]
    assert asyncio.run(pricer.live_price_meta("TEST", max_age_seconds=90))["fresh"] is True


def test_explicit_budget_does_not_invent_finnhub_timestamp(monkeypatch, providers):
    monkeypatch.setattr(pricer, "_public_configured", lambda: False)
    result = asyncio.run(pricer.live_price_meta("TEST", max_age_seconds=90))
    assert result["price"] is None
    assert result["fresh"] is False
    providers.finnhub.assert_not_awaited()


@pytest.mark.parametrize("budget", [None, 90])
def test_batch_forwards_optional_budget_and_preserves_cache_time(monkeypatch, budget):
    meta = {"ticker": "TEST", **trade()}
    live = AsyncMock(return_value=meta)
    update = AsyncMock()
    monkeypatch.setattr(pricer, "live_price_meta", live)
    monkeypatch.setattr(pricer, "get_db", lambda: SimpleNamespace(price_cache=SimpleNamespace(update_one=update)))
    monkeypatch.setattr(pricer, "_now", lambda: NOW)
    result = asyncio.run(pricer.batch_live_price_meta(["test", "TEST"], max_age_seconds=budget))
    if budget is None:
        live.assert_awaited_once_with("TEST")
    else:
        live.assert_awaited_once_with("TEST", max_age_seconds=90)
    assert result == {"TEST": meta}
    assert update.call_args.args[1]["$set"]["provider_ts"] == meta["provider_ts"]
