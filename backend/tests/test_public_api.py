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
        seen.append((request.method, request.url.path, request.headers.get("authorization")))
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
        ("GET", "/userapigateway/trading/account", "Bearer token"),
        ("POST", "/userapigateway/marketdata/quotes", "Bearer token"),
    ]


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
    assert "quantity" not in payload


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
