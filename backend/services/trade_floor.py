"""Trade Floor — autonomous paper-trading system on Alpaca.

Operates fully independently from the main scan display. The scan finds
opportunities; the Trade Floor decides whether to act and executes via
Alpaca paper API. All learning happens in `trade_floor_learning.py`.

Execution Gates (ALL must pass simultaneously):
  • Trade Score > 20
  • ≥2 distinct signal types firing
  • Regime gate clear (VIX < 25, SPY > 200-d EMA)
  • No earnings in 10d (unless beat_prob > 65% AND spread structure)
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

import httpx

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
VIX_HALT_THRESHOLD = 25.0
TRADE_SCORE_MIN = 20
HEADERS = {
    "APCA-API-KEY-ID": ALPACA_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET,
    "Content-Type": "application/json",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _alpaca_ready() -> bool:
    return bool(ALPACA_KEY and ALPACA_SECRET)


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


async def submit_fractional_limit_buy(ticker: str, notional: float, limit_price: float,
                                          client_order_id: str | None = None) -> dict[str, Any] | None:
    """Limit DAY order for fractional notional. NEVER market."""
    if not _alpaca_ready() or notional <= 0 or limit_price <= 0:
        return None
    payload: dict[str, Any] = {
        "symbol": ticker.upper(),
        "notional": round(notional, 2),
        "side": "buy",
        "type": "limit",
        "time_in_force": "day",
        "limit_price": round(limit_price, 4),
    }
    if client_order_id:
        payload["client_order_id"] = client_order_id
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as c:
            r = await c.post(f"{ALPACA_TRADE_BASE}/v2/orders", json=payload)
            if r.status_code in (200, 201):
                return r.json()
            logger.warning("alpaca limit buy %s: %s %s", ticker, r.status_code, r.text[:200])
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


# Backwards-compatible alias used by legacy code paths
submit_fractional_buy = submit_fractional_limit_buy


async def get_latest_ask(ticker: str) -> float | None:
    """Sole source for the limit price at order-submission time: Alpaca."""
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
            return ask if ask > 0 else None
    except Exception:
        return None


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
    """VIX + SPY 200-d EMA snapshot. Status = green/yellow/red."""
    from . import pricer
    import yfinance as yf

    def _yf_calc():
        try:
            spy = yf.Ticker("SPY").history(period="220d")["Close"]
            ema200 = spy.ewm(span=200, adjust=False).mean()
            spy_last = float(spy.iloc[-1])
            spy_ema = float(ema200.iloc[-1])
            vix = float(yf.Ticker("^VIX").history(period="1d")["Close"].iloc[-1])
            return spy_last, spy_ema, vix
        except Exception:
            return None, None, None
    loop = asyncio.get_event_loop()
    spy_last, spy_ema, vix = await loop.run_in_executor(None, _yf_calc)
    if vix is None or spy_last is None:
        return {"status": "unknown", "vix": None, "spy_last": None, "spy_ema200": None,
                 "halt_new_entries": False}
    halt = vix >= VIX_HALT_THRESHOLD or spy_last < spy_ema
    color = "red" if halt else ("yellow" if vix >= 20 else "green")
    return {
        "status": color, "vix": round(vix, 2),
        "spy_last": round(spy_last, 2), "spy_ema200": round(spy_ema, 2),
        "halt_new_entries": bool(halt),
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
    if not pm_managed and regime.get("halt_new_entries"):
        return False, f"regime halt (vix={regime.get('vix')}, spy_ema_break={regime.get('spy_last',0) < regime.get('spy_ema200',0)})"
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
    from . import execution_gate, portfolio_manager, pm_rules, stop_engine, trade_floor_learning as tfle  # local to avoid cycle
    db = get_db()
    executed: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    started = _now()
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
    equity = float(account.get("equity") or 0)

    # Pre-fetch live Alpaca state once for the per-row gate (then re-check
    # right before each submit for absolute safety).
    positions = await list_positions()
    held_tickers = {p.get("symbol", "").upper() for p in positions}
    open_orders = await list_orders(status="open")
    pending_tickers = {o.get("symbol", "").upper() for o in open_orders}
    regime = await regime_status()
    pm_mode = portfolio_manager._mode_from_regime(regime)
    ruleset = await pm_rules.get_ruleset()
    profile_override = await pm_rules.profile_override_for(pm_mode)
    pm_rows = portfolio_manager.evaluate_rows(scan_results, equity=equity, mode=pm_mode, profile_override=profile_override)
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
        # Determine entry price = current Alpaca ask (sole source).
        ask = await get_latest_ask(ticker)
        if not ask:
            # Fallback: scanner-known price for sizing only.
            ask = float(row.get("price") or row.get("current_price") or 0)
        if not ask or ask <= 0:
            rejected.append({"ticker": ticker, "score": score,
                              "reason": "no_ask_quote_from_alpaca"})
            continue
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

        # FINAL dedup check immediately before submission — fresh fetch
        try:
            live_positions_now = await list_positions()
            live_orders_now = await list_orders(status="open")
            live_held = {p.get("symbol", "").upper() for p in live_positions_now}
            live_pending = {o.get("symbol", "").upper() for o in live_orders_now}
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
            "raw_alpaca_ask": raw_ask,
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
            "status": "OPEN",
            "fill_status": "PENDING",
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
        try:
            await tfle.log_trade_initiation(trade_doc)
        except Exception as e:
            logger.warning("tfle.log_trade_initiation: %s", e)
        executed.append({"ticker": ticker, "notional": notional,
                          "score": score, "pm_score": pm_row.get("pm_score"),
                          "pm_action": pm_row.get("action"),
                          "limit_price": limit_price,
                          "stop_price": stop_price, "stop_pct": stop_calc["stop_pct"],
                          "order_id": order.get("id")})

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
    return {"updated": len(open_trades) - closed, "closed": closed}


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
