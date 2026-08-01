"""Separate Options Desk for a dedicated Alpaca paper options account.

This module deliberately does not import trade_floor.py. It uses only
OPTIONS_* credentials and executes only PM-approved options tickets.
"""
from __future__ import annotations

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
OPTIONS_HARD_STOP_PCT = -20.0
OPTIONS_RATCHET_TIERS: list[tuple[float, float]] = [
    (25.0, 5.0),
    (50.0, 25.0),
    (75.0, 50.0),
    (100.0, 75.0),
    (150.0, 120.0),
    (200.0, 150.0),
]
MIN_OPEN_INTEREST = 50
MIN_VOLUME_WHEN_LOW_OI = 10
MIN_OPTION_VOLUME_IF_OI_UNKNOWN = int(os.environ.get("OPTIONS_MIN_VOLUME_IF_OI_UNKNOWN", "100") or 100)
MAX_SPREAD_ABS = 0.75
MAX_SPREAD_PCT = 0.35
MIN_ABS_DELTA = 0.05
AUTO_MAX_ORDERS_PER_SCAN = int(os.environ.get("OPTIONS_AUTO_MAX_ORDERS_PER_SCAN", "5") or 5)
OPTIONS_ALPACA_REFRESH_LIMIT = int(os.environ.get("OPTIONS_ALPACA_REFRESH_LIMIT", "18") or 18)
OPTIONS_EXECUTION_ENABLED = os.environ.get("ENABLE_OPTIONS_EXECUTION", "false").strip().lower() in {"1", "true", "yes", "on"}
OPTIONS_ALLOW_INDICATIVE_EXECUTION = os.environ.get("OPTIONS_ALLOW_INDICATIVE_EXECUTION", "false").strip().lower() in {"1", "true", "yes", "on"}
OPTIONS_MAX_QUOTE_AGE_SECONDS = int(os.environ.get("OPTIONS_MAX_QUOTE_AGE_SECONDS", "900") or 900)
ALPACA_DATA_BASE = "https://data.alpaca.markets"
ALPACA_OPTIONS_FEED = os.environ.get("OPTIONS_APCA_DATA_FEED", "indicative").strip() or "indicative"
OCC_SYMBOL_RE = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")
ET = ZoneInfo("America/New_York")

KEY = os.environ.get("OPTIONS_APCA_API_KEY_ID", "").strip()
SECRET = os.environ.get("OPTIONS_APCA_API_SECRET_KEY", "").strip()
TRADE_BASE = os.environ.get("OPTIONS_APCA_API_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")
if TRADE_BASE.endswith("/v2"):
    TRADE_BASE = TRADE_BASE[:-3]

HEADERS = {
    "APCA-API-KEY-ID": KEY,
    "APCA-API-SECRET-KEY": SECRET,
    "Content-Type": "application/json",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def configured() -> bool:
    return bool(KEY and SECRET)


def paper_only() -> bool:
    return "paper-api.alpaca.markets" in TRADE_BASE


def options_execution_enabled() -> bool:
    return OPTIONS_EXECUTION_ENABLED


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
        "<b>OPTIONS FILLS</b>",
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
        "<b>OPTIONS RATCHET UPDATE</b>",
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
    title = "OPTIONS HARD STOP" if reason == "hard_stop" else "OPTIONS EXIT"
    reason_label = "Premium hard stop" if reason == "hard_stop" else "Ratchet floor hit"
    msg = "\n".join([
        f"<b>{title}</b>",
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
    if not configured():
        return {"ok": False, "configured": False, "paper_only": paper_only(), "reason": "missing_options_alpaca_keys"}
    try:
        premium_used = await daily_premium_used()
        async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as client:
            r = await client.get(f"{TRADE_BASE}/v2/account")
        if r.status_code != 200:
            return {"ok": False, "configured": True, "paper_only": paper_only(), "reason": f"alpaca_http_{r.status_code}"}
        data = r.json()
        return {
            "ok": True,
            "configured": True,
            "paper_only": paper_only(),
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
        return {"ok": False, "configured": True, "paper_only": paper_only(), "reason": exc.__class__.__name__}


async def positions() -> dict[str, Any]:
    if not configured():
        return {"positions": [], "configured": False}
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as client:
            r = await client.get(f"{TRADE_BASE}/v2/positions")
        return {"positions": r.json() if r.status_code == 200 else [], "configured": True}
    except Exception:
        return {"positions": [], "configured": True}


async def orders(status: str = "all", limit: int = 100) -> dict[str, Any]:
    if not configured():
        return {"orders": [], "configured": False}
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as client:
            r = await client.get(f"{TRADE_BASE}/v2/orders", params={"status": status, "limit": limit})
        return {"orders": r.json() if r.status_code == 200 else [], "configured": True}
    except Exception:
        return {"orders": [], "configured": True}


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
        async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as client:
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


def _option_position_context(position: dict[str, Any], snap: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve open option risk from Alpaca's live position first.

    Alpaca option snapshots can carry stale last trades on illiquid contracts.
    For open-position risk, the position endpoint is the authority because it is
    the same source Alpaca uses for market value and unrealized P/L.
    """
    snap = snap or {}
    qty = _safe_int(position.get("qty"))
    entry = _safe_float(position.get("avg_entry_price"))
    if entry <= 0:
        entry = _safe_float(position.get("cost_basis")) / max(1, qty * 100)

    current = 0.0
    price_source = "unavailable"
    if _position_raw_present(position.get("current_price")):
        current = max(0.0, _safe_float(position.get("current_price")))
        price_source = "alpaca_position_current_price"
    elif _position_raw_present(position.get("market_value")) and qty > 0:
        current = max(0.0, _safe_float(position.get("market_value")) / max(1, qty * 100))
        price_source = "alpaca_position_market_value"
    elif snap.get("ok"):
        bid = _safe_float(snap.get("bid"))
        mid = _safe_float(snap.get("mid"))
        if bid > 0:
            current = bid
            price_source = "alpaca_snapshot_bid"
        elif mid > 0:
            current = mid
            price_source = "alpaca_snapshot_mid"

    pnl_pct: float | None
    if _position_raw_present(position.get("unrealized_plpc")):
        pnl_pct = _safe_float(position.get("unrealized_plpc")) * 100.0
    elif entry > 0:
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
            if _open_interest_is_too_low(instrument):
                _add_block(blocked, "open interest too low")
            if _delta_is_too_low(instrument, row.get("strategy")):
                _add_block(blocked, "delta too low")
    elif route == "PASS":
        _add_block(blocked, "PM route is PASS")
    else:
        _add_block(blocked, "PM route is EQUITY")

    row["quality_state"] = quality_state
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
    cap = min(OPTIONS_EQUITY * MAX_RISK_PCT, MAX_RISK_USD)
    if route == "BOTH":
        return min(BOTH_RISK_USD, cap)
    if action == "ACCUMULATE":
        return min(ACCUMULATE_RISK_USD, cap)
    if action == "WATCH":
        return min(WATCH_RISK_USD, cap)
    if action == "STARTER":
        return min(STARTER_RISK_USD, cap)
    if score >= 78:
        return cap
    return min(STANDARD_RISK_USD, cap)


def _spread_is_too_wide(instrument: dict[str, Any]) -> bool:
    bid = float(instrument.get("bid") or 0)
    ask = float(instrument.get("ask") or instrument.get("premium") or 0)
    if bid <= 0 or ask <= 0:
        return True
    spread = float(instrument.get("spread") or 0)
    premium = ask
    spread_pct = spread / premium if premium > 0 else 1.0
    return spread > MAX_SPREAD_ABS or spread_pct > MAX_SPREAD_PCT


def _open_interest_is_too_low(instrument: dict[str, Any]) -> bool:
    if instrument.get("open_interest_source") == "unavailable" and instrument.get("data_provider") == "ALPACA_OPTIONS":
        return int(instrument.get("volume") or 0) < MIN_OPTION_VOLUME_IF_OI_UNKNOWN
    oi = int(instrument.get("open_interest") or 0)
    volume = int(instrument.get("volume") or 0)
    return oi < MIN_OPEN_INTEREST and volume < MIN_VOLUME_WHEN_LOW_OI


def _delta_is_too_low(instrument: dict[str, Any], strategy: str | None) -> bool:
    if strategy == "LOTTERY_CALL":
        return False
    delta = abs(float(instrument.get("delta") or 0))
    return delta > 0 and delta < MIN_ABS_DELTA


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
        risk_budget = _risk_budget(route, pm_row.get("action"), score)
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
                if _open_interest_is_too_low(instrument):
                    blocked.append("open interest too low")
                if _delta_is_too_low(instrument, opts.get("strategy")):
                    blocked.append("delta too low")
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
                "max_risk_pct": MAX_RISK_PCT,
                "max_risk_usd": MAX_RISK_USD,
                "watch_risk_usd": WATCH_RISK_USD,
                "starter_risk_usd": STARTER_RISK_USD,
                "accumulate_risk_usd": ACCUMULATE_RISK_USD,
                "both_risk_usd": BOTH_RISK_USD,
                "standard_risk_usd": STANDARD_RISK_USD,
                "daily_premium_cap_usd": OPTIONS_DAILY_PREMIUM_CAP_USD,
                "min_open_interest": MIN_OPEN_INTEREST,
                "min_volume_when_low_oi": MIN_VOLUME_WHEN_LOW_OI,
                "max_spread_abs": MAX_SPREAD_ABS,
                "max_spread_pct": MAX_SPREAD_PCT,
                "min_abs_delta": MIN_ABS_DELTA,
                "alpaca_refresh_limit": OPTIONS_ALPACA_REFRESH_LIMIT,
                "initial_stop_pct": OPTIONS_INITIAL_STOP_PCT,
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
    if _open_interest_is_too_low(fresh):
        return {"ok": False, "reason": "fresh_open_interest_or_volume_too_low", "instrument": fresh, "snapshot": snap}
    if _delta_is_too_low(fresh, ticket.get("strategy")):
        return {"ok": False, "reason": "fresh_delta_too_low", "instrument": fresh, "snapshot": snap}
    return {"ok": True, "symbol": str(symbol).upper(), "instrument": fresh, "snapshot": snap, "quote_age_seconds": quote_age}


async def execute(candidate_id: str, qty: int | None = None, limit_price: float | None = None) -> dict[str, Any]:
    db = get_db()
    ticket = await db.options_desk_candidates.find_one({"candidate_id": candidate_id}, {"_id": 0})
    if not ticket:
        return {"ok": False, "reason": "candidate_not_found"}
    if not configured():
        return {"ok": False, "reason": "missing_options_alpaca_keys", "candidate": ticket}
    if not OPTIONS_EXECUTION_ENABLED:
        return {"ok": False, "reason": "options_execution_disabled", "candidate": ticket}
    if not paper_only():
        return {"ok": False, "reason": "refusing_non_paper_options_account", "candidate": ticket}
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
    payload = {
        "symbol": symbol,
        "qty": str(order_qty),
        "side": "buy",
        "type": "limit",
        "time_in_force": "day",
        "limit_price": round(order_limit, 2),
    }
    async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as client:
        r = await client.post(f"{TRADE_BASE}/v2/orders", json=payload)
    if r.status_code not in (200, 201):
        return {"ok": False, "reason": f"alpaca_rejected_{r.status_code}", "detail": r.text[:220], "candidate": ticket}
    order = r.json()
    exit_policy = options_ratchet_state(entry_premium=order_limit, peak_premium=order_limit)
    record = stamped({
        "candidate": ticket,
        "order": order,
        "submitted_at": _now(),
        "status": "submitted",
        "exit_policy": exit_policy,
        "fresh_preflight": preflight,
    })
    await db.options_desk_orders.insert_one(record)
    return {"ok": True, "order": order, "candidate": ticket, "exit_policy": exit_policy}


async def auto_execute_latest(limit: int | None = None) -> dict[str, Any]:
    """PM-controlled automated options execution.

    The Options Desk does not re-decide the trade. It submits PM-ready tickets
    and enforces only mechanical paper execution constraints already present in
    execute(): risk budget, valid order fields, and Alpaca acceptance.
    """
    if not OPTIONS_EXECUTION_ENABLED:
        return {
            "ok": False,
            "auto": True,
            "reason": "options_execution_disabled",
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
    submitted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
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
    return {
        "ok": True,
        "auto": True,
        "ready": len(ready),
        "submitted": submitted,
        "skipped": skipped,
        "summary": candidate_set.get("summary", {}),
    }


async def refresh_and_auto_execute_latest(limit: int | None = None) -> dict[str, Any]:
    fill = await sync_fills()
    risk = await monitor_open_positions(enforce_hard_stop=True)
    await build_candidates(limit=100, persist=True)
    result = await auto_execute_latest(limit=limit)
    result["pre_execution_fill_sync"] = fill
    result["pre_execution_risk_check"] = risk
    return result


async def close(symbol: str, qty: int | None = None) -> dict[str, Any]:
    if not configured():
        return {"ok": False, "reason": "missing_options_alpaca_keys"}
    payload: dict[str, Any] = {"symbol": symbol, "side": "sell", "type": "market", "time_in_force": "day"}
    if qty:
        payload["qty"] = str(int(qty))
    async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as client:
        r = await client.post(f"{TRADE_BASE}/v2/orders", json=payload)
    if r.status_code not in (200, 201):
        return {"ok": False, "reason": f"alpaca_rejected_{r.status_code}", "detail": r.text[:220]}
    return {"ok": True, "order": r.json()}


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
            prior_peak = _safe_float(((existing or {}).get("exit_policy") or {}).get("peak_premium")) or fill_price
            current = 0.0
            snap = await _option_snapshot(symbol) if symbol in position_symbols else {"ok": False}
            price_context = {}
            if symbol in position_by_symbol:
                price_context = _option_position_context(position_by_symbol[symbol], snap)
                current = _safe_float(price_context.get("current"))
            elif snap.get("ok"):
                current = _safe_float(snap.get("bid")) or _safe_float(snap.get("mid"))
                price_context = {
                    "price_source": "alpaca_snapshot_bid" if _safe_float(snap.get("bid")) > 0 else "alpaca_snapshot_mid",
                    "snapshot_bid": _safe_float(snap.get("bid")),
                    "snapshot_mid": _safe_float(snap.get("mid")),
                    "snapshot_last": _safe_float(snap.get("last")),
                    "snapshot_mark": _safe_float(snap.get("mark")),
                    "data_conflict": False,
                }
            pnl_pct = price_context.get("pnl_pct") if price_context else None
            peak_basis = max(fill_price, current)
            if pnl_pct is None or _safe_float(pnl_pct) > 0:
                peak_basis = max(prior_peak, current, fill_price)
            exit_policy = options_ratchet_state(
                entry_premium=fill_price,
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
                "entry_premium": round(fill_price, 2),
                "entry_notional": round(fill_price * filled_qty * 100, 2),
                "entry_filled_at": order.get("filled_at") or order.get("updated_at"),
                "entry_order": order,
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
                {"symbol": symbol, "status": {"$in": ["active", "hard_stop_close_submitted", "ratchet_close_submitted", "flat_no_position"]}},
                {"_id": 1, "entry_premium": 1, "qty": 1},
            ).to_list(20)
            for trade in open_trades:
                entry = _safe_float(trade.get("entry_premium"))
                qty_for_pnl = min(filled_qty, _safe_int(trade.get("qty")) or filled_qty)
                realized = round((fill_price - entry) * qty_for_pnl * 100, 2) if entry > 0 else None
                realized_pct = round((fill_price - entry) / entry * 100.0, 2) if entry > 0 else None
                await db.options_desk_trades.update_one(
                    {"_id": trade["_id"]},
                    {"$set": {
                    "status": "closed",
                    "exit_order_id": order.get("id"),
                    "exit_premium": round(fill_price, 2),
                    "exit_filled_at": order.get("filled_at") or order.get("updated_at"),
                    "exit_order": order,
                    "closed_at": _now(),
                    "realized_pnl": realized,
                    "realized_pct": realized_pct,
                }},
                )
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
    active = [r for r in rows if r.get("status") in {"active", "hard_stop_close_submitted", "ratchet_close_submitted"}]
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
    db = get_db()
    rows = await db.options_desk_trades.find({}, {"_id": 0}).sort("last_synced_at", -1).to_list(1000)
    week_start, week_end = _week_window_et()
    active = [r for r in rows if r.get("status") in {"active", "hard_stop_close_submitted", "ratchet_close_submitted"}]
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
        "<b>CASE CAPITAL OPTIONS FUND REPORT</b>",
        "",
        f"Date: <b>{today}</b>",
        f"Open contracts: <b>{payload['active_count']}</b>",
        f"Closed today: <b>{payload['closed_today_count']}</b>",
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
        "<b>CASE CAPITAL OPTIONS WEEKLY REPORT</b>",
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
    """Check open option positions for theta and hard-stop exits.

    Theta is recorded for PM learning. The only forced exit in V1 is the hard
    premium stop: current option premium <= entry premium * 0.80.
    """
    db = get_db()
    pos = await positions()
    checks: list[dict[str, Any]] = []
    closed: list[dict[str, Any]] = []
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
            {"symbol": symbol, "status": {"$in": ["active", "flat_no_position"]}},
            {"_id": 1, "ticker": 1, "entry_premium": 1, "exit_policy": 1, "telegram": 1},
            sort=[("last_synced_at", -1)],
        )
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
        hard_stop = bool(pnl_pct is not None and _safe_float(pnl_pct) <= OPTIONS_HARD_STOP_PCT)
        check = {
            "symbol": symbol,
            "qty": qty,
            "entry_premium": round(entry, 2),
            "current_premium": round(current, 2),
            "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
            "theta": round(float(theta), 4) if theta is not None else None,
            "theta_pct_of_premium": round(theta_pct, 2) if theta_pct is not None else None,
            "theta_status": "WATCH" if theta_pct is not None and theta_pct <= -8.0 else "OK",
            "hard_stop_pct": OPTIONS_HARD_STOP_PCT,
            "hard_stop_triggered": hard_stop,
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
        ticker = (trade_doc or {}).get("ticker") or (_parse_occ_symbol(symbol) or {}).get("root") or symbol
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
            {"symbol": symbol, "status": {"$in": ["active", "flat_no_position"]}},
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
        ratchet_exit = bool(not hard_stop and ratchet.get("exit_triggered") and current_floor > OPTIONS_INITIAL_STOP_PCT)
        exit_reason = "hard_stop" if hard_stop else "ratchet" if ratchet_exit else None
        if exit_reason and enforce_hard_stop:
            result = await close(symbol=symbol, qty=qty)
            check["close_result"] = result
            if result.get("ok"):
                close_reason = "options_hard_stop_20pct" if exit_reason == "hard_stop" else "options_ratchet_floor"
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
        "errors": errors,
        "checks": checks,
    }))
    if closed:
        await log_activity(f"Options risk monitor closed {len(closed)} hard-stop position(s)", "warn", {"closed": closed})
    return {"ok": True, "positions_checked": len(checks), "closed": closed, "errors": errors, "checks": checks}


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
