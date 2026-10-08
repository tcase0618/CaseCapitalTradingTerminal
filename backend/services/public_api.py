"""Public.com Individual API adapter.

This module is research-only by default. It provides the read-side account and
market-data boundary, and permits order mutation only when the explicit live
rollout flags are enabled.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx


# The SDK emits an INFO line for its internal subscription cleanup on each
# monitor pass. Keep warnings/errors, but do not let normal cleanup drown out
# operational errors in the terminal journal.
logging.getLogger("public_api_sdk.async_order_subscription_manager").setLevel(logging.WARNING)


# The Public bearer token is short lived.  The scheduler creates several
# clients in one monitor pass, so a process-wide refreshed token prevents each
# client from independently refreshing the same expired credential.
_RUNTIME_ACCESS_TOKEN = ""
_TOKEN_REFRESH_LOCK = asyncio.Lock()
_REQUEST_BACKOFF_LOCK = asyncio.Lock()
_NEXT_REQUEST_AT = 0.0


def _request_interval_seconds() -> float:
    """Keep REST traffic below Public's account-wide request budget.

    Public documents a shared per-account limit. Use a conservative default
    below that ceiling because the monitor, scans, and reconciliation share
    the same credential. SDK streaming is a separate persistent connection;
    this applies to REST request bursts only.
    """
    try:
        return max(0.05, min(1.0, float(os.getenv("PUBLIC_API_MIN_REQUEST_INTERVAL_SECONDS", "0.12"))))
    except (TypeError, ValueError):
        return 0.12


class PublicAPIError(RuntimeError):
    """Transport, authentication, or schema error from Public."""


class PublicOrderRejected(PublicAPIError):
    """Submission never reached the order-placement endpoint."""


class PublicOrderSubmissionUnknown(PublicAPIError):
    """Placement may have reached the broker; reconcile before retrying."""


class PublicTradingBlocked(PermissionError):
    """Raised before a Public order mutation can be sent."""


async def _wait_for_request_slot() -> None:
    """Reserve a shared REST slot and honor any broker-imposed cooldown."""
    global _NEXT_REQUEST_AT
    async with _REQUEST_BACKOFF_LOCK:
        now = time.monotonic()
        scheduled = max(now, _NEXT_REQUEST_AT)
        _NEXT_REQUEST_AT = scheduled + _request_interval_seconds()
        delay = scheduled - now
    if delay > 0:
        await asyncio.sleep(delay)


async def _apply_rate_limit_backoff(response: httpx.Response, attempt: int) -> None:
    """Honor Public's retry hint and share the resulting cooldown process-wide."""
    global _NEXT_REQUEST_AT
    try:
        retry_after = float(response.headers.get("Retry-After", ""))
    except (TypeError, ValueError):
        retry_after = 0.0
    delay = max(0.5, min(15.0, retry_after or (0.75 * (2 ** attempt))))
    async with _REQUEST_BACKOFF_LOCK:
        _NEXT_REQUEST_AT = max(_NEXT_REQUEST_AT, time.monotonic() + delay)
    await asyncio.sleep(delay)


def _retryable_read_response(response: httpx.Response) -> bool:
    """Return whether a failed broker *read* can safely be retried.

    Public occasionally wraps an upstream 408/504 in a 400 response.  Those
    are transport failures, not malformed requests.  Mutations deliberately
    never use this helper: an ambiguous order response must be reconciled,
    never re-submitted.
    """
    if response.status_code in {408, 425, 429, 500, 502, 503, 504}:
        return True
    if response.status_code != 400:
        return False
    body = response.text.lower()
    return "upstream response" in body and any(code in body for code in ("408", "502", "503", "504", "gateway time-out", "gateway timeout"))


async def _apply_transient_read_backoff(attempt: int) -> None:
    """Back off a bounded amount and share the cooldown across read clients."""
    global _NEXT_REQUEST_AT
    base = min(4.0, 0.35 * (2 ** attempt))
    delay = base + random.uniform(0.0, min(0.25, base / 3))
    async with _REQUEST_BACKOFF_LOCK:
        _NEXT_REQUEST_AT = max(_NEXT_REQUEST_AT, time.monotonic() + delay)
    await asyncio.sleep(delay)


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
        access_token=_RUNTIME_ACCESS_TOKEN or os.environ.get("PUBLIC_API_ACCESS_TOKEN", "").strip(),
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
        "order_mutation_policy": (
            "explicit_live_rollout_enabled"
            if live_allowed
            else "blocked_until_explicit_live_rollout"
        ),
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
    row = quote.model_dump(by_alias=True, mode="json") if hasattr(quote, "model_dump") else dict(quote)
    instrument = row.get("instrument") or {}
    details = row.get("optionDetails") or row.get("option_details") or {}
    greeks = details.get("greeks") or {}
    symbol = instrument.get("symbol") or row.get("symbol")
    timestamp_values = [row.get(key) for key in ("lastTimestamp", "bidTimestamp", "askTimestamp") if row.get(key)]
    parsed_timestamps = []
    for value in timestamp_values:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            parsed_timestamps.append((parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc), value))
        except (TypeError, ValueError):
            continue
    newest_timestamp = max(parsed_timestamps, key=lambda item: item[0])[1] if parsed_timestamps else (timestamp_values[0] if timestamp_values else None)

    # A last trade may be newer than the NBBO. Keep that useful display mark,
    # but price orders from the older side of a two-sided bid/ask quote. The
    # quote is only as current as its oldest executable side.
    bid_ask_values = [row.get(key) for key in ("bidTimestamp", "askTimestamp") if row.get(key)]
    parsed_bid_ask = []
    for value in bid_ask_values:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            parsed_bid_ask.append((parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc), value))
        except (TypeError, ValueError):
            continue
    executable_quote_time = min(parsed_bid_ask, key=lambda item: item[0])[1] if len(parsed_bid_ask) == 2 else None
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
        "quoteTime": newest_timestamp,
        "executableQuoteTime": executable_quote_time,
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
    def __init__(
        self,
        cfg: PublicAPIConfig | None = None,
        http_client: httpx.AsyncClient | None = None,
        *,
        use_sdk: bool | None = None,
    ):
        self._runtime_token_cache = cfg is None
        self.cfg = cfg or config()
        self._http = http_client
        self._owned = http_client is None
        self._sdk = None
        # The SDK is retained for its typed bars/options functionality. High-
        # frequency account and quote reads can choose the documented REST
        # path to avoid rebuilding the SDK subscription manager per request.
        self._use_sdk = self.cfg.sdk_enabled if use_sdk is None else bool(use_sdk)

    def _should_use_sdk(self) -> bool:
        return bool(self._use_sdk and self.cfg.sdk_enabled and self.cfg.secret and self.cfg.account_id)

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
                if self._http is None:
                    self._http = httpx.AsyncClient(timeout=self.cfg.timeout_seconds)
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
            if self._owned and self._http:
                await self._http.aclose()
                self._http = None
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
        return await self._request("GET", path, params=params or None)

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", path, payload=payload)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        mutation: bool = False,
    ) -> dict[str, Any]:
        """Send an authenticated request and refresh once after a 401.

        Public access tokens expire while the portfolio monitor is running.
        All read and mutation paths must recover the same way; leaving the
        refresh only on preflight caused reconciliation and stop maintenance
        to fail after the first token expiry.
        """
        # All REST consumers share this small retry budget.  The one-minute
        # monitor opens several short-lived clients, so per-client retries
        # create a thundering herd after a Public 429.
        refreshed = False
        for attempt in range(3):
            await _wait_for_request_slot()
            client = self._mutation_client() if mutation else self._client()
            request = getattr(client, method.lower())
            kwargs: dict[str, Any] = {"headers": _auth_headers(self.cfg)}
            if payload is not None:
                kwargs["json"] = payload
            if params:
                kwargs["params"] = params
            try:
                response = await request(f"{self.cfg.api_base}{path}", **kwargs)
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
                if not mutation and attempt < 2:
                    await _apply_transient_read_backoff(attempt)
                    continue
                raise PublicAPIError(f"Public API transport failure: {exc.__class__.__name__}") from exc
            if response.status_code == 401 and not refreshed:
                refreshed = True
                await self._refresh_access_token()
                continue
            if response.status_code == 429 and attempt < 2:
                await _apply_rate_limit_backoff(response, attempt)
                continue
            if not mutation and _retryable_read_response(response) and attempt < 2:
                await _apply_transient_read_backoff(attempt)
                continue
            return self._decode(response)
        raise PublicAPIError("Public API retry budget exhausted")

    @staticmethod
    def _decode(response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            raise PublicAPIError(f"Public API HTTP {response.status_code}: {response.text[:240]}")
        # Public's successful DELETE responses may be intentionally empty.
        # Treat any successful empty body as an acknowledgement so callers can
        # confirm the resulting order state instead of failing JSON parsing.
        if not response.content:
            return {}
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

    async def history(
        self,
        *,
        start: str | None = None,
        end: str | None = None,
        page_size: int | None = None,
        next_token: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        """Return broker-authoritative account activity for research/reporting."""
        params: dict[str, Any] = {}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        if page_size is not None:
            params["pageSize"] = max(1, min(500, int(page_size)))
        if next_token:
            params["nextToken"] = next_token
        return await self._get(f"/userapigateway/trading/{self._account(account_id)}/history", **params)

    async def unrealized_tax_lots(self, symbol: str | None = None, account_id: str | None = None) -> dict[str, Any]:
        """Return unrealized lots without changing sell-lot instructions."""
        path = f"/userapigateway/trading/{self._account(account_id)}/taxlots/unrealized"
        if symbol:
            path = f"{path}/{symbol.upper().strip()}"
        return await self._get(path)

    async def quotes(self, symbols: Iterable[str]) -> dict[str, Any]:
        values = _symbols(symbols)
        if self._sdk:
            from public_api_sdk import InstrumentType, OrderInstrument
            quotes = await self._sdk.get_quotes(
                [OrderInstrument(symbol=symbol, type=InstrumentType.EQUITY) for symbol in values],
                account_id=self.cfg.account_id,
            )
            return {"quotes": [_sdk_quote_payload(quote) for quote in quotes]}
        payload = await self._post(
            f"/userapigateway/marketdata/{self._account()}/quotes",
            {"instruments": [{"symbol": symbol, "type": "EQUITY"} for symbol in values]},
        )
        rows = payload.get("quotes") or []
        if isinstance(rows, list):
            payload["quotes"] = [_sdk_quote_payload(row) if isinstance(row, dict) else row for row in rows]
        return payload

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
        raise PublicAPIError("Public option expirations require the enabled Public SDK; the REST fallback is unavailable")

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
        raise PublicAPIError("Public option chains require the enabled Public SDK; the REST fallback is unavailable")

    async def option_greeks(self, option_symbol: str) -> dict[str, Any]:
        if self._sdk:
            result = await self._sdk.get_option_greek(option_symbol.upper(), account_id=self.cfg.account_id)
            return result.model_dump(by_alias=True, mode="json")
        raise PublicAPIError("Public option greeks require the enabled Public SDK; the REST fallback is unavailable")

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

    async def intraday_bars(
        self,
        symbol: str,
        *,
        period: str = "DAY",
        aggregation: str = "ONE_MINUTE",
        trading_session: str = "ALL_SESSIONS",
    ) -> dict[str, Any]:
        """Fetch documented Public intraday bars without collapsing sessions.

        This is intentionally separate from :meth:`bars`, whose historical
        callers expect daily regular-session bars.  The scan and execution
        research paths can opt into this explicit 24/5 payload and retain the
        source's pre-market, regular, after-market, and overnight segments.
        """
        allowed_periods = {"DAY", "WEEK", "MONTH", "QUARTER", "HALF_YEAR", "YEAR", "FIVE_YEARS", "TEN_YEARS", "ALL", "YTD"}
        allowed_aggregations = {"ONE_MINUTE", "FIVE_MINUTES", "TEN_MINUTES", "FIFTEEN_MINUTES", "THIRTY_MINUTES", "ONE_HOUR", "ONE_DAY", "ONE_WEEK", "ONE_MONTH", "THREE_MONTHS", "SIX_MONTHS", "ONE_YEAR"}
        allowed_sessions = {"REGULAR_HOURS", "REGULAR_AND_EXTENDED_HOURS", "ALL_SESSIONS"}
        wanted_period = str(period).upper()
        wanted_aggregation = str(aggregation).upper()
        wanted_session = str(trading_session).upper()
        if wanted_period not in allowed_periods:
            raise PublicAPIError(f"Unsupported Public bar period: {period}")
        if wanted_aggregation not in allowed_aggregations:
            raise PublicAPIError(f"Unsupported Public bar aggregation: {aggregation}")
        if wanted_session not in allowed_sessions:
            raise PublicAPIError(f"Unsupported Public trading session toggle: {trading_session}")
        payload = await self._get(
            f"/userapigateway/historicdata/EQUITY/{symbol.upper().strip()}/{wanted_period}/{wanted_aggregation}",
            tradingSessionToggle=wanted_session,
        )
        payload["dataProvider"] = "PUBLIC_BARS_V2"
        payload["dataFeed"] = "public"
        payload["tradingSessionToggle"] = wanted_session
        return payload

    async def strategy_quote(self, payload: dict[str, Any], account_id: str | None = None) -> dict[str, Any]:
        """Return a combined quote for a multi-leg option strategy.

        This is a read-only pricing call. It is deliberately separate from
        Alpaca's execution-grade option chain used by Options Desk.
        """
        return await self._post(
            f"/userapigateway/option-details/{self._account(account_id)}/strategy-details/quote",
            payload,
        )

    async def preflight_multi_leg(self, payload: dict[str, Any], account_id: str | None = None) -> dict[str, Any]:
        """Run Public's multi-leg what-if validation without placing an order."""
        return await self._post(
            f"/userapigateway/trading/{self._account(account_id)}/preflight/multi-leg",
            payload,
        )

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

    async def _refresh_access_token(self) -> None:
        """Refresh the bearer token in memory without logging credentials."""
        global _RUNTIME_ACCESS_TOKEN
        if not self.cfg.secret:
            raise PublicAPIError("Public access token rejected and no secret is configured for refresh")
        async with _TOKEN_REFRESH_LOCK:
            # Another concurrent monitor task may have refreshed while this
            # request was waiting. Reuse that token instead of hitting the
            # token endpoint again and causing a refresh storm.
            if self._runtime_token_cache and _RUNTIME_ACCESS_TOKEN and _RUNTIME_ACCESS_TOKEN != self.cfg.access_token:
                self.cfg = replace(self.cfg, access_token=_RUNTIME_ACCESS_TOKEN)
                return
            if self._http is None:
                self._http = httpx.AsyncClient(timeout=self.cfg.timeout_seconds)
            response = await self._http.post(
                f"{self.cfg.api_base}/userapiauthservice/personal/access-tokens",
                json={"validityInMinutes": self.cfg.sdk_token_validity_minutes, "secret": self.cfg.secret},
                headers={"Content-Type": "application/json"},
            )
            refreshed = self._decode(response).get("accessToken")
            if not refreshed:
                raise PublicAPIError("Public token refresh returned no access token")
            if self._runtime_token_cache:
                _RUNTIME_ACCESS_TOKEN = str(refreshed)
            self.cfg = replace(self.cfg, access_token=str(refreshed))

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
        expiration_time: datetime | str | None = None,
        session: str = "CORE",
        order_id: str | None = None,
        order_class: str = "SIMPLE",
        take_profit: float | None = None,
        stop_loss: float | None = None,
        stop_loss_limit: float | None = None,
    ) -> dict[str, Any]:
        if (amount is None) == (quantity is None):
            raise PublicAPIError("Public equity order requires exactly one of amount or quantity")
        tif = time_in_force.upper()
        if tif not in {"DAY", "GTD"}:
            raise PublicAPIError(f"Unsupported Public time-in-force: {time_in_force}")
        market_session = session.upper()
        if market_session not in {"CORE", "EXTENDED", "TWENTY_FOUR_HOURS"}:
            raise PublicAPIError(f"Unsupported Public equity market session: {session}")
        if market_session != "CORE" and tif != "DAY":
            raise PublicAPIError("Public extended and 24-hour equity orders require DAY time-in-force")
        expiration: dict[str, str] = {"timeInForce": tif}
        if tif == "GTD":
            if not expiration_time:
                raise PublicAPIError("Public GTD order requires expiration_time")
            if isinstance(expiration_time, datetime):
                expires_at = expiration_time.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            else:
                expires_at = str(expiration_time)
            expiration["expirationTime"] = expires_at

        def _format_price(value: float) -> str:
            # Public documents a $0.0001 increment below $1 and $0.01 at or
            # above $1. Formatting to two decimals silently changes penny-
            # stock limits and stops, so retain the correct tick size here.
            precision = 4 if value < 1 else 2
            return f"{value:.{precision}f}"

        normalized_order_class = str(order_class or "SIMPLE").upper()
        if normalized_order_class not in {"SIMPLE", "BRACKET", "OCO", "OTO"}:
            raise PublicAPIError(f"Unsupported Public order class: {order_class}")
        if normalized_order_class != "SIMPLE":
            if amount is not None or quantity is None:
                raise PublicAPIError("Public bracket orders require whole-share quantity, not amount")
            if market_session != "CORE":
                raise PublicAPIError("Public bracket orders require the CORE market session")
            if stop_price is not None:
                raise PublicAPIError("Public bracket entry must use stop_loss, not a parent stop_price")
            if limit_price is None and normalized_order_class == "OCO":
                raise PublicAPIError("Public OCO bracket entry requires a limit price")
            if take_profit is None and stop_loss is None:
                raise PublicAPIError("Public bracket order requires take_profit and/or stop_loss")

        payload: dict[str, Any] = {
            "orderId": PublicAPIClient._order_id(order_id),
            "instrument": {"symbol": symbol.upper().strip(), "type": "EQUITY"},
            "orderSide": side.upper(),
            "orderType": "STOP_LIMIT" if stop_price is not None else "LIMIT" if limit_price is not None else "MARKET",
            "expiration": expiration,
            "equityMarketSession": market_session,
            "openCloseIndicator": "OPEN" if side.upper() == "BUY" else "CLOSE",
            "orderClass": normalized_order_class,
        }
        if amount is not None:
            payload["amount"] = f"{float(amount):.2f}"
        if quantity is not None:
            payload["quantity"] = f"{float(quantity):.8f}".rstrip("0").rstrip(".")
        payload["useMargin"] = False
        if limit_price is not None:
            payload["limitPrice"] = _format_price(float(limit_price))
        if stop_price is not None:
            payload["stopPrice"] = _format_price(float(stop_price))
        if take_profit is not None:
            payload["takeProfit"] = {"limitPrice": _format_price(float(take_profit))}
        if stop_loss is not None:
            loss: dict[str, str] = {"stopPrice": _format_price(float(stop_loss))}
            if stop_loss_limit is not None:
                loss["limitPrice"] = _format_price(float(stop_loss_limit))
            payload["stopLoss"] = loss
        return payload

    async def preflight_single_leg(self, payload: dict[str, Any], account_id: str | None = None) -> dict[str, Any]:
        account = self._account(account_id)
        return await self._request(
            "POST",
            f"/userapigateway/trading/{account}/preflight/single-leg",
            payload=payload,
            mutation=True,
        )

    async def place_order(self, payload: dict[str, Any], account_id: str | None = None) -> dict[str, Any]:
        cfg = self.cfg
        if cfg.research_only or not cfg.live_equity_enabled:
            raise PublicTradingBlocked("Public equity order blocked by configuration")
        account = self._account(account_id)
        return await self._request("POST", f"/userapigateway/trading/{account}/order", payload=payload, mutation=True)

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
        expiration_time: datetime | str | None = None,
        session: str = "CORE",
        client_order_id: str | None = None,
        account_id: str | None = None,
        order_class: str = "SIMPLE",
        take_profit: float | None = None,
        stop_loss: float | None = None,
        stop_loss_limit: float | None = None,
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
            expiration_time=expiration_time,
            session=session,
            order_id=client_order_id,
            order_class=order_class,
            take_profit=take_profit,
            stop_loss=stop_loss,
            stop_loss_limit=stop_loss_limit,
        )
        try:
            preflight = await self.preflight_single_leg(payload, account_id=account_id)
        except Exception as exc:
            raise PublicOrderRejected("Public preflight failed before placement") from exc
        # Public communicates validation failures through HTTP errors. A
        # successful preflight returns economics rather than a required verdict.
        outcome = str(
            preflight.get("outcome")
            or preflight.get("status")
            or preflight.get("result")
            or ""
        ).upper()
        if outcome and outcome not in {"SUCCESS", "VALID", "OK"}:
            raise PublicOrderRejected(f"Public preflight rejected order: {outcome}")
        economic_fields = {"orderValue", "estimatedCost", "buyingPowerRequirement", "estimatedQuantity"}
        if not outcome and not any(preflight.get(key) is not None for key in economic_fields):
            raise PublicOrderRejected("Public preflight returned no recognized validation or cost fields")
        try:
            placed = await self.place_order(payload, account_id=account_id)
        except Exception as exc:
            raise PublicOrderSubmissionUnknown("Public placement outcome requires reconciliation") from exc
        if not (placed.get("orderId") or placed.get("id")):
            raise PublicOrderSubmissionUnknown("Public placement returned no order id")
        return {"preflight": preflight, "order": placed, "payload": payload}

    async def get_order(self, order_id: str, account_id: str | None = None) -> dict[str, Any]:
        account = self._account(account_id)
        # Public's current contract documents the V2 route. Keep a read-only
        # legacy fallback for broker accounts still serving the older route;
        # no mutation is attempted by either lookup.
        try:
            return await self._request("GET", f"/userapigateway/trading/{account}/order/v2/{order_id}", mutation=True)
        except PublicAPIError as exc:
            if "HTTP 404" not in str(exc):
                raise
        return await self._request("GET", f"/userapigateway/trading/{account}/order/{order_id}", mutation=True)

    async def search_orders(
        self,
        *,
        created_after: str | None = None,
        created_before: str | None = None,
        status: str | None = None,
        symbols: Iterable[str] | None = None,
        side: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, Any]:
        """Return Public's broker-authoritative recent order history.

        This is a read-only POST endpoint.  It is used as a reconciliation
        source alongside individual order lookups, never as an order action.
        """
        payload: dict[str, Any] = {}
        if created_after:
            payload["createdAfter"] = created_after
        if created_before:
            payload["createdBefore"] = created_before
        if status:
            payload["status"] = str(status).upper()
        if side:
            payload["side"] = str(side).upper()
        wanted = _symbols(symbols or [])
        if wanted:
            payload["instruments"] = [{"symbol": symbol, "type": "EQUITY"} for symbol in wanted]
        return await self._post(f"/userapigateway/trading/{self._account(account_id)}/order/search", payload)

    async def cancel_order(self, order_id: str, account_id: str | None = None) -> dict[str, Any]:
        cfg = self.cfg
        if cfg.research_only or not cfg.live_equity_enabled:
            raise PublicTradingBlocked("Public order cancellation blocked by configuration")
        account = self._account(account_id)
        return await self._request("DELETE", f"/userapigateway/trading/{account}/order/{order_id}", mutation=True)

    async def replace_order(self, payload: dict[str, Any], account_id: str | None = None) -> dict[str, Any]:
        cfg = self.cfg
        if cfg.research_only or not cfg.live_equity_enabled:
            raise PublicTradingBlocked("Public order replacement blocked by configuration")
        account = self._account(account_id)
        return await self._request("PUT", f"/userapigateway/trading/{account}/order", payload=payload, mutation=True)


async def status() -> dict[str, Any]:
    cfg = config()
    state = safety_state(cfg)
    if not cfg.enabled:
        return {"ok": False, "connected": False, "reason": "PUBLIC_API_ENABLED=false", "config": state}
    if not (cfg.access_token or cfg.secret):
        return {"ok": False, "connected": False, "reason": "PUBLIC_API_SECRET or PUBLIC_API_ACCESS_TOKEN not configured", "config": state}
    try:
        async with PublicAPIClient(cfg, use_sdk=False) as client:
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
