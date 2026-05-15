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
from . import pricer

logger = logging.getLogger(__name__)


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _fetch_close(ticker: str, days_ago: int = 0) -> float | None:
    """Get a close price `days_ago` ago via Massive API (yfinance fallback).
    days_ago=0 returns the latest available close."""
    if days_ago <= 0:
        return await pricer.get_latest_close(ticker)
    target = (datetime.now(timezone.utc).date() - timedelta(days=days_ago)).isoformat()
    return await pricer.get_close_on_date(ticker, target)


# ============== Recording ==============
async def record_scan_picks(scan_doc: dict[str, Any]) -> int:
    """For every result in a scan, write a signal_performance row + (if options
    play exists) an options_performance row. Idempotent on (ticker, date).
    Also writes a `signal_first_seen` row ONCE per ticker — locks in the
    very first price + signal combo we surfaced, so the Performance page
    can show 'as if I bought on day-one of signal' P&L."""
    db = get_db()
    today = _today_iso()
    written = 0
    for r in scan_doc.get("results", []):
        ticker = r.get("ticker")
        if not ticker:
            continue
        entry_price = r.get("price")
        signals = r.get("signals", [])

        # signal_first_seen — ONE row per ticker, INSERT-only (never updated).
        # First time we ever surfaced this ticker, with that day's price.
        await db.signal_first_seen.update_one(
            {"ticker": ticker},
            {"$setOnInsert": stamped({
                "ticker": ticker,
                "first_seen_date": today,
                "first_seen_ts": _now().isoformat(),
                "first_seen_price": entry_price,
                "first_signals": signals,
                "first_signal_score": r.get("signal_score", 0),
                "first_risk_level": (r.get("risk") or {}).get("level"),
                "first_options_strategy": (r.get("options") or {}).get("strategy"),
                "first_options_contract": (r.get("options") or {}).get("contract"),
                "first_options_iv_rank": (r.get("options") or {}).get("iv_rank"),
                "first_options_premium": ((r.get("options") or {}).get("contract") or {}).get("premium"),
                "first_options_delta": ((r.get("options") or {}).get("contract") or {}).get("delta"),
                "first_options_strike": ((r.get("options") or {}).get("contract") or {}).get("strike"),
                "first_options_expiration": ((r.get("options") or {}).get("contract") or {}).get("expiration"),
                "first_thesis": r.get("thesis", ""),
            })},
            upsert=True,
        )
        # Also bump last_seen on every appearance
        await db.signal_first_seen.update_one(
            {"ticker": ticker},
            {"$set": {
                "last_seen_date": today,
                "last_seen_price": entry_price,
                "last_signal_score": r.get("signal_score", 0),
            },
             "$inc": {"times_found": 1}},
        )

        # signal_performance: 1 row per (ticker, date) — feeds 7/30/90d returns
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


async def ensure_first_seen_backfill() -> int:
    """One-time backfill: for every distinct ticker in signal_performance,
    create a signal_first_seen row from the earliest historical entry.
    Idempotent — only inserts new rows."""
    db = get_db()
    # Aggregate earliest entry per ticker
    pipeline = [
        {"$sort": {"ticker": 1, "date": 1}},
        {"$group": {
            "_id": "$ticker",
            "first_seen_date": {"$first": "$date"},
            "first_seen_price": {"$first": "$entry_price"},
            "first_signals": {"$first": "$signals"},
            "first_signal_score": {"$first": "$signal_score"},
            "first_risk_level": {"$first": "$risk_level"},
            "first_ts": {"$first": "$ts"},
            "times_found": {"$sum": 1},
            "last_seen_date": {"$last": "$date"},
        }},
    ]
    backfilled = 0
    async for r in db.signal_performance.aggregate(pipeline):
        ticker = r.get("_id")
        if not ticker or not r.get("first_seen_price"):
            continue
        res = await db.signal_first_seen.update_one(
            {"ticker": ticker},
            {"$setOnInsert": stamped({
                "ticker": ticker,
                "first_seen_date": r["first_seen_date"],
                "first_seen_ts": r.get("first_ts"),
                "first_seen_price": r["first_seen_price"],
                "first_signals": r.get("first_signals", []),
                "first_signal_score": r.get("first_signal_score"),
                "first_risk_level": r.get("first_risk_level"),
                "last_seen_date": r.get("last_seen_date"),
                "times_found": r.get("times_found", 1),
            })},
            upsert=True,
        )
        if res.upserted_id:
            backfilled += 1
    if backfilled:
        await log_activity(f"Backfilled {backfilled} signal_first_seen rows", "info")
    return backfilled


async def refresh_all_entry_prices(force: bool = True) -> dict[str, int]:
    """Re-fetch the close on `first_seen_date` for every tracked ticker
    using Massive API. Updates both `signal_first_seen.first_seen_price`
    and matching `signal_performance.entry_price` rows. This corrects any
    yfinance-era entry prices once Massive is plugged in."""
    db = get_db()
    # Clear stale price caches first
    await pricer.clear_cache()

    rows = await db.signal_first_seen.find({}, {"_id": 0}).to_list(2000)
    updated_first_seen = 0
    updated_perf = 0
    failures = 0
    for r in rows:
        ticker = r["ticker"]
        d = r.get("first_seen_date")
        if not ticker or not d:
            continue
        new_price = await pricer.get_close_on_date(ticker, d)
        if not new_price:
            failures += 1
            continue
        # Only update if it actually changed (or row had no price)
        cur = r.get("first_seen_price")
        if cur and abs(cur - new_price) < 0.01:
            continue
        await db.signal_first_seen.update_one(
            {"ticker": ticker},
            {"$set": {
                "first_seen_price": round(new_price, 2),
                "first_seen_price_source": "massive" if pricer.has_massive() else "yfinance",
                "first_seen_price_refreshed_at": _now().isoformat(),
            }},
        )
        updated_first_seen += 1
        # Also refresh the matching signal_performance row's entry_price
        res = await db.signal_performance.update_one(
            {"ticker": ticker, "date": d},
            {"$set": {"entry_price": round(new_price, 2)}},
        )
        if res.modified_count:
            updated_perf += 1
    await log_activity(
        f"Refreshed {updated_first_seen} entry prices via Massive ({failures} failed)",
        "info",
    )
    return {
        "first_seen_updated": updated_first_seen,
        "perf_rows_updated": updated_perf,
        "failures": failures,
        "source": pricer.source_label(),
    }



async def daily_pnl_curve(days: int = 90) -> list[dict[str, Any]]:
    """Robinhood-style stock curve via Massive API (yfinance fallback). For
    each date in the last N days, average % gain across every ticker that
    had been signaled by that date. Equal-weight."""
    db = get_db()
    rows = await db.signal_first_seen.find({}, {"_id": 0}).to_list(2000)
    if not rows:
        return []
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=days + 5)
    rows = [r for r in rows if r.get("first_seen_price") and r.get("first_seen_date")
             and r.get("first_seen_date") >= cutoff.isoformat()]
    if not rows:
        return []

    tickers = sorted({r["ticker"] for r in rows})
    hist = await pricer.batch_history(tickers, days=days + 10)
    if not hist:
        return []

    # date → {ticker: close}
    closes_by_date: dict[str, dict[str, float]] = {}
    for t, series in hist.items():
        for d, v in series.items():
            closes_by_date.setdefault(d, {})[t] = v

    first_seen_map = {r["ticker"]: (r["first_seen_date"], r["first_seen_price"]) for r in rows}
    sorted_dates = sorted(closes_by_date.keys())
    today = datetime.now(timezone.utc).date().isoformat()
    floor = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
    out: list[dict[str, Any]] = []
    for d in sorted_dates:
        if d < floor:
            continue
        gains: list[float] = []
        for t, (entry_date, entry_price) in first_seen_map.items():
            if entry_date > d:
                continue
            cur = closes_by_date.get(d, {}).get(t)
            if cur is None or not entry_price:
                continue
            gains.append((cur - entry_price) / entry_price * 100.0)
        if not gains:
            continue
        winners = sum(1 for g in gains if g > 0)
        out.append({
            "date": d,
            "avg_gain_pct": round(sum(gains) / len(gains), 2),
            "positions": len(gains),
            "winners": winners,
            "losers": len(gains) - winners,
            "is_today": d == today,
        })
    return out


async def daily_options_pnl_curve(days: int = 90) -> list[dict[str, Any]]:
    """Robinhood-style OPTIONS curve. Sources from `options_performance` —
    one row per (ticker, scan_date, expiration). For each historical day,
    average the proxy options P&L across every position that was open by
    that day (entered ≤ day, not yet expired):
        option_pl_pct = (current_spot - entry_spot) * delta * sign / premium * 100
    Capped at -100% (you can't lose more than premium)."""
    db = get_db()
    rows = await db.options_performance.find({}, {"_id": 0}).to_list(5000)
    if not rows:
        return []
    cutoff = datetime.now(timezone.utc).date() - timedelta(days=days + 5)
    rows = [r for r in rows
            if r.get("date") and r["date"] >= cutoff.isoformat()
            and r.get("entry_spot") and r.get("estimated_premium")
            and float(r.get("estimated_premium") or 0) > 0]
    if not rows:
        return []

    tickers = sorted({r["ticker"] for r in rows})
    hist = await pricer.batch_history(tickers, days=days + 10)
    if not hist:
        return []

    closes_by_date: dict[str, dict[str, float]] = {}
    for t, series in hist.items():
        for d, v in series.items():
            closes_by_date.setdefault(d, {})[t] = v

    sorted_dates = sorted(closes_by_date.keys())
    today = datetime.now(timezone.utc).date().isoformat()
    floor = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
    out: list[dict[str, Any]] = []
    for d in sorted_dates:
        if d < floor:
            continue
        gains: list[float] = []
        strats: set[str] = set()
        winners = 0
        for r in rows:
            entry_date = r["date"]
            if entry_date > d:
                continue
            expiration = r.get("expiration")
            if expiration and expiration < d:
                continue
            t = r["ticker"]
            cur = closes_by_date.get(d, {}).get(t)
            if cur is None:
                continue
            entry_spot = float(r["entry_spot"])
            delta = float(r.get("delta_at_entry") or 0)
            premium = float(r["estimated_premium"])
            if delta == 0 or premium <= 0:
                continue
            sign = 1 if (r.get("direction") or "BULL") == "BULL" else -1
            premium_delta = (cur - entry_spot) * delta * sign
            pct = max(premium_delta / premium * 100.0, -100.0)
            gains.append(pct)
            strats.add(r.get("strategy_type") or "?")
            if pct > 0:
                winners += 1
        if not gains:
            continue
        out.append({
            "date": d,
            "avg_gain_pct": round(sum(gains) / len(gains), 2),
            "positions": len(gains),
            "winners": winners,
            "losers": len(gains) - winners,
            "strategies": len(strats),
            "is_today": d == today,
        })
    return out


async def signals_tracker_summary(limit: int = 200) -> list[dict[str, Any]]:
    db = get_db()
    rows = await db.signal_first_seen.find({}, {"_id": 0}).sort("first_seen_date", -1).to_list(limit)
    out: list[dict[str, Any]] = []
    tickers = [r["ticker"] for r in rows]
    if not tickers:
        return out

    # Single pricer call routes through Massive first, yfinance fallback,
    # with built-in 10-min cache.
    cur_prices = await pricer.batch_latest_closes(tickers)

    for r in rows:
        t = r["ticker"]
        entry = r.get("first_seen_price")
        current = cur_prices.get(t)
        gain_pct = None
        gain_abs = None
        if entry and current and entry > 0:
            gain_pct = round((current - entry) / entry * 100, 2)
            gain_abs = round(current - entry, 2)
        delta = r.get("first_options_delta") or 0.0
        opt_premium = r.get("first_options_premium") or 0.0
        opt_proxy_pct = None
        if gain_pct is not None and delta and opt_premium > 0 and current and entry:
            premium_delta = (current - entry) * delta
            opt_proxy_pct = round(premium_delta / opt_premium * 100, 2)
        out.append({
            "ticker": t,
            "first_seen_date": r.get("first_seen_date"),
            "first_seen_price": entry,
            "current_price": current,
            "gain_pct": gain_pct,
            "gain_abs": gain_abs,
            "signals": r.get("first_signals") or [],
            "signal_score": r.get("first_signal_score"),
            "thesis": r.get("first_thesis", ""),
            "times_found": r.get("times_found", 1),
            "options_strategy": r.get("first_options_strategy"),
            "options_strike": r.get("first_options_strike"),
            "options_type": (r.get("first_options_contract") or {}).get("type"),
            "options_expiration": r.get("first_options_expiration"),
            "options_premium_at_entry": opt_premium or None,
            "options_iv_rank_at_entry": r.get("first_options_iv_rank"),
            "options_return_proxy_pct": opt_proxy_pct,
            "risk_level": r.get("first_risk_level"),
        })
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
