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
