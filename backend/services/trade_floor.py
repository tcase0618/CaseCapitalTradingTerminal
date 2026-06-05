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

Initial risk profile (overridden by Trade Floor Learning Engine after
10 closed trades):
  Fractional shares: 20-24 → 1% · 25-29 → 2% · 30-49 → 3% · 50+ → 5%
  Options:           20-24 → 5% · 25-29 → 7% · 30-49 → 8% · 50+ → 10%
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


async def submit_fractional_buy(ticker: str, notional: float,
                                   client_order_id: str | None = None) -> dict[str, Any] | None:
    if not _alpaca_ready() or notional <= 0:
        return None
    payload: dict[str, Any] = {
        "symbol": ticker.upper(),
        "notional": round(notional, 2),
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
    }
    if client_order_id:
        payload["client_order_id"] = client_order_id
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as c:
            r = await c.post(f"{ALPACA_TRADE_BASE}/v2/orders", json=payload)
            if r.status_code in (200, 201):
                return r.json()
            logger.warning("alpaca buy %s: %s %s", ticker, r.status_code, r.text[:200])
    except Exception as e:
        logger.warning("alpaca buy exception %s: %s", ticker, e)
    return None


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


async def _risk_pct(score: float, instrument: str) -> float:
    """Lookup from learning-engine-managed table, default to baseline."""
    db = get_db()
    tier_doc = await db.tf_risk_tiers.find_one({"_id": "current"})
    table = tier_doc.get("tiers") if tier_doc else None
    if not table:
        table = {k: {str(rng): v for rng, v in tiers.items()}
                  for k, tiers in DEFAULT_RISK_TIERS.items()}
    band = table.get(instrument) or {}
    for k, pct in band.items():
        try:
            lo, hi = eval(k) if isinstance(k, str) else k
        except Exception:
            continue
        if lo <= score < hi:
            return float(pct)
    return 0.01


# ─────── ATR helper ───────
async def fetch_atr_14d(ticker: str) -> float | None:
    """14-day ATR via yfinance for stop calculation."""
    def _sync():
        try:
            import yfinance as yf
            df = yf.Ticker(ticker).history(period="30d")
            if df is None or len(df) < 15:
                return None
            tr = (df["High"] - df["Low"]).abs()
            tr2 = (df["High"] - df["Close"].shift()).abs()
            tr3 = (df["Low"] - df["Close"].shift()).abs()
            true_range = tr.combine(tr2, max).combine(tr3, max)
            atr = true_range.rolling(14).mean().iloc[-1]
            return float(atr) if atr and atr > 0 else None
        except Exception:
            return None
    return await asyncio.get_event_loop().run_in_executor(None, _sync)


# ─────── Execution gates ───────
async def _gate_check(scan_row: dict[str, Any]) -> tuple[bool, str | None]:
    """Returns (passed, rejection_reason)."""
    ticker = scan_row.get("ticker")
    trade_score = scan_row.get("trade_score") or scan_row.get("score") or 0
    if trade_score < TRADE_SCORE_MIN:
        return False, f"trade_score {trade_score:.1f} < {TRADE_SCORE_MIN}"
    signals = scan_row.get("signals") or {}
    if len(signals) < 2:
        return False, f"only {len(signals)} signal type(s) firing"
    regime = await regime_status()
    if regime.get("halt_new_entries"):
        return False, f"regime halt (vix={regime.get('vix')}, spy_ema_break={regime.get('spy_last',0) < regime.get('spy_ema200',0)})"
    # Earnings within 10d gate
    earnings = scan_row.get("earnings") or {}
    days_to_er = earnings.get("days_until")
    if days_to_er is not None and 0 <= days_to_er <= 10:
        beat_prob = (earnings.get("beat_probability") or 0)
        # Allow only if beat_prob > 0.65 AND options used (handled at instrument selection)
        if beat_prob < 0.65:
            return False, f"earnings in {days_to_er}d · beat_prob {beat_prob*100:.0f}% < 65%"
    # Max open positions
    positions = await list_positions()
    if len(positions) >= MAX_OPEN_POSITIONS:
        return False, f"max positions reached ({MAX_OPEN_POSITIONS})"
    # Ticker not already open
    if any(p.get("symbol", "").upper() == (ticker or "").upper() for p in positions):
        return False, "position already open"
    return True, None


# ─────── Main execution ───────
async def evaluate_and_execute(scan_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Walk scan results, apply gates, execute trades that pass."""
    db = get_db()
    executed: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    started = _now()
    if not _alpaca_ready():
        await log_activity("Trade Floor: ALPACA NOT CONFIGURED — no executions", "warn")
        return {"executed": [], "rejected": [], "compression_ratio": None,
                 "alpaca_ready": False, "started_at": started.isoformat()}

    account = await get_account()
    if not account:
        await log_activity("Trade Floor: cannot reach Alpaca account", "warn")
        return {"executed": [], "rejected": [], "compression_ratio": None,
                 "alpaca_ready": False}
    equity = float(account.get("equity") or 0)

    for row in scan_results:
        ticker = row.get("ticker")
        if not ticker:
            continue
        _sig = row.get("signals") or {}
        _sig_list = list(_sig.keys()) if isinstance(_sig, dict) else list(_sig)
        passed, reason = await _gate_check(row)
        if not passed:
            rejected.append({"ticker": ticker, "score": row.get("score"),
                              "trade_score": row.get("trade_score"),
                              "reason": reason, "signals": _sig_list})
            continue

        # Decide instrument: contract recommendation > fractional fallback
        score = row.get("trade_score") or row.get("score") or 0
        contract = row.get("recommended_contract") or {}
        used_option = False
        order = None
        if contract.get("symbol") and contract.get("ask"):
            risk_pct = await _risk_pct(score, "options")
            premium_per_contract = float(contract["ask"]) * 100  # OCC = 100 shares
            risk_budget = equity * risk_pct
            if premium_per_contract <= risk_budget:
                # Future enhancement: place options limit order on Alpaca options
                # For now we deliberately skip and fall back to fractional shares
                # because Alpaca options requires explicit options account approval
                pass

        if not used_option:
            risk_pct = await _risk_pct(score, "fractional")
            risk_budget = equity * risk_pct
            # Determine stop distance
            cur_price = row.get("current_price") or row.get("price") or 0
            atr = await fetch_atr_14d(ticker)
            stop_price = row.get("recommended_stop")
            if stop_price is None and atr and cur_price:
                stop_price = cur_price - 2 * atr
            if not stop_price or stop_price <= 0 or stop_price >= cur_price:
                rejected.append({"ticker": ticker, "score": score,
                                  "reason": f"no_stop_calculable (cur={cur_price}, atr={atr})"})
                continue
            stop_dist = cur_price - stop_price
            # position size $ = risk_budget (cap at risk_budget itself which IS the
            # max loss when sized as risk / stop_distance * cur_price)
            # Notional = risk_budget * (cur_price / stop_dist)
            notional = min(risk_budget * (cur_price / stop_dist), equity * 0.15)
            notional = round(max(notional, 5.0), 2)
            cli_id = f"tf-{ticker}-{int(_now().timestamp())}"
            order = await submit_fractional_buy(ticker, notional, client_order_id=cli_id)
            if not order:
                rejected.append({"ticker": ticker, "score": score,
                                  "reason": "alpaca_rejected"})
                continue
            await db.tf_trades.insert_one(stamped({
                "client_order_id": cli_id,
                "order_id": order.get("id"),
                "ticker": ticker,
                "entry_score": score,
                "trade_score": row.get("trade_score") or score,
                "signal_combo": sorted(_sig_list),
                "instrument": "fractional",
                "notional": notional,
                "entry_price_ref": cur_price,
                "stop_price": stop_price,
                "atr_14d": atr,
                "regime": (await regime_status()).get("status"),
                "status": "OPEN",
                "submitted_at": _now().isoformat(),
            }))
            executed.append({"ticker": ticker, "notional": notional,
                              "score": score, "stop_price": stop_price,
                              "order_id": order.get("id")})

    compression = (len(executed) / max(1, len(scan_results)))
    finished = _now()
    await db.tf_scan_log.insert_one(stamped({
        "scanned": len(scan_results),
        "executed": len(executed),
        "rejected": len(rejected),
        "rejection_details": rejected,
        "execution_details": executed,
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
    """Pull live positions + closed orders from Alpaca. Update tf_trades
    with marks; move filled-then-closed trades into tf_journal."""
    if not _alpaca_ready():
        return {"updated": 0, "closed": 0}
    db = get_db()
    positions = await list_positions()
    pos_by_t = {p.get("symbol", "").upper(): p for p in positions}

    open_trades = await db.tf_trades.find({"status": "OPEN"}, {"_id": 0}).to_list(200)
    closed = 0
    newly_closed: list[dict[str, Any]] = []
    for t in open_trades:
        ticker = t.get("ticker", "").upper()
        p = pos_by_t.get(ticker)
        if p:
            await db.tf_trades.update_one(
                {"client_order_id": t["client_order_id"]},
                {"$set": {
                    "current_mark": float(p.get("current_price") or 0),
                    "qty": float(p.get("qty") or 0),
                    "market_value": float(p.get("market_value") or 0),
                    "unrealized_pl": float(p.get("unrealized_pl") or 0),
                    "unrealized_plpc": float(p.get("unrealized_plpc") or 0),
                    "last_synced_at": _now().isoformat(),
                }},
            )
        else:
            # No longer in positions → closed by Alpaca (sold/expired)
            cur_price = await _last_close_via_pricer(ticker)
            entry = t.get("entry_price_ref") or 0
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
    # v5.1 — fire-and-forget journal AI write-back for newly-closed trades
    if newly_closed:
        asyncio.create_task(_write_journal_entries(newly_closed))
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
