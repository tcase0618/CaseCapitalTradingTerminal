"""Portfolio P&L tracker — records every scan-surfaced stock, computes
7/30/90-day returns, and aggregates by signal combo + strategy.

Public API:
- record_scan_picks(scan_doc)            — log every result for later returns
- refresh_due_returns()                  — fill 7/30/90d return columns
- refresh_due_options_returns()          — refresh options proxy + actual price
- performance_by_signals()               — combo → avg return, win rate
- options_performance_summary()          — strategy → avg, IV at entry, etc.
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import FEATURE_VERSION, get_db, log_activity, stamped

logger = logging.getLogger(__name__)


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _fetch_close(ticker: str, days_ago: int = 0) -> float | None:
    """Get a close price `days_ago` ago. days_ago=0 returns latest."""
    try:
        import yfinance as yf

        def _sync():
            t = yf.Ticker(ticker)
            period = "1y" if days_ago > 90 else ("3mo" if days_ago > 14 else "1mo")
            h = t.history(period=period)
            if len(h) == 0:
                return None
            if days_ago <= 0:
                return float(h["Close"].iloc[-1])
            target_idx = max(0, len(h) - 1 - days_ago)
            return float(h["Close"].iloc[target_idx])
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync)
    except Exception as e:
        logger.warning("close fetch failed %s/%s: %s", ticker, days_ago, e)
        return None


# ============== Recording ==============
async def record_scan_picks(scan_doc: dict[str, Any]) -> int:
    """For every result in a scan, write a signal_performance row + (if options
    play exists) an options_performance row. Idempotent on (ticker, date)."""
    db = get_db()
    today = _today_iso()
    written = 0
    for r in scan_doc.get("results", []):
        ticker = r.get("ticker")
        if not ticker:
            continue
        entry_price = r.get("price")
        signals = r.get("signals", [])

        # signal_performance: 1 row per (ticker, date)
        await db.signal_performance.update_one(
            {"ticker": ticker, "date": today},
            {"$set": stamped({
                "ticker": ticker,
                "date": today,
                "ts": _now().isoformat(),
                "signals": signals,
                "signal_score": r.get("signal_score", 0),
                "entry_price": entry_price,
                "risk_level": (r.get("risk") or {}).get("level"),
                "squeeze_score": (r.get("squeeze") or {}).get("score"),
                "catalyst_date": (r.get("time_target") or {}).get("target_date") or r.get("catalyst_date", ""),
                "return_7d": None,
                "return_30d": None,
                "return_90d": None,
            })},
            upsert=True,
        )

        # options_performance — only if options block present
        opts = r.get("options")
        if opts and opts.get("contract"):
            ct = opts["contract"]
            await db.options_performance.update_one(
                {"ticker": ticker, "date": today, "expiration": ct.get("expiration")},
                {"$set": stamped({
                    "ticker": ticker,
                    "date": today,
                    "ts": _now().isoformat(),
                    "strategy_type": opts.get("strategy"),
                    "direction": opts.get("direction"),
                    "buy_strike": ct.get("strike"),
                    "expiration": ct.get("expiration"),
                    "estimated_premium": ct.get("premium"),
                    "delta_at_entry": ct.get("delta"),
                    "iv_rank_at_entry": opts.get("iv_rank"),
                    "iv_label_at_entry": opts.get("iv_label"),
                    "signals_fired": signals,
                    "catalyst_date": (r.get("time_target") or {}).get("target_date") or "",
                    "crush_risk": opts.get("crush_risk"),
                    "entry_spot": opts.get("spot"),
                    "spread": opts.get("spread"),
                    "estimated_return_proxy": None,
                    "estimated_return_actual": None,
                    "actual_stock_return": None,
                })},
                upsert=True,
            )
        written += 1
    if written:
        await log_activity(f"P&L tracker recorded {written} picks for {today}", "info")
    return written


# ============== Daily refresh: fill 7/30/90d returns ==============
async def refresh_due_returns() -> dict[str, int]:
    """Find rows where 7/30/90d returns are due (per row date) and fetch them."""
    db = get_db()
    now = _now()
    counters = {"r7": 0, "r30": 0, "r90": 0}

    # We loop over recent rows that are missing at least one return
    cursor = db.signal_performance.find({
        "$or": [
            {"return_7d": None},
            {"return_30d": None},
            {"return_90d": None},
        ]
    }, projection={"_id": 0})
    rows = await cursor.to_list(2000)

    for r in rows:
        try:
            row_date = datetime.fromisoformat(r["date"]).replace(tzinfo=timezone.utc)
        except Exception:
            continue
        age_days = (now - row_date).days
        entry = r.get("entry_price")
        if entry is None:
            entry = await _fetch_close(r["ticker"], days_ago=age_days)
            if entry:
                await db.signal_performance.update_one(
                    {"ticker": r["ticker"], "date": r["date"]},
                    {"$set": {"entry_price": entry}},
                )
        if not entry:
            continue
        updates: dict[str, Any] = {}
        if age_days >= 7 and r.get("return_7d") is None:
            cur = await _fetch_close(r["ticker"], days_ago=max(0, age_days - 7))
            if cur:
                updates["return_7d"] = round((cur - entry) / entry * 100.0, 2)
                counters["r7"] += 1
        if age_days >= 30 and r.get("return_30d") is None:
            cur = await _fetch_close(r["ticker"], days_ago=max(0, age_days - 30))
            if cur:
                updates["return_30d"] = round((cur - entry) / entry * 100.0, 2)
                counters["r30"] += 1
        if age_days >= 90 and r.get("return_90d") is None:
            cur = await _fetch_close(r["ticker"], days_ago=max(0, age_days - 90))
            if cur:
                updates["return_90d"] = round((cur - entry) / entry * 100.0, 2)
                counters["r90"] += 1
        if updates:
            await db.signal_performance.update_one(
                {"ticker": r["ticker"], "date": r["date"]},
                {"$set": updates},
            )
    return counters


# ============== Options performance refresh (Day+3 actual + proxy) ==============
async def _fetch_option_last(ticker: str, expiration: str, strike: float, is_call: bool) -> float | None:
    """Re-fetch a specific option's last price."""
    try:
        import yfinance as yf

        def _sync():
            t = yf.Ticker(ticker)
            chain = t.option_chain(expiration)
            df = chain.calls if is_call else chain.puts
            if not len(df):
                return None
            # nearest strike row
            d = (df["strike"] - strike).abs()
            idx = d.idxmin()
            row = df.loc[idx]
            last = float(row.get("lastPrice") or 0)
            if last > 0:
                return last
            bid = float(row.get("bid") or 0)
            ask = float(row.get("ask") or 0)
            return (bid + ask) / 2 if bid and ask else None
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync)
    except Exception as e:
        logger.warning("option last fetch failed %s/%s/%s: %s", ticker, expiration, strike, e)
        return None


async def refresh_due_options_returns() -> int:
    """For options_performance rows older than 3 days: log proxy AND actual."""
    db = get_db()
    now = _now()
    cursor = db.options_performance.find({
        "$or": [
            {"estimated_return_proxy": None},
            {"estimated_return_actual": None},
        ],
    }, projection={"_id": 0})
    rows = await cursor.to_list(2000)
    updated = 0
    for r in rows:
        try:
            row_date = datetime.fromisoformat(r["date"]).replace(tzinfo=timezone.utc)
        except Exception:
            continue
        age = (now - row_date).days
        if age < 3:
            continue
        ticker = r["ticker"]
        entry_spot = r.get("entry_spot")
        delta = r.get("delta_at_entry") or 0.5
        entry_premium = r.get("estimated_premium") or 0
        updates: dict[str, Any] = {}

        # proxy: stock_move × delta × 100 (per contract)
        if r.get("estimated_return_proxy") is None:
            cur_spot = await _fetch_close(ticker, days_ago=0)
            if cur_spot and entry_spot:
                stock_pct = (cur_spot - entry_spot) / entry_spot * 100.0
                # premium delta from spot move
                premium_delta = (cur_spot - entry_spot) * delta
                if entry_premium > 0:
                    proxy_pct = premium_delta / entry_premium * 100.0
                else:
                    proxy_pct = 0.0
                updates["actual_stock_return"] = round(stock_pct, 2)
                updates["estimated_return_proxy"] = round(proxy_pct, 2)

        # actual: re-fetch the option's last price
        if r.get("estimated_return_actual") is None:
            is_call = r.get("direction", "BULL") == "BULL"
            cur_premium = await _fetch_option_last(
                ticker, r["expiration"], r["buy_strike"], is_call,
            )
            if cur_premium and entry_premium > 0:
                actual_pct = (cur_premium - entry_premium) / entry_premium * 100.0
                updates["estimated_return_actual"] = round(actual_pct, 2)
                updates["current_option_premium"] = round(cur_premium, 2)

        if updates:
            await db.options_performance.update_one(
                {"_id_lookup": True, "ticker": ticker, "date": r["date"], "expiration": r["expiration"]},
                {"$set": updates},
            )
            # Re-run with the right filter (no _id_lookup field)
            await db.options_performance.update_one(
                {"ticker": ticker, "date": r["date"], "expiration": r["expiration"]},
                {"$set": updates},
            )
            updated += 1
    if updated:
        await log_activity(f"Options P&L refreshed {updated} rows", "info")
    return updated


# ============== Aggregations ==============
async def performance_by_signals() -> list[dict[str, Any]]:
    db = get_db()
    rows = await db.signal_performance.find({}, {"_id": 0}).to_list(5000)
    from collections import defaultdict
    agg30: dict[str, list[float]] = defaultdict(list)
    agg7: dict[str, list[float]] = defaultdict(list)
    agg90: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        key = " + ".join(sorted(r.get("signals", [])))
        if r.get("return_7d") is not None:
            agg7[key].append(r["return_7d"])
        if r.get("return_30d") is not None:
            agg30[key].append(r["return_30d"])
        if r.get("return_90d") is not None:
            agg90[key].append(r["return_90d"])

    out: list[dict[str, Any]] = []
    keys = set(agg7) | set(agg30) | set(agg90)
    for k in keys:
        v30 = agg30.get(k, [])
        v7 = agg7.get(k, [])
        v90 = agg90.get(k, [])
        out.append({
            "combo": k,
            "n": max(len(v30), len(v7), len(v90)),
            "avg_7d": round(sum(v7) / len(v7), 2) if v7 else None,
            "avg_30d": round(sum(v30) / len(v30), 2) if v30 else None,
            "avg_90d": round(sum(v90) / len(v90), 2) if v90 else None,
            "win_rate_30d": round(sum(1 for x in v30 if x > 0) / len(v30) * 100, 1) if v30 else None,
        })
    out.sort(key=lambda x: (x["avg_30d"] is None, -(x["avg_30d"] or -999)))
    return out


async def options_performance_summary() -> dict[str, Any]:
    db = get_db()
    rows = await db.options_performance.find({}, {"_id": 0}).to_list(5000)
    from collections import defaultdict
    by_strategy: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("estimated_return_proxy") is None and r.get("estimated_return_actual") is None:
            continue
        by_strategy[r.get("strategy_type") or "?"].append(r)

    summary = []
    for strat, items in by_strategy.items():
        proxies = [i["estimated_return_proxy"] for i in items if i.get("estimated_return_proxy") is not None]
        actuals = [i["estimated_return_actual"] for i in items if i.get("estimated_return_actual") is not None]
        ivs = [i["iv_rank_at_entry"] for i in items if i.get("iv_rank_at_entry") is not None]
        wins = [a for a in actuals if a > 0]
        summary.append({
            "strategy": strat,
            "n": len(items),
            "avg_return_proxy": round(sum(proxies) / len(proxies), 1) if proxies else None,
            "avg_return_actual": round(sum(actuals) / len(actuals), 1) if actuals else None,
            "avg_iv_at_entry": round(sum(ivs) / len(ivs), 1) if ivs else None,
            "win_rate_actual": round(len(wins) / len(actuals) * 100, 1) if actuals else None,
        })
    summary.sort(key=lambda x: (x["avg_return_actual"] is None, -(x["avg_return_actual"] or -999)))

    # Win rate by crush risk level — confirms AVOID_OPTIONS protected capital
    by_crush: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r.get("estimated_return_actual") is not None:
            by_crush[r.get("crush_risk", "?")].append(r["estimated_return_actual"])
    crush_rows = []
    for c, vs in by_crush.items():
        crush_rows.append({
            "crush_risk": c,
            "n": len(vs),
            "avg_return": round(sum(vs) / len(vs), 1),
            "win_rate": round(sum(1 for x in vs if x > 0) / len(vs) * 100, 1),
        })
    return {
        "by_strategy": summary,
        "by_crush_risk": crush_rows,
        "feature_version": FEATURE_VERSION,
    }
