"""Deterministic Robinhood Agentic Trading MCP client.

This is a provider/data boundary, not a replacement execution authority. The
client supports OAuth token loading and MCP read calls, but all order mutation
is fail-closed until a separately reviewed live rollout enables it. No
username, password, or 2FA value is accepted or persisted here.
"""
from __future__ import annotations

import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


class RobinhoodMCPError(RuntimeError):
    """Base error for Robinhood MCP transport and policy failures."""


class RobinhoodTradingBlocked(PermissionError):
    """Raised before an order mutation can reach the Robinhood MCP server."""


def _env_bool(key: str, default: bool = False) -> bool:
    value = os.environ.get(key)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class RobinhoodMCPConfig:
    enabled: bool
    mcp_url: str
    oauth_metadata_url: str
    client_id: str
    token_file: Path
    live_trading: bool
    require_review: bool
    max_account_usd: float
    max_order_usd: float
    max_open_exposure_usd: float
    timeout_seconds: float


def config() -> RobinhoodMCPConfig:
    return RobinhoodMCPConfig(
        enabled=_env_bool("ROBINHOOD_MCP_ENABLED", False),
        mcp_url=os.environ.get("ROBINHOOD_MCP_URL", "https://agent.robinhood.com/mcp/trading").strip(),
        oauth_metadata_url=os.environ.get("ROBINHOOD_OAUTH_METADATA_URL", "").strip(),
        client_id=os.environ.get("ROBINHOOD_MCP_CLIENT_ID", "").strip(),
        token_file=Path(os.environ.get("ROBINHOOD_TOKEN_FILE", "")).expanduser(),
        live_trading=_env_bool("ROBINHOOD_LIVE_TRADING", False),
        require_review=_env_bool("ROBINHOOD_REQUIRE_ORDER_REVIEW", True),
        max_account_usd=max(0.0, _env_float("ROBINHOOD_MAX_ACCOUNT_USD", 100.0)),
        max_order_usd=max(0.0, _env_float("ROBINHOOD_MAX_ORDER_USD", 5.0)),
        max_open_exposure_usd=max(0.0, _env_float("ROBINHOOD_MAX_OPEN_EXPOSURE_USD", 20.0)),
        timeout_seconds=max(3.0, min(30.0, _env_float("ROBINHOOD_MCP_TIMEOUT_SECONDS", 12.0))),
    )


def load_tokens(cfg: RobinhoodMCPConfig | None = None) -> dict[str, Any] | None:
    cfg = cfg or config()
    if not str(cfg.token_file) or not cfg.token_file.is_file():
        return None
    try:
        payload = json.loads(cfg.token_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RobinhoodMCPError(f"Robinhood token file is unreadable: {exc}") from exc
    if not isinstance(payload, dict) or not payload.get("access_token"):
        return None
    return payload


def save_tokens(tokens: dict[str, Any], cfg: RobinhoodMCPConfig | None = None) -> None:
    cfg = cfg or config()
    if not str(cfg.token_file):
        raise RobinhoodMCPError("ROBINHOOD_TOKEN_FILE is not configured")
    if any(key in tokens for key in ("username", "password", "two_factor", "2fa")):
        raise RobinhoodMCPError("Robinhood credentials and 2FA must never be stored")
    cfg.token_file.parent.mkdir(parents=True, exist_ok=True)
    cfg.token_file.write_text(json.dumps(tokens, sort_keys=True), encoding="utf-8")
    try:
        os.chmod(cfg.token_file, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def safety_state(cfg: RobinhoodMCPConfig | None = None) -> dict[str, Any]:
    cfg = cfg or config()
    tokens = load_tokens(cfg)
    return {
        "enabled": cfg.enabled,
        "provider": "robinhood_agentic_mcp",
        "mcp_url": cfg.mcp_url,
        "oauth_configured": bool(cfg.client_id or tokens),
        "token_present": bool(tokens),
        "live_trading": cfg.live_trading,
        "require_order_review": cfg.require_review,
        "max_account_usd": cfg.max_account_usd,
        "max_order_usd": cfg.max_order_usd,
        "max_open_exposure_usd": cfg.max_open_exposure_usd,
        "order_mutation_policy": "blocked_by_default_before_mcp",
        "credential_policy": "oauth_tokens_only; no username/password/2fa",
    }


def _bearer(tokens: dict[str, Any] | None) -> dict[str, str]:
    if not tokens or not tokens.get("access_token"):
        return {}
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _json_rpc(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params or {}}


class RobinhoodMCPClient:
    def __init__(self, cfg: RobinhoodMCPConfig | None = None, http_client: httpx.AsyncClient | None = None):
        self.cfg = cfg or config()
        self._http = http_client
        self._owned_http = http_client is None
        self._tokens = load_tokens(self.cfg)
        self._session_id: str | None = None
        self._initialized = False

    async def __aenter__(self) -> "RobinhoodMCPClient":
        if self._owned_http:
            self._http = httpx.AsyncClient(timeout=self.cfg.timeout_seconds)
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._owned_http and self._http:
            await self._http.aclose()

    def _require_transport(self) -> httpx.AsyncClient:
        if not self.cfg.enabled:
            raise RobinhoodMCPError("ROBINHOOD_MCP_ENABLED=false")
        if not self._http:
            raise RobinhoodMCPError("Robinhood MCP client must be used inside async context")
        if not self._tokens:
            raise RobinhoodMCPError("Robinhood OAuth token is not configured")
        return self._http

    async def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        client = self._require_transport()
        headers = {"Accept": "application/json, text/event-stream", **_bearer(self._tokens)}
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        response = await client.post(
            self.cfg.mcp_url,
            headers=headers,
            json=_json_rpc(method, params),
        )
        response.raise_for_status()
        self._session_id = response.headers.get("mcp-session-id") or self._session_id
        try:
            payload = response.json()
        except ValueError as exc:
            raise RobinhoodMCPError("Robinhood MCP returned non-JSON data") from exc
        if isinstance(payload, dict) and payload.get("error"):
            raise RobinhoodMCPError(f"Robinhood MCP error: {payload['error']}")
        return payload if isinstance(payload, dict) else {"result": payload}

    async def tools_list(self) -> dict[str, Any]:
        await self.initialize()
        return await self.call("tools/list")

    async def initialize(self) -> dict[str, Any]:
        if self._initialized:
            return {"ok": True, "already_initialized": True}
        result = await self.call(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "case-capital-readonly", "version": "0.1"},
            },
        )
        self._initialized = True
        return result

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.call("tools/call", {"name": name, "arguments": arguments or {}})

    async def probe_read_only(self) -> dict[str, Any]:
        """Discover tools only; schema-specific account/quote calls come later."""
        tools = await self.tools_list()
        result = tools.get("result") or {}
        names = [item.get("name") for item in result.get("tools", []) if isinstance(item, dict)]
        return {
            "ok": True,
            "tools_count": len(names),
            "tools": names,
            "mutation_tools_present": [name for name in names if re.search(r"(place|submit|cancel|modify|order)", name or "", re.I)],
            "read_only_probe": True,
            "orders_transmitted": 0,
        }


def assert_order_blocked(action: str = "order_mutation") -> None:
    cfg = config()
    raise RobinhoodTradingBlocked(
        f"Robinhood {action} blocked before MCP: live trading is disabled "
        f"(ROBINHOOD_LIVE_TRADING={cfg.live_trading}, require_review={cfg.require_review})."
    )


async def status() -> dict[str, Any]:
    cfg = config()
    state = safety_state(cfg)
    if not cfg.enabled:
        return {"ok": False, "connected": False, "reason": "ROBINHOOD_MCP_ENABLED=false", "config": state}
    if not load_tokens(cfg):
        return {"ok": False, "connected": False, "reason": "OAuth token not configured", "config": state}
    try:
        async with RobinhoodMCPClient(cfg) as client:
            probe = await client.probe_read_only()
        return {"ok": True, "connected": True, "reason": None, "config": state, "probe": probe}
    except Exception as exc:
        return {"ok": False, "connected": False, "reason": str(exc)[:300], "config": state}


def place_order(*_: Any, **__: Any) -> None:
    assert_order_blocked("place_order")


def modify_order(*_: Any, **__: Any) -> None:
    assert_order_blocked("modify_order")


def cancel_order(*_: Any, **__: Any) -> None:
    assert_order_blocked("cancel_order")
