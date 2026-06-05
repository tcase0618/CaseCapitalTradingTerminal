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


AUTO_BUY_SCORE = 50.0  # ≥ this auto-logs as bought into track record


async def log_picks(picks: list[dict[str, Any]]) -> int:
    """Auto-log every pick scoring ≥ AUTO_BUY_SCORE (50/100) as 'bought'
    into the lottery track record. Each pick must have a discovered contract.
    Idempotent per (ticker, date, strike, exp)."""
    db = get_db()
    n = 0
    today = _now().date().isoformat()
    for p in picks:
        if (p.get("score") or 0) < AUTO_BUY_SCORE:
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
                "auto_bought": True,
                "logged_at": _now().isoformat(),
            })},
            upsert=True,
        )
        n += 1
    if n:
        await log_activity(f"Lottery: auto-bought {n} picks (score ≥ {AUTO_BUY_SCORE:.0f})", "info")
    return n


async def _fetch_current_ask(ticker: str, exp: str, strike: float) -> float | None:
    """Fetch the current ask (or mid) for a logged lottery call."""
    def _sync():
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            chain = t.option_chain(exp)
            calls = chain.calls
            if calls is None or len(calls) == 0:
                return None
            d = (calls["strike"] - strike).abs()
            idx = d.idxmin()
            row = calls.loc[idx]
            ask = float(row.get("ask") or 0)
            if ask > 0:
                return ask
            last = float(row.get("lastPrice") or 0)
            if last > 0:
                return last
            bid = float(row.get("bid") or 0)
            return (bid + ask) / 2 if bid and ask else (bid or None)
        except Exception as e:
            logger.debug("fetch_current_ask %s/%s/%s: %s", ticker, exp, strike, e)
            return None
    return await asyncio.get_event_loop().run_in_executor(None, _sync)


async def refresh_settlements() -> dict[str, int]:
    """For every auto-bought lottery row:
      • Update `current_ask` to the live ask (for running P&L on open contracts).
      • If the contract has expired, freeze `settled_ask` to the final value
        (intrinsic at expiration if any data, otherwise 0)."""
    db = get_db()
    today = _now().date().isoformat()
    rows = await db.lottery_history.find({}, {"_id": 0}).to_list(2000)
    live = 0
    settled = 0
    for r in rows:
        if r.get("settled_ask") is not None:
            continue  # already settled
        exp = r.get("exp")
        ticker = r.get("ticker")
        strike = r.get("strike")
        if not (exp and ticker and strike is not None):
            continue
        cur = await _fetch_current_ask(ticker, exp, float(strike))
        update: dict[str, Any] = {
            "current_ask_refreshed_at": _now().isoformat(),
        }
        if cur is not None:
            update["current_ask"] = round(cur, 4)
            live += 1
        if exp < today:
            # Expired — freeze settlement. If we couldn't pull a chain price,
            # treat it as worthless (calls expiring OTM = $0).
            update["settled_ask"] = round(cur, 4) if cur is not None else 0.0
            update["settled_at"] = _now().isoformat()
            settled += 1
        await db.lottery_history.update_one(
            {"ticker": ticker, "date": r.get("date"), "strike": strike, "exp": exp},
            {"$set": update},
        )
    if live or settled:
        await log_activity(
            f"Lottery settlements: refreshed {live} live · settled {settled} expired",
            "info",
        )
    return {"refreshed": live, "settled": settled}


async def track_record() -> dict[str, Any]:
    """Running track record across all auto-bought lottery picks.
    Includes both settled outcomes and unrealized P&L on open positions."""
    db = get_db()
    rows = await db.lottery_history.find({}, {"_id": 0}).to_list(2000)
    if not rows:
        return {
            "total": 0, "open": 0, "settled": 0, "winners": 0,
            "hit_rate": None, "avg_winner_pct": None,
            "unrealized_avg_pct": None, "unrealized_winners": 0,
        }
    settled = [r for r in rows if r.get("settled_ask") is not None]
    winners = [r for r in settled if (r["settled_ask"] or 0) > (r["entry_ask"] or 0) * 2]
    avg_winner = (sum((r["settled_ask"] - r["entry_ask"]) / r["entry_ask"] * 100
                       for r in winners) / len(winners)) if winners else None

    open_rows = [r for r in rows
                 if r.get("settled_ask") is None
                 and r.get("current_ask") is not None
                 and r.get("entry_ask")]
    unrealized_pcts = [
        (r["current_ask"] - r["entry_ask"]) / r["entry_ask"] * 100
        for r in open_rows if r["entry_ask"] > 0
    ]
    unrealized_avg = (sum(unrealized_pcts) / len(unrealized_pcts)) if unrealized_pcts else None
    unrealized_winners = sum(1 for p in unrealized_pcts if p > 0)
    return {
        "total": len(rows),
        "open": len(rows) - len(settled),
        "settled": len(settled),
        "winners": len(winners),
        "hit_rate": round(len(winners) / len(settled), 3) if settled else None,
        "avg_winner_pct": round(avg_winner, 1) if avg_winner is not None else None,
        "unrealized_avg_pct": round(unrealized_avg, 1) if unrealized_avg is not None else None,
        "unrealized_winners": unrealized_winners,
    }


async def recent_picks(days: int = 14, tier: str | None = None) -> list[dict[str, Any]]:
    db = get_db()
    cutoff = (_now() - timedelta(days=days)).date().isoformat()
    q: dict[str, Any] = {"date": {"$gte": cutoff}}
    if tier:
        q["tier"] = tier
    return await db.lottery_history.find(q, {"_id": 0}).sort("date", -1).to_list(200)


# ─────── v5.1 — Dedicated Lottery Scan (Finviz screener) ───────
async def run_dedicated_lottery_scan() -> dict[str, Any]:
    """Runs a Finviz screener filtered to LOTTERY-grade microcaps:
      • Float < 20M
      • Price $1-$20
      • Relative volume > 2× (today vs 20-day avg)
      • Short interest > 15%
    Then applies lottery score bonuses (float<10M, days-to-cover>5, X-Factor
    spike, catalyst<14d, Form 4 cluster, congressional buy, PDUFA match,
    SEC Form 4 cluster). Pure-stock play — no options evaluation.
    """
    import httpx
    from bs4 import BeautifulSoup

    # Finviz screener — float u20 + price $1-$20 + relative volume 2+
    #   + short float 15+
    # See: https://finviz.com/screener.ashx?v=111&f=...
    url = (
        "https://finviz.com/screener.ashx?v=111"
        "&f=sh_float_u20,sh_price_1to20,sh_relvol_o2,sh_short_o15"
        "&o=-volume"
    )
    headers = {
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    }
    candidates: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=20.0, headers=headers,
                                       follow_redirects=True) as c:
            r = await c.get(url)
            if r.status_code != 200:
                return {"candidates": [], "fetched_at": _now().isoformat(),
                         "error": f"finviz HTTP {r.status_code}"}
            soup = BeautifulSoup(r.text, "html.parser")
            table = soup.find("table", class_="screener_table") or \
                     soup.find("table", attrs={"id": "screener-table"})
            if not table:
                return {"candidates": [], "fetched_at": _now().isoformat(),
                         "error": "table not found"}
            for tr in table.find_all("tr")[1:]:
                cells = [td.get_text(strip=True) for td in tr.find_all("td")]
                if len(cells) < 8:
                    continue
                try:
                    candidates.append({
                        "ticker": cells[1].upper(),
                        "company": cells[2] if len(cells) > 2 else "",
                        "sector": cells[3] if len(cells) > 3 else "",
                        "price": float(cells[8].replace("$", "").replace(",", "")) if len(cells) > 8 else None,
                    })
                except (ValueError, IndexError):
                    continue
    except Exception as e:
        logger.warning("dedicated lottery scan fetch failed: %s", e)
        return {"candidates": [], "fetched_at": _now().isoformat(), "error": str(e)}

    # Enrich with score bonuses from existing collections
    db = get_db()
    last_scan = await db.scan_results.find_one({}, {"_id": 0, "results": 1},
                                                  sort=[("started_at", -1)])
    scan_by_t: dict[str, dict] = {}
    if last_scan:
        for r in (last_scan.get("results") or []):
            scan_by_t[(r.get("ticker") or "").upper()] = r

    today = _now().date().isoformat()
    enriched: list[dict[str, Any]] = []
    for cand in candidates:
        t = cand["ticker"]
        bonuses: list[str] = []
        score = 50  # base score for passing the Finviz screen
        sr = scan_by_t.get(t) or {}
        sigs = sr.get("signals") or {}
        if isinstance(sigs, dict):
            if "insider_cluster_buy" in sigs:
                score += 10; bonuses.append("INSIDER_CLUSTER")
            if "CONGRESSIONAL_BUY" in sigs:
                score += 8; bonuses.append("CONGRESS_BUY")
            if "UNUSUAL_FLOW" in sigs:
                score += 8; bonuses.append("UNUSUAL_FLOW")
            if "upcoming_earnings" in sigs:
                score += 6; bonuses.append("CATALYST")
        # PDUFA cross-ref
        if await db.pharma_pdufa.count_documents({"ticker": t}):
            score += 10
            bonuses.append("PHARMA_PDUFA")
        # SEC Form 4 cluster cross-ref (last 7d)
        if await db.sec_filings.count_documents(
            {"ticker": t, "form": "Form 4",
              "created_at": {"$gte": (_now() - timedelta(days=7)).isoformat()}}
        ) >= 2:
            score += 8
            bonuses.append("SEC_FORM4_CLUSTER")
        # X-Factor social spike
        if await db.x_factor_alerts.count_documents(
            {"ticker": t, "fired_at": {"$gte": (_now() - timedelta(days=3)).isoformat()}}
        ):
            score += 6
            bonuses.append("X_FACTOR")
        tier = tier_for(score)
        suggested_risk = TIER_LIMITS.get(tier, 50)
        enriched.append({
            **cand,
            "lottery_score": min(100, score),
            "tier": tier,
            "bonuses": bonuses,
            "suggested_risk": suggested_risk,
            "scanned_at": _now().isoformat(),
        })
    enriched.sort(key=lambda x: -x["lottery_score"])
    # Persist as dedicated_lottery_scan
    await db.lottery_dedicated_scan.update_one(
        {"_id": "current"},
        {"$set": {"candidates": enriched, "scanned_at": _now().isoformat(),
                   "date": today}},
        upsert=True,
    )
    await log_activity(
        f"Dedicated lottery scan: {len(enriched)} candidates (Finviz microcap screen)",
        "info",
    )
    return {"candidates": enriched, "fetched_at": _now().isoformat(),
             "count": len(enriched)}


async def latest_dedicated_lottery() -> list[dict[str, Any]]:
    db = get_db()
    doc = await db.lottery_dedicated_scan.find_one({"_id": "current"}, {"_id": 0})
    return doc.get("candidates", []) if doc else []


async def add_manual_play(ticker: str, entry_price: float,
                            lottery_score: int | None = None,
                            risk_amount: float | None = None) -> dict[str, Any]:
    """User clicks ADD on a screener result and types their entry price."""
    db = get_db()
    today = _now().date().isoformat()
    doc = stamped({
        "ticker": ticker.upper(),
        "date": today,
        "entry_price": float(entry_price),
        "peak_price": float(entry_price),
        "current_price": float(entry_price),
        "lottery_score": lottery_score,
        "risk_amount": float(risk_amount) if risk_amount else None,
        "is_manual": True,
        "is_active": True,
        "hold_end": (_now().date() + timedelta(days=14)).isoformat(),
        "sent_to_trade_floor": False,
        "added_at": _now().isoformat(),
    })
    await db.lottery_manual_plays.insert_one(doc)
    await log_activity(f"Lottery: manual play added · {ticker} @ ${entry_price}", "info")
    return doc


async def settle_manual_play(ticker: str, exit_price: float, play_date: str) -> dict[str, Any]:
    """User enters EXACT exit price — calculates realized P&L permanently."""
    db = get_db()
    play = await db.lottery_manual_plays.find_one(
        {"ticker": ticker.upper(), "date": play_date, "is_active": True},
        {"_id": 0},
    )
    if not play:
        return {"ok": False, "reason": "play_not_found"}
    entry = play["entry_price"]
    realized_pct = ((exit_price - entry) / entry * 100) if entry else 0
    update = {
        "exit_price": float(exit_price),
        "realized_pct": round(realized_pct, 2),
        "is_active": False,
        "settled_at": _now().isoformat(),
    }
    await db.lottery_manual_plays.update_one(
        {"ticker": ticker.upper(), "date": play_date}, {"$set": update},
    )
    await log_activity(
        f"Lottery: settled {ticker} · entry ${entry} → exit ${exit_price} "
        f"({realized_pct:+.2f}%)", "info",
    )
    return {"ok": True, "realized_pct": realized_pct}


async def update_manual_peak_marks(refresh: bool = True) -> int:
    """Refresh current_price + peak_price for every active manual play."""
    if not refresh:
        return 0
    from . import pricer
    db = get_db()
    plays = await db.lottery_manual_plays.find({"is_active": True}, {"_id": 0}).to_list(200)
    updated = 0
    for p in plays:
        cur = await pricer.get_latest_close(p["ticker"])
        if cur is None:
            continue
        peak = max(p.get("peak_price") or 0, cur)
        await db.lottery_manual_plays.update_one(
            {"ticker": p["ticker"], "date": p["date"]},
            {"$set": {"current_price": cur, "peak_price": peak,
                       "marks_updated_at": _now().isoformat()}},
        )
        updated += 1
    return updated


async def list_manual_plays(active_only: bool = False) -> list[dict[str, Any]]:
    db = get_db()
    q = {"is_active": True} if active_only else {}
    return await db.lottery_manual_plays.find(q, {"_id": 0}).sort("added_at", -1).to_list(500)


async def lottery_manual_track_record() -> dict[str, Any]:
    """Completely isolated from any other tracker."""
    plays = await list_manual_plays(active_only=False)
    settled = [p for p in plays if not p.get("is_active")]
    winners = [p for p in settled if (p.get("realized_pct") or 0) > 0]
    total_pnl = sum((p.get("realized_pct") or 0) for p in settled)
    avg_winner = (sum(p["realized_pct"] for p in winners) / len(winners)) if winners else None
    losers = [p for p in settled if (p.get("realized_pct") or 0) <= 0]
    avg_loser = (sum(p["realized_pct"] for p in losers) / len(losers)) if losers else None
    return {
        "total_plays": len(plays),
        "settled": len(settled),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": round(len(winners) / len(settled), 3) if settled else None,
        "avg_winner_pct": round(avg_winner, 2) if avg_winner is not None else None,
        "avg_loser_pct": round(avg_loser, 2) if avg_loser is not None else None,
        "total_pnl_pct": round(total_pnl, 2),
        "history": settled,
    }

