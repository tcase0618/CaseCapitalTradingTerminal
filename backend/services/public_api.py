"""Public.com Individual API adapter.

This module is deliberately research-only by default.  It provides the
read-side account and market-data boundary needed to validate Public as a
future broker, while order mutation remains explicitly blocked until a
separate live rollout enables it.
"""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any, Iterable

import httpx


class PublicAPIError(RuntimeError):
    """Transport, authentication, or schema error from Public."""


class PublicTradingBlocked(PermissionError):
    """Raised before a Public order mutation can be sent."""


def _bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class PublicAPIConfig:
    enabled: bool
    api_base: str
    secret: str
    access_token: str
    account_id: str
    research_only: bool
    live_equity_enabled: bool
    max_account_usd: float
    max_order_usd: float
    timeout_seconds: float
    sdk_enabled: bool = False
    sdk_token_validity_minutes: int = 60


def config() -> PublicAPIConfig:
    return PublicAPIConfig(
        enabled=_bool("PUBLIC_API_ENABLED"),
        api_base=os.environ.get("PUBLIC_API_BASE_URL", "https://api.public.com").rstrip("/"),
        secret=os.environ.get("PUBLIC_API_SECRET", "").strip(),
        access_token=os.environ.get("PUBLIC_API_ACCESS_TOKEN", "").strip(),
        account_id=os.environ.get("PUBLIC_ACCOUNT_ID", "").strip(),
        research_only=_bool("PUBLIC_RESEARCH_ONLY", True),
        live_equity_enabled=_bool("PUBLIC_LIVE_EQUITY_ENABLED"),
        max_account_usd=max(0.0, _float("PUBLIC_MAX_ACCOUNT_USD", 100.0)),
        max_order_usd=max(0.0, _float("PUBLIC_MAX_ORDER_USD", 5.0)),
        timeout_seconds=max(3.0, min(30.0, _float("PUBLIC_API_TIMEOUT_SECONDS", 12.0))),
        sdk_enabled=_bool("PUBLIC_API_SDK_ENABLED", True),
        sdk_token_validity_minutes=max(5, min(1440, int(_float("PUBLIC_API_SDK_TOKEN_VALIDITY_MINUTES", 60)))),
    )


def safety_state(cfg: PublicAPIConfig | None = None) -> dict[str, Any]:
    cfg = cfg or config()
    live_allowed = bool(cfg.live_equity_enabled and not cfg.research_only)
    return {
        "enabled": cfg.enabled,
        "provider": "public_individual_api",
        "api_base": cfg.api_base,
        "token_configured": bool(cfg.access_token),
        "secret_configured": bool(cfg.secret),
        "account_id_configured": bool(cfg.account_id),
        "research_only": cfg.research_only,
        "live_equity_enabled": cfg.live_equity_enabled,
        "live_order_mutation_allowed": live_allowed,
        "max_account_usd": cfg.max_account_usd,
        "max_order_usd": cfg.max_order_usd,
        "order_mutation_policy": "blocked_by_default_before_public_rollout",
        "sdk_enabled": cfg.sdk_enabled,
        "credential_policy": "sdk_api_secret_or_bearer_token; never log secret or token",
    }


def configured(cfg: PublicAPIConfig | None = None) -> bool:
    cfg = cfg or config()
    return bool(cfg.enabled and (cfg.access_token or cfg.secret))


def _auth_headers(cfg: PublicAPIConfig) -> dict[str, str]:
    if not cfg.access_token:
        return {}
    return {"Authorization": f"Bearer {cfg.access_token}", "Content-Type": "application/json"}


def _symbols(symbols: Iterable[str]) -> list[str]:
    return sorted({str(s).strip().upper() for s in symbols if str(s).strip()})


def _sdk_quote_payload(quote: Any, option_type: str | None = None) -> dict[str, Any]:
    """Flatten the SDK's typed quote model for the terminal's adapters."""
    row = quote.model_dump(by_alias=True, mode="json")
    instrument = row.get("instrument") or {}
    details = row.get("optionDetails") or row.get("option_details") or {}
    greeks = details.get("greeks") or {}
    symbol = instrument.get("symbol") or row.get("symbol")
    flat = {
        **row,
        "symbol": symbol,
        "ticker": symbol,
        "lastPrice": row.get("last"),
        "bidPrice": row.get("bid"),
        "askPrice": row.get("ask"),
        "bid": row.get("bid"),
        "ask": row.get("ask"),
        "bidSize": row.get("bidSize"),
        "askSize": row.get("askSize"),
        "openInterest": row.get("openInterest"),
        "volume": row.get("volume"),
        "strikePrice": details.get("strikePrice"),
        "midPrice": details.get("midPrice"),
        "impliedVolatility": greeks.get("impliedVolatility"),
        "delta": greeks.get("delta"),
        "gamma": greeks.get("gamma"),
        "theta": greeks.get("theta"),
        "vega": greeks.get("vega"),
        "quoteTime": row.get("lastTimestamp") or row.get("bidTimestamp") or row.get("askTimestamp"),
    }
    if option_type:
        flat["type"] = option_type
    return flat


def _sdk_bar_period(days: int) -> Any:
    from public_api_sdk import BarPeriod

    if days <= 365:
        return BarPeriod.YEAR
    if days <= 5 * 365:
        return BarPeriod.FIVE_YEARS
    return BarPeriod.TEN_YEARS


class PublicAPIClient:
    def __init__(self, cfg: PublicAPIConfig | None = None, http_client: httpx.AsyncClient | None = None):
        self.cfg = cfg or config()
        self._http = http_client
        self._owned = http_client is None
        self._sdk = None

    def _should_use_sdk(self) -> bool:
        return bool(self.cfg.sdk_enabled and self.cfg.secret and self.cfg.account_id)

    async def __aenter__(self) -> "PublicAPIClient":
        if self._should_use_sdk():
            try:
                from public_api_sdk import (
                    ApiKeyAuthConfig,
                    AsyncPublicApiClient,
                    AsyncPublicApiClientConfiguration,
                )
                self._sdk = AsyncPublicApiClient(
                    auth_config=ApiKeyAuthConfig(
                        api_secret_key=self.cfg.secret,
                        validity_minutes=self.cfg.sdk_token_validity_minutes,
                    ),
                    config=AsyncPublicApiClientConfiguration(
                        default_account_number=self.cfg.account_id,
                        base_url=self.cfg.api_base,
                    ),
                )
                await self._sdk.__aenter__()
                return self
            except ImportError as exc:
                raise PublicAPIError("PUBLIC_API_SDK_ENABLED=true but publicdotcom-py is not installed") from exc
        if self._owned:
            self._http = httpx.AsyncClient(timeout=self.cfg.timeout_seconds, headers=_auth_headers(self.cfg))
        return self

    async def __aexit__(self, *_: Any) -> None:
        if self._sdk:
            await self._sdk.__aexit__(None, None, None)
            self._sdk = None
            return
        if self._owned and self._http:
            await self._http.aclose()

    def _client(self) -> httpx.AsyncClient:
        if not self.cfg.enabled:
            raise PublicAPIError("PUBLIC_API_ENABLED=false")
        if not self._http:
            raise PublicAPIError("PublicAPIClient must be used inside async context")
        if not self.cfg.access_token:
            raise PublicAPIError("PUBLIC_API_ACCESS_TOKEN is not configured")
        return self._http

    async def _get(self, path: str, **params: Any) -> dict[str, Any]:
        response = await self._client().get(
            f"{self.cfg.api_base}{path}",
            params=params or None,
            headers=_auth_headers(self.cfg),
        )
        return self._decode(response)

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._client().post(
            f"{self.cfg.api_base}{path}",
            json=payload,
            headers=_auth_headers(self.cfg),
        )
        return self._decode(response)

    @staticmethod
    def _decode(response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            raise PublicAPIError(f"Public API HTTP {response.status_code}: {response.text[:240]}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise PublicAPIError("Public API returned non-JSON data") from exc
        return payload if isinstance(payload, dict) else {"data": payload}

    async def accounts(self) -> dict[str, Any]:
        if self._sdk:
            return (await self._sdk.get_accounts()).model_dump(by_alias=True, mode="json")
        return await self._get("/userapigateway/trading/account")

    async def portfolio(self, account_id: str | None = None) -> dict[str, Any]:
        account = account_id or self.cfg.account_id
        if not account:
            raise PublicAPIError("PUBLIC_ACCOUNT_ID is not configured")
        if self._sdk:
            return (await self._sdk.get_portfolio(account_id=account)).model_dump(by_alias=True, mode="json")
        return await self._get(f"/userapigateway/trading/{account}/portfolio/v2")

    async def quotes(self, symbols: Iterable[str]) -> dict[str, Any]:
        values = _symbols(symbols)
        if self._sdk:
            from public_api_sdk import InstrumentType, OrderInstrument
            quotes = await self._sdk.get_quotes(
                [OrderInstrument(symbol=symbol, type=InstrumentType.EQUITY) for symbol in values],
                account_id=self.cfg.account_id,
            )
            return {"quotes": [_sdk_quote_payload(quote) for quote in quotes]}
        return await self._post("/userapigateway/marketdata/quotes", {"symbols": values})

    async def option_expirations(self, symbol: str) -> dict[str, Any]:
        if self._sdk:
            from public_api_sdk import InstrumentType, OptionExpirationsRequest, OrderInstrument
            result = await self._sdk.get_option_expirations(
                OptionExpirationsRequest(
                    instrument=OrderInstrument(symbol=symbol.upper(), type=InstrumentType.EQUITY)
                ),
                account_id=self.cfg.account_id,
            )
            return result.model_dump(by_alias=True, mode="json")
        return await self._post("/userapigateway/marketdata/options/expirations", {"symbol": symbol.upper()})

    async def option_chain(self, symbol: str, expiration: str | None = None, option_type: str | None = None) -> dict[str, Any]:
        if self._sdk:
            if not expiration:
                raise PublicAPIError("Public SDK option_chain requires an expiration date")
            from public_api_sdk import InstrumentType, OptionChainRequest, OrderInstrument
            result = await self._sdk.get_option_chain(
                OptionChainRequest(
                    instrument=OrderInstrument(symbol=symbol.upper(), type=InstrumentType.EQUITY),
                    expiration_date=expiration,
                ),
                account_id=self.cfg.account_id,
            )
            payload = result.model_dump(by_alias=True, mode="json")
            calls = [_sdk_quote_payload(quote, "C") for quote in result.calls]
            puts = [_sdk_quote_payload(quote, "P") for quote in result.puts]
            payload["calls"] = calls
            payload["puts"] = puts
            payload["options"] = calls + puts
            if option_type:
                wanted = option_type.upper()
                if wanted in {"CALL", "C"}:
                    payload["puts"] = []
                elif wanted in {"PUT", "P"}:
                    payload["calls"] = []
            return payload
        payload: dict[str, Any] = {"symbol": symbol.upper()}
        if expiration:
            payload["expirationDate"] = expiration
        if option_type:
            payload["type"] = option_type.upper()
        return await self._post("/userapigateway/marketdata/options/chain", payload)

    async def option_greeks(self, option_symbol: str) -> dict[str, Any]:
        return await self._get(f"/userapigateway/marketdata/options/{option_symbol.upper()}/greeks")

    async def bars(self, symbol: str, days: int = 120) -> dict[str, Any]:
        """Return Public daily regular-session bars in terminal format."""
        if not self._sdk:
            raise PublicAPIError("Public historical bars require the SDK")
        from public_api_sdk import BarAggregation, InstrumentType
        result = await self._sdk.get_bars(
            symbol.upper(),
            _sdk_bar_period(max(1, days)),
            instrument_type=InstrumentType.EQUITY,
            aggregation=BarAggregation.ONE_DAY,
        )
        payload = result.model_dump(by_alias=True, mode="json")
        session = payload.get("regularMarket") or payload.get("regular_market") or {}
        return {
            "symbol": symbol.upper(),
            "bars": session.get("bars") or [],
            "dataProvider": "PUBLIC_BARS",
            "dataFeed": "public",
        }

    async def strategy_quote(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/userapigateway/marketdata/options/strategy-quote", payload)

    async def instruments(self, symbol: str | None = None) -> dict[str, Any]:
        if symbol:
            return await self._get(f"/userapigateway/instruments/{symbol.upper()}")
        return await self._get("/userapigateway/instruments")

    def _mutation_client(self) -> httpx.AsyncClient:
        """Return the bearer-authenticated HTTP client for order mutations.

        The SDK read path may authenticate from a secret, but order requests
        are kept on the documented bearer-token path so the terminal has an
        explicit, inspectable execution credential and request contract.
        """
        if not self.cfg.access_token:
            raise PublicAPIError("Public order mutations require PUBLIC_API_ACCESS_TOKEN")
        return self._client()

    def _account(self, account_id: str | None = None) -> str:
        account = (account_id or self.cfg.account_id).strip()
        if not account:
            raise PublicAPIError("PUBLIC_ACCOUNT_ID is not configured")
        return account

    @staticmethod
    def _order_id(value: str | None) -> str:
        """Map a terminal idempotency key to Public's required UUID order id."""
        if value:
            try:
                return str(uuid.UUID(value))
            except ValueError:
                return str(uuid.uuid5(uuid.NAMESPACE_URL, f"case-capital:public:{value}"))
        return str(uuid.uuid4())

    @staticmethod
    def _equity_order_payload(
        *,
        symbol: str,
        side: str,
        amount: float | None = None,
        quantity: float | None = None,
        limit_price: float | None = None,
        stop_price: float | None = None,
        time_in_force: str = "DAY",
        session: str = "CORE",
        order_id: str | None = None,
    ) -> dict[str, Any]:
        if (amount is None) == (quantity is None):
            raise PublicAPIError("Public equity order requires exactly one of amount or quantity")
        payload: dict[str, Any] = {
            "orderId": PublicAPIClient._order_id(order_id),
            "instrument": {"symbol": symbol.upper().strip(), "type": "EQUITY"},
            "orderSide": side.upper(),
            "orderType": "STOP_LIMIT" if stop_price is not None else "LIMIT" if limit_price is not None else "MARKET",
            "expiration": {"timeInForce": time_in_force.upper()},
            "equityMarketSession": session.upper(),
            "openCloseIndicator": "OPEN" if side.upper() == "BUY" else "CLOSE",
        }
        if amount is not None:
            payload["amount"] = f"{float(amount):.2f}"
        if quantity is not None:
            payload["quantity"] = f"{float(quantity):.8f}".rstrip("0").rstrip(".")
        if limit_price is not None:
            payload["limitPrice"] = f"{float(limit_price):.4f}"
        if stop_price is not None:
            payload["stopPrice"] = f"{float(stop_price):.4f}"
        return payload

    async def preflight_single_leg(self, payload: dict[str, Any], account_id: str | None = None) -> dict[str, Any]:
        account = self._account(account_id)
        response = await self._mutation_client().post(
            f"{self.cfg.api_base}/userapigateway/trading/{account}/preflight/single-leg",
            json=payload,
            headers=_auth_headers(self.cfg),
        )
        return self._decode(response)

    async def place_order(self, payload: dict[str, Any], account_id: str | None = None) -> dict[str, Any]:
        cfg = self.cfg
        if cfg.research_only or not cfg.live_equity_enabled:
            raise PublicTradingBlocked("Public equity order blocked by configuration")
        account = self._account(account_id)
        response = await self._mutation_client().post(
            f"{cfg.api_base}/userapigateway/trading/{account}/order",
            json=payload,
            headers=_auth_headers(cfg),
        )
        return self._decode(response)

    async def submit_equity_order(
        self,
        *,
        symbol: str,
        side: str,
        amount: float | None = None,
        quantity: float | None = None,
        limit_price: float | None = None,
        stop_price: float | None = None,
        time_in_force: str = "DAY",
        session: str = "CORE",
        client_order_id: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        """Preflight, then submit one Public equity order.

        Public order placement is asynchronous. Returning both broker
        responses lets the caller persist the preflight evidence beside the
        submission and then poll ``get_order`` using the returned order id.
        """
        payload = self._equity_order_payload(
            symbol=symbol,
            side=side,
            amount=amount,
            quantity=quantity,
            limit_price=limit_price,
            stop_price=stop_price,
            time_in_force=time_in_force,
            session=session,
            order_id=client_order_id,
        )
        preflight = await self.preflight_single_leg(payload, account_id=account_id)
        outcome = str(preflight.get("outcome") or preflight.get("status") or "SUCCESS").upper()
        if outcome not in {"SUCCESS", "VALID", "OK"}:
            raise PublicAPIError(f"Public preflight rejected order: {outcome}")
        placed = await self.place_order(payload, account_id=account_id)
        return {"preflight": preflight, "order": placed, "payload": payload}

    async def get_order(self, order_id: str, account_id: str | None = None) -> dict[str, Any]:
        account = self._account(account_id)
        response = await self._mutation_client().get(
            f"{self.cfg.api_base}/userapigateway/trading/{account}/order/{order_id}",
            headers=_auth_headers(self.cfg),
        )
        return self._decode(response)

    async def cancel_order(self, order_id: str, account_id: str | None = None) -> dict[str, Any]:
        cfg = self.cfg
        if cfg.research_only or not cfg.live_equity_enabled:
            raise PublicTradingBlocked("Public order cancellation blocked by configuration")
        account = self._account(account_id)
        response = await self._mutation_client().delete(
            f"{cfg.api_base}/userapigateway/trading/{account}/order/{order_id}",
            headers=_auth_headers(cfg),
        )
        return self._decode(response)

    async def replace_order(self, payload: dict[str, Any], account_id: str | None = None) -> dict[str, Any]:
        cfg = self.cfg
        if cfg.research_only or not cfg.live_equity_enabled:
            raise PublicTradingBlocked("Public order replacement blocked by configuration")
        account = self._account(account_id)
        response = await self._mutation_client().put(
            f"{cfg.api_base}/userapigateway/trading/{account}/order",
            json=payload,
            headers=_auth_headers(cfg),
        )
        return self._decode(response)


async def status() -> dict[str, Any]:
    cfg = config()
    state = safety_state(cfg)
    if not cfg.enabled:
        return {"ok": False, "connected": False, "reason": "PUBLIC_API_ENABLED=false", "config": state}
    if not (cfg.access_token or cfg.secret):
        return {"ok": False, "connected": False, "reason": "PUBLIC_API_SECRET or PUBLIC_API_ACCESS_TOKEN not configured", "config": state}
    try:
        async with PublicAPIClient(cfg) as client:
            accounts = await client.accounts()
        return {
            "ok": True,
            "connected": True,
            "reason": None,
            "config": state,
            "account_count": len(accounts.get("accounts") or []),
            "read_only_probe": True,
            "orders_transmitted": 0,
        }
    except Exception as exc:
        return {"ok": False, "connected": False, "reason": str(exc)[:300], "config": state}


def assert_order_blocked(action: str = "order_mutation") -> None:
    cfg = config()
    raise PublicTradingBlocked(
        f"Public {action} blocked: research-only mode is active "
        f"(PUBLIC_RESEARCH_ONLY={cfg.research_only}, PUBLIC_LIVE_EQUITY_ENABLED={cfg.live_equity_enabled})."
    )


def place_order(*_: Any, **__: Any) -> None:
    """Compatibility guard; async mutations must use PublicAPIClient."""
    assert_order_blocked("place_order")


def replace_order(*_: Any, **__: Any) -> None:
    assert_order_blocked("replace_order")


def cancel_order(*_: Any, **__: Any) -> None:
    assert_order_blocked("cancel_order")
