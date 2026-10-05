from datetime import datetime, timezone
import json

import httpx
import pytest

from services import public_api


def _cfg(**overrides):
    values = {
        "enabled": True,
        "api_base": "https://api.public.com",
        "secret": "secret",
        "access_token": "token",
        "account_id": "acct-1",
        "research_only": True,
        "live_equity_enabled": False,
        "max_account_usd": 100.0,
        "max_order_usd": 5.0,
        "timeout_seconds": 12.0,
    }
    values.update(overrides)
    return public_api.PublicAPIConfig(**values)


@pytest.mark.asyncio
async def test_public_read_only_endpoints_use_bearer_and_never_mutate():
    seen = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("authorization"), request.content))
        if request.url.path.endswith("/account"):
            return httpx.Response(200, json={"accounts": [{"accountId": "acct-1"}]})
        return httpx.Response(200, json={"quotes": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        async with public_api.PublicAPIClient(_cfg(), http) as client:
            accounts = await client.accounts()
            quotes = await client.quotes(["aapl", "AAPL"])
    assert accounts["accounts"][0]["accountId"] == "acct-1"
    assert quotes["quotes"] == []
    assert seen == [
        ("GET", "/userapigateway/trading/account", "Bearer token", b""),
        ("POST", "/userapigateway/marketdata/acct-1/quotes", "Bearer token", b'{"instruments":[{"symbol":"AAPL","type":"EQUITY"}]}'),
    ]


@pytest.mark.asyncio
async def test_public_rest_quotes_normalize_timestamp_for_execution_freshness():
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"quotes": [{
            "instrument": {"symbol": "AAPL", "type": "EQUITY"},
            "last": "200.00",
            "lastTimestamp": "2026-09-14T12:00:00Z",
            "bid": "199.90",
            "bidTimestamp": "2026-09-14T12:00:01Z",
            "ask": "200.10",
            "askTimestamp": "2026-09-14T12:00:02Z",
        }]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with public_api.PublicAPIClient(_cfg(), http) as client:
            quote = (await client.quotes(["AAPL"]))["quotes"][0]
    assert quote["symbol"] == "AAPL"
    assert quote["quoteTime"] == "2026-09-14T12:00:02Z"
    assert quote["executableQuoteTime"] == "2026-09-14T12:00:01Z"


def test_public_sdk_quote_uses_newest_market_timestamp():
    class Quote:
        def model_dump(self, **_kwargs):
            return {
                "instrument": {"symbol": "AAPL"},
                "last": "100.00",
                "bid": "99.90",
                "ask": "100.10",
                "lastTimestamp": "2026-09-09T12:00:00Z",
                "bidTimestamp": "2026-09-09T12:01:00Z",
                "askTimestamp": "2026-09-09T12:00:30Z",
            }

    payload = public_api._sdk_quote_payload(Quote())
    assert payload["quoteTime"] == "2026-09-09T12:01:00Z"
    assert payload["executableQuoteTime"] == "2026-09-09T12:00:30Z"


@pytest.mark.asyncio
async def test_public_option_reads_fail_explicitly_without_sdk_not_against_retired_rest_routes():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: httpx.Response(500))) as http:
        async with public_api.PublicAPIClient(_cfg(sdk_enabled=False), http, use_sdk=False) as client:
            with pytest.raises(public_api.PublicAPIError, match="require the enabled Public SDK"):
                await client.option_expirations("NVDA")
            with pytest.raises(public_api.PublicAPIError, match="require the enabled Public SDK"):
                await client.option_chain("NVDA", expiration="2026-10-02")
            with pytest.raises(public_api.PublicAPIError, match="require the enabled Public SDK"):
                await client.option_greeks("NVDA261002C00050000")


def test_public_defaults_fail_closed_and_mutations_are_blocked(monkeypatch):
    monkeypatch.delenv("PUBLIC_RESEARCH_ONLY", raising=False)
    state = public_api.safety_state(_cfg())
    assert state["research_only"] is True
    assert state["live_order_mutation_allowed"] is False
    with pytest.raises(public_api.PublicTradingBlocked):
        public_api.place_order("AAPL")


def test_public_equity_order_payload_is_deterministic_and_not_market():
    payload = public_api.PublicAPIClient._equity_order_payload(
        symbol="aapl",
        side="buy",
        amount=4,
        limit_price=150.25,
        order_id="cc-aapl-cycle-1",
    )
    assert payload["orderId"] == public_api.PublicAPIClient._equity_order_payload(
        symbol="AAPL", side="BUY", amount=4, limit_price=150.25, order_id="cc-aapl-cycle-1"
    )["orderId"]
    assert payload["instrument"] == {"symbol": "AAPL", "type": "EQUITY"}
    assert payload["orderType"] == "LIMIT"
    assert payload["amount"] == "4.00"
    assert payload["limitPrice"] == "150.25"
    assert payload["useMargin"] is False
    assert "quantity" not in payload


def test_public_gtd_stop_payload_keeps_subdollar_tick_precision():
    expires = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    payload = public_api.PublicAPIClient._equity_order_payload(
        symbol="penny",
        side="SELL",
        quantity=2,
        stop_price=0.1234,
        limit_price=0.1221,
        time_in_force="GTD",
        expiration_time=expires,
    )
    assert payload["orderType"] == "STOP_LIMIT"
    assert payload["expiration"] == {"timeInForce": "GTD", "expirationTime": "2026-10-01T12:00:00Z"}
    assert payload["stopPrice"] == "0.1234"
    assert payload["limitPrice"] == "0.1221"


def test_public_rejects_invalid_gtd_24_hour_combination():
    with pytest.raises(public_api.PublicAPIError, match="require DAY"):
        public_api.PublicAPIClient._equity_order_payload(
            symbol="AAPL", side="SELL", quantity=1, stop_price=10,
            limit_price=9.9, time_in_force="GTD", session="TWENTY_FOUR_HOURS",
            expiration_time=datetime(2026, 10, 1, tzinfo=timezone.utc),
        )


def test_public_bracket_payload_requires_documented_core_whole_share_shape():
    payload = public_api.PublicAPIClient._equity_order_payload(
        symbol="AAPL",
        side="BUY",
        quantity=2,
        limit_price=150.25,
        session="CORE",
        order_class="OTO",
        stop_loss=140.00,
        stop_loss_limit=139.50,
    )
    assert payload["orderClass"] == "OTO"
    assert payload["quantity"] == "2"
    assert payload["stopLoss"] == {"stopPrice": "140.00", "limitPrice": "139.50"}
    with pytest.raises(public_api.PublicAPIError, match="whole-share quantity"):
        public_api.PublicAPIClient._equity_order_payload(
            symbol="AAPL", side="BUY", amount=6, limit_price=150, order_class="OTO", stop_loss=140,
        )
    with pytest.raises(public_api.PublicAPIError, match="CORE"):
        public_api.PublicAPIClient._equity_order_payload(
            symbol="AAPL", side="BUY", quantity=1, limit_price=150, session="TWENTY_FOUR_HOURS", order_class="OTO", stop_loss=140,
        )


def test_public_accepts_successful_empty_broker_response():
    assert public_api.PublicAPIClient._decode(httpx.Response(204)) == {}


@pytest.mark.asyncio
async def test_public_order_mutations_fail_closed_in_research_mode():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(500))) as http:
        async with public_api.PublicAPIClient(_cfg(), http) as client:
            with pytest.raises(public_api.PublicTradingBlocked):
                await client.place_order({"orderId": "x"})


@pytest.mark.asyncio
async def test_public_equity_submit_preflights_before_placing():
    seen = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        if request.url.path.endswith("/preflight/single-leg"):
            return httpx.Response(200, json={"outcome": "SUCCESS", "estimatedCost": "4.00"})
        if request.url.path.endswith("/order"):
            return httpx.Response(200, json={"orderId": "public-order-1"})
        return httpx.Response(404)

    cfg = _cfg(research_only=False, live_equity_enabled=True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with public_api.PublicAPIClient(cfg, http) as client:
            result = await client.submit_equity_order(
                symbol="AAPL", side="BUY", amount=4, limit_price=150.25,
                client_order_id="cc-public-aapl-1",
            )
    assert result["order"]["orderId"] == "public-order-1"
    assert seen == [
        ("POST", "/userapigateway/trading/acct-1/preflight/single-leg"),
        ("POST", "/userapigateway/trading/acct-1/order"),
    ]


@pytest.mark.asyncio
async def test_public_equity_submit_accepts_documented_preflight_shape():
    seen = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/preflight/single-leg"):
            return httpx.Response(200, json={"estimatedCost": "4.00", "orderValue": "4.00"})
        return httpx.Response(200, json={"orderId": "order-1"})

    cfg = _cfg(research_only=False, live_equity_enabled=True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with public_api.PublicAPIClient(cfg, http) as client:
            result = await client.submit_equity_order(
                symbol="AAPL", side="BUY", amount=4, limit_price=150.25,
                client_order_id="cc-public-aapl-documented-preflight",
            )
    assert result["order"]["orderId"] == "order-1"
    assert seen == ["/userapigateway/trading/acct-1/preflight/single-leg", "/userapigateway/trading/acct-1/order"]


@pytest.mark.asyncio
async def test_public_order_status_prefers_v2_account_scoped_endpoint():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/userapigateway/trading/acct-1/order/v2/order-1"
        return httpx.Response(200, json={"orderId": "order-1", "status": "FILLED"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with public_api.PublicAPIClient(_cfg(), http) as client:
            result = await client.get_order("order-1")
    assert result["status"] == "FILLED"


@pytest.mark.asyncio
async def test_public_order_status_uses_legacy_read_fallback_only_after_v2_404():
    seen = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if "/v2/" in request.url.path:
            return httpx.Response(404, json={"message": "not found"})
        return httpx.Response(200, json={"orderId": "order-1", "status": "FILLED"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with public_api.PublicAPIClient(_cfg(), http) as client:
            result = await client.get_order("order-1")
    assert result["status"] == "FILLED"
    assert seen == [
        "/userapigateway/trading/acct-1/order/v2/order-1",
        "/userapigateway/trading/acct-1/order/order-1",
    ]


@pytest.mark.asyncio
async def test_public_search_orders_and_intraday_bars_are_read_only_contract_calls():
    seen = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, dict(request.url.params), json.loads(request.content) if request.content else {}))
        if request.url.path.endswith("/order/search"):
            return httpx.Response(200, json={"orders": [{"orderId": "o-1", "status": "FILLED"}]})
        return httpx.Response(200, json={"regularMarket": {"bars": []}, "preMarketOvernight": {"bars": []}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with public_api.PublicAPIClient(_cfg(), http, use_sdk=False) as client:
            orders = await client.search_orders(created_after="2026-10-01T00:00:00Z", symbols=["aapl"])
            bars = await client.intraday_bars("aapl")
    assert orders["orders"][0]["orderId"] == "o-1"
    assert bars["dataProvider"] == "PUBLIC_BARS_V2"
    assert seen[0] == (
        "POST", "/userapigateway/trading/acct-1/order/search", {},
        {"createdAfter": "2026-10-01T00:00:00Z", "instruments": [{"symbol": "AAPL", "type": "EQUITY"}]},
    )
    assert seen[1] == (
        "GET", "/userapigateway/historicdata/EQUITY/AAPL/DAY/ONE_MINUTE",
        {"tradingSessionToggle": "ALL_SESSIONS"}, {},
    )


@pytest.mark.asyncio
async def test_public_history_and_tax_lots_are_account_scoped_read_only():
    seen = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, dict(request.url.params)))
        return httpx.Response(200, json={"transactions": [], "lots": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with public_api.PublicAPIClient(_cfg(), http) as client:
            history = await client.history(start="2026-09-01T00:00:00Z", page_size=50)
            lots = await client.unrealized_tax_lots("AAPL")
    assert history["transactions"] == []
    assert lots["lots"] == []
    assert seen == [
        ("GET", "/userapigateway/trading/acct-1/history", {"start": "2026-09-01T00:00:00Z", "pageSize": "50"}),
        ("GET", "/userapigateway/trading/acct-1/taxlots/unrealized/AAPL", {}),
    ]


@pytest.mark.asyncio
async def test_public_strategy_quote_and_multileg_preflight_are_read_only_and_scoped():
    seen = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={"estimatedCost": "3.00", "strategyQuote": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with public_api.PublicAPIClient(_cfg(), http) as client:
            quote = await client.strategy_quote({"legs": []})
            preflight = await client.preflight_multi_leg({"legs": []})
    assert quote["strategyQuote"] == {}
    assert preflight["estimatedCost"] == "3.00"
    assert seen == [
        ("POST", "/userapigateway/option-details/acct-1/strategy-details/quote"),
        ("POST", "/userapigateway/trading/acct-1/preflight/multi-leg"),
    ]


@pytest.mark.asyncio
async def test_public_preflight_refreshes_rejected_bearer_token():
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.headers.get("authorization")))
        if request.url.path.endswith("/preflight/single-leg") and len([c for c in calls if c[1].endswith("preflight/single-leg")]) == 1:
            return httpx.Response(401)
        if request.url.path.endswith("/access-tokens"):
            return httpx.Response(200, json={"accessToken": "refreshed-token"})
        return httpx.Response(200, json={"outcome": "SUCCESS"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with public_api.PublicAPIClient(_cfg(), http) as client:
            result = await client.preflight_single_leg({"orderId": "x"})
    assert result["outcome"] == "SUCCESS"
    assert calls[0][2] == "Bearer token"
    assert calls[1][1].endswith("/access-tokens")
    assert calls[2][2] == "Bearer refreshed-token"


@pytest.mark.asyncio
async def test_public_read_path_refreshes_expired_bearer_token_once():
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.headers.get("authorization")))
        if request.url.path.endswith("/portfolio/v2") and len([call for call in calls if call[1].endswith("/portfolio/v2")]) == 1:
            return httpx.Response(401)
        if request.url.path.endswith("/access-tokens"):
            return httpx.Response(200, json={"accessToken": "refreshed-token"})
        return httpx.Response(200, json={"positions": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with public_api.PublicAPIClient(_cfg(), http) as client:
            result = await client.portfolio()
    assert result == {"positions": []}
    assert calls[0][2] == "Bearer token"
    assert calls[1][1].endswith("/access-tokens")
    assert calls[2][2] == "Bearer refreshed-token"


@pytest.mark.asyncio
async def test_public_read_path_retries_rate_limit_once(monkeypatch):
    calls = 0

    async def no_wait(_response, _attempt):
        return None

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429) if calls == 1 else httpx.Response(200, json={"positions": []})

    monkeypatch.setattr(public_api, "_apply_rate_limit_backoff", no_wait)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with public_api.PublicAPIClient(_cfg(), http) as client:
            result = await client.portfolio()
    assert result == {"positions": []}
    assert calls == 2


@pytest.mark.asyncio
async def test_public_read_path_retries_wrapped_upstream_gateway_timeout(monkeypatch):
    calls = 0

    async def no_wait(_attempt):
        return None

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(400, json={"message": "Invalid upstream response: 504 Gateway Time-out"})
        return httpx.Response(200, json={"positions": []})

    monkeypatch.setattr(public_api, "_apply_transient_read_backoff", no_wait)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with public_api.PublicAPIClient(_cfg(), http) as client:
            result = await client.portfolio()
    assert result == {"positions": []}
    assert calls == 2


@pytest.mark.asyncio
async def test_public_mutation_does_not_retry_transient_gateway_response():
    calls = 0

    async def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(504, json={"message": "gateway timeout"})

    cfg = _cfg(research_only=False, live_equity_enabled=True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        async with public_api.PublicAPIClient(cfg, http) as client:
            with pytest.raises(public_api.PublicAPIError, match="HTTP 504"):
                await client.place_order({"orderId": "no-retry"})
    assert calls == 1
