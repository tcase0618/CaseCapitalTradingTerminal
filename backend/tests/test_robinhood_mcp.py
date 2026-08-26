import json
from pathlib import Path

import httpx
import pytest

from services import robinhood_mcp


def _cfg(tmp_path: Path, **overrides):
    values = {
        "enabled": True,
        "mcp_url": "https://agent.robinhood.com/mcp/trading",
        "oauth_metadata_url": "",
        "client_id": "client-test",
        "token_file": tmp_path / "oauth.json",
        "live_trading": False,
        "require_review": True,
        "max_account_usd": 100.0,
        "max_order_usd": 5.0,
        "max_open_exposure_usd": 20.0,
        "timeout_seconds": 12.0,
    }
    values.update(overrides)
    return robinhood_mcp.RobinhoodMCPConfig(**values)


@pytest.mark.asyncio
async def test_tools_list_uses_mcp_json_rpc_and_never_places_order(tmp_path):
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        result = {"capabilities": {}} if seen["body"]["method"] == "initialize" else {"tools": [{"name": "get_equity_quotes"}]}
        return httpx.Response(200, headers={"mcp-session-id": "session-test"}, json={"jsonrpc": "2.0", "id": seen["body"]["id"], "result": result})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        cfg = _cfg(tmp_path)
        robinhood_mcp.save_tokens({"access_token": "token", "expires_at": 9999999999}, cfg)
        async with robinhood_mcp.RobinhoodMCPClient(cfg, http) as client:
            result = await client.tools_list()
    assert result["result"]["tools"][0]["name"] == "get_equity_quotes"
    assert seen["body"]["method"] == "tools/list"
    assert seen["auth"] == "Bearer token"


def test_credentials_and_order_mutations_are_blocked(tmp_path):
    cfg = _cfg(tmp_path)
    with pytest.raises(robinhood_mcp.RobinhoodMCPError):
        robinhood_mcp.save_tokens({"access_token": "x", "password": "no"}, cfg)
    with pytest.raises(robinhood_mcp.RobinhoodTradingBlocked):
        robinhood_mcp.place_order("AAPL", 1)
    with pytest.raises(robinhood_mcp.RobinhoodTradingBlocked):
        robinhood_mcp.modify_order("id")
    with pytest.raises(robinhood_mcp.RobinhoodTradingBlocked):
        robinhood_mcp.cancel_order("id")


def test_token_file_is_restricted_and_safety_defaults_fail_closed(tmp_path):
    cfg = _cfg(tmp_path)
    robinhood_mcp.save_tokens({"access_token": "x"}, cfg)
    assert robinhood_mcp.load_tokens(cfg)["access_token"] == "x"
    state = robinhood_mcp.safety_state(cfg)
    assert state["live_trading"] is False
    assert state["require_order_review"] is True
    assert state["max_account_usd"] == 100.0
    assert state["max_order_usd"] == 5.0
    assert state["max_open_exposure_usd"] == 20.0
