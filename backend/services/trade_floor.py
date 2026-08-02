"""Trade Floor — autonomous paper-trading system on Alpaca.

Operates fully independently from the main scan display. The scan finds
opportunities; the Trade Floor decides whether to act and executes via
Alpaca paper API. All learning happens in `trade_floor_learning.py`.

Execution Gates (ALL must pass simultaneously):
  • Trade Score > 20
  • ≥2 distinct signal types firing
  • Regime gate clear unless DEFCON/unknown. RED and DOWNTREND do not
    blindly halt; they force stricter PM modes and regime-specific tags.
  • Pre-earnings prediction is not an execution override. PEAD-style rows
    can flow after the print, but binary pre-print entries stay guarded.
  • < 10 open positions
  • Ticker NOT already in Alpaca open positions OR open orders
    (checked twice: at gate AND immediately before submission)

Orders are ALL **limit DAY orders** at the current ask price (the
Trade Floor Learning Engine will adapt the entry-price logic over
time). Unfilled orders auto-cancel after 24 hours via
`cancel_stale_orders()` and the ticker only gets another order when it
appears in a future scan as a fresh signal.

Stops are produced by `stop_engine.compute_stop(...)` — analytical,
learnable, NO ATR, NO yfinance.

Hard absolute risk caps (NEVER exceeded):
  Fractional: 20-24=$10 · 25-29=$20 · 30-49=$30 · 50+=$50
  Options:    20-24=$50 · 25-29=$70 · 30-49=$80 · 50+=$100
"""
from __future__ import annotations
import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from . import stop_engine
from .db import get_db, log_activity, stamped

logger = logging.getLogger(__name__)

ALPACA_KEY = os.environ.get("APCA_API_KEY_ID", "").strip()
ALPACA_SECRET = os.environ.get("APCA_API_SECRET_KEY", "").strip()
ALPACA_TRADE_BASE = os.environ.get(
    "APCA_API_BASE_URL", "https://paper-api.alpaca.markets",
).rstrip("/")
if ALPACA_TRADE_BASE.endswith("/v2"):
    ALPACA_TRADE_BASE = ALPACA_TRADE_BASE[:-3]
ALPACA_DATA_BASE = "https://data.alpaca.markets/v2"

MAX_OPEN_POSITIONS = 10
VIX_RED_THRESHOLD = 25.0
VIX_DOOMSDAY_THRESHOLD = 40.0
SPY_INTRADAY_DOOMSDAY_DROP_PCT = -4.0
TRADE_SCORE_MIN = 20
HEADERS = {
    "APCA-API-KEY-ID": ALPACA_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET,
    "Content-Type": "application/json",
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_et() -> datetime:
    return datetime.now(ZoneInfo("America/New_York"))


def _regular_equity_session_open(now: datetime | None = None) -> bool:
    now = now or _now_et()
    if now.weekday() > 4:
        return False
    minutes = now.hour * 60 + now.minute
    return (9 * 60 + 30) <= minutes < (16 * 60)


def _equity_24h_session_open(now: datetime | None = None) -> bool:
    """Alpaca 24/5 equities window for extended-hours limit orders.

    Equities can route outside regular hours only as limit orders with
    extended_hours=true. Options remain regular-session only in options_desk.
    """
    now = now or _now_et()
    minutes = now.hour * 60 + now.minute
    if now.weekday() == 6:
        return minutes >= 20 * 60
    if 0 <= now.weekday() <= 3:
        return True
    if now.weekday() == 4:
        return minutes < 20 * 60
    return False


def equity_order_session(now: datetime | None = None) -> dict[str, Any]:
    now = now or _now_et()
    regular = _regular_equity_session_open(now)
    extended = (not regular) and _equity_24h_session_open(now)
    return {
        "regular_open": regular,
        "extended_24h_open": extended,
        "tradable_now": regular or extended,
        "extended_hours": extended,
        "order_type": "limit",
        "time_in_force": "day",
        "checked_at": now.isoformat(),
    }


def _alpaca_ready() -> bool:
    return bool(ALPACA_KEY and ALPACA_SECRET)


def paper_only() -> bool:
    return "paper-api.alpaca.markets" in ALPACA_TRADE_BASE


# ─────── Alpaca thin client ───────
async def get_account() -> dict[str, Any] | None:
    if not _alpaca_ready():
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as c:
            r = await c.get(f"{ALPACA_TRADE_BASE}/v2/account")
            if r.status_code != 200:
                return None
            return r.json()
    except Exception as e:
        logger.warning("alpaca account: %s", e)
        return None


async def list_positions() -> list[dict[str, Any]]:
    if not _alpaca_ready():
        return []
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as c:
            r = await c.get(f"{ALPACA_TRADE_BASE}/v2/positions")
            return r.json() if r.status_code == 200 else []
    except Exception:
        return []


async def list_orders(status: str = "all", limit: int = 100) -> list[dict[str, Any]]:
    if not _alpaca_ready():
        return []
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as c:
            r = await c.get(f"{ALPACA_TRADE_BASE}/v2/orders",
                              params={"status": status, "limit": limit})
            return r.json() if r.status_code == 200 else []
    except Exception:
        return []


async def active_queued_equity_orders(limit: int = 200) -> list[dict[str, Any]]:
    db = get_db()
    return await db.tf_queued_orders.find(
        {"status": {"$in": ["QUEUED", "SUBMIT_FAILED_RETRYABLE"]}},
        {"_id": 0},
    ).sort("queued_at", 1).to_list(limit)


async def _queue_fractional_limit_buy(
    ticker: str,
    notional: float,
    limit_price: float,
    *,
    client_order_id: str | None,
    session: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    db = get_db()
    ticker = ticker.upper()
    client_order_id = client_order_id or f"tf-{ticker}-{int(_now().timestamp())}"
    now = _now().isoformat()
    doc = {
        "ticker": ticker,
        "symbol": ticker,
        "notional": round(notional, 2),
        "limit_price": round(limit_price, 4),
        "client_order_id": client_order_id,
        "side": "buy",
        "type": "limit",
        "time_in_force": "day",
        "session": session,
        "reason": reason,
        "queued_at": now,
        "updated_at": now,
        "status": "QUEUED",
    }
    await db.tf_queued_orders.update_one(
        {"ticker": ticker, "status": {"$in": ["QUEUED", "SUBMIT_FAILED_RETRYABLE"]}},
        {"$setOnInsert": stamped(doc), "$set": {"updated_at": now, "session": session, "reason": reason}},
        upsert=True,
    )
    await log_activity(
        f"Trade Floor queued equity order for next 24/5 session: {ticker}",
        "info",
        {"ticker": ticker, "notional": notional, "limit_price": limit_price, "reason": reason, "session": session},
    )
    return {
        "id": f"queued-{client_order_id}",
        "symbol": ticker,
        "status": "queued_for_next_equity_session",
        "client_order_id": client_order_id,
        "limit_price": round(limit_price, 4),
        "notional": round(notional, 2),
        "_case_session": session,
        "_case_queued": True,
    }


async def _submit_fractional_limit_buy_now(
    ticker: str,
    notional: float,
    limit_price: float,
    *,
    client_order_id: str | None,
    session: dict[str, Any],
) -> dict[str, Any] | None:
    payload: dict[str, Any] = {
        "symbol": ticker.upper(),
        "notional": round(notional, 2),
        "side": "buy",
        "type": "limit",
        "time_in_force": "day",
        "limit_price": round(limit_price, 4),
    }
    if session["extended_hours"]:
        payload["extended_hours"] = True
    if client_order_id:
        payload["client_order_id"] = client_order_id
    async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as c:
        r = await c.post(f"{ALPACA_TRADE_BASE}/v2/orders", json=payload)
        if r.status_code in (200, 201):
            order = r.json()
            order["_case_session"] = session
            return order
        logger.warning("alpaca limit buy %s: %s %s", ticker, r.status_code, r.text[:200])
    return None


async def submit_fractional_limit_buy(ticker: str, notional: float, limit_price: float,
                                          client_order_id: str | None = None) -> dict[str, Any] | None:
    """Limit DAY order for fractional notional. NEVER market."""
    if not _alpaca_ready() or notional <= 0 or limit_price <= 0:
        return None
    try:
        from . import safety
        enabled, status = await safety.trading_enabled(scope="equity_submit")
        if not enabled:
            await log_activity(
                f"Trade Floor submit blocked by safety halt: {ticker}",
                "warn",
                {"ticker": ticker, "safety": status},
            )
            return None
    except Exception:
        return None
    if not paper_only() and os.environ.get("ALLOW_LIVE_EQUITY_EXECUTION", "").strip().lower() not in {"1", "true", "yes", "on", "explicit-yes-i-mean-it"}:
        await log_activity("Trade Floor submit blocked: non-paper equity account", "warn", {"ticker": ticker, "base": ALPACA_TRADE_BASE})
        return None
    session = equity_order_session()
    if not session["tradable_now"]:
        return await _queue_fractional_limit_buy(
            ticker,
            notional,
            limit_price,
            client_order_id=client_order_id,
            session=session,
            reason="outside_alpaca_equity_24h_window",
        )
    try:
        return await _submit_fractional_limit_buy_now(
            ticker,
            notional,
            limit_price,
            client_order_id=client_order_id,
            session=session,
        )
    except Exception as e:
        logger.warning("alpaca limit buy exception %s: %s", ticker, e)
    return None


async def execution_probe(ticker: str = "AAPL", notional: float = 1.0, place_order: bool = False) -> dict[str, Any]:
    """Account/quote/order smoke test for Alpaca.

    The order path is paper-only and capped. It is intentionally separate from
    PM/Trade Floor execution so diagnostics do not contaminate PM learning.
    """
    ticker = (ticker or "AAPL").upper()
    notional = max(0.01, min(float(notional or 1.0), 5.0))
    result: dict[str, Any] = {
        "ok": False,
        "ticker": ticker,
        "notional": notional,
        "place_order": place_order,
        "base_url": ALPACA_TRADE_BASE,
        "paper_only": "paper-api.alpaca.markets" in ALPACA_TRADE_BASE,
        "account_ok": False,
        "quote_ok": False,
        "order_ok": False,
        "reason": None,
    }
    if not _alpaca_ready():
        result["reason"] = "missing_alpaca_key_or_secret"
        return result
    account = await get_account()
    if not account:
        result["reason"] = "alpaca_account_unauthorized_or_unreachable"
        return result
    result["account_ok"] = True
    result["account"] = {
        "status": account.get("status"),
        "equity": account.get("equity"),
        "cash": account.get("cash"),
        "buying_power": account.get("buying_power"),
        "trading_blocked": account.get("trading_blocked"),
    }
    ask = await get_latest_ask(ticker)
    if not ask:
        result["reason"] = "alpaca_quote_unavailable"
        return result
    result["quote_ok"] = True
    result["ask"] = ask
    if not place_order:
        result["ok"] = True
        result["reason"] = "dry_run_ok"
        return result
    if not result["paper_only"]:
        result["reason"] = "refusing_test_order_on_non_paper_base_url"
        return result
    cli_id = f"tf-probe-{ticker}-{int(_now().timestamp())}"
    order = await submit_fractional_limit_buy(ticker, notional, round(float(ask), 4), client_order_id=cli_id)
    if not order:
        result["reason"] = "alpaca_test_order_rejected"
        return result
    result["ok"] = True
    result["order_ok"] = True
    result["reason"] = "paper_test_order_submitted"
    result["order"] = {
        "id": order.get("id"),
        "client_order_id": order.get("client_order_id"),
        "symbol": order.get("symbol"),
        "status": order.get("status"),
        "submitted_at": order.get("submitted_at"),
        "limit_price": order.get("limit_price"),
        "notional": order.get("notional"),
    }
    try:
        await get_db().tf_execution_tests.insert_one(stamped(result))
    except Exception:
        pass
    return result


async def flush_queued_equity_orders(limit: int = 25) -> dict[str, Any]:
    """Submit PM-approved queued equity intents when Alpaca can accept them."""
    session = equity_order_session()
    if not session["tradable_now"]:
        return {"ok": True, "submitted": [], "skipped": [], "session": session, "reason": "equity_session_closed"}
    from . import safety

    enabled, safety_status = await safety.trading_enabled(scope="equity_queue")
    if not enabled:
        return {"ok": False, "submitted": [], "skipped": [], "session": session, "reason": "safety_halt", "safety": safety_status}

    db = get_db()
    rows = await active_queued_equity_orders(limit=limit)
    submitted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    try:
        live_positions = await list_positions()
        live_orders = await list_orders(status="open")
        held = {p.get("symbol", "").upper() for p in live_positions}
        pending = {o.get("symbol", "").upper() for o in live_orders}
    except Exception:
        held, pending = set(), set()

    for row in rows:
        ticker = (row.get("ticker") or row.get("symbol") or "").upper()
        if not ticker:
            continue
        if ticker in held or ticker in pending:
            await db.tf_queued_orders.update_one(
                {"client_order_id": row.get("client_order_id")},
                {"$set": {"status": "CANCELLED_DUPLICATE", "updated_at": _now().isoformat(), "duplicate_reason": "held_or_pending_in_alpaca"}},
            )
            skipped.append({"ticker": ticker, "reason": "held_or_pending_in_alpaca"})
            continue
        try:
            order = await _submit_fractional_limit_buy_now(
                ticker,
                float(row.get("notional") or 0),
                float(row.get("limit_price") or 0),
                client_order_id=row.get("client_order_id"),
                session=session,
            )
        except Exception as exc:
            order = None
            skipped.append({"ticker": ticker, "reason": exc.__class__.__name__})
        if order:
            now = _now().isoformat()
            await db.tf_queued_orders.update_one(
                {"client_order_id": row.get("client_order_id")},
                {"$set": {"status": "SUBMITTED", "submitted_at": now, "updated_at": now, "order": order, "order_id": order.get("id"), "submit_session": session}},
            )
            await db.tf_trades.update_one(
                {"client_order_id": row.get("client_order_id")},
                {"$set": {"status": "OPEN", "fill_status": "PENDING", "order_id": order.get("id"), "submitted_at": now, "order_session": session}},
            )
            pending.add(ticker)
            submitted.append({"ticker": ticker, "order_id": order.get("id"), "limit_price": row.get("limit_price")})
        else:
            await db.tf_queued_orders.update_one(
                {"client_order_id": row.get("client_order_id")},
                {"$set": {"status": "SUBMIT_FAILED_RETRYABLE", "updated_at": _now().isoformat(), "last_failure": skipped[-1] if skipped else {"reason": "alpaca_rejected"}}},
            )
    if submitted or skipped:
        await log_activity(
            f"Trade Floor queue flush: {len(submitted)} submitted / {len(skipped)} skipped",
            "success" if submitted else "info",
            {"submitted": submitted, "skipped": skipped[:12], "session": session},
        )
    return {"ok": True, "submitted": submitted, "skipped": skipped, "session": session}


# Backwards-compatible alias used by legacy code paths
submit_fractional_buy = submit_fractional_limit_buy


async def get_latest_ask_meta(ticker: str) -> dict[str, Any] | None:
    """Sole source for executable limit-price metadata: Alpaca latest quote."""
    if not _alpaca_ready():
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0, headers={
            "APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET,
        }) as c:
            r = await c.get(f"{ALPACA_DATA_BASE}/stocks/{ticker.upper()}/quotes/latest",
                              params={"feed": "iex"})
            if r.status_code != 200:
                return None
            q = (r.json() or {}).get("quote") or {}
            ask = float(q.get("ap") or 0)
            if ask <= 0:
                return None
            try:
                from . import safety
                age_s = safety.quote_age_seconds(q.get("t"))
            except Exception:
                age_s = None
            return {
                "price": ask,
                "ts": q.get("t"),
                "age_s": age_s,
                "source": "alpaca_iex_latest_quote",
                "bid": float(q.get("bp") or 0),
                "ask": ask,
                "raw": q,
            }
    except Exception:
        return None


async def get_latest_ask(ticker: str) -> float | None:
    meta = await get_latest_ask_meta(ticker)
    return float(meta["price"]) if meta and meta.get("price") else None


async def cancel_order(order_id: str) -> bool:
    """Cancel a single Alpaca order by id."""
    if not _alpaca_ready() or not order_id:
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0, headers=HEADERS) as c:
            r = await c.delete(f"{ALPACA_TRADE_BASE}/v2/orders/{order_id}")
            return r.status_code in (200, 204, 207)
    except Exception:
        return False


async def cancel_stale_orders(max_age_hours: int = 24) -> dict[str, Any]:
    """Cancel any TF buy order that has been unfilled for > max_age_hours.
    Logs each cancellation into `tf_unfilled_log` so the ticker is marked
    'never re-attempted until a fresh signal appears'."""
    if not _alpaca_ready():
        return {"checked": 0, "cancelled": 0}
    db = get_db()
    open_orders = await list_orders(status="open", limit=200)
    now = _now()
    cancelled: list[dict[str, Any]] = []
    for o in open_orders:
        try:
            cli = o.get("client_order_id") or ""
            if not cli.startswith("tf-"):
                continue
            submitted = o.get("submitted_at") or o.get("created_at")
            if not submitted:
                continue
            ts = datetime.fromisoformat(submitted.replace("Z", "+00:00"))
            if (now - ts).total_seconds() < max_age_hours * 3600:
                continue
            ok = await cancel_order(o.get("id"))
            if not ok:
                continue
            await db.tf_trades.update_one(
                {"client_order_id": cli},
                {"$set": {
                    "status": "UNFILLED_CANCELLED",
                    "fill_status": "UNFILLED",
                    "cancelled_at": now.isoformat(),
                    "cancel_reason": f"day_order_expired_after_{max_age_hours}h",
                }},
            )
            await db.tf_unfilled_log.insert_one(stamped({
                "ticker": o.get("symbol"),
                "client_order_id": cli,
                "alpaca_order_id": o.get("id"),
                "submitted_at": submitted,
                "cancelled_at": now.isoformat(),
                "limit_price": float(o.get("limit_price") or 0),
                "notional": float(o.get("notional") or 0),
                "age_hours": round((now - ts).total_seconds() / 3600, 2),
            }))
            cancelled.append({"ticker": o.get("symbol"), "order_id": o.get("id")})
        except Exception as e:
            logger.warning("cancel_stale_orders: %s", e)
            continue
    if cancelled:
        await log_activity(
            f"Trade Floor: cancelled {len(cancelled)} stale order(s) "
            f"(>{max_age_hours}h unfilled).", "info")
    return {"checked": len(open_orders), "cancelled": len(cancelled),
             "details": cancelled}


async def close_position(ticker: str) -> dict[str, Any] | None:
    if not _alpaca_ready():
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as c:
            r = await c.delete(f"{ALPACA_TRADE_BASE}/v2/positions/{ticker.upper()}")
            if r.status_code in (200, 207):
                return r.json()
    except Exception as e:
        logger.warning("close %s: %s", ticker, e)
    return None


# ─────── Regime gate ───────
async def regime_status() -> dict[str, Any]:
    """Four-weather market snapshot.

    GREEN: SPY above 200d EMA and VIX below 25.
    DOWNTREND: SPY below 200d EMA while volatility is not a shock.
    RED: VIX shock. This dominates downtrend.
    DOOMSDAY: crash/data-failure posture. This is the only automatic
    hard-halt from this regime function.
    """
    import yfinance as yf

    def _yf_calc():
        try:
            spy_hist = yf.Ticker("SPY").history(period="220d")
            spy = spy_hist["Close"]
            ema200 = spy.ewm(span=200, adjust=False).mean()
            spy_last = float(spy.iloc[-1])
            spy_prev = float(spy.iloc[-2]) if len(spy) >= 2 else spy_last
            spy_ema = float(ema200.iloc[-1])
            vix = float(yf.Ticker("^VIX").history(period="1d")["Close"].iloc[-1])
            spy_day_change_pct = ((spy_last - spy_prev) / spy_prev * 100.0) if spy_prev else 0.0
            return spy_last, spy_ema, vix, spy_day_change_pct
        except Exception:
            return None, None, None, None
    loop = asyncio.get_event_loop()
    spy_last, spy_ema, vix, spy_day_change_pct = await loop.run_in_executor(None, _yf_calc)
    if vix is None or spy_last is None:
        return {
            "status": "unknown",
            "weather": "UNKNOWN",
            "vix": None,
            "spy_last": None,
            "spy_ema200": None,
            "spy_day_change_pct": None,
            "halt_new_entries": True,
            "reason": "regime_data_unavailable",
            "playbook": "DATA_FAIL_SAFE",
            "source": "yfinance_degraded_failed",
            "checked_at": _now().isoformat(),
        }

    below_ema = spy_last < spy_ema
    doomsday = vix >= VIX_DOOMSDAY_THRESHOLD or spy_day_change_pct <= SPY_INTRADAY_DOOMSDAY_DROP_PCT
    if doomsday:
        status = "doomsday"
        playbook = "FREEZE_AND_TRIAGE"
        halt = True
        reason = "doomsday_trigger"
    elif vix >= VIX_RED_THRESHOLD:
        status = "red"
        playbook = "VOL_SHOCK_HALF_SIZE"
        halt = False
        reason = "volatility_shock"
    elif below_ema:
        status = "downtrend"
        playbook = "GRIND_RAISED_LONG_BAR"
        halt = False
        reason = "spy_below_200d_ema"
    else:
        status = "green"
        playbook = "RISK_ON"
        halt = False
        reason = "risk_on"
    return {
        "status": status,
        "weather": status.upper(),
        "vix": round(vix, 2),
        "spy_last": round(spy_last, 2),
        "spy_ema200": round(spy_ema, 2),
        "spy_day_change_pct": round(spy_day_change_pct, 2),
        "spy_below_200d": bool(below_ema),
        "halt_new_entries": bool(halt),
        "reason": reason,
        "playbook": playbook,
        "checked_at": _now().isoformat(),
    }


# ─────── Risk tier table ───────
DEFAULT_RISK_TIERS = {
    "fractional": {(20, 25): 0.01, (25, 30): 0.02, (30, 50): 0.03, (50, 999): 0.05},
    "options":    {(20, 25): 0.05, (25, 30): 0.07, (30, 50): 0.08, (50, 999): 0.10},
}

# ─────── HARD ABSOLUTE risk caps (per-trade dollar ceiling — never exceeded) ───────
FRACTIONAL_HARD_CAPS = [(20, 25, 10.0), (25, 30, 20.0), (30, 50, 30.0), (50, 999, 50.0)]
OPTIONS_HARD_CAPS    = [(20, 25, 50.0), (25, 30, 70.0), (30, 50, 80.0), (50, 999, 100.0)]


def hard_cap_for(score: float, instrument: str) -> float:
    """Absolute dollar cap for a single Trade Floor trade. The position
    sizer reduces any larger calculated notional down to this cap. There
    are no exceptions and no overrides."""
    table = OPTIONS_HARD_CAPS if instrument == "options" else FRACTIONAL_HARD_CAPS
    for lo, hi, cap in table:
        if lo <= score < hi:
            return cap
    return table[0][2]


async def _risk_pct(score: float, instrument: str) -> float:
    """Lookup from learning-engine-managed table, default to baseline."""
    db = get_db()
    tier_doc = await db.tf_risk_tiers.find_one({"_id": "current"})
    table = tier_doc.get("tiers") if tier_doc else None
    if not table:
        table = {k: {f"{rng[0]}-{rng[1]}": v for rng, v in tiers.items()}
                  for k, tiers in DEFAULT_RISK_TIERS.items()}
    band = table.get(instrument) or {}
    for k, pct in band.items():
        try:
            if isinstance(k, str) and "-" in k:
                lo_s, hi_s = k.split("-", 1)
                lo, hi = int(lo_s), int(hi_s)
            elif isinstance(k, (list, tuple)):
                lo, hi = int(k[0]), int(k[1])
            else:
                continue
        except Exception:
            continue
        if lo <= score < hi:
            return float(pct)
    return 0.01


# ─────── Stop engine ───────
# Stops are produced by services.stop_engine.compute_stop(...). No ATR.
# No yfinance for volatility. Alpaca is the sole price/volatility source.


# ─────── Execution gates ───────
async def _gate_check(scan_row: dict[str, Any], *,
                       held_tickers: set[str] | None = None,
                       pending_tickers: set[str] | None = None,
                       position_count: int | None = None,
                       regime: dict[str, Any] | None = None,
                       pm_managed: bool = False) -> tuple[bool, str | None]:
    """Returns (passed, rejection_reason). Optional kwargs let callers
    pass pre-fetched Alpaca state to avoid hitting the API per-row."""
    ticker = (scan_row.get("ticker") or "").upper()
    trade_score = scan_row.get("trade_score") or scan_row.get("score") or 0
    if not pm_managed and trade_score < TRADE_SCORE_MIN:
        return False, f"trade_score {trade_score:.1f} < {TRADE_SCORE_MIN}"
    signals = scan_row.get("signals") or {}
    if not pm_managed and len(signals) < 2:
        return False, f"only {len(signals)} signal type(s) firing"
    if regime is None:
        regime = await regime_status()
    if regime.get("halt_new_entries"):
        return False, f"regime halt ({regime.get('status')}: {regime.get('reason')})"
    # Earnings within 10d gate
    earnings = scan_row.get("earnings") or {}
    days_to_er = earnings.get("days_until")
    if not pm_managed and days_to_er is not None and 0 <= days_to_er <= 10:
        beat_prob = (earnings.get("beat_probability") or 0)
        if beat_prob < 0.65:
            return False, f"earnings in {days_to_er}d · beat_prob {beat_prob*100:.0f}% < 65%"
    # Max open positions
    if position_count is None:
        position_count = len(await list_positions())
    # v5.3 — Position-count cap removed per spec. The Trade Floor now opens
    # every signal that clears the other gates; risk is bounded by hard
    # per-trade dollar caps and per-tier % of equity instead.
    # Ticker already open OR has a pending order
    if held_tickers is None or pending_tickers is None:
        positions = await list_positions()
        held_tickers = {p.get("symbol", "").upper() for p in positions}
        open_orders = await list_orders(status="open")
        pending_tickers = {o.get("symbol", "").upper() for o in open_orders}
        queued_orders = await active_queued_equity_orders()
        pending_tickers |= {(q.get("ticker") or q.get("symbol") or "").upper() for q in queued_orders}
    if ticker in held_tickers:
        return False, "ticker_already_open_in_alpaca"
    if ticker in pending_tickers:
        return False, "ticker_has_pending_open_order"
    return True, None


# ─────── Main execution ───────
async def evaluate_and_execute(scan_results: list[dict[str, Any]], only_tickers: set[str] | None = None) -> dict[str, Any]:
    """Walk scan results, apply gates, execute limit DAY orders for any
    candidate that clears every gate.

    CRITICAL: a fresh check against Alpaca's live open positions AND open
    orders happens immediately before EVERY single submit attempt. A
    ticker that already has a position or a queued/working order will
    never receive a duplicate order."""
    from . import execution_gate, portfolio_manager, pm_rules, safety, stop_engine, trade_floor_learning as tfle  # local to avoid cycle
    db = get_db()
    executed: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    started = _now()
    enabled, safety_status = await safety.trading_enabled(scope="equity")
    if not enabled:
        await log_activity("Trade Floor: blocked by global safety halt", "warn", safety_status)
        return {
            "executed": [],
            "rejected": [],
            "halted": True,
            "reason": safety_status.get("reason") or "safety_halt",
            "safety": safety_status,
            "started_at": started.isoformat(),
        }
    if not _alpaca_ready():
        await log_activity("Trade Floor: ALPACA NOT CONFIGURED — no executions", "warn")
        return {"executed": [], "rejected": [], "compression_ratio": None,
                 "alpaca_ready": False, "started_at": started.isoformat()}

    gate_root = await execution_gate.check(scope="equity", record=True)
    if not gate_root.get("ok"):
        await log_activity(
            "Trade Floor: execution gate blocked equity orders",
            "warn",
            {"blockers": gate_root.get("blockers"), "truth_grade": gate_root.get("truth_grade")},
        )
        return {
            "executed": [],
            "rejected": [],
            "compression_ratio": None,
            "alpaca_ready": True,
            "started_at": started.isoformat(),
            "execution_gate": gate_root,
        }

    account = await get_account()
    if not account:
        await log_activity("Trade Floor: cannot reach Alpaca account", "warn")
        return {"executed": [], "rejected": [], "compression_ratio": None,
                 "alpaca_ready": False}
    breaker = await safety.check_daily_loss(account, source="equity_execute")
    if breaker.get("tripped"):
        return {
            "executed": [],
            "rejected": [],
            "halted": True,
            "reason": "daily_loss_breaker",
            "daily_loss_breaker": breaker,
            "alpaca_ready": True,
            "started_at": started.isoformat(),
        }
    equity = float(account.get("equity") or 0)

    # Pre-fetch live Alpaca state once for the per-row gate (then re-check
    # right before each submit for absolute safety).
    positions = await list_positions()
    held_tickers = {p.get("symbol", "").upper() for p in positions}
    open_orders = await list_orders(status="open")
    pending_tickers = {o.get("symbol", "").upper() for o in open_orders}
    queued_orders = await active_queued_equity_orders()
    pending_tickers |= {(q.get("ticker") or q.get("symbol") or "").upper() for q in queued_orders}
    regime = await regime_status()
    pm_mode = portfolio_manager._mode_from_regime(regime)
    ruleset = await pm_rules.get_ruleset()
    profile_override = await pm_rules.profile_override_for(pm_mode)
    pm_rows = portfolio_manager.evaluate_rows(
        scan_results,
        equity=equity,
        mode=pm_mode,
        profile_override=profile_override,
        regime=regime,
    )
    pm_by_ticker = {r["ticker"]: r for r in pm_rows}

    for row in scan_results:
        ticker = (row.get("ticker") or "").upper()
        if not ticker:
            continue
        if only_tickers and ticker not in only_tickers:
            continue
        row_gate = await execution_gate.check(
            scope="equity",
            ticker=ticker,
            sector=row.get("sector"),
            truth=gate_root.get("truth"),
            record=True,
        )
        if not row_gate.get("ok"):
            rejected.append({
                "ticker": ticker,
                "score": row.get("score"),
                "trade_score": row.get("trade_score"),
                "reason": "execution_gate_blocked",
                "gate": {
                    "decision": row_gate.get("decision"),
                    "blockers": row_gate.get("blockers"),
                    "warnings": row_gate.get("warnings"),
                },
            })
            continue
        pm_row = pm_by_ticker.get(ticker)
        if not pm_row or pm_row.get("action") not in {"ACCUMULATE", "STARTER"} or float(pm_row.get("allocation_usd") or 0) <= 0:
            rejected.append({
                "ticker": ticker,
                "score": row.get("score"),
                "trade_score": row.get("trade_score"),
                "reason": f"PM_NOT_APPROVED ({(pm_row or {}).get('action', 'NO_PM_ROW')})",
                "pm_action": (pm_row or {}).get("action"),
                "pm_score": (pm_row or {}).get("pm_score"),
            })
            continue
        _sig = row.get("signals") or {}
        _sig_list = list(_sig.keys()) if isinstance(_sig, dict) else list(_sig)
        passed, reason = await _gate_check(
            row, held_tickers=held_tickers, pending_tickers=pending_tickers,
            position_count=len(positions), regime=regime, pm_managed=True,
        )
        if not passed:
            rejected.append({"ticker": ticker, "score": row.get("score"),
                              "trade_score": row.get("trade_score"),
                              "reason": reason, "signals": _sig_list})
            continue

        score = float(pm_row.get("pm_score") or row.get("trade_score") or row.get("score") or 0)
        instrument = "fractional"
        # NOTE: Alpaca options trading not yet enabled on this account; the
        # recommended_contract field on the scan row is logged but not used.

        risk_pct = float(pm_row.get("risk_usd") or 0) / equity if equity > 0 else 0.0
        hard_cap = float(pm_row.get("allocation_usd") or 0)
        # Determine executable entry price from a fresh Alpaca ask. Scanner
        # prices are intentionally not executable because they may be stale.
        quote_meta = await get_latest_ask_meta(ticker)
        if not quote_meta:
            rejected.append({"ticker": ticker, "score": score,
                              "reason": "no_fresh_ask"})
            continue
        fresh, age_s = safety.quote_is_fresh(quote_meta)
        quote_meta["age_s"] = age_s
        if not fresh:
            rejected.append({"ticker": ticker, "score": score,
                              "reason": "stale_quote", "quote_meta": quote_meta})
            continue
        ask = float(quote_meta["price"])
        raw_ask = float(ask)
        entry_high = float(pm_row.get("entry_high") or row.get("entry_high") or 0)
        scanner_price = float(pm_row.get("price") or row.get("price") or 0)
        if entry_high > 0 and raw_ask > entry_high:
            ask = entry_high
        elif scanner_price > 0 and raw_ask > scanner_price * 1.03:
            ask = round(scanner_price * 1.01, 4)

        # Compute analytical stop (NO ATR). PM remains the sizing authority;
        # this stop record gives Trade Floor a live operational stop object.
        hold_days = (row.get("targets") or {}).get("hold_period_high") \
                       or row.get("hold_period_high") \
                       or row.get("recommended_hold_days") or 30
        sector = row.get("sector")
        stop_calc = await stop_engine.compute_stop(
            ticker=ticker, entry_price=ask, signal_combo=_sig_list,
            score=score, hold_window_days=int(hold_days or 30), sector=sector,
            instrument=instrument,
        )
        stop_price = float(pm_row.get("stop") or stop_calc["stop_price"])

        # AXIOM target: prefer scan blended target; fall back to signal uplift.
        axiom_target = row.get("target_blended") or row.get("target_high")
        if not axiom_target:
            try:
                from . import risk_target as _rt
                uplift, _label = _rt._signal_uplift(_sig_list)
                axiom_target = round(ask * (1 + max(uplift, 0.15)), 2)
            except Exception:
                axiom_target = round(ask * 1.20, 2)
        axiom_target = float(axiom_target)
        # Phase 2 target = entry + 1.5 × (AXIOM target − entry)
        phase2_target = round(ask + 1.5 * (axiom_target - ask), 4)

        # PM is the sizing authority. Trade Floor does not resize approved rows.
        notional = round(float(pm_row.get("allocation_usd") or 0), 2)
        if notional < 1.0:
            rejected.append({"ticker": ticker, "score": score,
                              "reason": f"notional<${notional}_too_small"})
            continue

        # FINAL safety + dedup check immediately before submission.
        enabled, safety_status = await safety.trading_enabled(scope="equity_row")
        if not enabled:
            rejected.append({"ticker": ticker, "score": score,
                              "reason": "safety_halt", "safety": safety_status})
            continue
        try:
            live_positions_now = await list_positions()
            live_orders_now = await list_orders(status="open")
            live_held = {p.get("symbol", "").upper() for p in live_positions_now}
            live_pending = {o.get("symbol", "").upper() for o in live_orders_now}
            live_queued = await active_queued_equity_orders()
            live_pending |= {(q.get("ticker") or q.get("symbol") or "").upper() for q in live_queued}
        except Exception:
            live_held, live_pending = held_tickers, pending_tickers
        if ticker in live_held:
            rejected.append({"ticker": ticker, "score": score,
                              "reason": "ticker_already_open_in_alpaca (final check)"})
            continue
        if ticker in live_pending:
            rejected.append({"ticker": ticker, "score": score,
                              "reason": "ticker_has_pending_open_order (final check)"})
            continue

        cli_id = f"tf-{ticker}-{int(_now().timestamp())}"
        limit_price = round(ask, 4)
        order = await submit_fractional_limit_buy(
            ticker, notional, limit_price=limit_price, client_order_id=cli_id,
        )
        if not order:
            rejected.append({"ticker": ticker, "score": score,
                              "reason": "alpaca_rejected"})
            continue

        # Update local sets so subsequent rows in the same scan don't double-submit.
        pending_tickers.add(ticker)
        order_queued = bool(order.get("_case_queued"))

        # Log to TF Learning Engine via the standard tf_trades record + the
        # learning helper (records limit_price/stop/breakdown for recal).
        trade_doc = stamped({
            "client_order_id": cli_id,
            "order_id": order.get("id"),
            "ticker": ticker,
            "entry_score": score,
            "trade_score": row.get("trade_score") or score,
            "pm_score": pm_row.get("pm_score"),
            "pm_action": pm_row.get("action"),
            "pm_mode": pm_mode,
            "pm_ruleset_id": ruleset.get("ruleset_id"),
            "pm_ruleset_name": ruleset.get("name"),
            "pm_ratchet_plan": pm_row.get("ratchet_plan") or {"enabled": False},
            "pm_ratchet_level": 0,
            "signal_combo": sorted(_sig_list),
            "instrument": instrument,
            "notional": notional,
            "risk_pct_used": risk_pct,
            "hard_cap_applied": hard_cap,
            "pm_plan": pm_row,
            "limit_price": limit_price,
            "order_session": order.get("_case_session") or equity_order_session(),
            "raw_alpaca_ask": raw_ask,
            "quote_meta": {
                **quote_meta,
                "price_used": limit_price,
                "guard_adjusted": limit_price != round(raw_ask, 4),
            },
            "limit_price_guard": {
                "entry_high": entry_high,
                "scanner_price": scanner_price,
                "capped": limit_price != round(raw_ask, 4),
            },
            "entry_price_ref": limit_price,        # initial entry = ask
            "stop_price": stop_price,
            "current_stop": stop_price,            # mutable — moves up on phase hits
            "stop_pct": stop_calc["stop_pct"],
            "stop_breakdown": stop_calc["breakdown"],
            "hold_window_days": int(hold_days or 30),
            "sector": sector,
            "regime": regime.get("status"),
            "status": "QUEUED" if order_queued else "OPEN",
            "fill_status": "QUEUED" if order_queued else "PENDING",
            "submitted_at": _now().isoformat(),
            # v5.3 — three-phase exit system
            "axiom_target": axiom_target,
            "phase1_target": (pm_row.get("ratchet_plan") or {}).get("initial_target_price") or axiom_target,
            "phase2_target": phase2_target,
            "pm_active_target": (pm_row.get("ratchet_plan") or {}).get("initial_target_price") or axiom_target,
            "pm_active_stop": (pm_row.get("ratchet_plan") or {}).get("initial_stop_price") or stop_price,
            "phase": 1,                             # active phase: 1, 2, or 3
            "phases_hit": {},
            "qty_total": None,                      # set on fill
            "qty_remaining": None,                  # set on fill
            "peak_price_since_entry": None,
        })
        await db.tf_trades.insert_one(trade_doc)
        if order_queued:
            await db.tf_queued_orders.update_one(
                {"client_order_id": cli_id},
                {"$set": {"trade_doc_id": str(trade_doc.get("_id")), "trade_status": "QUEUED"}},
            )
        try:
            await tfle.log_trade_initiation(trade_doc)
        except Exception as e:
            logger.warning("tfle.log_trade_initiation: %s", e)
        executed.append({"ticker": ticker, "notional": notional,
                          "score": score, "pm_score": pm_row.get("pm_score"),
                          "pm_action": pm_row.get("action"),
                          "limit_price": limit_price,
                          "stop_price": stop_price, "stop_pct": stop_calc["stop_pct"],
                          "order_id": order.get("id"),
                          "queued": order_queued})

    compression = (len(executed) / max(1, len(scan_results)))
    finished = _now()
    await db.tf_scan_log.insert_one(stamped({
        "scanned": len(scan_results),
        "executed": len(executed),
        "rejected": len(rejected),
        "rejection_details": rejected,
        "execution_details": executed,
        "pm_mode": pm_mode,
        "pm_ruleset_id": ruleset.get("ruleset_id"),
        "pm_approved": sum(1 for r in pm_rows if r.get("action") in {"ACCUMULATE", "STARTER"} and float(r.get("allocation_usd") or 0) > 0),
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "compression_ratio": round(compression, 3),
    }))
    await log_activity(
        f"Trade Floor: {len(executed)} executed / {len(rejected)} rejected "
        f"(compression {compression*100:.0f}%)", "info",
    )
    return {"executed": executed, "rejected": rejected,
             "compression_ratio": round(compression, 3),
             "started_at": started.isoformat(), "alpaca_ready": True}


# ─────── Position monitoring & journaling ───────
async def sync_positions_and_close_settled():
    """Pull live positions + open orders from Alpaca. Update tf_trades with
    marks, fill timing, running lowest_price_reached; mark filled-then-closed
    trades into tf_journal and log them to the Trade Floor Learning Engine."""
    if not _alpaca_ready():
        return {"updated": 0, "closed": 0}
    db = get_db()
    positions = await list_positions()
    reconciled = await reconcile_live_positions(positions=positions)
    pos_by_t = {p.get("symbol", "").upper(): p for p in positions}

    # Detect fills by pulling recent filled orders.
    filled_orders = await list_orders(status="all", limit=200)
    fills_by_cli = {o.get("client_order_id"): o for o in filled_orders
                      if o.get("client_order_id", "").startswith("tf-") and o.get("filled_at")}

    open_trades = await db.tf_trades.find(
        {"status": {"$in": ["OPEN", "UNFILLED_CANCELLED"]}},
        {"_id": 0},
    ).to_list(200)
    closed = 0
    newly_closed: list[dict[str, Any]] = []
    for t in open_trades:
        if t.get("status") != "OPEN":
            continue
        ticker = t.get("ticker", "").upper()
        p = pos_by_t.get(ticker)
        # ── Mark fill timing once ──
        if t.get("fill_status") in (None, "PENDING") and t.get("client_order_id") in fills_by_cli:
            o = fills_by_cli[t["client_order_id"]]
            try:
                sub = datetime.fromisoformat((t.get("submitted_at") or "").replace("Z", "+00:00"))
                fill_at = datetime.fromisoformat(o["filled_at"].replace("Z", "+00:00"))
                secs = (fill_at - sub).total_seconds()
            except Exception:
                secs = None
            qty_filled = float(o.get("filled_qty") or 0)
            avg_fill = float(o.get("filled_avg_price") or t.get("limit_price") or 0)
            await db.tf_trades.update_one(
                {"client_order_id": t["client_order_id"]},
                {"$set": {
                    "fill_status": "FILLED",
                    "filled_at": o.get("filled_at"),
                    "fill_seconds": secs,
                    "filled_avg_price": avg_fill,
                    "qty_total": qty_filled,
                    "qty_remaining": qty_filled,
                    "peak_price_since_entry": avg_fill,
                }},
            )
            t["filled_avg_price"] = avg_fill
            t["qty_total"] = qty_filled
            t["qty_remaining"] = qty_filled
        if t.get("fill_status") in (None, "PENDING"):
            continue
        if p:
            cur = float(p.get("current_price") or 0)
            new_low = cur
            existing_low = t.get("lowest_price_reached")
            if existing_low is not None and cur > 0:
                new_low = min(float(existing_low), cur)
            await db.tf_trades.update_one(
                {"client_order_id": t["client_order_id"]},
                {"$set": {
                    "current_mark": cur,
                    "qty": float(p.get("qty") or 0),
                    "market_value": float(p.get("market_value") or 0),
                    "unrealized_pl": float(p.get("unrealized_pl") or 0),
                    "unrealized_plpc": float(p.get("unrealized_plpc") or 0),
                    "lowest_price_reached": new_low if cur > 0 else existing_low,
                    "last_synced_at": _now().isoformat(),
                }},
            )
        else:
            # No longer in positions → either never filled (handled by stale-order
            # sweep) OR sold by Alpaca / manual close.
            cur_price = await _last_close_via_pricer(ticker)
            entry = t.get("entry_price_ref") or t.get("limit_price") or 0
            realized_pct = ((cur_price - entry) / entry * 100) if (entry and cur_price) else None
            await db.tf_trades.update_one(
                {"client_order_id": t["client_order_id"]},
                {"$set": {
                    "status": "CLOSED",
                    "exit_price": cur_price,
                    "realized_pct": realized_pct,
                    "closed_at": _now().isoformat(),
                }},
            )
            closed += 1
            newly_closed.append({**t, "exit_price": cur_price,
                                  "realized_pct": realized_pct})
    # v5.1 — fire-and-forget journal AI + Trade Floor learning write-back
    if newly_closed:
        asyncio.create_task(_write_journal_entries(newly_closed))
        try:
            from . import trade_floor_learning as tfle
            asyncio.create_task(tfle.log_trade_outcomes(newly_closed))
        except Exception as e:
            logger.warning("tfle.log_trade_outcomes dispatch: %s", e)
    return {"updated": len(open_trades) - closed, "closed": closed, "reconciled": reconciled}


async def reconcile_live_positions(
    positions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Ensure broker-held equity positions have local stop-ledger records.

    Alpaca is the broker source of truth. If a position exists there but the
    terminal has no OPEN tf_trades row, the phase exit worker cannot enforce
    stops. This rebuilds a conservative OPEN/FILLED ledger row from Alpaca
    position data and the latest matching filled Trade Floor buy order.
    """
    if not _alpaca_ready():
        return {"ok": False, "reason": "alpaca_not_configured", "created": 0}
    db = get_db()
    positions = positions if positions is not None else await list_positions()
    live_by_symbol = {
        str(p.get("symbol") or "").upper(): p
        for p in (positions or [])
        if str(p.get("symbol") or "").strip()
    }
    if not live_by_symbol:
        return {"ok": True, "created": 0, "checked": 0, "symbols": []}

    existing = await db.tf_trades.find(
        {"status": "OPEN", "ticker": {"$in": list(live_by_symbol)}},
        {"_id": 0, "ticker": 1},
    ).to_list(500)
    existing_symbols = {str(t.get("ticker") or "").upper() for t in existing}

    filled_orders = await list_orders(status="all", limit=200)
    orders_by_symbol: dict[str, dict[str, Any]] = {}
    for order in filled_orders:
        symbol = str(order.get("symbol") or "").upper()
        if not symbol or not str(order.get("client_order_id") or "").startswith("tf-"):
            continue
        if str(order.get("side") or "").lower() != "buy" or not order.get("filled_at"):
            continue
        previous = orders_by_symbol.get(symbol)
        if not previous or str(order.get("filled_at") or "") > str(previous.get("filled_at") or ""):
            orders_by_symbol[symbol] = order

    created: list[dict[str, Any]] = []
    for symbol, position in live_by_symbol.items():
        if symbol in existing_symbols:
            continue
        qty = _safe_float(position.get("qty"))
        entry = _safe_float(position.get("avg_entry_price"))
        current = _safe_float(position.get("current_price"))
        if qty <= 0 or entry <= 0:
            continue
        order = orders_by_symbol.get(symbol) or {}
        stop_calc = await stop_engine.compute_stop(
            ticker=symbol,
            entry_price=entry,
            signal_combo=["RECONCILED_POSITION"],
            score=30,
            hold_window_days=30,
            sector=None,
            instrument="fractional",
        )
        target = round(entry * 1.15, 4)
        now_iso = _now().isoformat()
        client_order_id = (
            order.get("client_order_id")
            or f"tf-reconciled-{symbol}-{int(_now().timestamp())}"
        )
        doc = stamped({
            "client_order_id": client_order_id,
            "order_id": order.get("id"),
            "ticker": symbol,
            "symbol": symbol,
            "company": position.get("asset_class") or "Reconciled live Alpaca position",
            "entry_score": None,
            "trade_score": 30,
            "signal_combo": ["RECONCILED_POSITION"],
            "instrument": "fractional",
            "notional": round(entry * qty, 2),
            "market_value": _safe_float(position.get("market_value")),
            "limit_price": entry,
            "entry_price_ref": entry,
            "filled_avg_price": entry,
            "filled_at": order.get("filled_at") or now_iso,
            "fill_seconds": None,
            "qty_total": qty,
            "qty_remaining": qty,
            "qty": qty,
            "unrealized_pl": _safe_float(position.get("unrealized_pl")),
            "unrealized_plpc": _safe_float(position.get("unrealized_plpc")),
            "current_mark": current,
            "lowest_price_reached": current if current > 0 else entry,
            "peak_price_since_entry": max(entry, current),
            "stop_price": stop_calc["stop_price"],
            "current_stop": stop_calc["stop_price"],
            "pm_active_stop": stop_calc["stop_price"],
            "stop_pct": stop_calc["stop_pct"],
            "stop_breakdown": stop_calc["breakdown"],
            "target_price": target,
            "take_profit_price": target,
            "phase1_target": target,
            "pm_active_target": target,
            "hold_window_days": 30,
            "status": "OPEN",
            "fill_status": "FILLED",
            "side": "buy",
            "phase": 1,
            "phases_hit": {},
            "source": "alpaca_reconciliation",
            "submitted_at": order.get("submitted_at") or now_iso,
            "last_synced_at": now_iso,
            "reconciled_from_alpaca_position": True,
            "reconciled_at": now_iso,
            "reconciliation_reason": "live_alpaca_position_missing_tf_trade",
        })
        try:
            await db.tf_trades.insert_one(doc)
            created.append({
                "ticker": symbol,
                "qty": qty,
                "entry": entry,
                "current": current,
                "stop": stop_calc["stop_price"],
                "client_order_id": client_order_id,
            })
        except Exception as exc:
            logger.warning("tf position reconciliation failed for %s: %s", symbol, exc)

    if created:
        await log_activity(
            f"Trade Floor reconciled {len(created)} live Alpaca position(s) into stop ledger",
            "warn",
            {"positions": created},
        )
    return {
        "ok": True,
        "checked": len(live_by_symbol),
        "created": len(created),
        "symbols": [c["ticker"] for c in created],
        "details": created,
    }


def annotate_live_positions_with_stops(
    live_positions: list[dict[str, Any]],
    db_positions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    stops_by_symbol = {
        str(t.get("ticker") or t.get("symbol") or "").upper(): t
        for t in (db_positions or [])
    }
    annotated: list[dict[str, Any]] = []
    for position in live_positions or []:
        row = dict(position)
        symbol = str(row.get("symbol") or row.get("ticker") or "").upper()
        trade = stops_by_symbol.get(symbol) or {}
        current = _safe_float(row.get("current_price") or row.get("current_mark"))
        stop = _safe_float(trade.get("current_stop") or trade.get("stop_price"))
        row["current_stop"] = stop or None
        row["stop_price"] = stop or None
        row["stop_source"] = "tf_trades" if stop else None
        row["below_stop"] = bool(stop and current and current <= stop)
        row["dist_to_stop_pct"] = (
            round(((current - stop) / current) * 100, 2)
            if stop and current else None
        )
        row["stop_client_order_id"] = trade.get("client_order_id")
        annotated.append(row)
    return annotated


async def _write_journal_entries(trades: list[dict[str, Any]]):
    """Generate plain-language journal entries via Claude for closed trades.
    Stored in tf_trades.journal_summary and tf_journal collection."""
    db = get_db()
    try:
        from . import claude_service
    except Exception:
        return
    for t in trades:
        try:
            ret = t.get("realized_pct") or 0
            combo = " · ".join(t.get("signal_combo") or [])
            prompt = (
                f"In 4-6 conversational sentences, write a plain-language journal entry "
                f"for the AXIOM Trade Floor's closed paper trade. "
                f"Facts: ticker {t.get('ticker')}, signal combo [{combo}], "
                f"entry ${t.get('entry_price_ref'):.2f}, exit ${t.get('exit_price', 0):.2f}, "
                f"return {ret:+.2f}%, instrument {t.get('instrument')}, "
                f"stop ${t.get('stop_price', 0):.2f}, regime {t.get('regime')}. "
                f"Cover: WHY we took it, WHAT we were targeting, WHAT happened, "
                f"WHAT we learned, WHAT we'll do differently. No raw data dumps — "
                f"speak like an analyst writing in their own journal."
            )
            summary = await claude_service._call_claude(
                "You write concise, candid trade journal entries.",
                prompt,
            )
            if summary:
                await db.tf_trades.update_one(
                    {"client_order_id": t["client_order_id"]},
                    {"$set": {"journal_summary": summary[:1500]}},
                )
                await db.tf_journal.insert_one(stamped({
                    "ticker": t.get("ticker"),
                    "date": _now().date().isoformat(),
                    "client_order_id": t["client_order_id"],
                    "signal_combo": t.get("signal_combo"),
                    "entry_price": t.get("entry_price_ref"),
                    "exit_price": t.get("exit_price"),
                    "realized_pct": ret,
                    "journal": summary,
                }))
        except Exception as e:
            logger.warning("journal write-back for %s: %s", t.get("ticker"), e)


async def _last_close_via_pricer(ticker: str) -> float | None:
    from . import pricer
    return await pricer.get_latest_close(ticker)


# ─────── Public read endpoints helpers ───────
async def open_positions_view() -> list[dict[str, Any]]:
    db = get_db()
    return await db.tf_trades.find({"status": "OPEN"}, {"_id": 0}).sort(
        "submitted_at", -1).to_list(50)


async def latest_scan_log() -> dict[str, Any] | None:
    db = get_db()
    return await db.tf_scan_log.find_one({}, {"_id": 0}, sort=[("started_at", -1)])


async def trade_history() -> list[dict[str, Any]]:
    db = get_db()
    return await db.tf_trades.find({"status": "CLOSED"}, {"_id": 0}).sort(
        "closed_at", -1).to_list(200)


async def daily_journal(date_iso: str | None = None) -> list[dict[str, Any]]:
    db = get_db()
    if date_iso:
        return await db.tf_journal.find({"date": date_iso}, {"_id": 0}).to_list(50)
    return await db.tf_journal.find({}, {"_id": 0}).sort("date", -1).to_list(60)
