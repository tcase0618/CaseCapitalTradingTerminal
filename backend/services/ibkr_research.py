"""Interactive Brokers read-only research adapter.

This service is intentionally market-data only. Alpaca remains the account,
portfolio, and trading authority. IBKR is used for equities/options contract
metadata, quotes when subscriptions permit, delayed data when applicable, and
historical bars. It never places, modifies, cancels, transmits, or stages orders.
"""
from __future__ import annotations

import math
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND_DIR / ".env", override=False)

try:  # keep backend importable even before optional IBKR dependency is installed
    from ibapi.client import EClient as _IB_EClient
    from ibapi.wrapper import EWrapper as _IB_EWrapper
except Exception:  # pragma: no cover - exercised only when dependency is absent
    class _IB_EWrapper:  # type: ignore[no-redef]
        pass

    class _IB_EClient:  # type: ignore[no-redef]
        pass


class IbkrTradingBlocked(PermissionError):
    """Raised before any IBKR order mutation can reach Gateway."""


class IbkrAccountDataBlocked(PermissionError):
    """Raised when callers try to use IBKR as an account/portfolio source."""


class IbkrUnavailable(RuntimeError):
    """Raised when IBKR is disabled, missing dependencies, or disconnected."""


_IB_LOCK = threading.RLock()


def _env_bool(key: str, default: bool = False) -> bool:
    value = os.environ.get(key)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class IbkrConfig:
    enabled: bool
    mode: str
    data_only: bool
    host: str
    port: int
    client_id: int
    allow_trading: bool
    timeout_seconds: float


def config() -> IbkrConfig:
    try:
        port = int(os.environ.get("IBKR_PORT", "7496"))
    except Exception:
        port = 7496
    try:
        client_id = int(os.environ.get("IBKR_CLIENT_ID", "1"))
    except Exception:
        client_id = 1
    try:
        timeout_seconds = float(os.environ.get("IBKR_TIMEOUT_SECONDS", "8"))
    except Exception:
        timeout_seconds = 8.0
    return IbkrConfig(
        enabled=_env_bool("IBKR_ENABLED", False),
        mode=os.environ.get("IBKR_MODE", "live").strip().lower() or "live",
        data_only=_env_bool("IBKR_DATA_ONLY", True),
        host=os.environ.get("IBKR_HOST", "127.0.0.1").strip() or "127.0.0.1",
        port=port,
        client_id=client_id,
        allow_trading=_env_bool("IBKR_ALLOW_TRADING", False),
        timeout_seconds=max(2.0, min(timeout_seconds, 30.0)),
    )


def safety_state() -> dict[str, Any]:
    cfg = config()
    return {
        "enabled": cfg.enabled,
        "mode": cfg.mode,
        "data_only": cfg.data_only,
        "allow_trading": cfg.allow_trading,
        "host": cfg.host,
        "port": cfg.port,
        "client_id": cfg.client_id,
        "order_mutation_policy": "blocked_before_gateway",
        "account_data_policy": "blocked_use_alpaca_for_account_truth",
        "credentials_policy": "no_username_password_or_2fa_stored",
    }


def assert_order_blocked(action: str = "order_mutation") -> None:
    cfg = config()
    reason = (
        f"IBKR {action} blocked: Case Capital IBKR adapter is data-only "
        f"(IBKR_DATA_ONLY={cfg.data_only}, IBKR_ALLOW_TRADING={cfg.allow_trading})."
    )
    raise IbkrTradingBlocked(reason)


def place_order(*_: Any, **__: Any) -> None:
    assert_order_blocked("place_order")


def modify_order(*_: Any, **__: Any) -> None:
    assert_order_blocked("modify_order")


def cancel_order(*_: Any, **__: Any) -> None:
    assert_order_blocked("cancel_order")


def transmit_order(*_: Any, **__: Any) -> None:
    assert_order_blocked("transmit_order")


def stage_order(*_: Any, **__: Any) -> None:
    assert_order_blocked("stage_order")


def assert_account_data_blocked(action: str = "account_data") -> None:
    raise IbkrAccountDataBlocked(
        f"IBKR {action} blocked: Case Capital uses Alpaca for account, portfolio, P&L, orders, and execution truth."
    )


def account(*_: Any, **__: Any) -> None:
    assert_account_data_blocked("account")


def positions(*_: Any, **__: Any) -> None:
    assert_account_data_blocked("positions")


def open_orders(*_: Any, **__: Any) -> None:
    assert_account_data_blocked("open_orders")


def executions(*_: Any, **__: Any) -> None:
    assert_account_data_blocked("executions")


def _import_ibapi() -> tuple[Any, ...]:
    try:
        from ibapi.client import EClient
        from ibapi.common import BarData
        from ibapi.contract import Contract
        from ibapi.execution import ExecutionFilter
        from ibapi.wrapper import EWrapper
    except Exception as exc:  # pragma: no cover - depends on optional package install
        raise IbkrUnavailable(f"IBKR Python dependency unavailable: {exc}") from exc
    return EClient, EWrapper, Contract, ExecutionFilter, BarData


def _contract_to_dict(contract: Any) -> dict[str, Any]:
    return {
        "con_id": getattr(contract, "conId", None),
        "symbol": getattr(contract, "symbol", None),
        "sec_type": getattr(contract, "secType", None),
        "last_trade_date_or_contract_month": getattr(contract, "lastTradeDateOrContractMonth", None),
        "strike": getattr(contract, "strike", None),
        "right": getattr(contract, "right", None),
        "multiplier": getattr(contract, "multiplier", None),
        "exchange": getattr(contract, "exchange", None),
        "primary_exchange": getattr(contract, "primaryExchange", None),
        "currency": getattr(contract, "currency", None),
        "local_symbol": getattr(contract, "localSymbol", None),
        "trading_class": getattr(contract, "tradingClass", None),
    }


def _float_or_none(value: Any) -> float | None:
    try:
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except Exception:
        return None


def _make_stock_contract(symbol: str, *, exchange: str = "SMART", currency: str = "USD") -> Any:
    _, _, Contract, _, _ = _import_ibapi()
    c = Contract()
    c.symbol = symbol.upper().strip()
    c.secType = "STK"
    c.exchange = exchange
    c.currency = currency
    return c


def _normalize_expiry(expiry: str) -> str:
    clean = str(expiry or "").strip().replace("-", "")
    if len(clean) != 8 or not clean.isdigit():
        raise ValueError("Option expiry must be YYYYMMDD or YYYY-MM-DD")
    return clean


def _normalize_right(right: str) -> str:
    value = str(right or "").strip().upper()
    if value in {"CALL", "C"}:
        return "C"
    if value in {"PUT", "P"}:
        return "P"
    raise ValueError("Option right must be C/CALL or P/PUT")


def _make_option_contract(
    symbol: str,
    expiry: str,
    strike: float,
    right: str,
    *,
    exchange: str = "SMART",
    currency: str = "USD",
    multiplier: str = "100",
    trading_class: str | None = None,
) -> Any:
    _, _, Contract, _, _ = _import_ibapi()
    c = Contract()
    c.symbol = symbol.upper().strip()
    c.secType = "OPT"
    c.exchange = exchange
    c.currency = currency
    c.lastTradeDateOrContractMonth = _normalize_expiry(expiry)
    c.strike = float(strike)
    c.right = _normalize_right(right)
    c.multiplier = str(multiplier or "100")
    if trading_class:
        c.tradingClass = str(trading_class).strip().upper()
    return c


class _ReadOnlyIbApp(_IB_EWrapper, _IB_EClient):
    def __init__(self) -> None:
        EClient, EWrapper, _, _, _ = _import_ibapi()
        EWrapper.__init__(self)
        EClient.__init__(self, self)
        self.ready = threading.Event()
        self.account_done = threading.Event()
        self.positions_done = threading.Event()
        self.open_orders_done = threading.Event()
        self.executions_done: dict[int, threading.Event] = {}
        self.contract_done: dict[int, threading.Event] = {}
        self.history_done: dict[int, threading.Event] = {}
        self.next_order_id: int | None = None
        self.errors: list[dict[str, Any]] = []
        self.account_summary: list[dict[str, Any]] = []
        self.positions: list[dict[str, Any]] = []
        self.portfolio: list[dict[str, Any]] = []
        self.open_orders: list[dict[str, Any]] = []
        self.executions: dict[int, list[dict[str, Any]]] = {}
        self.contract_details: dict[int, list[dict[str, Any]]] = {}
        self.option_params_done: dict[int, threading.Event] = {}
        self.option_params: dict[int, list[dict[str, Any]]] = {}
        self.ticks: dict[int, dict[str, Any]] = {}
        self.history: dict[int, list[dict[str, Any]]] = {}
        self._req_id = 1000

    def req_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def nextValidId(self, orderId: int) -> None:  # noqa: N802 - IB API callback
        self.next_order_id = orderId
        self.ready.set()

    def error(self, reqId: int, errorCode: int, errorString: str, *args: Any) -> None:  # noqa: N802
        self.errors.append({
            "req_id": reqId,
            "code": errorCode,
            "message": str(errorString),
            "at": _now_iso(),
        })

    def accountSummary(self, reqId: int, account: str, tag: str, value: str, currency: str) -> None:  # noqa: N802
        self.account_summary.append({"account": account, "tag": tag, "value": value, "currency": currency})

    def accountSummaryEnd(self, reqId: int) -> None:  # noqa: N802
        self.account_done.set()

    def position(self, account: str, contract: Any, position: float, avgCost: float) -> None:
        self.positions.append({
            "account": account,
            "contract": _contract_to_dict(contract),
            "symbol": getattr(contract, "symbol", None),
            "position": _float_or_none(position),
            "avg_cost": _float_or_none(avgCost),
        })

    def positionEnd(self) -> None:  # noqa: N802
        self.positions_done.set()

    def updatePortfolio(  # noqa: N802
        self,
        contract: Any,
        position: float,
        marketPrice: float,
        marketValue: float,
        averageCost: float,
        unrealizedPNL: float,
        realizedPNL: float,
        accountName: str,
    ) -> None:
        self.portfolio.append({
            "account": accountName,
            "contract": _contract_to_dict(contract),
            "symbol": getattr(contract, "symbol", None),
            "position": _float_or_none(position),
            "market_price": _float_or_none(marketPrice),
            "market_value": _float_or_none(marketValue),
            "avg_cost": _float_or_none(averageCost),
            "unrealized_pnl": _float_or_none(unrealizedPNL),
            "realized_pnl": _float_or_none(realizedPNL),
        })

    def openOrder(self, orderId: int, contract: Any, order: Any, orderState: Any) -> None:  # noqa: N802
        self.open_orders.append({
            "order_id": orderId,
            "contract": _contract_to_dict(contract),
            "symbol": getattr(contract, "symbol", None),
            "action": getattr(order, "action", None),
            "order_type": getattr(order, "orderType", None),
            "total_quantity": _float_or_none(getattr(order, "totalQuantity", None)),
            "limit_price": _float_or_none(getattr(order, "lmtPrice", None)),
            "aux_price": _float_or_none(getattr(order, "auxPrice", None)),
            "status": getattr(orderState, "status", None),
        })

    def openOrderEnd(self) -> None:  # noqa: N802
        self.open_orders_done.set()

    def execDetails(self, reqId: int, contract: Any, execution: Any) -> None:  # noqa: N802
        self.executions.setdefault(reqId, []).append({
            "contract": _contract_to_dict(contract),
            "symbol": getattr(contract, "symbol", None),
            "exec_id": getattr(execution, "execId", None),
            "time": getattr(execution, "time", None),
            "account": getattr(execution, "acctNumber", None),
            "exchange": getattr(execution, "exchange", None),
            "side": getattr(execution, "side", None),
            "shares": _float_or_none(getattr(execution, "shares", None)),
            "price": _float_or_none(getattr(execution, "price", None)),
            "order_id": getattr(execution, "orderId", None),
        })

    def execDetailsEnd(self, reqId: int) -> None:  # noqa: N802
        self.executions_done.setdefault(reqId, threading.Event()).set()

    def contractDetails(self, reqId: int, contractDetails: Any) -> None:  # noqa: N802
        contract = getattr(contractDetails, "contract", None)
        self.contract_details.setdefault(reqId, []).append({
            "contract": _contract_to_dict(contract),
            "market_name": getattr(contractDetails, "marketName", None),
            "min_tick": _float_or_none(getattr(contractDetails, "minTick", None)),
            "order_types": getattr(contractDetails, "orderTypes", None),
            "valid_exchanges": getattr(contractDetails, "validExchanges", None),
            "long_name": getattr(contractDetails, "longName", None),
            "industry": getattr(contractDetails, "industry", None),
            "category": getattr(contractDetails, "category", None),
            "subcategory": getattr(contractDetails, "subcategory", None),
            "time_zone_id": getattr(contractDetails, "timeZoneId", None),
        })

    def contractDetailsEnd(self, reqId: int) -> None:  # noqa: N802
        self.contract_done.setdefault(reqId, threading.Event()).set()

    def securityDefinitionOptionParameter(  # noqa: N802
        self,
        reqId: int,
        exchange: str,
        underlyingConId: int,
        tradingClass: str,
        multiplier: str,
        expirations: Any,
        strikes: Any,
    ) -> None:
        self.option_params.setdefault(reqId, []).append({
            "exchange": exchange,
            "underlying_con_id": underlyingConId,
            "trading_class": tradingClass,
            "multiplier": multiplier,
            "expirations": sorted([str(x) for x in list(expirations or [])]),
            "strikes": sorted([
                float(x) for x in list(strikes or [])
                if _float_or_none(x) is not None and float(x) > 0
            ]),
        })

    def securityDefinitionOptionParameterEnd(self, reqId: int) -> None:  # noqa: N802
        self.option_params_done.setdefault(reqId, threading.Event()).set()

    def tickPrice(self, reqId: int, tickType: int, price: float, attrib: Any) -> None:  # noqa: N802
        field = {
            1: "bid",
            2: "ask",
            4: "last",
            6: "high",
            7: "low",
            9: "close",
            66: "delayed_bid",
            67: "delayed_ask",
            68: "delayed_last",
            72: "delayed_high",
            73: "delayed_low",
            75: "delayed_close",
        }.get(tickType, f"tick_price_{tickType}")
        self.ticks.setdefault(reqId, {})[field] = _float_or_none(price)
        self.ticks[reqId]["updated_at"] = _now_iso()

    def tickSize(self, reqId: int, tickType: int, size: float) -> None:  # noqa: N802
        field = {
            0: "bid_size",
            3: "ask_size",
            5: "last_size",
            69: "delayed_bid_size",
            70: "delayed_ask_size",
            71: "delayed_last_size",
        }.get(tickType, f"tick_size_{tickType}")
        self.ticks.setdefault(reqId, {})[field] = _float_or_none(size)
        self.ticks[reqId]["updated_at"] = _now_iso()

    def tickOptionComputation(  # noqa: N802
        self,
        reqId: int,
        tickType: int,
        tickAttrib: Any,
        impliedVol: float,
        delta: float,
        optPrice: float,
        pvDividend: float,
        gamma: float,
        vega: float,
        theta: float,
        undPrice: float,
    ) -> None:
        prefix = {
            10: "bid_option",
            11: "ask_option",
            12: "last_option",
            13: "model_option",
            80: "delayed_bid_option",
            81: "delayed_ask_option",
            82: "delayed_last_option",
            83: "delayed_model_option",
        }.get(tickType, f"option_{tickType}")
        self.ticks.setdefault(reqId, {}).update({
            f"{prefix}_implied_vol": _float_or_none(impliedVol),
            f"{prefix}_delta": _float_or_none(delta),
            f"{prefix}_price": _float_or_none(optPrice),
            f"{prefix}_gamma": _float_or_none(gamma),
            f"{prefix}_vega": _float_or_none(vega),
            f"{prefix}_theta": _float_or_none(theta),
            f"{prefix}_underlying_price": _float_or_none(undPrice),
            "updated_at": _now_iso(),
        })

    def historicalData(self, reqId: int, bar: Any) -> None:  # noqa: N802
        self.history.setdefault(reqId, []).append({
            "date": getattr(bar, "date", None),
            "open": _float_or_none(getattr(bar, "open", None)),
            "high": _float_or_none(getattr(bar, "high", None)),
            "low": _float_or_none(getattr(bar, "low", None)),
            "close": _float_or_none(getattr(bar, "close", None)),
            "volume": _float_or_none(getattr(bar, "volume", None)),
            "wap": _float_or_none(getattr(bar, "wap", None)),
            "bar_count": _float_or_none(getattr(bar, "barCount", None)),
        })

    def historicalDataEnd(self, reqId: int, start: str, end: str) -> None:  # noqa: N802
        self.history_done.setdefault(reqId, threading.Event()).set()


def _with_connection(fn: Any) -> dict[str, Any]:
    cfg = config()
    if not cfg.enabled:
        return {"ok": False, "reason": "IBKR_ENABLED=false", "config": safety_state()}
    if not cfg.data_only:
        return {"ok": False, "reason": "IBKR_DATA_ONLY must remain true for this adapter", "config": safety_state()}
    if cfg.allow_trading:
        return {"ok": False, "reason": "IBKR_ALLOW_TRADING must remain false for this adapter", "config": safety_state()}
    try:
        _import_ibapi()
    except IbkrUnavailable as exc:
        return {"ok": False, "reason": str(exc), "config": safety_state()}

    with _IB_LOCK:
        app = _ReadOnlyIbApp()
        thread = None
        try:
            app.connect(cfg.host, cfg.port, cfg.client_id)
            thread = threading.Thread(target=app.run, name="ibkr-readonly-client", daemon=True)
            thread.start()
            if not app.ready.wait(cfg.timeout_seconds):
                raise IbkrUnavailable("IBKR connection timed out before nextValidId")
            payload = fn(app, cfg)
            payload.setdefault("ok", True)
            payload.setdefault("checked_at", _now_iso())
            payload.setdefault("config", safety_state())
            payload.setdefault("errors", app.errors[-10:])
            return payload
        except Exception as exc:
            return {"ok": False, "reason": str(exc)[:500], "checked_at": _now_iso(), "config": safety_state(), "errors": app.errors[-10:]}
        finally:
            try:
                if app.isConnected():
                    app.disconnect()
            except Exception:
                pass
            if thread and thread.is_alive():
                thread.join(timeout=1.0)


def status() -> dict[str, Any]:
    def _probe(app: _ReadOnlyIbApp, cfg: IbkrConfig) -> dict[str, Any]:
        return {
            "connected": bool(app.isConnected()),
            "mode": cfg.mode,
            "data_only": cfg.data_only,
            "allow_trading": cfg.allow_trading,
            "next_valid_id_seen": app.next_order_id is not None,
            "quality": "live_data_only",
        }

    return _with_connection(_probe)


def contract_info(symbol: str) -> dict[str, Any]:
    def _run(app: _ReadOnlyIbApp, cfg: IbkrConfig) -> dict[str, Any]:
        contract = _make_stock_contract(symbol)
        req_id = app.req_id()
        app.contract_done[req_id] = threading.Event()
        app.reqContractDetails(req_id, contract)
        app.contract_done[req_id].wait(cfg.timeout_seconds)
        return {"symbol": symbol.upper(), "contracts": app.contract_details.get(req_id, [])}

    return _with_connection(_run)


def _quote_contract(
    app: _ReadOnlyIbApp,
    cfg: IbkrConfig,
    contract: Any,
    *,
    delayed_allowed: bool,
    no_tick_quality: str,
    no_tick_reason: str,
) -> tuple[dict[str, Any], bool, list[dict[str, Any]]]:
    request_ids: list[int] = []

    def _request_ticks(market_data_type: int, label: str) -> tuple[int, dict[str, Any]]:
        req_id = app.req_id()
        request_ids.append(req_id)
        app.reqMarketDataType(market_data_type)
        app.reqMktData(req_id, contract, "100,101,104,106", False, False, [])
        time.sleep(min(3.0, cfg.timeout_seconds))
        app.cancelMktData(req_id)
        payload = dict(app.ticks.get(req_id, {}))
        payload["market_data_type"] = label
        return req_id, payload

    _, ticks = _request_ticks(1, "live_if_subscribed")
    if delayed_allowed and not any(ticks.get(k) is not None for k in ("bid", "ask", "last", "close")):
        _, delayed_ticks = _request_ticks(3, "delayed_if_available")
        ticks = {**ticks, **delayed_ticks}
    if delayed_allowed and not any(ticks.get(k) is not None for k in ("bid", "ask", "last", "close", "delayed_bid", "delayed_ask", "delayed_last", "delayed_close", "model_option_price")):
        _, frozen_ticks = _request_ticks(4, "delayed_frozen_if_available")
        ticks = {**ticks, **frozen_ticks}
    price_fields = ("bid", "ask", "last", "close", "delayed_bid", "delayed_ask", "delayed_last", "delayed_close")
    option_fields = (
        "bid_option_price", "ask_option_price", "last_option_price", "model_option_price",
        "bid_option_delta", "ask_option_delta", "last_option_delta", "model_option_delta",
        "delayed_bid_option_price", "delayed_ask_option_price", "delayed_last_option_price", "delayed_model_option_price",
        "delayed_bid_option_delta", "delayed_ask_option_delta", "delayed_last_option_delta", "delayed_model_option_delta",
    )
    has_tick = any(ticks.get(k) is not None for k in (*price_fields, *option_fields))
    relevant_ids = set(request_ids)
    relevant_errors = [
        e for e in app.errors
        if e.get("req_id") in relevant_ids or e.get("code") in {10167, 10168, 10186, 200, 354, 420, 10090, 2186}
    ]
    if not has_tick:
        ticks["data_quality"] = no_tick_quality
        ticks["reason"] = no_tick_reason
    return ticks, has_tick, relevant_errors[-8:]


def quote(symbol: str, *, delayed_allowed: bool = True) -> dict[str, Any]:
    def _run(app: _ReadOnlyIbApp, cfg: IbkrConfig) -> dict[str, Any]:
        contract = _make_stock_contract(symbol)
        ticks, has_tick, relevant_errors = _quote_contract(
            app,
            cfg,
            contract,
            delayed_allowed=delayed_allowed,
            no_tick_quality="NO_TICK",
            no_tick_reason="IBKR returned no live or delayed quote ticks; check market-data subscription, delayed permissions, session, or contract routing.",
        )
        if not has_tick:
            return {
                "ok": False,
                "symbol": symbol.upper(),
                "quote": ticks,
                "data_quality": ticks["data_quality"],
                "reason": ticks["reason"],
                "permission_errors": relevant_errors[-8:],
            }
        ticks["data_quality"] = "LIVE_OR_DELAYED_TICK"
        return {"symbol": symbol.upper(), "quote": ticks, "data_quality": ticks["data_quality"]}

    return _with_connection(_run)


def option_chain(symbol: str, *, max_expirations: int = 12, max_strikes: int = 240) -> dict[str, Any]:
    def _run(app: _ReadOnlyIbApp, cfg: IbkrConfig) -> dict[str, Any]:
        stock = _make_stock_contract(symbol)
        contract_req_id = app.req_id()
        app.contract_done[contract_req_id] = threading.Event()
        app.reqContractDetails(contract_req_id, stock)
        app.contract_done[contract_req_id].wait(cfg.timeout_seconds)
        underlying_rows = app.contract_details.get(contract_req_id, [])
        underlying = next((row for row in underlying_rows if (row.get("contract") or {}).get("con_id")), None)
        if not underlying:
            return {
                "ok": False,
                "symbol": symbol.upper(),
                "data_quality": "NO_UNDERLYING_CONTRACT",
                "reason": "IBKR could not resolve the underlying stock contract before requesting option parameters.",
                "underlying_candidates": underlying_rows[:5],
            }

        underlying_contract = underlying.get("contract") or {}
        underlying_con_id = underlying_contract.get("con_id")
        req_id = app.req_id()
        app.option_params_done[req_id] = threading.Event()
        app.reqSecDefOptParams(req_id, symbol.upper(), "", "STK", int(underlying_con_id))
        app.option_params_done[req_id].wait(cfg.timeout_seconds)
        chains_raw = app.option_params.get(req_id, [])
        chains: list[dict[str, Any]] = []
        all_expirations: set[str] = set()
        all_strikes: set[float] = set()
        for row in chains_raw:
            expirations = list(row.get("expirations") or [])
            strikes = list(row.get("strikes") or [])
            all_expirations.update(expirations)
            all_strikes.update(strikes)
            chains.append({
                **row,
                "expirations": expirations[:max(1, max_expirations)],
                "strikes": strikes[:max(1, max_strikes)],
                "total_expirations": len(expirations),
                "total_strikes": len(strikes),
                "truncated": len(expirations) > max_expirations or len(strikes) > max_strikes,
            })
        if not chains_raw:
            return {
                "ok": False,
                "symbol": symbol.upper(),
                "underlying": underlying_contract,
                "chains": [],
                "data_quality": "NO_OPTION_CHAIN",
                "reason": "IBKR returned no option-chain parameters; check option permissions or whether the symbol is optionable.",
            }
        return {
            "symbol": symbol.upper(),
            "underlying": underlying_contract,
            "chains": chains,
            "summary": {
                "chain_count": len(chains_raw),
                "exchange_count": len({row.get("exchange") for row in chains_raw if row.get("exchange")}),
                "trading_classes": sorted({row.get("trading_class") for row in chains_raw if row.get("trading_class")}),
                "total_unique_expirations": len(all_expirations),
                "total_unique_strikes": len(all_strikes),
                "returned_max_expirations_per_chain": max_expirations,
                "returned_max_strikes_per_chain": max_strikes,
            },
            "data_quality": "OPTION_CHAIN_METADATA",
        }

    return _with_connection(_run)


def option_contract_info(
    symbol: str,
    *,
    expiry: str,
    strike: float,
    right: str,
    exchange: str = "SMART",
    trading_class: str | None = None,
) -> dict[str, Any]:
    def _run(app: _ReadOnlyIbApp, cfg: IbkrConfig) -> dict[str, Any]:
        contract = _make_option_contract(symbol, expiry, strike, right, exchange=exchange, trading_class=trading_class)
        req_id = app.req_id()
        app.contract_done[req_id] = threading.Event()
        app.reqContractDetails(req_id, contract)
        app.contract_done[req_id].wait(cfg.timeout_seconds)
        rows = app.contract_details.get(req_id, [])
        return {
            "symbol": symbol.upper(),
            "option": _contract_to_dict(contract),
            "contracts": rows,
            "data_quality": "OPTION_CONTRACT_DETAILS" if rows else "NO_OPTION_CONTRACT_DETAILS",
        }

    return _with_connection(_run)


def option_quote(
    symbol: str,
    *,
    expiry: str,
    strike: float,
    right: str,
    delayed_allowed: bool = True,
    exchange: str = "SMART",
    trading_class: str | None = None,
) -> dict[str, Any]:
    def _run(app: _ReadOnlyIbApp, cfg: IbkrConfig) -> dict[str, Any]:
        contract = _make_option_contract(symbol, expiry, strike, right, exchange=exchange, trading_class=trading_class)
        ticks, has_tick, relevant_errors = _quote_contract(
            app,
            cfg,
            contract,
            delayed_allowed=delayed_allowed,
            no_tick_quality="NO_OPTION_TICK",
            no_tick_reason="IBKR returned no live or delayed option quote ticks; check OPRA/subscription permissions, delayed permissions, or contract routing.",
        )
        if not has_tick:
            return {
                "ok": False,
                "symbol": symbol.upper(),
                "option": _contract_to_dict(contract),
                "quote": ticks,
                "data_quality": ticks["data_quality"],
                "reason": ticks["reason"],
                "permission_errors": relevant_errors[-8:],
            }
        ticks["data_quality"] = "LIVE_OR_DELAYED_OPTION_TICK"
        return {
            "symbol": symbol.upper(),
            "option": _contract_to_dict(contract),
            "quote": ticks,
            "data_quality": ticks["data_quality"],
        }

    return _with_connection(_run)


def option_historical_data(
    symbol: str,
    *,
    expiry: str,
    strike: float,
    right: str,
    duration: str = "1 W",
    bar_size: str = "1 day",
    what_to_show: str = "TRADES",
    use_rth: bool = True,
    exchange: str = "SMART",
    trading_class: str | None = None,
) -> dict[str, Any]:
    def _run(app: _ReadOnlyIbApp, cfg: IbkrConfig) -> dict[str, Any]:
        contract = _make_option_contract(symbol, expiry, strike, right, exchange=exchange, trading_class=trading_class)
        req_id = app.req_id()
        app.history_done[req_id] = threading.Event()
        app.reqHistoricalData(
            req_id,
            contract,
            "",
            duration,
            bar_size,
            what_to_show,
            1 if use_rth else 0,
            1,
            False,
            [],
        )
        app.history_done[req_id].wait(cfg.timeout_seconds)
        bars = app.history.get(req_id, [])
        return {
            "symbol": symbol.upper(),
            "option": _contract_to_dict(contract),
            "duration": duration,
            "bar_size": bar_size,
            "what_to_show": what_to_show,
            "use_rth": use_rth,
            "bars": bars,
            "data_quality": "OPTION_HISTORICAL_BARS" if bars else "NO_OPTION_HISTORICAL_BARS",
        }

    return _with_connection(_run)


def historical_data(
    symbol: str,
    *,
    duration: str = "1 M",
    bar_size: str = "1 day",
    what_to_show: str = "TRADES",
    use_rth: bool = True,
) -> dict[str, Any]:
    def _run(app: _ReadOnlyIbApp, cfg: IbkrConfig) -> dict[str, Any]:
        contract = _make_stock_contract(symbol)
        req_id = app.req_id()
        app.history_done[req_id] = threading.Event()
        app.reqHistoricalData(
            req_id,
            contract,
            "",
            duration,
            bar_size,
            what_to_show,
            1 if use_rth else 0,
            1,
            False,
            [],
        )
        app.history_done[req_id].wait(cfg.timeout_seconds)
        return {
            "symbol": symbol.upper(),
            "duration": duration,
            "bar_size": bar_size,
            "what_to_show": what_to_show,
            "use_rth": use_rth,
            "bars": app.history.get(req_id, []),
        }

    return _with_connection(_run)
