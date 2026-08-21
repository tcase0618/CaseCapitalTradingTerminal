"""Separate Options Desk for a dedicated Alpaca paper options account.

This module deliberately does not import trade_floor.py. It uses only
OPTIONS_* credentials and executes only PM-approved options tickets.
"""
from __future__ import annotations

import asyncio
import os
import re
import html
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any

import httpx

from .db import get_db, log_activity, stamped

OPTIONS_EQUITY = 20_000.0
MAX_RISK_PCT = 0.05
MAX_RISK_USD = 1000.0
WATCH_RISK_USD = 200.0
STARTER_RISK_USD = 350.0
ACCUMULATE_RISK_USD = 600.0
BOTH_RISK_USD = 1000.0
STANDARD_RISK_USD = 350.0
OPTIONS_DAILY_PREMIUM_CAP_USD = 4000.0
OPTIONS_INITIAL_STOP_PCT = -20.0
PRICE_BASIS = "mid"
OPTIONS_HARD_STOP_PCT = -25.0
TAKE_PROFIT_TIER1_PCT = 30.0
TAKE_PROFIT_TIER1_SELL_FRACTION = 0.5
TIME_STOP_DTE_FRACTION = 0.40
TIME_STOP_MIN_PNL_PCT = 10.0
THETA_STOP_PCT_OF_PREMIUM = -3.0
EVENT_STOP_DAYS_BEFORE_EARNINGS = 1
OPTIONS_RATCHET_TIERS: list[tuple[float, float]] = [
    (25.0, 5.0),
    (50.0, 25.0),
    (75.0, 50.0),
    (100.0, 75.0),
    (150.0, 120.0),
    (200.0, 150.0),
]
OPTION_ACTIVE_STATUSES = {
    "active",
    "hard_stop_close_submitted",
    "ratchet_close_submitted",
    "theta_stop_close_submitted",
    "time_stop_close_submitted",
    "event_stop_close_submitted",
    "take_profit_tier1_close_submitted",
    "tail_take_profit_tier1_close_submitted",
    "tail_ratchet_trail_close_submitted",
    "tail_dte_exit_close_submitted",
    "pending_protective_close_market_closed",
}
MIN_OPEN_INTEREST = 500
MIN_VOLUME_WHEN_LOW_OI = 200
MIN_OPTION_VOLUME_IF_OI_UNKNOWN = int(os.environ.get("OPTIONS_MIN_VOLUME_IF_OI_UNKNOWN", "200") or 200)
MAX_SPREAD_ABS = 0.75
MAX_SPREAD_PCT = 0.08
MAX_INDICATIVE_SPREAD_PCT = 0.20
MIN_INDICATIVE_OPTION_VOLUME = 250
MIN_OPTION_PREMIUM = 1.00
MIN_ABS_DELTA = 0.45
MAX_ABS_DELTA = 0.70
AUTO_MAX_ORDERS_PER_SCAN = int(os.environ.get("OPTIONS_AUTO_MAX_ORDERS_PER_SCAN", "2") or 2)
AUTO_MAX_ORDERS_PER_DAY = int(os.environ.get("OPTIONS_AUTO_MAX_ORDERS_PER_DAY", "5") or 5)
OPTIONS_ALPACA_REFRESH_LIMIT = int(os.environ.get("OPTIONS_ALPACA_REFRESH_LIMIT", "18") or 18)
OPTIONS_EXECUTION_ENABLED = os.environ.get("ENABLE_OPTIONS_EXECUTION", "false").strip().lower() in {"1", "true", "yes", "on"}
OPTIONS_ALLOW_INDICATIVE_EXECUTION = os.environ.get("OPTIONS_ALLOW_INDICATIVE_EXECUTION", "false").strip().lower() in {"1", "true", "yes", "on"}
OPTIONS_MAX_QUOTE_AGE_SECONDS = int(os.environ.get("OPTIONS_MAX_QUOTE_AGE_SECONDS", "900") or 900)
ALPACA_DATA_BASE = "https://data.alpaca.markets"
ALPACA_OPTIONS_FEED = os.environ.get("OPTIONS_APCA_DATA_FEED", "indicative").strip() or "indicative"
OCC_SYMBOL_RE = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")
ET = ZoneInfo("America/New_York")

def _options_key() -> str:
    return os.environ.get("OPTIONS_APCA_API_KEY_ID", "").strip()


def _options_secret() -> str:
    return os.environ.get("OPTIONS_APCA_API_SECRET_KEY", "").strip()


def _equity_key() -> str:
    return os.environ.get("APCA_API_KEY_ID", "").strip()


def _options_trade_base() -> str:
    base = os.environ.get("OPTIONS_APCA_API_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")
    if base.endswith("/v2"):
        base = base[:-3]
    return base


def _options_headers() -> dict[str, str]:
    return {
        "APCA-API-KEY-ID": _options_key(),
        "APCA-API-SECRET-KEY": _options_secret(),
        "Content-Type": "application/json",
    }


def _mask_key(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    if len(value) <= 8:
        return f"{value[:2]}...{value[-2:]}"
    return f"{value[:4]}...{value[-4:]}"


def options_account_route_guard() -> dict[str, Any]:
    """Prove Options Desk requests are routed through OPTIONS_APCA only."""
    opt_key = _options_key()
    opt_secret = _options_secret()
    equity_key = _equity_key()
    if not opt_key or not opt_secret:
        return {
            "ok": False,
            "reason": "missing_options_alpaca_keys",
            "options_key_id": _mask_key(opt_key),
            "equity_key_id": _mask_key(equity_key),
            "base_url": _options_trade_base(),
        }
    if equity_key and opt_key == equity_key:
        return {
            "ok": False,
            "reason": "options_credentials_match_equity_account",
            "options_key_id": _mask_key(opt_key),
            "equity_key_id": _mask_key(equity_key),
            "base_url": _options_trade_base(),
        }
    return {
        "ok": True,
        "options_key_id": _mask_key(opt_key),
        "equity_key_id": _mask_key(equity_key),
        "base_url": _options_trade_base(),
        "paper_only": paper_only(),
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_et() -> str:
    return datetime.now(ET).strftime("%b %d %H:%M ET")


def configured() -> bool:
    return bool(_options_key() and _options_secret())


def paper_only() -> bool:
    return "paper-api.alpaca.markets" in _options_trade_base()


def options_execution_enabled() -> bool:
    return OPTIONS_EXECUTION_ENABLED


def _candidate_order_id(candidate_id: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_-]+", "-", str(candidate_id or "").strip())
    return f"od-{clean}"[:120]


def _local_options_session_open(now: datetime | None = None) -> bool:
    now_et = now.astimezone(ET) if now else datetime.now(ET)
    if now_et.weekday() >= 5:
        return False
    minutes = now_et.hour * 60 + now_et.minute
    return (9 * 60 + 30) <= minutes < (16 * 60)


async def _options_market_status() -> dict[str, Any]:
    """Return whether options orders should be sent right now.

    Alpaca option market orders are regular-session only. The local ET guard is
    intentionally strict even if the account clock reports an extended-hours
    equity session.
    """
    local_open = _local_options_session_open()
    status: dict[str, Any] = {
        "ok": True,
        "is_open": local_open,
        "source": "local_regular_options_session",
        "checked_at": _now(),
    }
    if not configured():
        return {**status, "ok": False, "reason": "missing_options_alpaca_keys"}
    try:
        async with httpx.AsyncClient(timeout=5.0, headers=_options_headers()) as client:
            r = await client.get(f"{_options_trade_base()}/v2/clock")
        if r.status_code in (200, 201):
            clock = r.json()
            status.update({
                "source": "alpaca_clock_and_local_regular_options_session",
                "alpaca_is_open": bool(clock.get("is_open")),
                "next_open": clock.get("next_open"),
                "next_close": clock.get("next_close"),
                "is_open": bool(clock.get("is_open")) and local_open,
            })
        else:
            status.update({"ok": False, "reason": f"alpaca_clock_{r.status_code}", "detail": r.text[:180]})
    except Exception as exc:
        status.update({"ok": False, "reason": "alpaca_clock_error", "detail": str(exc)[:180]})
    return status


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _esc(v: Any) -> str:
    return html.escape(str(v or ""), quote=False)


def _fmt_money(v: Any) -> str:
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return "-"


def _fmt_pct(v: Any) -> str:
    try:
        n = float(v)
        return f"{'+' if n >= 0 else ''}{n:.1f}%"
    except (TypeError, ValueError):
        return "-"


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _quote_age_seconds(snapshot: dict[str, Any]) -> int | None:
    ts = _parse_dt(snapshot.get("quote_time") or snapshot.get("trade_time"))
    if not ts:
        return None
    return max(0, int((datetime.now(timezone.utc) - ts.astimezone(timezone.utc)).total_seconds()))


def _execution_grade_allowed(data_quality: str | None) -> bool:
    if str(data_quality or "").upper() == "EXECUTION_GRADE":
        return True
    return bool(OPTIONS_ALLOW_INDICATIVE_EXECUTION)


async def _telegram_send(text: str) -> bool:
    try:
        from . import telegram_service
        return await telegram_service.send_message(text)
    except Exception:
        return False


def _format_contract_line(item: dict[str, Any]) -> str:
    return (
        f"<b>${_esc(item.get('ticker'))}</b> {_esc(item.get('option_type'))} "
        f"{_esc(item.get('expiration'))} ${_safe_float(item.get('strike')):g} "
        f"Qty <b>{_safe_int(item.get('qty'))}</b> @ <b>{_fmt_money(item.get('entry_premium'))}</b>"
    )


def _next_ratchet_tier(ratchet: dict[str, Any]) -> dict[str, Any] | None:
    peak = _safe_float(ratchet.get("peak_gain_pct"))
    for tier in ratchet.get("tiers") or []:
        if _safe_float(tier.get("trigger_gain_pct")) > peak:
            return tier
    return None


async def _send_grouped_fill_message(fills: list[dict[str, Any]]) -> bool:
    if not fills:
        return False
    lines = [
        "<b>CASE CAPITAL | OPTIONS FILLS</b>",
        f"<code>{_now_et()}</code>",
        "",
        f"Filled contracts: <b>{len(fills)}</b>",
    ]
    total_risk = sum(_safe_float(x.get("entry_notional")) for x in fills)
    if total_risk:
        lines.append(f"Total premium risk: <b>{_fmt_money(total_risk)}</b>")
    lines.append("")
    lines.extend(_format_contract_line(x) for x in fills[:12])
    if len(fills) > 12:
        lines.append(f"...and {len(fills) - 12} more.")
    lines.extend(["", "Hard stop: <b>-20%</b>", "Ratchet: <b>No TP</b> / next tier <b>+25% -> lock +5%</b>"])
    return await _telegram_send("\n".join(lines))


async def _send_ratchet_message(symbol: str, ticker: str, entry: float, current: float, ratchet: dict[str, Any], next_tier: dict[str, Any] | None) -> bool:
    next_text = "MAX LOCK" if not next_tier else f"+{next_tier.get('trigger_gain_pct'):g}% -> lock +{next_tier.get('locked_gain_pct'):g}%"
    msg = "\n".join([
        "<b>CASE CAPITAL | OPTIONS RISK UPDATE</b>",
        f"<code>{_now_et()}</code>",
        "",
        f"<b>${_esc(ticker)}</b> {_esc(symbol)}",
        f"Entry: <b>{_fmt_money(entry)}</b>",
        f"Peak: <b>{_fmt_money(ratchet.get('peak_premium'))}</b> ({_fmt_pct(ratchet.get('peak_gain_pct'))})",
        f"New floor: <b>{_fmt_money(ratchet.get('floor_premium'))}</b> ({_fmt_pct(ratchet.get('locked_floor_pct'))})",
        f"Current bid/mark: <b>{_fmt_money(current)}</b>",
        f"Next tier: <b>{next_text}</b>",
    ])
    return await _telegram_send(msg)


async def _send_exit_message(symbol: str, ticker: str, reason: str, entry: float, exit_price: float, qty: int) -> bool:
    pnl = (exit_price - entry) * qty * 100 if entry > 0 and exit_price >= 0 else None
    pct = ((exit_price - entry) / entry * 100.0) if entry > 0 and exit_price >= 0 else None
    title = "CASE CAPITAL | OPTIONS EXIT"
    reason_label = "Premium hard stop" if reason == "hard_stop" else "Ratchet floor hit"
    msg = "\n".join([
        f"<b>{title}</b>",
        f"<code>{_now_et()}</code>",
        "",
        f"<b>${_esc(ticker)}</b> {_esc(symbol)}",
        f"Reason: <b>{reason_label}</b>",
        f"Entry: <b>{_fmt_money(entry)}</b>",
        f"Exit trigger: <b>{_fmt_money(exit_price)}</b>",
        f"P/L: <b>{_fmt_pct(pct)}</b>",
        f"Dollars: <b>{_fmt_money(pnl)}</b>",
    ])
    return await _telegram_send(msg)


async def account() -> dict[str, Any]:
    route = options_account_route_guard()
    if not route.get("ok"):
        return {"ok": False, "configured": configured(), "paper_only": paper_only(), "route": route, "reason": route.get("reason")}
    try:
        premium_used = await daily_premium_used()
        async with httpx.AsyncClient(timeout=15.0, headers=_options_headers()) as client:
            r = await client.get(f"{_options_trade_base()}/v2/account")
        if r.status_code != 200:
            return {"ok": False, "configured": True, "paper_only": paper_only(), "route": route, "reason": f"alpaca_http_{r.status_code}"}
        data = r.json()
        return {
            "ok": True,
            "configured": True,
            "paper_only": paper_only(),
            "route": route,
            "daily_premium_used": premium_used,
            "daily_premium_cap": OPTIONS_DAILY_PREMIUM_CAP_USD,
            "account": {
                "status": data.get("status"),
                "equity": data.get("equity"),
                "cash": data.get("cash"),
                "buying_power": data.get("buying_power"),
                "options_approved_level": data.get("options_approved_level"),
                "options_trading_level": data.get("options_trading_level"),
                "trading_blocked": data.get("trading_blocked"),
            },
        }
    except Exception as exc:
        return {"ok": False, "configured": True, "paper_only": paper_only(), "route": route, "reason": exc.__class__.__name__}


async def positions() -> dict[str, Any]:
    route = options_account_route_guard()
    if not route.get("ok"):
        return {"positions": [], "configured": configured(), "route": route}
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=_options_headers()) as client:
            r = await client.get(f"{_options_trade_base()}/v2/positions")
        return {"positions": r.json() if r.status_code == 200 else [], "configured": True, "route": route}
    except Exception:
        return {"positions": [], "configured": True, "route": route}


async def orders(status: str = "all", limit: int = 100) -> dict[str, Any]:
    route = options_account_route_guard()
    if not route.get("ok"):
        return {"orders": [], "configured": configured(), "route": route}
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=_options_headers()) as client:
            r = await client.get(f"{_options_trade_base()}/v2/orders", params={"status": status, "limit": limit})
        return {"orders": r.json() if r.status_code == 200 else [], "configured": True, "route": route}
    except Exception:
        return {"orders": [], "configured": True, "route": route}


async def account_identity() -> dict[str, Any]:
    route = options_account_route_guard()
    payload: dict[str, Any] = {
        "ok": bool(route.get("ok")),
        "configured": configured(),
        "paper_only": paper_only(),
        "route": route,
    }
    if not route.get("ok"):
        return payload
    try:
        async with httpx.AsyncClient(timeout=10.0, headers=_options_headers()) as client:
            r = await client.get(f"{_options_trade_base()}/v2/account")
        payload["alpaca_status_code"] = r.status_code
        if r.status_code == 200:
            data = r.json() or {}
            payload["account"] = {
                "status": data.get("status"),
                "options_trading_level": data.get("options_trading_level"),
                "options_approved_level": data.get("options_approved_level"),
                "trading_blocked": data.get("trading_blocked"),
                "account_number_last4": str(data.get("account_number") or "")[-4:],
            }
        else:
            payload["reason"] = f"alpaca_http_{r.status_code}"
    except Exception as exc:
        payload.update({"ok": False, "reason": exc.__class__.__name__})
    return payload


def _order_premium_usd(order: dict[str, Any]) -> float:
    try:
        qty = float(order.get("qty") or order.get("filled_qty") or 0)
    except (TypeError, ValueError):
        qty = 0.0
    try:
        price = float(order.get("limit_price") or order.get("filled_avg_price") or 0)
    except (TypeError, ValueError):
        price = 0.0
    return max(0.0, qty * price * 100)


def _parse_occ_symbol(symbol: str) -> dict[str, Any] | None:
    m = OCC_SYMBOL_RE.match(str(symbol or "").upper())
    if not m:
        return None
    root, yymmdd, cp, strike_raw = m.groups()
    try:
        exp = datetime.strptime(yymmdd, "%y%m%d").date().isoformat()
        strike = int(strike_raw) / 1000.0
    except Exception:
        return None
    return {"root": root, "expiration": exp, "type": cp, "strike": strike}


def _ibkr_params_from_occ_symbol(symbol: str) -> dict[str, Any] | None:
    parsed = _parse_occ_symbol(symbol)
    if not parsed:
        return None
    return {
        "symbol": parsed["root"],
        "expiry": str(parsed["expiration"]).replace("-", ""),
        "strike": parsed["strike"],
        "right": parsed["type"],
        "exchange": "SMART",
        "trading_class": parsed["root"],
    }


def _alpaca_order_preview_from_ticket(ticket: dict[str, Any]) -> dict[str, Any]:
    instrument = ticket.get("instrument") or {}
    symbol = str(instrument.get("symbol") or instrument.get("contractSymbol") or "").upper()
    ask = _safe_float(instrument.get("ask") or instrument.get("premium") or instrument.get("net_debit"))
    qty = _safe_int(ticket.get("contracts"))
    return {
        "broker": "ALPACA_OPTIONS",
        "submit_endpoint": "/api/options_desk/execute",
        "candidate_id": ticket.get("candidate_id"),
        "payload": {
            "symbol": symbol,
            "qty": str(qty),
            "side": "buy",
            "type": "limit",
            "time_in_force": "day",
            "limit_price": round(ask, 2) if ask > 0 else None,
            "client_order_id": _candidate_order_id(str(ticket.get("candidate_id") or "")),
        },
        "not_submitted": True,
        "execution_authority": "alpaca_options_only",
    }


async def _option_snapshot(symbol: str) -> dict[str, Any]:
    parsed = _parse_occ_symbol(symbol)
    if not configured() or not parsed:
        return {"ok": False, "symbol": symbol, "reason": "unparseable_option_symbol"}
    params = {
        "feed": ALPACA_OPTIONS_FEED,
        "root_symbol": parsed["root"],
        "expiration_date_gte": parsed["expiration"],
        "expiration_date_lte": parsed["expiration"],
        "strike_price_gte": parsed["strike"],
        "strike_price_lte": parsed["strike"],
        "limit": 100,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=_options_headers()) as client:
            r = await client.get(f"{ALPACA_DATA_BASE}/v1beta1/options/snapshots/{parsed['root']}", params=params)
        if r.status_code != 200:
            return {"ok": False, "symbol": symbol, "reason": f"alpaca_data_http_{r.status_code}"}
        snap = ((r.json() or {}).get("snapshots") or {}).get(symbol)
        if not snap:
            return {"ok": False, "symbol": symbol, "reason": "snapshot_not_returned"}
        quote = snap.get("latestQuote") or {}
        trade = snap.get("latestTrade") or {}
        greeks = snap.get("greeks") or {}
        daily_bar = snap.get("dailyBar") or {}
        bid = _safe_float(quote.get("bp"))
        ask = _safe_float(quote.get("ap"))
        last = _safe_float(trade.get("p"))
        mid = round((bid + ask) / 2, 2) if bid > 0 and ask > 0 else 0.0
        mark = bid or mid or last
        data_quality = "INDICATIVE" if ALPACA_OPTIONS_FEED == "indicative" else "EXECUTION_GRADE"
        return {
            "ok": True,
            "symbol": symbol,
            "bid": round(bid, 2),
            "ask": round(ask, 2),
            "mid": mid,
            "last": round(last, 2),
            "mark": round(mark, 2),
            "theta": _safe_float(greeks.get("theta")),
            "delta": _safe_float(greeks.get("delta")),
            "provider_delta_present": bool(_safe_float(greeks.get("delta"))),
            "gamma": _safe_float(greeks.get("gamma")),
            "vega": _safe_float(greeks.get("vega")),
            "quote_time": quote.get("t"),
            "trade_time": trade.get("t"),
            "data_feed": ALPACA_OPTIONS_FEED,
            "data_quality": data_quality,
            "volume": _safe_int(daily_bar.get("v")),
            "open_interest": _safe_int(snap.get("openInterest")),
            "open_interest_source": "reported" if snap.get("openInterest") is not None else "unavailable",
        }
    except Exception as exc:
        return {"ok": False, "symbol": symbol, "reason": exc.__class__.__name__}


def _options_ratchet_floor_pct(gain_pct: float) -> float:
    floor = OPTIONS_INITIAL_STOP_PCT
    for trigger, locked in OPTIONS_RATCHET_TIERS:
        if gain_pct >= trigger:
            floor = locked
    return floor


def options_ratchet_state(entry_premium: float, current_bid: float | None = None, peak_premium: float | None = None) -> dict[str, Any]:
    entry = max(0.0, float(entry_premium or 0))
    has_current = current_bid is not None
    current = max(0.0, float(current_bid or 0))
    peak = max(entry, float(peak_premium or 0), current)
    gain_pct = ((peak - entry) / entry * 100.0) if entry > 0 else 0.0
    floor_pct = _options_ratchet_floor_pct(gain_pct)
    floor_premium = entry * (1 + floor_pct / 100.0) if entry > 0 else 0.0
    exit_triggered = bool(has_current and floor_premium > 0 and current <= floor_premium)
    return {
        "policy": "premium_ratchet_no_take_profit",
        "take_profit": None,
        "entry_premium": round(entry, 2),
        "current_bid": round(current, 2),
        "peak_premium": round(peak, 2),
        "peak_gain_pct": round(gain_pct, 2),
        "locked_floor_pct": round(floor_pct, 2),
        "floor_premium": round(floor_premium, 2),
        "exit_triggered": exit_triggered,
        "exit_basis": "live bid <= ratchet floor",
        "tiers": [{"trigger_gain_pct": t, "locked_gain_pct": l} for t, l in OPTIONS_RATCHET_TIERS],
        "initial_stop_pct": OPTIONS_INITIAL_STOP_PCT,
    }


def _position_raw_present(v: Any) -> bool:
    return v is not None and str(v).strip() != ""


def _snapshot_mid(snap: dict[str, Any] | None) -> float:
    snap = snap or {}
    bid = _safe_float(snap.get("bid"))
    ask = _safe_float(snap.get("ask"))
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return _safe_float(snap.get("mid"))


def _spread_cost_context(fill_price: float, snap: dict[str, Any] | None) -> dict[str, Any]:
    snap = snap or {}
    bid = _safe_float(snap.get("bid"))
    ask = _safe_float(snap.get("ask"))
    mid = _snapshot_mid(snap)
    spread = ask - bid if bid > 0 and ask > 0 else _safe_float(snap.get("spread"))
    spread_pct = spread / ask if ask > 0 and spread > 0 else None
    spread_cost_paid = fill_price - mid if fill_price > 0 and mid > 0 else None
    spread_cost_pct = spread_cost_paid / mid * 100.0 if spread_cost_paid is not None and mid > 0 else None
    return {
        "price_basis": PRICE_BASIS,
        "fill_price": round(fill_price, 4) if fill_price > 0 else None,
        "mid_at_fill": round(mid, 4) if mid > 0 else None,
        "bid_at_fill": round(bid, 4) if bid > 0 else None,
        "ask_at_fill": round(ask, 4) if ask > 0 else None,
        "spread_at_fill": round(spread, 4) if spread and spread > 0 else None,
        "spread_pct_at_fill": round(spread_pct * 100.0, 2) if spread_pct is not None else None,
        "spread_cost_paid": round(spread_cost_paid, 4) if spread_cost_paid is not None else None,
        "spread_cost_pct": round(spread_cost_pct, 2) if spread_cost_pct is not None else None,
    }


def _option_position_context(position: dict[str, Any], snap: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve option risk on a mid-price basis when a live quote exists."""
    snap = snap or {}
    qty = _safe_int(position.get("qty"))
    entry = _safe_float(position.get("avg_entry_price"))
    if entry <= 0:
        entry = _safe_float(position.get("cost_basis")) / max(1, qty * 100)

    current = 0.0
    price_source = "unavailable"
    snap_mid = _snapshot_mid(snap) if snap.get("ok") else 0.0
    if snap_mid > 0:
        current = max(0.0, snap_mid)
        price_source = "alpaca_snapshot_mid"
    elif _position_raw_present(position.get("current_price")):
        current = max(0.0, _safe_float(position.get("current_price")))
        price_source = "alpaca_position_current_price_fallback"
    elif _position_raw_present(position.get("market_value")) and qty > 0:
        current = max(0.0, _safe_float(position.get("market_value")) / max(1, qty * 100))
        price_source = "alpaca_position_market_value_fallback"
    elif snap.get("ok"):
        bid = _safe_float(snap.get("bid"))
        if bid > 0:
            current = bid
            price_source = "alpaca_snapshot_bid"

    pnl_pct: float | None
    if entry > 0 and current > 0:
        pnl_pct = (current - entry) / entry * 100.0
    else:
        pnl_pct = None

    if _position_raw_present(position.get("unrealized_pl")):
        unrealized: float | None = _safe_float(position.get("unrealized_pl"))
    elif entry > 0:
        unrealized = (current - entry) * qty * 100
    else:
        unrealized = None

    snap_last = _safe_float(snap.get("last")) if snap.get("ok") else 0.0
    snap_bid = _safe_float(snap.get("bid")) if snap.get("ok") else 0.0
    data_conflict = bool(
        snap.get("ok")
        and snap_last > 0
        and price_source.startswith("alpaca_position")
        and abs(snap_last - current) >= max(0.02, entry * 0.5)
    )

    return {
        "qty": qty,
        "entry": entry,
        "current": current,
        "pnl_pct": pnl_pct,
        "unrealized": unrealized,
        "price_source": price_source,
        "position_unrealized_plpc": _safe_float(position.get("unrealized_plpc")) if _position_raw_present(position.get("unrealized_plpc")) else None,
        "position_unrealized_pl": _safe_float(position.get("unrealized_pl")) if _position_raw_present(position.get("unrealized_pl")) else None,
        "snapshot_bid": snap_bid,
        "snapshot_mid": _safe_float(snap.get("mid")) if snap.get("ok") else 0.0,
        "snapshot_last": snap_last,
        "snapshot_mark": _safe_float(snap.get("mark")) if snap.get("ok") else 0.0,
        "data_conflict": data_conflict,
        "price_basis": PRICE_BASIS,
        "broker_reported_pnl_pct": _safe_float(position.get("unrealized_plpc")) * 100.0 if _position_raw_present(position.get("unrealized_plpc")) else None,
    }


async def daily_premium_used() -> float:
    today = datetime.now(timezone.utc).date()
    live = await orders(status="all", limit=500)
    total = 0.0
    for order in live.get("orders") or []:
        if str(order.get("side") or "").lower() != "buy":
            continue
        if str(order.get("status") or "").lower() in {"canceled", "expired", "rejected"}:
            continue
        submitted = order.get("submitted_at") or order.get("created_at")
        try:
            submitted_date = datetime.fromisoformat(str(submitted).replace("Z", "+00:00")).date()
        except Exception:
            continue
        if submitted_date == today:
            total += _order_premium_usd(order)
    return round(total, 2)


def _signals(row: dict[str, Any]) -> list[str]:
    sigs = row.get("signals") or []
    if isinstance(sigs, dict):
        return sorted(str(k) for k, v in sigs.items() if v)
    return sorted(str(s) for s in sigs)


def _summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(rows),
        "equity": sum(1 for x in rows if x.get("route") == "EQUITY"),
        "option": sum(1 for x in rows if x.get("route") == "OPTION"),
        "both": sum(1 for x in rows if x.get("route") == "BOTH"),
        "pass": sum(1 for x in rows if x.get("route") == "PASS"),
        "ready": sum(1 for x in rows if x.get("manual_fire_ready")),
    }


def _options_data_policy(alpaca_refreshes_used: int | None = None) -> dict[str, Any]:
    return {
        "alpaca_refresh_limit": OPTIONS_ALPACA_REFRESH_LIMIT,
        "alpaca_refreshes_used": alpaca_refreshes_used,
        "refresh_order": "PM score descending",
        "skips_equity_only": True,
        "options_execution_enabled": OPTIONS_EXECUTION_ENABLED,
        "allow_indicative_execution": OPTIONS_ALLOW_INDICATIVE_EXECUTION,
        "execution_grade_required": not OPTIONS_ALLOW_INDICATIVE_EXECUTION,
        "max_quote_age_seconds": OPTIONS_MAX_QUOTE_AGE_SECONDS,
        "daily_premium_cap_usd": OPTIONS_DAILY_PREMIUM_CAP_USD,
    }


def _add_block(blocked: list[str], reason: str) -> None:
    if reason not in blocked:
        blocked.append(reason)


def _legacy_strategy_lane(row: dict[str, Any], instrument: dict[str, Any]) -> dict[str, Any]:
    route = str(row.get("route") or "").upper()
    strategy = str(row.get("strategy") or "").upper()
    iv_rank = _safe_float(row.get("iv_rank"), 50.0)
    dte = _safe_int(instrument.get("dte") or instrument.get("days_to_expiration"))
    if route not in {"OPTION", "BOTH"}:
        lane = "NO_OPTION_TRADE"
        posture = "NONE"
        preferred = "stock_or_pass"
    elif dte >= 180:
        lane = "LEAPS_CORE"
        posture = "LONG_DURATION_DEFINED_PREMIUM"
        preferred = "long_call_leap_or_diagonal_overlay"
    elif "EARNING" in strategy or "EVENT" in strategy:
        lane = "EVENT_DEFINED_RISK"
        posture = "BINARY_EVENT"
        preferred = "debit_spread_or_small_single_leg"
    elif iv_rank >= 70:
        lane = "HIGH_IV_SPREAD_OR_PASS"
        posture = "VOL_EXPENSIVE"
        preferred = "debit_spread"
    elif "PUT" in strategy or str(row.get("direction") or "").upper() == "BEARISH":
        lane = "BEARISH_PUT_MOMENTUM"
        posture = "DOWNSIDE_MOMENTUM"
        preferred = "long_put_or_debit_put_spread"
    else:
        lane = "TACTICAL_MOMENTUM_CALL"
        posture = "SHORT_TO_MEDIUM_CONVEXITY"
        preferred = "long_call"
    return {
        "lane": lane,
        "risk_posture": posture,
        "preferred_structure": preferred,
        "iv_rank": iv_rank,
        "dte": dte or None,
        "reasons": ["Lane inferred from cached candidate fields; refresh candidates for full lane evidence."],
    }


def _normalize_candidate_execution_state(ticket: dict[str, Any]) -> dict[str, Any]:
    """Re-apply current execution policy to cached candidate rows.

    Cached rows can outlive risk-policy and data-quality changes. The Options
    Desk should never show or auto-use a stale ready state.
    """
    row = {**(ticket or {})}
    instrument = row.get("instrument") or {}
    route = row.get("route")
    blocked = [str(x) for x in (row.get("blocked_reasons") or []) if x]
    data_provider = row.get("data_provider") or instrument.get("data_provider")
    data_quality = row.get("data_quality") or instrument.get("data_quality")
    risk_budget = _safe_float(row.get("risk_budget"))
    contract_risk = _contract_risk(instrument)
    contracts = _safe_int(row.get("contracts"))
    if contracts <= 0 and contract_risk > 0 and risk_budget > 0:
        contracts = int(risk_budget // contract_risk)

    quality_state = (
        "EXECUTION_GRADE"
        if data_provider == "ALPACA_OPTIONS" and _execution_grade_allowed(data_quality)
        else "RESEARCH_ONLY"
    )

    if route in {"OPTION", "BOTH"}:
        if not OPTIONS_EXECUTION_ENABLED:
            _add_block(blocked, "options execution is disabled")
        if not paper_only():
            _add_block(blocked, "options desk is not pointed at Alpaca paper")
        if data_provider != "ALPACA_OPTIONS":
            _add_block(blocked, "missing Alpaca execution-grade options data")
        elif not _execution_grade_allowed(data_quality):
            _add_block(blocked, f"{data_quality or 'unknown'} options data is not execution grade")
        if contract_risk <= 0:
            _add_block(blocked, "missing option max loss")
        if contract_risk > risk_budget:
            _add_block(blocked, "contract risk exceeds PM budget")
        if instrument.get("kind") == "spread":
            _add_block(blocked, "multi-leg spread execution is not enabled yet")
        if instrument.get("kind") == "single_leg":
            if _spread_is_too_wide(instrument):
                _add_block(blocked, "spread too wide")
            if _indicative_execution_too_thin(instrument):
                _add_block(blocked, "indicative option market too thin")
            if _open_interest_is_too_low(instrument):
                _add_block(blocked, "open interest too low")
            if _provider_delta_missing(instrument):
                _add_block(blocked, "no provider-reported delta - execution requires real greeks")
            elif _delta_out_of_band(instrument, row.get("strategy")):
                _add_block(blocked, "delta out of execution band")
    elif route == "PASS":
        _add_block(blocked, "PM route is PASS")
    else:
        _add_block(blocked, "PM route is EQUITY")

    row["quality_state"] = quality_state
    if not isinstance(row.get("strategy_lane"), dict) or not row.get("strategy_lane", {}).get("lane"):
        row["strategy_lane"] = _legacy_strategy_lane(row, instrument)
    row["contracts"] = contracts
    row["blocked_reasons"] = blocked
    row["manual_fire_ready"] = route in {"OPTION", "BOTH"} and not blocked and contracts > 0
    return row


def _route(pm_row: dict[str, Any], scan_row: dict[str, Any]) -> tuple[str, list[str]]:
    opts = scan_row.get("options") or {}
    contract = opts.get("contract") or {}
    spread = opts.get("spread") or {}
    reasons: list[str] = []
    action = pm_row.get("action")
    if action not in {"ACCUMULATE", "STARTER", "WATCH"}:
        return "PASS", ["PM did not approve active sizing."]
    if opts.get("strategy") == "AVOID_OPTIONS" or pm_row.get("option_view") == "STOCK_ONLY":
        return "EQUITY", ["Options engine says avoid options or hold stock instead."]
    if pm_row.get("option_view") == "STOCK_PREFERRED":
        return "EQUITY", ["PM prefers equity expression over options for this setup."]
    iv_rank = float(opts.get("iv_rank") or 50)
    rr = float(pm_row.get("risk_reward") or 0)
    score = float(pm_row.get("pm_score") or 0)
    option_ok = bool(contract or spread)
    if not option_ok:
        return "EQUITY", ["No usable option contract or spread candidate."]
    if pm_row.get("option_view") == "SPREAD_ONLY" and not spread:
        return "EQUITY", ["PM requires a defined-risk spread, but no spread candidate was built."]
    if score >= 78 and rr >= 2.2 and iv_rank < 65:
        return "BOTH", ["High score and clean option conditions allow both desks."]
    if score >= 64 and rr >= 1.5:
        reasons.append("PM approves options as best expression for this setup.")
        return "OPTION", reasons
    if action == "WATCH" and score >= 52 and rr >= 1.15:
        reasons.append("Paper scout lane: small defined-risk option allowed for PM watchlist learning.")
        return "OPTION", reasons
    return "EQUITY", ["Equity expression preferred under current PM thresholds."]


def _pm_can_consider_options(pm_row: dict[str, Any], scan_row: dict[str, Any]) -> bool:
    opts = scan_row.get("options") or {}
    action = pm_row.get("action")
    if action not in {"ACCUMULATE", "STARTER", "WATCH"}:
        return False
    if opts.get("strategy") == "AVOID_OPTIONS" or pm_row.get("option_view") == "STOCK_ONLY":
        return False
    if pm_row.get("option_view") == "STOCK_PREFERRED":
        return False
    if pm_row.get("option_view") == "SPREAD_ONLY" and not (opts.get("spread") or {}):
        return False
    rr = float(pm_row.get("risk_reward") or 0)
    score = float(pm_row.get("pm_score") or 0)
    if score >= 64 and rr >= 1.5:
        return True
    return action == "WATCH" and score >= 52 and rr >= 1.15


def _risk_budget(route: str, action: str, score: float) -> float:
    if route not in {"OPTION", "BOTH"}:
        return 0.0
    grind_lane_equity_pct = 0.85
    grind_risk_pct_flat = 0.015
    cap = min(OPTIONS_EQUITY * MAX_RISK_PCT, MAX_RISK_USD)
    flat_budget = OPTIONS_EQUITY * grind_lane_equity_pct * grind_risk_pct_flat
    return round(min(flat_budget, cap), 2)


async def _auto_orders_submitted_today() -> int:
    db = get_db()
    now_et = datetime.now(ET)
    start_et = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = start_et.astimezone(timezone.utc).isoformat()
    return await db.options_desk_orders.count_documents({
        "auto": True,
        "auto_submitted_at": {"$gte": start_utc},
        "status": {"$in": ["auto_submitted", "submitted", "filled"]},
    })


def _days_held(entry_time: Any) -> float:
    try:
        if not entry_time:
            return 0.0
        dt = datetime.fromisoformat(str(entry_time).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 86400.0)
    except Exception:
        return 0.0


async def _days_to_next_earnings(ticker: str) -> int | None:
    try:
        db = get_db()
        today = datetime.now(timezone.utc).date()
        rows = []
        snap = await db.earnings_snapshots.find_one({}, {"_id": 0}, sort=[("created_at", -1)])
        if snap:
            for bucket in (snap.get("by_day") or {}).values():
                if isinstance(bucket, list):
                    rows.extend(bucket)
            rows.extend(snap.get("rows") or [])
        for row in rows:
            if str(row.get("ticker") or "").upper() != str(ticker or "").upper():
                continue
            ds = row.get("earnings_date")
            if not ds:
                continue
            d = datetime.fromisoformat(str(ds)).date()
            delta = (d - today).days
            if delta >= 0:
                return delta
    except Exception:
        return None
    return None


def _strategy_lane(pm_row: dict[str, Any], scan_row: dict[str, Any], opts: dict[str, Any], instrument: dict[str, Any], route: str) -> dict[str, Any]:
    signals = {str(s).upper() for s in _signals(scan_row)}
    strategy = str(opts.get("strategy") or "").upper()
    action = str(pm_row.get("action") or "").upper()
    iv_rank = _safe_float(opts.get("iv_rank"), 50.0)
    dte = _safe_int(instrument.get("dte") or instrument.get("days_to_expiration"))
    kind = instrument.get("kind") or "unknown"
    reasons: list[str] = []

    if route not in {"OPTION", "BOTH"}:
        return {
            "lane": "NO_OPTION_TRADE",
            "risk_posture": "NONE",
            "preferred_structure": "stock_or_pass",
            "reasons": ["PM route does not require an options contract."],
        }
    if dte >= 180 or action == "ACCUMULATE" and route == "BOTH":
        reasons.append("Longer-duration PM-approved setup can support a LEAPS sleeve review.")
        lane = "LEAPS_CORE"
        posture = "LONG_DURATION_DEFINED_PREMIUM"
        preferred = "long_call_leap_or_diagonal_overlay"
    elif "UPCOMING_EARNINGS" in signals or strategy in {"EARNINGS_CALL", "EVENT_CALL", "EVENT_PUT"}:
        reasons.append("Event/binary catalyst requires defined premium risk and smaller sizing.")
        lane = "EVENT_DEFINED_RISK"
        posture = "BINARY_EVENT"
        preferred = "debit_spread_or_small_single_leg"
    elif iv_rank >= 70:
        reasons.append("High IV makes outright premium expensive; prefer spread or pass.")
        lane = "HIGH_IV_SPREAD_OR_PASS"
        posture = "VOL_EXPENSIVE"
        preferred = "debit_spread"
    elif strategy in {"BEAR_PUT", "PUT", "DEBIT_PUT_SPREAD"} or str(opts.get("direction") or "").upper() == "BEARISH":
        reasons.append("Bearish PM/options setup routes to put-defined premium.")
        lane = "BEARISH_PUT_MOMENTUM"
        posture = "DOWNSIDE_MOMENTUM"
        preferred = "long_put_or_debit_put_spread"
    elif "UNUSUAL_FLOW" in signals or "CALL_SWEEP" in signals or "MOMENTUM_STACK" in signals:
        reasons.append("Flow or momentum stack favors tactical convexity.")
        lane = "TACTICAL_MOMENTUM_CALL"
        posture = "SHORT_TO_MEDIUM_CONVEXITY"
        preferred = "long_call"
    else:
        reasons.append("General PM-approved option candidate; keep risk capped until edge is proven.")
        lane = "GENERAL_DEFINED_PREMIUM"
        posture = "STANDARD_OPTIONS_RISK"
        preferred = "long_call_or_debit_spread"

    if kind == "spread":
        preferred = "debit_spread"
    return {
        "lane": lane,
        "risk_posture": posture,
        "preferred_structure": preferred,
        "iv_rank": iv_rank,
        "dte": dte or None,
        "reasons": reasons,
    }


def _spread_is_too_wide(instrument: dict[str, Any]) -> bool:
    bid = float(instrument.get("bid") or 0)
    ask = float(instrument.get("ask") or instrument.get("premium") or 0)
    if bid <= 0 or ask <= 0:
        return True
    if ask < MIN_OPTION_PREMIUM:
        return True
    spread = float(instrument.get("spread") or 0)
    if spread <= 0 and ask > bid:
        spread = ask - bid
    premium = ask
    spread_pct = spread / premium if premium > 0 else 1.0
    return spread > MAX_SPREAD_ABS or spread_pct > MAX_SPREAD_PCT


def _indicative_execution_too_thin(instrument: dict[str, Any]) -> bool:
    if str(instrument.get("data_quality") or "").upper() != "INDICATIVE":
        return False
    bid = _safe_float(instrument.get("bid"))
    ask = _safe_float(instrument.get("ask") or instrument.get("premium"))
    spread = _safe_float(instrument.get("spread"))
    volume = _safe_int(instrument.get("volume"))
    if bid <= 0 or ask < MIN_OPTION_PREMIUM:
        return True
    spread_pct = spread / ask if ask > 0 else 1.0
    return spread_pct > MAX_INDICATIVE_SPREAD_PCT or volume < MIN_INDICATIVE_OPTION_VOLUME


def _open_interest_is_too_low(instrument: dict[str, Any]) -> bool:
    if instrument.get("open_interest_source") == "unavailable" and instrument.get("data_provider") == "ALPACA_OPTIONS":
        return int(instrument.get("volume") or 0) < MIN_OPTION_VOLUME_IF_OI_UNKNOWN
    oi = int(instrument.get("open_interest") or 0)
    volume = int(instrument.get("volume") or 0)
    return oi < MIN_OPEN_INTEREST and volume < MIN_VOLUME_WHEN_LOW_OI


def _delta_out_of_band(instrument: dict[str, Any], strategy: str | None) -> bool:
    delta = abs(float(instrument.get("delta") or 0))
    return delta < MIN_ABS_DELTA or delta > MAX_ABS_DELTA


def _provider_delta_missing(instrument: dict[str, Any]) -> bool:
    if instrument.get("provider_delta_present") is True:
        return False
    if instrument.get("provider_delta_present") is False:
        return True
    return _safe_float(instrument.get("delta")) <= 0


def _selected_instrument(opts: dict[str, Any]) -> dict[str, Any]:
    if opts.get("spread"):
        return {"kind": "spread", **opts["spread"]}
    contract = opts.get("contract") or {}
    return {"kind": "single_leg", **contract}


def _contract_risk(instrument: dict[str, Any]) -> float:
    max_loss = float(instrument.get("max_loss") or 0)
    if instrument.get("kind") == "single_leg":
        ask = float(instrument.get("ask") or 0)
        if ask > 0:
            return max(max_loss, ask * 100)
    return max_loss


async def build_candidates(limit: int = 25, persist: bool = True) -> dict[str, Any]:
    from . import portfolio_manager

    db = get_db()
    scan = await db.scan_results.find_one({}, {"_id": 0}, sort=[("finished_at", -1)])
    rows = (scan or {}).get("results") or []
    pm_rows = portfolio_manager.evaluate_rows(rows, equity=portfolio_manager.DEFAULT_EQUITY, mode="BALANCED")
    by_ticker = {r["ticker"]: r for r in pm_rows}
    rows = sorted(
        rows,
        key=lambda r: float((by_ticker.get(str(r.get("ticker") or "").upper()) or {}).get("pm_score") or 0),
        reverse=True,
    )
    out: list[dict[str, Any]] = []
    alpaca_refreshes = 0
    throttle_doc = await db.options_lane_throttle.find_one({"_id": "latest"}, {"_id": 0}) or {}
    lane_throttles = throttle_doc.get("throttles") or {}
    for row in rows:
        ticker = str(row.get("ticker") or "").upper()
        pm_row = by_ticker.get(ticker)
        opts = row.get("options") or {}
        if not ticker or not pm_row or not opts:
            continue
        attempted_live_refresh = False
        try:
            if _pm_can_consider_options(pm_row, row) and alpaca_refreshes < OPTIONS_ALPACA_REFRESH_LIMIT:
                from . import options_engine
                attempted_live_refresh = True
                refreshed = await options_engine.analyze_ticker(row)
                if refreshed:
                    opts = refreshed
                    row = {**row, "options": opts}
                alpaca_refreshes += 1
        except Exception:
            pass
        route, route_reasons = _route(pm_row, row)
        instrument = _selected_instrument(opts)
        score = float(pm_row.get("pm_score") or 0)
        strategy_lane = _strategy_lane(pm_row, row, opts, instrument, route)
        risk_budget = _risk_budget(route, pm_row.get("action"), score)
        throttle = lane_throttles.get(str(strategy_lane.get("lane") or ""))
        throttle_multiplier = _safe_float((throttle or {}).get("multiplier"), 1.0)
        if throttle_multiplier < 1.0:
            risk_budget = risk_budget * max(0.0, throttle_multiplier)
        contract_risk = _contract_risk(instrument)
        entry_premium = float(instrument.get("ask") or instrument.get("premium") or instrument.get("net_debit") or 0)
        exit_policy = options_ratchet_state(entry_premium=entry_premium)
        contracts = int(risk_budget // contract_risk) if contract_risk > 0 and risk_budget > 0 else 0
        blocked = []
        data_provider = opts.get("data_provider") or instrument.get("data_provider")
        data_quality = opts.get("data_quality") or instrument.get("data_quality")
        quality_state = "EXECUTION_GRADE" if data_provider == "ALPACA_OPTIONS" and _execution_grade_allowed(data_quality) else "RESEARCH_ONLY"
        if route in {"OPTION", "BOTH"}:
            if not OPTIONS_EXECUTION_ENABLED:
                blocked.append("options execution is disabled")
            if not paper_only():
                blocked.append("options desk is not pointed at Alpaca paper")
            if data_provider != "ALPACA_OPTIONS":
                blocked.append("missing Alpaca execution-grade options data")
            elif not _execution_grade_allowed(data_quality):
                blocked.append(f"{data_quality or 'unknown'} options data is not execution grade")
            if contract_risk <= 0:
                blocked.append("missing option max loss")
            if contract_risk > risk_budget:
                blocked.append("contract risk exceeds PM budget")
            if instrument.get("kind") == "spread":
                blocked.append("multi-leg spread execution is not enabled yet")
            if instrument.get("kind") == "single_leg":
                if _spread_is_too_wide(instrument):
                    blocked.append("spread too wide")
                if _indicative_execution_too_thin(instrument):
                    blocked.append("indicative option market too thin")
                if _open_interest_is_too_low(instrument):
                    blocked.append("open interest too low")
                if _provider_delta_missing(instrument):
                    blocked.append("no provider-reported delta - execution requires real greeks")
                elif _delta_out_of_band(instrument, opts.get("strategy")):
                    blocked.append("delta out of execution band")
        elif route == "PASS":
            blocked.append("PM route is PASS")
        else:
            blocked.append("PM route is EQUITY")
        ticket = {
            "candidate_id": f"opt-{ticker}-{str((scan or {}).get('finished_at') or _now())[:19]}",
            "ticker": ticker,
            "route": route,
            "pm_action": pm_row.get("action"),
            "pm_score": pm_row.get("pm_score"),
            "risk_reward": pm_row.get("risk_reward"),
            "signals": _signals(row),
            "strategy": opts.get("strategy"),
            "strategy_lane": strategy_lane,
            "direction": opts.get("direction"),
            "strategy_reason": opts.get("strategy_reason") or opts.get("one_liner"),
            "iv_rank": opts.get("iv_rank"),
            "iv_label": opts.get("iv_label"),
            "data_provider": opts.get("data_provider") or instrument.get("data_provider"),
            "data_feed": opts.get("data_feed") or instrument.get("data_feed"),
            "data_quality": opts.get("data_quality") or instrument.get("data_quality"),
            "crush_risk": opts.get("crush_risk"),
            "instrument": instrument,
            "risk_budget": round(risk_budget, 2),
            "risk_policy": {
                "mode": "grind_flat_until_expectancy_proven",
                "max_risk_pct": MAX_RISK_PCT,
                "max_risk_usd": MAX_RISK_USD,
                "flat_grind_budget_usd": _risk_budget("OPTION", pm_row.get("action"), score),
                "lane_throttle_multiplier": round(throttle_multiplier, 2),
                "lane_throttle_sample_size": ((throttle or {}).get("stats") or {}).get("sample_size"),
                "lane_throttle_expectancy_pct": ((throttle or {}).get("stats") or {}).get("expectancy_pct"),
                "daily_premium_cap_usd": OPTIONS_DAILY_PREMIUM_CAP_USD,
                "auto_max_orders_per_scan": AUTO_MAX_ORDERS_PER_SCAN,
                "auto_max_orders_per_day": AUTO_MAX_ORDERS_PER_DAY,
                "min_open_interest": MIN_OPEN_INTEREST,
                "min_volume_when_low_oi": MIN_VOLUME_WHEN_LOW_OI,
                "max_spread_abs": MAX_SPREAD_ABS,
                "max_spread_pct": MAX_SPREAD_PCT,
                "min_abs_delta": MIN_ABS_DELTA,
                "max_abs_delta": MAX_ABS_DELTA,
                "min_option_premium": MIN_OPTION_PREMIUM,
                "price_basis": PRICE_BASIS,
                "alpaca_refresh_limit": OPTIONS_ALPACA_REFRESH_LIMIT,
                "initial_stop_pct": OPTIONS_INITIAL_STOP_PCT,
                "hard_stop_pct": OPTIONS_HARD_STOP_PCT,
                "take_profit_tier1_pct": TAKE_PROFIT_TIER1_PCT,
                "take_profit_tier1_sell_fraction": TAKE_PROFIT_TIER1_SELL_FRACTION,
                "time_stop_dte_fraction": TIME_STOP_DTE_FRACTION,
                "theta_stop_pct_of_premium": THETA_STOP_PCT_OF_PREMIUM,
                "ratchet_tiers": [{"trigger_gain_pct": t, "locked_gain_pct": l} for t, l in OPTIONS_RATCHET_TIERS],
            },
            "exit_policy": exit_policy,
            "quality_state": quality_state,
            "options_live_refresh_attempted": attempted_live_refresh,
            "contracts": contracts,
            "manual_fire_ready": route in {"OPTION", "BOTH"} and not blocked and contracts > 0,
            "blocked_reasons": blocked,
            "route_reasons": route_reasons,
            "scan_finished_at": (scan or {}).get("finished_at"),
            "generated_at": _now(),
        }
        out.append(ticket)
    out.sort(key=lambda x: (x["manual_fire_ready"], x["route"] == "BOTH", x.get("pm_score") or 0), reverse=True)
    out = out[:limit]
    if persist:
        await db.options_desk_candidates.delete_many({})
        if out:
            await db.options_desk_candidates.insert_many([stamped(x) for x in out])
    return {
        "generated_at": _now(),
        "scan_finished_at": (scan or {}).get("finished_at"),
        "options_equity_basis": OPTIONS_EQUITY,
        "options_data_policy": _options_data_policy(alpaca_refreshes),
        "summary": _summary(out),
        "candidates": out,
    }


async def candidates() -> dict[str, Any]:
    db = get_db()
    rows = await db.options_desk_candidates.find({}, {"_id": 0}).sort("pm_score", -1).to_list(100)
    if not rows:
        return await build_candidates(persist=True)
    rows = [_normalize_candidate_execution_state(x) for x in rows]
    return {
        "generated_at": _now(),
        "options_equity_basis": OPTIONS_EQUITY,
        "options_data_policy": _options_data_policy(
            sum(1 for x in rows if x.get("options_live_refresh_attempted"))
        ),
        "summary": _summary(rows),
        "candidates": rows,
    }


async def _ibkr_validate_option_ticket(ticket: dict[str, Any]) -> dict[str, Any]:
    instrument = ticket.get("instrument") or {}
    symbol = str(instrument.get("symbol") or instrument.get("contractSymbol") or "").upper()
    params = _ibkr_params_from_occ_symbol(symbol)
    if not params:
        return {
            "ok": False,
            "symbol": symbol,
            "role": "read_only_validation",
            "reason": "unparseable_occ_symbol",
        }
    try:
        from . import ibkr_research

        contract = await asyncio.to_thread(ibkr_research.option_contract_info, **params)
        quote = await asyncio.to_thread(ibkr_research.option_quote, **params, delayed_allowed=True)
        return {
            "ok": bool(contract.get("ok") and (contract.get("contracts") or quote.get("ok"))),
            "role": "read_only_validation",
            "data_only": True,
            "allow_trading": False,
            "params": params,
            "contract_quality": contract.get("data_quality"),
            "contract_count": len(contract.get("contracts") or []),
            "first_contract": (contract.get("contracts") or [{}])[0].get("contract"),
            "quote_quality": quote.get("data_quality"),
            "quote": quote.get("quote"),
            "reason": quote.get("reason") if not quote.get("ok") else None,
            "permission_errors": quote.get("permission_errors"),
            "errors": (contract.get("errors") or [])[-3:] + (quote.get("errors") or [])[-3:],
        }
    except Exception as exc:
        return {
            "ok": False,
            "symbol": symbol,
            "role": "read_only_validation",
            "reason": exc.__class__.__name__,
            "detail": str(exc)[:240],
        }


async def alpaca_workflow(limit: int = 25, persist: bool = True, validate_ibkr: bool = True) -> dict[str, Any]:
    """Build the PM-to-Alpaca options workflow without submitting orders.

    IBKR is used only as a read-only validation/data layer. Alpaca remains the
    only broker named in the executable order preview and the only path used by
    execute()/auto_execute_latest().
    """
    candidate_set = await build_candidates(limit=max(1, min(int(limit or 25), 100)), persist=True)
    rows = candidate_set.get("candidates") or []
    workflows: list[dict[str, Any]] = []
    for ticket in rows:
        route = str(ticket.get("route") or "").upper()
        if route not in {"OPTION", "BOTH"}:
            continue
        instrument = ticket.get("instrument") or {}
        symbol = str(instrument.get("symbol") or instrument.get("contractSymbol") or "").upper()
        ibkr_validation = {"ok": None, "role": "disabled_for_this_request"}
        if validate_ibkr:
            ibkr_validation = await _ibkr_validate_option_ticket(ticket)
        preview = _alpaca_order_preview_from_ticket(ticket)
        alpaca_preflight = {
            "configured": configured(),
            "paper_only": paper_only(),
            "options_execution_enabled": OPTIONS_EXECUTION_ENABLED,
            "candidate_ready": bool(ticket.get("manual_fire_ready")),
            "blocked_reasons": ticket.get("blocked_reasons") or [],
            "data_provider": ticket.get("data_provider") or instrument.get("data_provider"),
            "data_quality": ticket.get("data_quality") or instrument.get("data_quality"),
            "risk_budget": ticket.get("risk_budget"),
            "contracts": ticket.get("contracts"),
            "premium_estimate": preview.get("payload", {}).get("limit_price"),
        }
        workflows.append({
            "ticker": ticket.get("ticker"),
            "candidate_id": ticket.get("candidate_id"),
            "route": route,
            "pm_action": ticket.get("pm_action"),
            "pm_score": ticket.get("pm_score"),
            "risk_reward": ticket.get("risk_reward"),
            "strategy": ticket.get("strategy"),
            "strategy_lane": ticket.get("strategy_lane"),
            "instrument": instrument,
            "ibkr_validation": ibkr_validation,
            "alpaca_preflight": alpaca_preflight,
            "alpaca_order_preview": preview,
            "ready_for_alpaca_execute": bool(ticket.get("manual_fire_ready")),
            "execution_method": "POST /api/options_desk/execute with candidate_id",
        })
    summary = {
        "scan_finished_at": candidate_set.get("scan_finished_at"),
        "workflow_count": len(workflows),
        "ready_for_alpaca": sum(1 for x in workflows if x.get("ready_for_alpaca_execute")),
        "ibkr_validated": sum(1 for x in workflows if (x.get("ibkr_validation") or {}).get("ok") is True),
        "ibkr_unavailable_or_partial": sum(1 for x in workflows if (x.get("ibkr_validation") or {}).get("ok") is not True),
        "alpaca_execution_authority": "ONLY",
        "ibkr_execution_authority": "NONE_DATA_ONLY",
    }
    payload = {
        "ok": True,
        "generated_at": _now(),
        "summary": summary,
        "candidate_summary": candidate_set.get("summary"),
        "policy": {
            "scanner_pm_source": "latest scan plus Portfolio Manager routing",
            "research_validation_source": "IBKR read-only option contract/model data when enabled",
            "execution_source": "Alpaca Options Desk only",
            "ibkr_orders_enabled": False,
            "alpaca_orders_submitted_by_this_endpoint": False,
        },
        "workflows": workflows,
    }
    if persist:
        db = get_db()
        await db.options_desk_alpaca_workflows.insert_one(stamped(payload))
    return payload


async def _fresh_execution_preflight(ticket: dict[str, Any]) -> dict[str, Any]:
    instrument = ticket.get("instrument") or {}
    symbol = instrument.get("symbol") or instrument.get("contractSymbol")
    if not symbol:
        return {"ok": False, "reason": "missing_option_symbol_from_data_provider"}
    if instrument.get("kind") != "single_leg":
        return {"ok": False, "reason": "multi_leg_execution_not_enabled_v1"}
    snap = await _option_snapshot(str(symbol).upper())
    if not snap.get("ok"):
        return {"ok": False, "reason": "fresh_option_snapshot_failed", "snapshot": snap}
    quote_age = _quote_age_seconds(snap)
    if quote_age is None:
        return {"ok": False, "reason": "fresh_quote_timestamp_missing", "snapshot": snap}
    if quote_age > OPTIONS_MAX_QUOTE_AGE_SECONDS:
        return {"ok": False, "reason": "fresh_quote_stale", "quote_age_seconds": quote_age, "snapshot": snap}
    if not _execution_grade_allowed(snap.get("data_quality")):
        return {"ok": False, "reason": "fresh_quote_not_execution_grade", "snapshot": snap}

    fresh = {**instrument}
    fresh.update({
        "symbol": str(symbol).upper(),
        "bid": snap.get("bid"),
        "ask": snap.get("ask"),
        "premium": snap.get("ask") or snap.get("mid") or snap.get("last"),
        "spread": round(_safe_float(snap.get("ask")) - _safe_float(snap.get("bid")), 2),
        "delta": snap.get("delta") or instrument.get("delta"),
        "provider_delta_present": bool(_safe_float(snap.get("delta"))),
        "volume": snap.get("volume") or instrument.get("volume"),
        "open_interest": snap.get("open_interest") or instrument.get("open_interest"),
        "open_interest_source": snap.get("open_interest_source") or instrument.get("open_interest_source"),
        "data_provider": "ALPACA_OPTIONS",
        "data_feed": snap.get("data_feed"),
        "data_quality": snap.get("data_quality"),
        "quote_age_seconds": quote_age,
    })
    if _spread_is_too_wide(fresh):
        return {"ok": False, "reason": "fresh_spread_too_wide", "instrument": fresh, "snapshot": snap}
    if _indicative_execution_too_thin(fresh):
        return {"ok": False, "reason": "fresh_indicative_market_too_thin", "instrument": fresh, "snapshot": snap}
    if _open_interest_is_too_low(fresh):
        return {"ok": False, "reason": "fresh_open_interest_or_volume_too_low", "instrument": fresh, "snapshot": snap}
    if _provider_delta_missing(fresh):
        return {"ok": False, "reason": "fresh_provider_delta_missing", "instrument": fresh, "snapshot": snap}
    if _delta_out_of_band(fresh, ticket.get("strategy")):
        return {"ok": False, "reason": "fresh_delta_out_of_band", "instrument": fresh, "snapshot": snap}
    return {"ok": True, "symbol": str(symbol).upper(), "instrument": fresh, "snapshot": snap, "quote_age_seconds": quote_age}


async def _symbol_already_exposed(symbol: str) -> dict[str, Any]:
    symbol = str(symbol or "").upper()
    if not symbol:
        return {"blocked": False}
    live_positions = await positions()
    for position in live_positions.get("positions") or []:
        if str(position.get("symbol") or "").upper() == symbol and _safe_int(position.get("qty")) > 0:
            return {"blocked": True, "reason": "contract_already_open_in_alpaca", "position": position}
    live_orders = await orders(status="open", limit=200)
    for order in live_orders.get("orders") or []:
        if (
            str(order.get("symbol") or "").upper() == symbol
            and str(order.get("side") or "").lower() == "buy"
            and str(order.get("status") or "").lower() not in {"canceled", "expired", "rejected", "filled"}
        ):
            return {"blocked": True, "reason": "contract_has_open_buy_order", "order": order}
    db = get_db()
    local = await db.options_desk_trades.find_one(
        {"symbol": symbol, "status": {"$in": sorted(OPTION_ACTIVE_STATUSES)}},
        {"_id": 0},
    )
    if local:
        return {"blocked": True, "reason": "contract_already_active_in_local_ledger", "trade": local}
    return {"blocked": False}


async def execute(candidate_id: str, qty: int | None = None, limit_price: float | None = None) -> dict[str, Any]:
    from . import execution_gate, safety

    db = get_db()
    enabled, safety_status = await safety.trading_enabled(scope="options")
    if not enabled:
        return {"ok": False, "reason": "safety_halt", "safety": safety_status}
    ticket = await db.options_desk_candidates.find_one({"candidate_id": candidate_id}, {"_id": 0})
    if not ticket:
        return {"ok": False, "reason": "candidate_not_found"}
    gate = await execution_gate.check(scope="options", ticker=ticket.get("ticker"), record=True)
    if not gate.get("ok"):
        return {"ok": False, "reason": "execution_gate_blocked", "gate": gate, "candidate": ticket}
    route = options_account_route_guard()
    if not route.get("ok"):
        return {"ok": False, "reason": route.get("reason") or "options_account_route_blocked", "route": route, "candidate": ticket}
    if not OPTIONS_EXECUTION_ENABLED:
        return {"ok": False, "reason": "options_execution_disabled", "candidate": ticket}
    if not paper_only():
        return {"ok": False, "reason": "refusing_non_paper_options_account", "candidate": ticket}
    market_status = await _options_market_status()
    if not market_status.get("is_open"):
        return {"ok": False, "reason": "options_market_closed", "market_status": market_status, "candidate": ticket}
    if not ticket.get("manual_fire_ready"):
        return {"ok": False, "reason": "candidate_not_manual_fire_ready", "blocked": ticket.get("blocked_reasons"), "candidate": ticket}
    instrument = ticket.get("instrument") or {}
    if (ticket.get("data_provider") or instrument.get("data_provider")) != "ALPACA_OPTIONS":
        return {"ok": False, "reason": "missing_alpaca_execution_grade_options_data", "candidate": ticket}
    if not _execution_grade_allowed(ticket.get("data_quality") or instrument.get("data_quality")):
        return {"ok": False, "reason": "candidate_options_data_not_execution_grade", "candidate": ticket}
    if instrument.get("kind") != "single_leg":
        return {"ok": False, "reason": "multi_leg_execution_not_enabled_v1", "candidate": ticket}
    preflight = await _fresh_execution_preflight(ticket)
    if not preflight.get("ok"):
        return {"ok": False, **preflight, "candidate": ticket}
    instrument = preflight["instrument"]
    ask = float(instrument.get("ask") or instrument.get("premium") or 0)
    order_qty = int(qty or ticket.get("contracts") or 0)
    order_limit = float(limit_price or ask)
    if order_qty <= 0 or order_limit <= 0:
        return {"ok": False, "reason": "invalid_qty_or_limit", "candidate": ticket}
    order_premium = order_qty * order_limit * 100
    if order_premium > float(ticket.get("risk_budget") or 0) + 0.01:
        return {"ok": False, "reason": "order_exceeds_risk_budget", "candidate": ticket}
    premium_used = await daily_premium_used()
    if premium_used + order_premium > OPTIONS_DAILY_PREMIUM_CAP_USD + 0.01:
        return {
            "ok": False,
            "reason": "options_daily_premium_cap_exceeded",
            "daily_premium_used": premium_used,
            "daily_premium_cap": OPTIONS_DAILY_PREMIUM_CAP_USD,
            "order_premium": round(order_premium, 2),
            "candidate": ticket,
        }
    symbol = preflight.get("symbol")
    if not symbol:
        return {"ok": False, "reason": "missing_option_symbol_from_data_provider", "candidate": ticket}
    exposure = await _symbol_already_exposed(symbol)
    if exposure.get("blocked"):
        return {"ok": False, **exposure, "symbol": symbol, "candidate": ticket}
    client_order_id = _candidate_order_id(candidate_id)
    existing_local = await db.options_desk_orders.find_one(
        {
            "$or": [
                {"order.client_order_id": client_order_id},
                {"client_order_id": client_order_id},
            ],
            "status": {"$in": ["submitted", "auto_submitted", "filled"]},
        },
        {"_id": 0},
    )
    if existing_local:
        return {
            "ok": False,
            "reason": "duplicate_client_order_id",
            "client_order_id": client_order_id,
            "existing_order": existing_local.get("order"),
            "candidate": ticket,
        }
    live_orders = await orders(status="all", limit=200)
    for existing in live_orders.get("orders") or []:
        if existing.get("client_order_id") == client_order_id:
            return {
                "ok": False,
                "reason": "duplicate_client_order_id",
                "client_order_id": client_order_id,
                "existing_order": existing,
                "candidate": ticket,
            }
    payload = {
        "symbol": symbol,
        "qty": str(order_qty),
        "side": "buy",
        "type": "limit",
        "time_in_force": "day",
        "limit_price": round(order_limit, 2),
        "client_order_id": client_order_id,
    }
    async with httpx.AsyncClient(timeout=15.0, headers=_options_headers()) as client:
        r = await client.post(f"{_options_trade_base()}/v2/orders", json=payload)
    if r.status_code not in (200, 201):
        return {"ok": False, "reason": f"alpaca_rejected_{r.status_code}", "detail": r.text[:220], "candidate": ticket}
    order = r.json()
    pricing_truth = _spread_cost_context(_safe_float(order.get("filled_avg_price")) or order_limit, preflight.get("snapshot"))
    exit_policy = options_ratchet_state(entry_premium=order_limit, peak_premium=order_limit)
    record = stamped({
        "candidate": ticket,
        "order": order,
        "client_order_id": client_order_id,
        "submitted_at": _now(),
        "status": "submitted",
        "account_route": route,
        "exit_policy": exit_policy,
        "fresh_preflight": preflight,
        "pricing_truth": pricing_truth,
    })
    await db.options_desk_orders.insert_one(record)
    return {"ok": True, "order": order, "candidate": ticket, "exit_policy": exit_policy}


async def _acquire_auto_execute_lock(ttl_seconds: int = 240) -> dict[str, Any]:
    db = get_db()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    lock_until = (now + timedelta(seconds=ttl_seconds)).isoformat()
    lock_id = "options_auto_execute_lock"
    existing = await db.bot_state.find_one({"_id": lock_id}, {"_id": 0})
    if existing and str(existing.get("locked_until") or "") > now_iso:
        return {"ok": False, "reason": "options_auto_execute_already_running", "lock": existing}
    await db.bot_state.update_one(
        {"_id": lock_id},
        {"$set": {"locked_at": now_iso, "locked_until": lock_until, "owner": "options_desk.auto_execute_latest"}},
        upsert=True,
    )
    return {"ok": True, "lock_id": lock_id, "locked_until": lock_until}


async def _release_auto_execute_lock() -> None:
    try:
        await get_db().bot_state.update_one(
            {"_id": "options_auto_execute_lock"},
            {"$set": {"locked_until": datetime.now(timezone.utc).isoformat(), "released_at": _now()}},
        )
    except Exception:
        pass


async def auto_execute_latest(limit: int | None = None) -> dict[str, Any]:
    """PM-controlled automated options execution.

    The Options Desk does not re-decide the trade. It submits PM-ready tickets
    and enforces only mechanical paper execution constraints already present in
    execute(): risk budget, valid order fields, and Alpaca acceptance.
    """
    from . import execution_gate, safety

    lock = await _acquire_auto_execute_lock()
    if not lock.get("ok"):
        return {
            "ok": False,
            "auto": True,
            "reason": lock.get("reason"),
            "lock": lock.get("lock"),
            "submitted": [],
            "skipped": [],
            "summary": {},
        }
    try:
        return await _auto_execute_latest_locked(limit=limit)
    finally:
        await _release_auto_execute_lock()


async def _auto_execute_latest_locked(limit: int | None = None) -> dict[str, Any]:
    from . import execution_gate, safety

    enabled, safety_status = await safety.trading_enabled(scope="options_auto")
    if not enabled:
        return {
            "ok": False,
            "auto": True,
            "reason": "safety_halt",
            "safety": safety_status,
            "submitted": [],
            "skipped": [],
            "summary": {},
        }
    if not OPTIONS_EXECUTION_ENABLED:
        return {
            "ok": False,
            "auto": True,
            "reason": "options_execution_disabled",
            "submitted": [],
            "skipped": [],
            "summary": {},
        }
    gate_root = await execution_gate.check(scope="options", record=True)
    if not gate_root.get("ok"):
        return {
            "ok": False,
            "auto": True,
            "reason": "execution_gate_blocked",
            "execution_gate": gate_root,
            "submitted": [],
            "skipped": [],
            "summary": {},
        }
    db = get_db()
    rows = await db.options_desk_candidates.find({}, {"_id": 0}).sort("pm_score", -1).to_list(100)
    if rows:
        rows = [_normalize_candidate_execution_state(x) for x in rows]
        candidate_set = {
            "generated_at": _now(),
            "options_equity_basis": OPTIONS_EQUITY,
            "options_data_policy": _options_data_policy(
                sum(1 for x in rows if x.get("options_live_refresh_attempted"))
            ),
            "summary": _summary(rows),
            "candidates": rows,
        }
    else:
        candidate_set = await build_candidates(limit=100, persist=True)
    ready = [c for c in candidate_set.get("candidates", []) if c.get("manual_fire_ready")]
    max_orders = int(limit or AUTO_MAX_ORDERS_PER_SCAN)
    submitted_today = await _auto_orders_submitted_today()
    remaining_today = max(0, AUTO_MAX_ORDERS_PER_DAY - submitted_today)
    max_orders = min(max_orders, remaining_today)
    submitted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    if remaining_today <= 0:
        skipped.extend({"ticker": c.get("ticker"), "reason": "auto_daily_order_limit_reached"} for c in ready)
        ready = []
    for ticket in ready:
        if len(submitted) >= max_orders:
            skipped.append({"ticker": ticket.get("ticker"), "reason": "auto_order_limit_reached"})
            continue
        symbol = (ticket.get("instrument") or {}).get("symbol") or (ticket.get("instrument") or {}).get("contractSymbol")
        scan_finished_at = ticket.get("scan_finished_at")
        existing = await db.options_desk_orders.find_one({
            "candidate.scan_finished_at": scan_finished_at,
            "$or": [
                {"candidate.instrument.symbol": symbol},
                {"candidate.instrument.contractSymbol": symbol},
            ],
            "status": {"$in": ["submitted", "auto_submitted"]},
        }, {"_id": 0})
        if existing:
            skipped.append({"ticker": ticket.get("ticker"), "symbol": symbol, "reason": "duplicate_scan_contract"})
            continue
        result = await execute(ticket["candidate_id"])
        if result.get("ok"):
            await db.options_desk_orders.update_one(
                {"order.id": result.get("order", {}).get("id")},
                {"$set": {"status": "auto_submitted", "auto": True, "auto_submitted_at": _now()}},
            )
            submitted.append({
                "ticker": ticket.get("ticker"),
                "symbol": symbol,
                "route": ticket.get("route"),
                "contracts": ticket.get("contracts"),
                "risk_budget": ticket.get("risk_budget"),
                "order_id": result.get("order", {}).get("id"),
            })
        else:
            skipped.append({
                "ticker": ticket.get("ticker"),
                "symbol": symbol,
                "reason": result.get("reason"),
                "detail": result.get("detail"),
            })
    await log_activity(
        f"Options Desk auto-execute: {len(submitted)} submitted, {len(skipped)} skipped",
        "success" if submitted else "info",
        {"submitted": submitted, "skipped": skipped[:10]},
    )
    payload = {
        "ok": True,
        "auto": True,
        "ready": len(ready),
        "auto_orders_submitted_today_before_run": submitted_today,
        "auto_max_orders_per_day": AUTO_MAX_ORDERS_PER_DAY,
        "submitted": submitted,
        "skipped": skipped,
        "summary": candidate_set.get("summary", {}),
    }
    try:
        from . import telegram_events
        await telegram_events.dispatch_options_execution_report(payload)
    except Exception:
        pass
    return payload


async def refresh_and_auto_execute_latest(limit: int | None = None) -> dict[str, Any]:
    fill = await sync_fills()
    risk = await monitor_open_positions(enforce_hard_stop=True)
    await build_candidates(limit=100, persist=True)
    result = await auto_execute_latest(limit=limit)
    result["pre_execution_fill_sync"] = fill
    result["pre_execution_risk_check"] = risk
    return result


async def close(symbol: str, qty: int | None = None) -> dict[str, Any]:
    route = options_account_route_guard()
    if not route.get("ok"):
        return {"ok": False, "reason": route.get("reason") or "options_account_route_blocked", "route": route}
    market_status = await _options_market_status()
    if not market_status.get("is_open"):
        return {"ok": False, "reason": "options_market_closed", "market_status": market_status}
    payload: dict[str, Any] = {"symbol": symbol, "side": "sell", "type": "market", "time_in_force": "day"}
    if qty:
        payload["qty"] = str(int(qty))
    async with httpx.AsyncClient(timeout=15.0, headers=_options_headers()) as client:
        r = await client.post(f"{_options_trade_base()}/v2/orders", json=payload)
    if r.status_code not in (200, 201):
        detail = r.text[:220]
        if "no available quote" not in detail.lower():
            return {"ok": False, "reason": f"alpaca_rejected_{r.status_code}", "detail": detail}
        snap = await _option_snapshot(symbol)
        bid = _safe_float(snap.get("bid")) if snap.get("ok") else 0.0
        limit_price = round(max(0.01, bid), 2)
        limit_payload: dict[str, Any] = {
            "symbol": symbol,
            "side": "sell",
            "type": "limit",
            "time_in_force": "day",
            "limit_price": limit_price,
        }
        if qty:
            limit_payload["qty"] = str(int(qty))
        async with httpx.AsyncClient(timeout=15.0, headers=_options_headers()) as client:
            retry = await client.post(f"{_options_trade_base()}/v2/orders", json=limit_payload)
        if retry.status_code not in (200, 201):
            return {
                "ok": False,
                "reason": f"alpaca_rejected_{retry.status_code}",
                "detail": retry.text[:220],
                "market_reject_detail": detail,
                "limit_payload": limit_payload,
                "snapshot": snap,
            }
        return {
            "ok": True,
            "order": retry.json(),
            "account_route": route,
            "fallback": "limit_sell_after_no_quote_market_reject",
            "market_reject_detail": detail,
            "snapshot": snap,
        }
    return {"ok": True, "order": r.json(), "account_route": route}


def _order_fill_price(order: dict[str, Any]) -> float:
    return _safe_float(order.get("filled_avg_price")) or _safe_float(order.get("limit_price"))


def _order_fill_qty(order: dict[str, Any]) -> int:
    return _safe_int(order.get("filled_qty")) or _safe_int(order.get("qty"))


async def sync_fills(limit: int = 500) -> dict[str, Any]:
    """Convert Alpaca option fills into durable Options Desk trade records."""
    db = get_db()
    live_orders = await orders(status="all", limit=limit)
    live_positions = await positions()
    position_by_symbol = {
        str(p.get("symbol") or "").upper(): p
        for p in live_positions.get("positions") or []
        if _parse_occ_symbol(str(p.get("symbol") or "").upper()) and _safe_int(p.get("qty")) > 0
    }
    position_symbols = {
        symbol
        for symbol in position_by_symbol.keys()
    }
    upserted = 0
    closed = 0
    ignored = 0
    fill_notifications: list[dict[str, Any]] = []
    fill_order_ids: list[str] = []
    for order in live_orders.get("orders") or []:
        symbol = str(order.get("symbol") or "").upper()
        parsed = _parse_occ_symbol(symbol)
        if not parsed:
            ignored += 1
            continue
        status = str(order.get("status") or "").lower()
        side = str(order.get("side") or "").lower()
        filled_qty = _order_fill_qty(order)
        fill_price = _order_fill_price(order)
        if filled_qty <= 0 or fill_price <= 0 or status not in {"filled", "partially_filled"}:
            ignored += 1
            continue
        if side == "buy":
            local_order = await db.options_desk_orders.find_one({"order.id": order.get("id")}, {"_id": 0})
            existing = await db.options_desk_trades.find_one({"entry_order_id": order.get("id")}, {"_id": 0})
            fill_snap = (((local_order or {}).get("fresh_preflight") or {}).get("snapshot") or {})
            if not fill_snap.get("ok"):
                fill_snap = await _option_snapshot(symbol) if symbol in position_symbols else {"ok": False}
            pricing_truth = _spread_cost_context(fill_price, fill_snap)
            mid_at_fill = _safe_float(pricing_truth.get("mid_at_fill")) or fill_price
            prior_peak = _safe_float(((existing or {}).get("exit_policy") or {}).get("peak_premium")) or mid_at_fill
            current = 0.0
            snap = await _option_snapshot(symbol) if symbol in position_symbols else {"ok": False}
            price_context = {}
            if symbol in position_by_symbol:
                price_context = _option_position_context(position_by_symbol[symbol], snap)
                if mid_at_fill > 0 and _safe_float(price_context.get("current")) > 0:
                    price_context["entry"] = mid_at_fill
                    price_context["pnl_pct"] = (_safe_float(price_context.get("current")) - mid_at_fill) / mid_at_fill * 100.0
                    price_context["unrealized"] = (_safe_float(price_context.get("current")) - fill_price) * filled_qty * 100
                    price_context["price_basis"] = PRICE_BASIS
                current = _safe_float(price_context.get("current"))
            elif snap.get("ok"):
                current = _snapshot_mid(snap) or _safe_float(snap.get("bid"))
                price_context = {
                    "price_source": "alpaca_snapshot_mid" if _snapshot_mid(snap) > 0 else "alpaca_snapshot_bid",
                    "snapshot_bid": _safe_float(snap.get("bid")),
                    "snapshot_mid": _safe_float(snap.get("mid")),
                    "snapshot_last": _safe_float(snap.get("last")),
                    "snapshot_mark": _safe_float(snap.get("mark")),
                    "entry": mid_at_fill,
                    "current": current,
                    "pnl_pct": ((current - mid_at_fill) / mid_at_fill * 100.0) if mid_at_fill > 0 and current > 0 else None,
                    "price_basis": PRICE_BASIS,
                    "data_conflict": False,
                }
            pnl_pct = price_context.get("pnl_pct") if price_context else None
            peak_basis = max(mid_at_fill, current)
            if pnl_pct is None or _safe_float(pnl_pct) > 0:
                peak_basis = max(prior_peak, current, mid_at_fill)
            exit_policy = options_ratchet_state(
                entry_premium=mid_at_fill,
                current_bid=current,
                peak_premium=peak_basis,
            )
            trade = {
                "trade_id": f"opt-trade-{order.get('id')}",
                "entry_order_id": order.get("id"),
                "symbol": symbol,
                "ticker": parsed["root"],
                "option_type": "CALL" if parsed["type"] == "C" else "PUT",
                "strike": parsed["strike"],
                "expiration": parsed["expiration"],
                "qty": filled_qty,
                "entry_premium": round(mid_at_fill, 2),
                "fill_price": round(fill_price, 2),
                "mid_at_fill": round(mid_at_fill, 2),
                "spread_at_fill": pricing_truth.get("spread_at_fill"),
                "spread_cost_paid": pricing_truth.get("spread_cost_paid"),
                "pricing_truth": pricing_truth,
                "entry_notional": round(fill_price * filled_qty * 100, 2),
                "entry_mid_notional": round(mid_at_fill * filled_qty * 100, 2),
                "entry_filled_at": order.get("filled_at") or order.get("updated_at"),
                "entry_order": order,
                "dte_at_entry": _safe_int((((local_order or {}).get("candidate") or {}).get("instrument") or {}).get("days_to_expiration")),
                "candidate": (local_order or {}).get("candidate"),
                "pm": {
                    "route": ((local_order or {}).get("candidate") or {}).get("route"),
                    "action": ((local_order or {}).get("candidate") or {}).get("pm_action"),
                    "score": ((local_order or {}).get("candidate") or {}).get("pm_score"),
                    "risk_budget": ((local_order or {}).get("candidate") or {}).get("risk_budget"),
                },
                "status": "active" if symbol in position_symbols else "flat_no_position",
                "exit_policy": exit_policy,
                "current_premium": round(current, 2),
                "unrealized_pnl": round(_safe_float(price_context.get("unrealized")), 2) if price_context and price_context.get("unrealized") is not None else None,
                "unrealized_pct": round(_safe_float(pnl_pct), 2) if pnl_pct is not None else None,
                "price_source": price_context.get("price_source") if price_context else None,
                "price_context": price_context,
                "last_synced_at": _now(),
            }
            await db.options_desk_trades.update_one(
                {"entry_order_id": order.get("id")},
                {"$set": trade, "$setOnInsert": stamped({})},
                upsert=True,
            )
            await db.options_desk_orders.update_one(
                {"order.id": order.get("id")},
                {"$set": {"status": status, "fill_synced": True, "filled_order": order, "exit_policy": exit_policy}},
            )
            already_notified = bool(((local_order or {}).get("telegram") or {}).get("fill_sent")) or bool(((existing or {}).get("telegram") or {}).get("fill_sent"))
            if not already_notified:
                fill_notifications.append(trade)
                fill_order_ids.append(str(order.get("id")))
            upserted += 1
        elif side == "sell":
            open_trades = await db.options_desk_trades.find(
                {"symbol": symbol, "status": {"$in": sorted(OPTION_ACTIVE_STATUSES | {"flat_no_position"})}},
                {"_id": 1, "trade_id": 1, "entry_premium": 1, "fill_price": 1, "mid_at_fill": 1, "qty": 1, "entry_filled_at": 1, "candidate": 1, "close_reason": 1, "strategy_lane": 1, "spread_cost_paid": 1, "dte_at_entry": 1, "ticker": 1, "symbol": 1},
            ).to_list(20)
            remaining_fill_qty = filled_qty
            for trade in open_trades:
                if remaining_fill_qty <= 0:
                    break
                entry_mid = _safe_float(trade.get("mid_at_fill") or trade.get("entry_premium"))
                entry_fill = _safe_float(trade.get("fill_price") or trade.get("entry_premium"))
                trade_qty = _safe_int(trade.get("qty")) or filled_qty
                qty_for_pnl = min(remaining_fill_qty, trade_qty)
                remaining_fill_qty -= qty_for_pnl
                snap = await _option_snapshot(symbol)
                exit_mid = _snapshot_mid(snap) or fill_price
                realized = round((fill_price - entry_fill) * qty_for_pnl * 100, 2) if entry_fill > 0 else None
                realized_pct = round((fill_price - entry_fill) / entry_fill * 100.0, 2) if entry_fill > 0 else None
                realized_pct_mid = round((exit_mid - entry_mid) / entry_mid * 100.0, 2) if entry_mid > 0 else None
                remaining_qty = max(0, trade_qty - qty_for_pnl)
                partial = {
                    "exit_order_id": order.get("id"),
                    "qty": qty_for_pnl,
                    "exit_premium": round(fill_price, 2),
                    "exit_mid": round(exit_mid, 2),
                    "exit_filled_at": order.get("filled_at") or order.get("updated_at"),
                    "realized_pnl": realized,
                    "realized_pct": realized_pct,
                    "realized_pct_mid_basis": realized_pct_mid,
                    "reason": trade.get("close_reason") or "sell_fill",
                    "synced_at": _now(),
                }
                if remaining_qty > 0:
                    await db.options_desk_trades.update_one(
                        {"_id": trade["_id"]},
                        {"$set": {
                            "qty": remaining_qty,
                            "tier1_taken": True,
                            "last_partial_exit": partial,
                            "last_synced_at": _now(),
                        }, "$push": {"partial_exits": partial}},
                    )
                else:
                    closed_doc = {
                        **trade,
                        "status": "closed",
                        "exit_order_id": order.get("id"),
                        "exit_premium": round(fill_price, 2),
                        "exit_mid": round(exit_mid, 2),
                        "exit_filled_at": order.get("filled_at") or order.get("updated_at"),
                        "exit_order": order,
                        "closed_at": _now(),
                        "realized_pnl": realized,
                        "realized_pct": realized_pct,
                        "realized_pct_mid_basis": realized_pct_mid,
                        "close_reason": trade.get("close_reason") or "sell_fill",
                    }
                    await db.options_desk_trades.update_one(
                        {"_id": trade["_id"]},
                        {"$set": {k: v for k, v in closed_doc.items() if k != "_id"}},
                    )
                    try:
                        from . import expectancy_ledger
                        await expectancy_ledger.record_closed_trade(closed_doc)
                    except Exception:
                        pass
                    closed += 1
    fill_message_sent = False
    if fill_notifications:
        fill_message_sent = await _send_grouped_fill_message(fill_notifications)
        if fill_message_sent:
            await db.options_desk_orders.update_many(
                {"order.id": {"$in": fill_order_ids}},
                {"$set": {"telegram.fill_sent": True, "telegram.fill_sent_at": _now()}},
            )
            await db.options_desk_trades.update_many(
                {"entry_order_id": {"$in": fill_order_ids}},
                {"$set": {"telegram.fill_sent": True, "telegram.fill_sent_at": _now()}},
            )
    await db.options_desk_fill_sync.insert_one(stamped({
        "synced_at": _now(),
        "upserted": upserted,
        "closed": closed,
        "ignored": ignored,
        "fill_notifications": len(fill_notifications),
        "fill_message_sent": fill_message_sent,
        "position_symbols": sorted(position_symbols),
    }))
    return {"ok": True, "upserted": upserted, "closed": closed, "ignored": ignored, "fill_notifications": len(fill_notifications), "fill_message_sent": fill_message_sent, "active_symbols": sorted(position_symbols)}


async def trades(limit: int = 100, sync_live: bool = True) -> dict[str, Any]:
    if sync_live:
        await sync_fills()
    db = get_db()
    rows = await db.options_desk_trades.find({}, {"_id": 0}).sort("last_synced_at", -1).to_list(limit)
    return {
        "ok": True,
        "generated_at": _now(),
        "trades": rows,
        "active": sum(1 for r in rows if r.get("status") == "active"),
        "closed": sum(1 for r in rows if r.get("status") == "closed"),
    }


def _dte(expiration: str | None) -> int:
    try:
        exp = datetime.fromisoformat(str(expiration)).date()
        return (exp - datetime.now(timezone.utc).date()).days
    except Exception:
        return 0


def _history_stats(closes: dict[str, float]) -> dict[str, float]:
    values = [float(v) for _, v in sorted((closes or {}).items()) if _safe_float(v) > 0]
    if len(values) < 3:
        return {"last": 0.0, "mom_63": 0.0, "mom_126": 0.0, "mom_252": 0.0, "vol_annual": 0.0}
    last = values[-1]
    def mom(days: int) -> float:
        idx = max(0, len(values) - 1 - days)
        base = values[idx]
        return ((last - base) / base * 100.0) if base > 0 else 0.0
    returns = [(values[i] / values[i - 1] - 1.0) for i in range(1, len(values)) if values[i - 1] > 0]
    if returns:
        mean = sum(returns) / len(returns)
        variance = sum((x - mean) ** 2 for x in returns) / len(returns)
        vol_annual = (variance ** 0.5) * (252 ** 0.5) * 100.0
    else:
        vol_annual = 0.0
    return {
        "last": round(last, 2),
        "mom_63": round(mom(63), 2),
        "mom_126": round(mom(126), 2),
        "mom_252": round(mom(252), 2),
        "vol_annual": round(vol_annual, 2),
    }


async def _kronos_style_1y_projection(ticker: str, delta: float = 0.75, premium: float = 0.0, spot_hint: float = 0.0) -> dict[str, Any]:
    try:
        from . import pricer
        closes = await pricer.get_history(ticker, days=280)
    except Exception:
        closes = {}
    stats = _history_stats(closes)
    base_underlying = (stats["mom_63"] * 0.35) + (stats["mom_126"] * 0.30) + (stats["mom_252"] * 0.35)
    base_underlying = max(-35.0, min(80.0, base_underlying))
    vol = stats["vol_annual"] or 28.0
    spot = spot_hint or stats["last"]
    leverage = max(1.0, min(8.0, (spot / max(premium, 0.25)) * abs(delta) / 10.0)) if spot else 1.0
    expected_contract = max(-100.0, min(350.0, base_underlying * leverage))
    return {
        "source": "kronos_style_1y_proxy",
        "expected_underlying_1y_pct": round(base_underlying, 2),
        "expected_contract_1y_pct": round(expected_contract, 2),
        "cone_low_pct": round(max(-100.0, expected_contract - vol * leverage * 0.75), 2),
        "cone_high_pct": round(min(500.0, expected_contract + vol * leverage * 0.95), 2),
        "history": stats,
    }


async def leaps_sleeve(limit_candidates: int = 12) -> dict[str, Any]:
    db = get_db()
    pos = await positions()
    trade_rows = await db.options_desk_trades.find({}, {"_id": 0}).sort("last_synced_at", -1).to_list(500)
    trade_by_symbol = {str(t.get("symbol") or "").upper(): t for t in trade_rows}
    open_options = []
    short_calls_by_root: dict[str, list[dict[str, Any]]] = {}
    for p in pos.get("positions") or []:
        symbol = str(p.get("symbol") or "").upper()
        parsed = _parse_occ_symbol(symbol)
        if not parsed:
            continue
        qty = _safe_int(p.get("qty"))
        item = {"position": p, "parsed": parsed, "dte": _dte(parsed.get("expiration"))}
        if parsed["type"] == "C" and qty < 0:
            short_calls_by_root.setdefault(parsed["root"], []).append(item)
        if parsed["type"] == "C" and qty > 0 and item["dte"] >= 180:
            open_options.append(item)

    holdings: list[dict[str, Any]] = []
    for item in open_options:
        p = item["position"]
        parsed = item["parsed"]
        trade = trade_by_symbol.get(str(p.get("symbol") or "").upper()) or {}
        snap = await _option_snapshot(str(p.get("symbol") or "").upper())
        ctx = _option_position_context(p, snap)
        projection = await _kronos_style_1y_projection(
            parsed["root"],
            delta=abs(_safe_float(snap.get("delta")) or 0.75),
            premium=_safe_float(ctx.get("current")) or _safe_float(ctx.get("entry")),
        )
        overlays = short_calls_by_root.get(parsed["root"], [])
        strategy = "LEAPS_DIAGONAL_ACTIVE" if overlays else "LEAPS_HOLD_SELL_CALL_WHEN_PREMIUM_CLEARS"
        holdings.append({
            "symbol": p.get("symbol"),
            "ticker": parsed["root"],
            "expiration": parsed["expiration"],
            "days_to_expiration": item["dte"],
            "strike": parsed["strike"],
            "qty": _safe_int(p.get("qty")),
            "entry_premium": round(_safe_float(ctx.get("entry")), 2),
            "current_premium": round(_safe_float(ctx.get("current")), 2),
            "unrealized_pct": round(_safe_float(ctx.get("pnl_pct")), 2) if ctx.get("pnl_pct") is not None else None,
            "strategy_current": strategy,
            "covered_call_overlay_count": len(overlays),
            "overlay_symbols": [x["position"].get("symbol") for x in overlays],
            "kronos_1y": projection,
            "trade": trade,
            "data_quality": snap.get("data_quality") if snap.get("ok") else "POSITION_ONLY",
        })

    scan = await db.scan_results.find_one({}, {"_id": 0}, sort=[("finished_at", -1)])
    candidates: list[dict[str, Any]] = []
    try:
        from . import portfolio_manager
        rows = (scan or {}).get("results") or []
        pm_rows = portfolio_manager.evaluate_rows(rows, equity=portfolio_manager.DEFAULT_EQUITY, mode="BALANCED")
        by_ticker = {r["ticker"]: r for r in pm_rows}
        for row in rows:
            ticker = str(row.get("ticker") or "").upper()
            pm = by_ticker.get(ticker) or {}
            if pm.get("action") not in {"ACCUMULATE", "STARTER"}:
                continue
            if _safe_float(pm.get("pm_score")) < 70:
                continue
            projection = await _kronos_style_1y_projection(ticker)
            candidates.append({
                "ticker": ticker,
                "pm_action": pm.get("action"),
                "pm_score": pm.get("pm_score"),
                "risk_reward": pm.get("risk_reward"),
                "strategy_candidate": "LEAPS_CALL_OR_DIAGONAL",
                "kronos_1y": projection,
                "reason": "High PM score with long-term upside candidate. Requires LEAPS chain liquidity before buying.",
            })
    except Exception:
        candidates = []

    candidates.sort(key=lambda x: _safe_float((x.get("kronos_1y") or {}).get("expected_contract_1y_pct")), reverse=True)
    return {
        "ok": True,
        "generated_at": _now(),
        "mode": "read_only_leaps_sleeve",
        "summary": {
            "open_leaps": len(holdings),
            "diagonal_overlays": sum(1 for h in holdings if h.get("covered_call_overlay_count")),
            "candidate_count": min(len(candidates), limit_candidates),
        },
        "holdings": holdings,
        "candidates": candidates[:limit_candidates],
        "policy": {
            "min_dte": 180,
            "preferred_dte": "365-730",
            "strategy": "Buy long-dated calls on PM-approved long-term setups; sell short calls only when premium and strike cushion clear.",
            "execution": "read_only_until_leaps_execution_rules_are_enabled",
        },
    }


def _today_et_key() -> str:
    return datetime.now(timezone.utc).astimezone(ET).strftime("%Y-%m-%d")


def _week_window_et() -> tuple[str, str]:
    now = datetime.now(timezone.utc).astimezone(ET)
    monday = now.date() - timedelta(days=now.weekday())
    friday = monday + timedelta(days=4)
    return monday.isoformat(), friday.isoformat()


def _date_key_et(value: Any) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(ET).strftime("%Y-%m-%d")
    except Exception:
        return None


def _trade_gain_row(trade: dict[str, Any]) -> dict[str, Any] | None:
    pct = trade.get("unrealized_pct") if trade.get("status") != "closed" else trade.get("realized_pct")
    dollars = trade.get("unrealized_pnl") if trade.get("status") != "closed" else trade.get("realized_pnl")
    if pct is None:
        return None
    return {
        "symbol": trade.get("symbol"),
        "ticker": trade.get("ticker"),
        "pct": _safe_float(pct),
        "dollars": _safe_float(dollars),
        "status": trade.get("status"),
    }


async def options_daily_report_payload() -> dict[str, Any]:
    fill = await sync_fills()
    risk = await monitor_open_positions(enforce_hard_stop=True)
    db = get_db()
    rows = await db.options_desk_trades.find({}, {"_id": 0}).sort("last_synced_at", -1).to_list(500)
    today = _today_et_key()
    active = [r for r in rows if r.get("status") in OPTION_ACTIVE_STATUSES]
    closed_today = [r for r in rows if r.get("status") == "closed" and _date_key_et(r.get("closed_at") or r.get("exit_filled_at")) == today]
    unrealized = sum(_safe_float(r.get("unrealized_pnl")) for r in active)
    realized = sum(_safe_float(r.get("realized_pnl")) for r in closed_today)
    risk_deployed = sum(_safe_float(r.get("entry_notional")) for r in active)
    theta_watch = sum(1 for r in active if r.get("theta_status") == "WATCH")
    gain_rows = [x for x in (_trade_gain_row(r) for r in active + closed_today) if x]
    biggest_gain = max(gain_rows, key=lambda x: x["pct"], default=None)
    biggest_loser = min(gain_rows, key=lambda x: x["pct"], default=None)
    return {
        "ok": True,
        "date": today,
        "fill": fill,
        "risk": risk,
        "active_count": len(active),
        "closed_today_count": len(closed_today),
        "unrealized_gain": round(unrealized, 2),
        "realized_gain": round(realized, 2),
        "risk_deployed": round(risk_deployed, 2),
        "daily_premium_cap": OPTIONS_DAILY_PREMIUM_CAP_USD,
        "theta_watch": theta_watch,
        "biggest_gain": biggest_gain,
        "biggest_loser": biggest_loser,
    }


async def options_weekly_report_payload() -> dict[str, Any]:
    fill = await sync_fills()
    risk = await monitor_open_positions(enforce_hard_stop=True)
    try:
        from . import expectancy_ledger
        expectancy = await expectancy_ledger.weekly_expectancy_report()
    except Exception as exc:
        expectancy = {"ok": False, "reason": exc.__class__.__name__}
    db = get_db()
    rows = await db.options_desk_trades.find({}, {"_id": 0}).sort("last_synced_at", -1).to_list(1000)
    week_start, week_end = _week_window_et()
    active = [r for r in rows if r.get("status") in OPTION_ACTIVE_STATUSES]
    closed_week = [
        r for r in rows
        if r.get("status") == "closed"
        and (week_start <= (_date_key_et(r.get("closed_at") or r.get("exit_filled_at")) or "") <= week_end)
    ]
    unrealized = sum(_safe_float(r.get("unrealized_pnl")) for r in active)
    realized = sum(_safe_float(r.get("realized_pnl")) for r in closed_week)
    risk_deployed = sum(_safe_float(r.get("entry_notional")) for r in active)
    theta_watch = sum(1 for r in active if r.get("theta_status") == "WATCH")
    gain_rows = [x for x in (_trade_gain_row(r) for r in active + closed_week) if x]
    biggest_gain = max(gain_rows, key=lambda x: x["pct"], default=None)
    biggest_loser = min(gain_rows, key=lambda x: x["pct"], default=None)
    return {
        "ok": True,
        "week_start": week_start,
        "week_end": week_end,
        "fill": fill,
        "risk": risk,
        "active_count": len(active),
        "closed_week_count": len(closed_week),
        "unrealized_gain": round(unrealized, 2),
        "realized_gain": round(realized, 2),
        "risk_deployed": round(risk_deployed, 2),
        "daily_premium_cap": OPTIONS_DAILY_PREMIUM_CAP_USD,
        "theta_watch": theta_watch,
        "biggest_gain": biggest_gain,
        "biggest_loser": biggest_loser,
        "expectancy": expectancy,
    }


def _gain_line(label: str, row: dict[str, Any] | None) -> str:
    if not row:
        return f"{label}: <b>-</b>"
    return (
        f"{label}: <b>${_esc(row.get('ticker'))}</b> "
        f"{_fmt_pct(row.get('pct'))} / {_fmt_money(row.get('dollars'))}"
    )


async def dispatch_options_daily_report(force: bool = False) -> dict[str, Any]:
    db = get_db()
    today = _today_et_key()
    state_id = f"options_daily_report:{today}"
    if not force:
        existing = await db.bot_state.find_one({"_id": state_id}, {"_id": 0})
        if existing and existing.get("sent"):
            return {"ok": True, "sent": False, "reason": "already_sent", "date": today}
    payload = await options_daily_report_payload()
    lines = [
        "<b>CASE CAPITAL | OPTIONS DAILY REPORT</b>",
        f"<code>{_now_et()}</code>",
        "",
        f"Date: <b>{today}</b>",
        f"Open contracts: <b>{payload['active_count']}</b>",
        f"Closed today: <b>{payload['closed_today_count']}</b>",
        f"Unrealized gains: <b>{_fmt_money(payload['unrealized_gain'])}</b>",
        f"Realized gains: <b>{_fmt_money(payload['realized_gain'])}</b>",
        f"Risk deployed: <b>{_fmt_money(payload['risk_deployed'])}</b> / {_fmt_money(payload['daily_premium_cap'])}",
        f"Theta watch: <b>{payload['theta_watch']}</b>",
        f"Grind expectancy: <b>{_fmt_pct(((payload.get('expectancy') or {}).get('grind') or {}).get('expectancy_pct'))}</b> "
        f"n={(((payload.get('expectancy') or {}).get('grind') or {}).get('sample_size') or 0)}",
        "",
        _gain_line("Biggest gain", payload.get("biggest_gain")),
        _gain_line("Biggest loser", payload.get("biggest_loser")),
    ]
    sent = await _telegram_send("\n".join(lines))
    await db.bot_state.update_one(
        {"_id": state_id},
        {"$set": {"sent": sent, "sent_at": _now(), "payload": payload}},
        upsert=True,
    )
    return {"ok": True, "sent": sent, **payload}


async def dispatch_options_weekly_report(force: bool = False) -> dict[str, Any]:
    db = get_db()
    week_start, week_end = _week_window_et()
    state_id = f"options_weekly_report:{week_start}:{week_end}"
    if not force:
        existing = await db.bot_state.find_one({"_id": state_id}, {"_id": 0})
        if existing and existing.get("sent"):
            return {"ok": True, "sent": False, "reason": "already_sent", "week_start": week_start, "week_end": week_end}
    payload = await options_weekly_report_payload()
    lines = [
        "<b>CASE CAPITAL | OPTIONS WEEKLY REPORT</b>",
        f"<code>{_now_et()}</code>",
        "",
        f"Week: <b>{payload['week_start']} -> {payload['week_end']}</b>",
        f"Open contracts: <b>{payload['active_count']}</b>",
        f"Closed this week: <b>{payload['closed_week_count']}</b>",
        f"Unrealized gains: <b>{_fmt_money(payload['unrealized_gain'])}</b>",
        f"Realized gains: <b>{_fmt_money(payload['realized_gain'])}</b>",
        f"Risk deployed: <b>{_fmt_money(payload['risk_deployed'])}</b> / {_fmt_money(payload['daily_premium_cap'])}",
        f"Theta watch: <b>{payload['theta_watch']}</b>",
        "",
        _gain_line("Biggest gain", payload.get("biggest_gain")),
        _gain_line("Biggest loser", payload.get("biggest_loser")),
    ]
    sent = await _telegram_send("\n".join(lines))
    await db.bot_state.update_one(
        {"_id": state_id},
        {"$set": {"sent": sent, "sent_at": _now(), "payload": payload}},
        upsert=True,
    )
    return {"ok": True, "sent": sent, **payload}


async def monitor_open_positions(enforce_hard_stop: bool = True) -> dict[str, Any]:
    """Check open option positions using Options Desk V2 mid-price risk math."""
    db = get_db()
    pos = await positions()
    checks: list[dict[str, Any]] = []
    closed: list[dict[str, Any]] = []
    pending_closes: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for p in pos.get("positions") or []:
        symbol = str(p.get("symbol") or "").upper()
        if not _parse_occ_symbol(symbol):
            continue
        qty = _safe_int(p.get("qty"))
        if qty <= 0:
            continue
        snap = await _option_snapshot(symbol)
        price_context = _option_position_context(p, snap)
        entry = _safe_float(price_context.get("entry"))
        current = _safe_float(price_context.get("current"))
        pnl_pct = price_context.get("pnl_pct")
        existing = await db.options_desk_orders.find_one(
            {"$or": [{"order.symbol": symbol}, {"candidate.instrument.symbol": symbol}, {"candidate.instrument.contractSymbol": symbol}]},
            {"_id": 0, "exit_policy": 1},
            sort=[("submitted_at", -1)],
        )
        trade_doc = await db.options_desk_trades.find_one(
            {"symbol": symbol, "status": {"$in": ["active", "flat_no_position", "pending_protective_close_market_closed"]}},
            {"_id": 1, "ticker": 1, "entry_premium": 1, "fill_price": 1, "mid_at_fill": 1, "entry_filled_at": 1, "dte_at_entry": 1, "exit_policy": 1, "telegram": 1, "tier1_taken": 1, "candidate": 1},
            sort=[("last_synced_at", -1)],
        )
        if trade_doc and _safe_float(trade_doc.get("mid_at_fill")) > 0 and current > 0:
            mid_entry = _safe_float(trade_doc.get("mid_at_fill"))
            price_context["entry"] = mid_entry
            price_context["pnl_pct"] = (current - mid_entry) / mid_entry * 100.0
            price_context["unrealized"] = (current - _safe_float(trade_doc.get("fill_price") or mid_entry)) * qty * 100
            entry = mid_entry
            pnl_pct = price_context.get("pnl_pct")
        ticker = (trade_doc or {}).get("ticker") or (_parse_occ_symbol(symbol) or {}).get("root") or symbol
        prior_exit = (existing or {}).get("exit_policy") or {}
        if trade_doc and trade_doc.get("exit_policy"):
            prior_exit = trade_doc.get("exit_policy") or prior_exit
        prior_peak = _safe_float(prior_exit.get("peak_premium")) or entry
        peak_basis = max(entry, current)
        if pnl_pct is None or _safe_float(pnl_pct) > 0:
            peak_basis = max(prior_peak, current, entry)
        ratchet = options_ratchet_state(entry_premium=entry, current_bid=current, peak_premium=peak_basis)
        theta = snap.get("theta") if snap.get("ok") else None
        theta_pct = (float(theta) / current * 100.0) if theta is not None and current > 0 else None
        days_held = _days_held((trade_doc or {}).get("entry_filled_at"))
        dte_at_entry = _safe_int((trade_doc or {}).get("dte_at_entry"))
        dte_elapsed_frac = days_held / max(1, dte_at_entry) if dte_at_entry > 0 else 0.0
        days_to_earnings = await _days_to_next_earnings(ticker) if trade_doc else None
        hard_stop = bool(pnl_pct is not None and _safe_float(pnl_pct) <= OPTIONS_HARD_STOP_PCT)
        take_profit_tier1 = bool(
            pnl_pct is not None
            and _safe_float(pnl_pct) >= TAKE_PROFIT_TIER1_PCT
            and not (trade_doc or {}).get("tier1_taken")
            and qty > 1
        )
        time_stop = bool(
            dte_at_entry > 0
            and dte_elapsed_frac >= TIME_STOP_DTE_FRACTION
            and (pnl_pct is None or _safe_float(pnl_pct) < TIME_STOP_MIN_PNL_PCT)
        )
        theta_stop = bool(
            theta_pct is not None
            and theta_pct <= THETA_STOP_PCT_OF_PREMIUM
            and (pnl_pct is None or _safe_float(pnl_pct) <= 0)
        )
        strategy_lane_name = str((((trade_doc or {}).get("candidate") or {}).get("strategy_lane") or {}).get("lane") or "")
        event_stop = bool(
            days_to_earnings is not None
            and days_to_earnings <= EVENT_STOP_DAYS_BEFORE_EARNINGS
            and strategy_lane_name != "EVENT_DEFINED_RISK"
        )
        check = {
            "symbol": symbol,
            "qty": qty,
            "entry_premium": round(entry, 2),
            "current_premium": round(current, 2),
            "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
            "theta": round(float(theta), 4) if theta is not None else None,
            "theta_pct_of_premium": round(theta_pct, 2) if theta_pct is not None else None,
            "theta_status": "WATCH" if theta_pct is not None and theta_pct <= -8.0 else "OK",
            "days_held": round(days_held, 2),
            "dte_at_entry": dte_at_entry or None,
            "dte_elapsed_frac": round(dte_elapsed_frac, 3) if dte_at_entry else None,
            "days_to_next_earnings": days_to_earnings,
            "spread_cost_paid": (trade_doc or {}).get("spread_cost_paid"),
            "price_basis": PRICE_BASIS,
            "hard_stop_pct": OPTIONS_HARD_STOP_PCT,
            "hard_stop_triggered": hard_stop,
            "take_profit_tier1_triggered": take_profit_tier1,
            "time_stop_triggered": time_stop,
            "theta_stop_triggered": theta_stop,
            "event_stop_triggered": event_stop,
            "ratchet": ratchet,
            "snapshot": snap,
            "price_source": price_context.get("price_source"),
            "position_unrealized_plpc": price_context.get("position_unrealized_plpc"),
            "position_unrealized_pl": price_context.get("position_unrealized_pl"),
            "snapshot_bid": price_context.get("snapshot_bid"),
            "snapshot_mid": price_context.get("snapshot_mid"),
            "snapshot_last": price_context.get("snapshot_last"),
            "snapshot_mark": price_context.get("snapshot_mark"),
            "data_conflict": price_context.get("data_conflict"),
            "checked_at": _now(),
        }
        unrealized = round(_safe_float(price_context.get("unrealized")), 2) if price_context.get("unrealized") is not None else None
        previous_notified_floor = _safe_float((((trade_doc or {}).get("telegram") or {}).get("last_ratchet_floor_pct")), OPTIONS_INITIAL_STOP_PCT)
        current_floor = _safe_float(ratchet.get("locked_floor_pct"), OPTIONS_INITIAL_STOP_PCT)
        ratchet_notification_allowed = bool(pnl_pct is not None and _safe_float(pnl_pct) > 0 and current > entry)
        if (
            trade_doc
            and ratchet_notification_allowed
            and current_floor > previous_notified_floor
            and current_floor > OPTIONS_INITIAL_STOP_PCT
            and not (((trade_doc.get("telegram") or {}).get(f"ratchet_{current_floor:g}_sent")))
        ):
            sent = await _send_ratchet_message(symbol, ticker, entry, current, ratchet, _next_ratchet_tier(ratchet))
            if sent:
                await db.options_desk_trades.update_one(
                    {"_id": trade_doc["_id"]},
                    {"$set": {
                        "telegram.last_ratchet_floor_pct": current_floor,
                        f"telegram.ratchet_{current_floor:g}_sent": True,
                        "telegram.last_ratchet_sent_at": _now(),
                    }},
                )
        if existing:
            await db.options_desk_orders.update_many(
                {"$or": [{"order.symbol": symbol}, {"candidate.instrument.symbol": symbol}, {"candidate.instrument.contractSymbol": symbol}]},
                {"$set": {"exit_policy": ratchet, "last_risk_check": check}},
            )
        await db.options_desk_trades.update_many(
            {"symbol": symbol, "status": {"$in": ["active", "flat_no_position", "pending_protective_close_market_closed"]}},
            {"$set": {
                "status": "active",
                "current_premium": round(current, 2),
                "unrealized_pnl": unrealized,
                "unrealized_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
                "price_source": price_context.get("price_source"),
                "price_context": price_context,
                "theta": check["theta"],
                "theta_pct_of_premium": check["theta_pct_of_premium"],
                "theta_status": check["theta_status"],
                "exit_policy": ratchet,
                "last_risk_check": check,
                "last_synced_at": _now(),
            }},
        )
        ratchet_exit = bool(
            not any([hard_stop, event_stop, time_stop, theta_stop])
            and ratchet.get("exit_triggered")
            and current_floor > OPTIONS_INITIAL_STOP_PCT
        )
        exit_reason = (
            "hard_stop" if hard_stop
            else "event_stop" if event_stop
            else "time_stop" if time_stop
            else "theta_stop" if theta_stop
            else "ratchet" if ratchet_exit
            else None
        )
        if take_profit_tier1 and enforce_hard_stop:
            tier_qty = max(1, int(qty * TAKE_PROFIT_TIER1_SELL_FRACTION))
            market_status = await _options_market_status()
            if market_status.get("is_open"):
                result = await close(symbol=symbol, qty=tier_qty)
                check["take_profit_tier1_close_result"] = result
                if result.get("ok"):
                    await db.options_desk_trades.update_many(
                        {"symbol": symbol, "status": "active"},
                        {"$set": {
                            "tier1_taken": True,
                            "tier1_taken_at": _now(),
                            "last_take_profit_order": result.get("order"),
                            "close_reason": "options_take_profit_tier1",
                        }},
                    )
                    closed.append({"symbol": symbol, "qty": tier_qty, "reason": "options_take_profit_tier1", "order_id": result.get("order", {}).get("id")})
                else:
                    errors.append({"symbol": symbol, "reason": result.get("reason"), "detail": result.get("detail"), "exit": "take_profit_tier1"})
            else:
                pending = {"symbol": symbol, "qty": tier_qty, "reason": "options_take_profit_tier1", "market_status": market_status}
                pending_closes.append(pending)
                check["pending_take_profit_tier1"] = pending
        if exit_reason and enforce_hard_stop:
            market_status = await _options_market_status()
            if not market_status.get("is_open"):
                close_reason = {
                    "hard_stop": "options_hard_stop_mid_basis",
                    "event_stop": "options_event_stop",
                    "time_stop": "options_time_stop",
                    "theta_stop": "options_theta_stop",
                    "ratchet": "options_ratchet_floor",
                }.get(exit_reason, exit_reason)
                pending = {
                    "symbol": symbol,
                    "qty": qty,
                    "reason": close_reason,
                    "market_status": market_status,
                }
                pending_closes.append(pending)
                check["pending_close"] = pending
                await db.options_desk_orders.update_many(
                    {"$or": [{"order.symbol": symbol}, {"candidate.instrument.symbol": symbol}, {"candidate.instrument.contractSymbol": symbol}]},
                    {"$set": {
                        "status": "pending_protective_close_market_closed",
                        "pending_close": pending,
                        "pending_close_at": _now(),
                        "close_reason": close_reason,
                    }},
                )
                await db.options_desk_trades.update_many(
                    {"symbol": symbol, "status": {"$in": ["active", "pending_protective_close_market_closed"]}},
                    {"$set": {
                        "status": "pending_protective_close_market_closed",
                        "pending_close": pending,
                        "pending_close_at": _now(),
                        "close_reason": close_reason,
                    }},
                )
                checks.append(check)
                continue
            result = await close(symbol=symbol, qty=qty)
            check["close_result"] = result
            if result.get("ok"):
                close_reason = {
                    "hard_stop": "options_hard_stop_mid_basis",
                    "event_stop": "options_event_stop",
                    "time_stop": "options_time_stop",
                    "theta_stop": "options_theta_stop",
                    "ratchet": "options_ratchet_floor",
                }.get(exit_reason, exit_reason)
                closed.append({"symbol": symbol, "qty": qty, "reason": close_reason, "order_id": result.get("order", {}).get("id")})
                await db.options_desk_orders.update_many(
                    {"$or": [{"order.symbol": symbol}, {"candidate.instrument.symbol": symbol}, {"candidate.instrument.contractSymbol": symbol}]},
                    {"$set": {"status": f"{exit_reason}_close_submitted", "closed_by_monitor_at": _now(), "close_order": result.get("order"), "close_reason": close_reason}},
                )
                await db.options_desk_trades.update_many(
                    {"symbol": symbol, "status": "active"},
                    {"$set": {"status": f"{exit_reason}_close_submitted", "closed_by_monitor_at": _now(), "close_order": result.get("order"), "close_reason": close_reason}},
                )
                sent = await _send_exit_message(symbol, ticker, exit_reason, entry, current, qty)
                if sent:
                    await db.options_desk_trades.update_many(
                        {"symbol": symbol, "status": f"{exit_reason}_close_submitted"},
                        {"$set": {"telegram.exit_sent": True, "telegram.exit_sent_at": _now()}},
                    )
            else:
                errors.append({"symbol": symbol, "reason": result.get("reason"), "detail": result.get("detail")})
        checks.append(check)
    await db.options_desk_risk_checks.insert_one(stamped({
        "ok": True,
        "checked_at": _now(),
        "enforce_hard_stop": enforce_hard_stop,
        "hard_stop_pct": OPTIONS_HARD_STOP_PCT,
        "positions_checked": len(checks),
        "closed": closed,
        "pending_closes": pending_closes,
        "errors": errors,
        "checks": checks,
    }))
    if closed:
        await log_activity(f"Options risk monitor closed {len(closed)} hard-stop position(s)", "warn", {"closed": closed})
    return {"ok": True, "positions_checked": len(checks), "closed": closed, "pending_closes": pending_closes, "errors": errors, "checks": checks}


async def sync() -> dict[str, Any]:
    db = get_db()
    pos = await positions()
    ords = await orders(status="all", limit=100)
    fill = await sync_fills()
    risk = await monitor_open_positions(enforce_hard_stop=True)
    await db.options_desk_sync.insert_one(stamped({
        "positions": pos.get("positions", []),
        "orders": ords.get("orders", []),
        "fill": fill,
        "risk": risk,
        "synced_at": _now(),
    }))
    return {"ok": True, "positions": len(pos.get("positions", [])), "orders": len(ords.get("orders", [])), "fill": fill, "risk": risk}


async def latest_risk_check() -> dict[str, Any]:
    db = get_db()
    row = await db.options_desk_risk_checks.find_one({}, {"_id": 0}, sort=[("checked_at", -1)])
    if row:
        return {"ok": True, **row}
    return {
        "ok": True,
        "checked_at": None,
        "enforce_hard_stop": True,
        "hard_stop_pct": OPTIONS_HARD_STOP_PCT,
        "positions_checked": 0,
        "closed": [],
        "errors": [],
        "checks": [],
    }


async def mark_accuracy_audit(persist: bool = True) -> dict[str, Any]:
    """Compare stored option trade marks against live Alpaca position marks.

    This is read-only against Alpaca. It exists because options snapshots can
    show stale last trades while Alpaca positions carry the execution account's
    live mark, which is the safer source for open-position risk.
    """
    db = get_db()
    live_positions = await positions()
    position_by_symbol = {
        str(p.get("symbol") or "").upper(): p
        for p in live_positions.get("positions") or []
        if _parse_occ_symbol(str(p.get("symbol") or "").upper()) and _safe_int(p.get("qty")) > 0
    }
    trades = await db.options_desk_trades.find(
        {"status": {"$in": sorted(OPTION_ACTIVE_STATUSES)}},
        {"_id": 0},
    ).sort("last_synced_at", -1).to_list(200)

    rows: list[dict[str, Any]] = []
    for trade in trades:
        symbol = str(trade.get("symbol") or "").upper()
        position = position_by_symbol.get(symbol)
        issues: list[dict[str, Any]] = []
        snap = await _option_snapshot(symbol) if symbol else {"ok": False, "reason": "missing_symbol"}
        context: dict[str, Any] = {}
        if position:
            context = _option_position_context(position, snap)
        else:
            issues.append({"severity": "CRITICAL", "code": "missing_live_position"})

        entry = _safe_float(trade.get("entry_premium"))
        stored_current = _safe_float(trade.get("current_premium"))
        live_current = _safe_float(context.get("current")) if context else 0.0
        stored_pct = trade.get("unrealized_pct")
        live_pct = context.get("pnl_pct") if context else None
        if not snap.get("ok"):
            issues.append({"severity": "WARN", "code": "snapshot_unavailable", "detail": snap.get("reason")})
        if context.get("data_conflict"):
            issues.append({"severity": "CRITICAL", "code": "position_snapshot_conflict"})
        if entry <= 0:
            issues.append({"severity": "CRITICAL", "code": "missing_entry_premium"})
        if position and live_current <= 0:
            code = "zero_live_mark_hard_stop_due" if entry > 0 else "missing_live_mark"
            issues.append({"severity": "CRITICAL", "code": code})
        if stored_current > 0 and live_current > 0:
            drift_pct = abs(stored_current - live_current) / max(0.01, live_current) * 100.0
            if drift_pct >= 10.0:
                issues.append({"severity": "WARN", "code": "stored_mark_drift_gt_10pct", "drift_pct": round(drift_pct, 2)})
        if stored_pct is not None and live_pct is not None and abs(_safe_float(stored_pct) - _safe_float(live_pct)) >= 15.0:
            issues.append({
                "severity": "WARN",
                "code": "stored_pnl_drift_gt_15pct",
                "stored_pct": round(_safe_float(stored_pct), 2),
                "live_pct": round(_safe_float(live_pct), 2),
            })

        prior_exit = trade.get("exit_policy") or {}
        prior_peak = _safe_float(prior_exit.get("peak_premium")) or entry
        ratchet = options_ratchet_state(
            entry_premium=entry,
            current_bid=live_current if position else None,
            peak_premium=max(entry, prior_peak, live_current),
        )
        if position and ratchet.get("exit_triggered") and str(trade.get("status") or "") == "active":
            issues.append({
                "severity": "CRITICAL",
                "code": "ratchet_or_stop_exit_due",
                "floor_premium": ratchet.get("floor_premium"),
                "live_current": round(live_current, 2),
            })

        severity = "CRITICAL" if any(i.get("severity") == "CRITICAL" for i in issues) else "WARN" if issues else "PASS"
        rows.append({
            "symbol": symbol,
            "ticker": trade.get("ticker") or ((_parse_occ_symbol(symbol) or {}).get("root")),
            "status": trade.get("status"),
            "entry_premium": round(entry, 2),
            "stored_current_premium": round(stored_current, 2) if stored_current else None,
            "live_current_premium": round(live_current, 2) if live_current else None,
            "stored_unrealized_pct": round(_safe_float(stored_pct), 2) if stored_pct is not None else None,
            "live_unrealized_pct": round(_safe_float(live_pct), 2) if live_pct is not None else None,
            "price_source": context.get("price_source"),
            "snapshot": snap,
            "ratchet": ratchet,
            "issues": issues,
            "severity": severity,
        })

    payload = {
        "ok": not any(r.get("severity") == "CRITICAL" for r in rows),
        "checked_at": _now(),
        "positions_seen": len(position_by_symbol),
        "trades_checked": len(rows),
        "critical": sum(1 for r in rows if r.get("severity") == "CRITICAL"),
        "warnings": sum(1 for r in rows if r.get("severity") == "WARN"),
        "rows": rows,
    }
    if persist:
        await db.options_mark_audits.insert_one(stamped(payload))
        await db.bot_state.update_one({"_id": "options_mark_audit_latest"}, {"$set": payload}, upsert=True)
    return payload


async def latest_mark_audit() -> dict[str, Any]:
    db = get_db()
    row = await db.options_mark_audits.find_one({}, {"_id": 0}, sort=[("checked_at", -1)])
    if row:
        return {"ok": True, **row}
    return {
        "ok": True,
        "checked_at": None,
        "positions_seen": 0,
        "trades_checked": 0,
        "critical": 0,
        "warnings": 0,
        "rows": [],
    }


async def learning_status(limit: int = 200) -> dict[str, Any]:
    db = get_db()
    rows = await db.options_desk_orders.find({}, {"_id": 0}).sort("submitted_at", -1).to_list(limit)
    candidates = await db.options_desk_candidates.find({}, {"_id": 0}).to_list(200)
    return {
        "generated_at": _now(),
        "phase": "paper_data_collection" if rows else "pre_execution",
        "orders": len(rows),
        "ready_candidates": sum(1 for c in candidates if c.get("manual_fire_ready")),
        "route_counts": {
            "EQUITY": sum(1 for c in candidates if c.get("route") == "EQUITY"),
            "OPTION": sum(1 for c in candidates if c.get("route") == "OPTION"),
            "BOTH": sum(1 for c in candidates if c.get("route") == "BOTH"),
            "PASS": sum(1 for c in candidates if c.get("route") == "PASS"),
        },
        "latest_decisions": candidates[:12],
        "recommendations": ["Collect manual-fire paper outcomes before promoting any autonomous options behavior."],
    }


async def backtest(limit_scans: int = 120) -> dict[str, Any]:
    latest = await build_candidates(limit=100, persist=False)
    rows = latest.get("candidates") or []
    risk = sum(float(r.get("risk_budget") or 0) for r in rows if r.get("manual_fire_ready"))
    return {
        "generated_at": _now(),
        "method": "latest_scan_candidate_replay_v1",
        "summary": {
            **latest.get("summary", {}),
            "risk_budget_ready": round(risk, 2),
            "equity_basis": OPTIONS_EQUITY,
            "risk_pct": round((risk / OPTIONS_EQUITY) * 100, 2),
        },
        "sample_rows": rows[:60],
        "note": "Full historical options P&L requires stored option-chain snapshots or executed paper outcomes.",
    }
