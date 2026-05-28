"""Lottery Picks — high-payoff, low-cost contract finder + history tracker.

Scores each scanned ticker on 4 factors → 0-100 lottery score:
  • Squeeze compression  (0-30 pts)  — sourced from existing squeeze.score
  • Unusual options flow (0-25 pts)  — from existing flow_score or claude opts
  • Catalyst proximity   (0-25 pts)  — earnings/contract/macro proximity
  • Cheap-IV bonus       (0-20 pts)  — IV rank < 35

Short-interest pile-on adds up to +15 pts (capped).

Tiers: JACKPOT ≥80 · HOT ≥65 · WARM ≥50 · COLD <50.

Contract finder pulls calls 10-20% OTM, $0.10-$0.75 premium, 14-28 DTE
via yfinance options chains. EV math:
  • P(double) ≈ delta × 0.55      (rough heuristic)
  • P(10x)    ≈ max(0, delta × 0.08)
  • P(total loss) = 1 - P(any return)
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import get_db, log_activity, stamped

logger = logging.getLogger(__name__)


TIER_LIMITS = {"JACKPOT": 500, "HOT": 200, "WARM": 100, "COLD": 50}


def tier_for(score: float) -> str:
    if score >= 80:
        return "JACKPOT"
    if score >= 65:
        return "HOT"
    if score >= 50:
        return "WARM"
    return "COLD"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def score_lottery(result: dict[str, Any]) -> dict[str, Any]:
    """Score a single scan result row (the row from scanner output) for
    lottery potential. Returns {score, factors, tier}."""
    factors: dict[str, float] = {}
    # 1) Squeeze compression
    sq = (result.get("squeeze") or {}).get("score") or 0
    factors["squeeze"] = min(30.0, sq * 0.3)
    # 2) Unusual flow
    flow = 0.0
    flow_meta = result.get("flow") or {}
    flow_dir = (flow_meta.get("direction") or "").upper()
    flow_score_raw = float(flow_meta.get("score") or 0)
    if "UNUSUAL_FLOW" in (result.get("signals") or []):
        flow = 20.0
    if "CALL_SWEEP" in (result.get("signals") or []):
        flow += 5.0
    if flow_dir == "BULLISH":
        flow += 2.0
    factors["flow"] = min(25.0, flow + flow_score_raw * 5)
    # 3) Catalyst proximity
    cat = 0.0
    if "upcoming_earnings" in (result.get("signals") or []):
        cat += 12.0
    if "CONTRACT_SURGE" in (result.get("signals") or []):
        cat += 10.0
    if result.get("dark_horse"):
        cat += 8.0
    factors["catalyst"] = min(25.0, cat)
    # 4) IV cheap bonus
    opt = result.get("options") or {}
    iv_rank = opt.get("iv_rank")
    if iv_rank is not None:
        if iv_rank < 25:
            factors["iv_cheap"] = 20.0
        elif iv_rank < 35:
            factors["iv_cheap"] = 12.0
        elif iv_rank < 50:
            factors["iv_cheap"] = 5.0
        else:
            factors["iv_cheap"] = 0.0
    else:
        factors["iv_cheap"] = 6.0
    # Short interest bonus (squeeze fuel)
    short_pct = result.get("short_float_pct")
    si_bonus = 0.0
    if isinstance(short_pct, (int, float)):
        if short_pct > 25:
            si_bonus = 15.0
        elif short_pct > 15:
            si_bonus = 10.0
        elif short_pct > 8:
            si_bonus = 5.0
    factors["si_bonus"] = si_bonus

    total = sum(factors.values())
    total = min(100.0, total)
    return {
        "score": round(total, 1),
        "tier": tier_for(total),
        "factors": {k: round(v, 1) for k, v in factors.items()},
    }


async def find_contract(ticker: str, current_price: float | None) -> dict[str, Any] | None:
    """Find a call contract 10-20% OTM, $0.10-$0.75 premium, 14-28 DTE.
    Returns {strike, ask, total_cost, breakeven, delta, dte, exp} or None."""
    if not current_price or current_price <= 0:
        return None
    def _sync():
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            today = _now().date()
            best = None
            for exp_str in (t.options or [])[:6]:
                try:
                    exp_d = datetime.strptime(exp_str, "%Y-%m-%d").date()
                except Exception:
                    continue
                dte = (exp_d - today).days
                if dte < 14 or dte > 28:
                    continue
                try:
                    chain = t.option_chain(exp_str)
                    calls = chain.calls
                except Exception:
                    continue
                if calls is None or len(calls) == 0:
                    continue
                lower = current_price * 1.10
                upper = current_price * 1.20
                candidates = calls[
                    (calls["strike"] >= lower) & (calls["strike"] <= upper)
                    & (calls["ask"].fillna(0) >= 0.10) & (calls["ask"].fillna(0) <= 0.75)
                ]
                if candidates is None or len(candidates) == 0:
                    continue
                # Pick highest delta (most likely to pay off)
                if "delta" in candidates.columns:
                    cand = candidates.sort_values("delta", ascending=False).iloc[0]
                else:
                    cand = candidates.iloc[0]
                strike = float(cand["strike"])
                ask = float(cand["ask"])
                delta = float(cand.get("delta", 0)) if "delta" in candidates.columns else None
                pick = {
                    "strike": strike, "ask": ask,
                    "total_cost": round(ask * 100, 2),
                    "breakeven": round(strike + ask, 2),
                    "delta": round(delta, 3) if delta else None,
                    "dte": dte, "exp": exp_str,
                    "iv": float(cand.get("impliedVolatility", 0)) if "impliedVolatility" in candidates.columns else None,
                }
                if best is None or (pick.get("delta") or 0) > (best.get("delta") or 0):
                    best = pick
            return best
        except Exception as e:
            logger.debug("find_contract %s: %s", ticker, e)
            return None
    return await asyncio.get_event_loop().run_in_executor(None, _sync)


def ev_math(delta: float | None) -> dict[str, float]:
    """Heuristic expected-value breakdown."""
    if delta is None or delta <= 0:
        return {"p_double": 0.0, "p_10x": 0.0, "p_total_loss": 0.95}
    p_double = round(min(0.65, delta * 0.55), 3)
    p_10x = round(max(0.0, delta * 0.08), 3)
    p_total_loss = round(max(0.20, 1 - (p_double + p_10x * 1.5)), 3)
    return {"p_double": p_double, "p_10x": p_10x, "p_total_loss": p_total_loss}


async def evaluate_for_scan(scan_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Score every scan result + find contract for tier ≥ WARM.
    Returns the full sorted lottery list."""
    if not scan_results:
        return []
    picks: list[dict[str, Any]] = []
    for r in scan_results:
        ticker = r.get("ticker")
        if not ticker:
            continue
        scored = score_lottery(r)
        contract = None
        ev = None
        if scored["score"] >= 50:
            contract = await find_contract(ticker, r.get("price"))
            if contract:
                ev = ev_math(contract.get("delta"))
        picks.append({
            "ticker": ticker,
            "score": scored["score"],
            "tier": scored["tier"],
            "factors": scored["factors"],
            "max_bet": TIER_LIMITS[scored["tier"]],
            "current_price": r.get("price"),
            "signals": r.get("signals") or [],
            "contract": contract,
            "ev": ev,
            "evaluated_at": _now().isoformat(),
        })
    picks.sort(key=lambda x: -x["score"])
    return picks


async def log_picks(picks: list[dict[str, Any]]) -> int:
    """Log every JACKPOT/HOT pick with its contract price for history tracking."""
    db = get_db()
    n = 0
    today = _now().date().isoformat()
    for p in picks:
        if p["tier"] not in ("JACKPOT", "HOT"):
            continue
        if not p.get("contract"):
            continue
        # Idempotent per-day-per-ticker-per-strike
        key = {
            "ticker": p["ticker"],
            "date": today,
            "strike": p["contract"]["strike"],
            "exp": p["contract"]["exp"],
        }
        await db.lottery_history.update_one(
            key,
            {"$set": stamped({
                **key,
                "tier": p["tier"],
                "score": p["score"],
                "entry_ask": p["contract"]["ask"],
                "entry_breakeven": p["contract"]["breakeven"],
                "entry_delta": p["contract"]["delta"],
                "entry_current_price": p["current_price"],
                "logged_at": _now().isoformat(),
            })},
            upsert=True,
        )
        n += 1
    if n:
        await log_activity(f"Lottery: logged {n} JACKPOT/HOT picks for tracking", "info")
    return n


async def track_record() -> dict[str, Any]:
    """Returns running track record stats across all logged lottery history."""
    db = get_db()
    rows = await db.lottery_history.find({}, {"_id": 0}).to_list(2000)
    if not rows:
        return {"total": 0, "settled": 0, "winners": 0, "hit_rate": None, "avg_winner": None}
    settled = [r for r in rows if r.get("settled_ask") is not None]
    winners = [r for r in settled if (r["settled_ask"] or 0) > (r["entry_ask"] or 0) * 2]
    avg_winner = (sum((r["settled_ask"] - r["entry_ask"]) / r["entry_ask"] * 100
                       for r in winners) / len(winners)) if winners else None
    return {
        "total": len(rows),
        "settled": len(settled),
        "winners": len(winners),
        "hit_rate": round(len(winners) / len(settled), 3) if settled else None,
        "avg_winner_pct": round(avg_winner, 1) if avg_winner is not None else None,
    }


async def recent_picks(days: int = 14, tier: str | None = None) -> list[dict[str, Any]]:
    db = get_db()
    cutoff = (_now() - timedelta(days=days)).date().isoformat()
    q: dict[str, Any] = {"date": {"$gte": cutoff}}
    if tier:
        q["tier"] = tier
    return await db.lottery_history.find(q, {"_id": 0}).sort("date", -1).to_list(200)
