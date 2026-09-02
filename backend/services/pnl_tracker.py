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
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any

from .db import FEATURE_VERSION, get_db, log_activity, stamped
from . import pricer
from .market_dates import add_trading_days, trading_days_between

logger = logging.getLogger(__name__)


def _today_iso() -> str:
    # Performance records are operator-facing and must use the terminal's ET
    # trading date, not UTC (which rolls over during the US evening session).
    return datetime.now(ZoneInfo("America/New_York")).date().isoformat()


def _today_et():
    return datetime.now(ZoneInfo("America/New_York")).date()


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
    play exists) an options_performance row. Idempotent on (ticker, date,
    screener_id).
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
        scanner = r.get("strategy_scanner") or {}
        screener_id = str(scanner.get("screener_id") or r.get("source_scan") or "CORE")
        scanner_family = str(scanner.get("family") or r.get("scanner_family") or "CORE")

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

        # signal_performance: one row per (ticker, date, screener). This keeps
        # independent strategy evidence separate even when the ticker overlaps.
        await db.signal_performance.update_one(
            {"ticker": ticker, "date": today, "screener_id": screener_id},
            {"$set": stamped({
                "ticker": ticker,
                "date": today,
                "screener_id": screener_id,
                "scanner_family": scanner_family,
                "ts": _now().isoformat(),
                "signals": signals,
                "signal_score": r.get("signal_score", 0),
                "regime": r.get("regime"),
                "regime_playbook": r.get("regime_playbook"),
                "pead": r.get("pead"),
                "sector": r.get("sector"),
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
        today_et = _today_et()
        row_calendar_date = row_date.date()
        age_days = trading_days_between(row_calendar_date, today_et)
        entry = r.get("entry_price")
        if entry is None:
            entry = await pricer.get_close_on_date(r["ticker"], row_calendar_date.isoformat())
            if entry:
                await db.signal_performance.update_one(
                    {"ticker": r["ticker"], "date": r["date"]},
                    {"$set": {"entry_price": entry}},
                )
        if not entry:
            continue
        updates: dict[str, Any] = {}
        if age_days >= 7 and r.get("return_7d") is None:
            target = add_trading_days(row_calendar_date, 7)
            cur = await pricer.get_close_on_date(r["ticker"], target.isoformat())
            if cur is not None:
                updates["return_7d"] = round((cur - entry) / entry * 100.0, 2)
                counters["r7"] += 1
        if age_days >= 30 and r.get("return_30d") is None:
            target = add_trading_days(row_calendar_date, 30)
            cur = await pricer.get_close_on_date(r["ticker"], target.isoformat())
            if cur is not None:
                updates["return_30d"] = round((cur - entry) / entry * 100.0, 2)
                counters["r30"] += 1
        if age_days >= 90 and r.get("return_90d") is None:
            target = add_trading_days(row_calendar_date, 90)
            cur = await pricer.get_close_on_date(r["ticker"], target.isoformat())
            if cur is not None:
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


async def refresh_all_entry_prices(force: bool = False) -> dict[str, int]:
    """Fill MISSING entry prices using Massive's historical close. Never
    overwrites an existing valid price — the original intraday yfinance
    fill is the truth-of-entry. Set `force=True` only if you want to
    rewrite every row (typically only after a data corruption)."""
    db = get_db()
    # Clear stale price caches so current-price queries re-hit Massive
    await pricer.clear_cache()

    rows = await db.signal_first_seen.find({}, {"_id": 0}).to_list(2000)
    filled_first_seen = 0
    filled_perf = 0
    failures = 0
    for r in rows:
        ticker = r["ticker"]
        d = r.get("first_seen_date")
        if not ticker or not d:
            continue
        cur = r.get("first_seen_price")
        # Skip rows that already have a valid entry price (the intraday yfinance
        # fill is more accurate than a day-end close).
        if cur and cur > 0 and not force:
            continue
        new_price = await pricer.get_close_on_date(ticker, d)
        if not new_price:
            failures += 1
            continue
        await db.signal_first_seen.update_one(
            {"ticker": ticker},
            {"$set": {
                "first_seen_price": round(new_price, 2),
                "first_seen_price_source": pricer.source_label(),
                "first_seen_price_refreshed_at": _now().isoformat(),
            }},
        )
        filled_first_seen += 1
        res = await db.signal_performance.update_one(
            {"ticker": ticker, "date": d, "entry_price": None},
            {"$set": {"entry_price": round(new_price, 2)}},
        )
        if res.modified_count:
            filled_perf += 1
    await log_activity(
        f"Entry-price backfill: filled {filled_first_seen} first_seen + "
        f"{filled_perf} perf rows ({failures} failed)", "info",
    )
    return {
        "first_seen_filled": filled_first_seen,
        "perf_rows_filled": filled_perf,
        "failures": failures,
        "source": pricer.source_label(),
    }


async def restore_intraday_entry_prices() -> dict[str, int]:
    """Restore first_seen_price from the earliest scan_results record where
    we recorded the actual intraday yfinance price at scan time. This
    rebuilds entries the Massive-refresh overwrote with day-end closes."""
    db = get_db()
    rows = await db.signal_first_seen.find({}, {"_id": 0}).to_list(2000)
    restored = 0
    for r in rows:
        ticker = r["ticker"]
        d = r.get("first_seen_date")
        if not ticker or not d:
            continue
        # Walk scan_results from oldest to newest, find first one that
        # surfaced this ticker on or near `d` — that price is the truth.
        async for scan in db.scan_results.find(
            {"results.ticker": ticker}, {"_id": 0, "results": 1, "finished_at": 1},
        ).sort("finished_at", 1):
            for sr in scan.get("results") or []:
                if sr.get("ticker") != ticker:
                    continue
                price = sr.get("price")
                if price and price > 0:
                    await db.signal_first_seen.update_one(
                        {"ticker": ticker},
                        {"$set": {
                            "first_seen_price": round(float(price), 4),
                            "first_seen_price_source": "restored_intraday",
                            "first_seen_price_refreshed_at": _now().isoformat(),
                        }},
                    )
                    restored += 1
                    break
            else:
                continue
            break
    await log_activity(f"Restored {restored} intraday entry prices", "info")
    return {"restored": restored}


async def refresh_current_prices_only() -> dict[str, Any]:
    """Force-refresh the current-price cache for every tracked ticker via
    Massive's grouped endpoint. Does NOT touch entry prices."""
    db = get_db()
    rows = await db.signal_first_seen.find(
        {}, {"_id": 0, "ticker": 1},
    ).to_list(2000)
    tickers = [r["ticker"] for r in rows if r.get("ticker")]
    if not tickers:
        return {"refreshed": 0, "source": pricer.source_label()}
    await pricer.clear_cache()
    prices = await pricer.batch_latest_closes(tickers, force=True)
    valid = {t: p for t, p in prices.items() if p is not None}
    return {
        "tickers_requested": len(tickers),
        "tickers_refreshed": len(valid),
        "tickers_missing": len(tickers) - len(valid),
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
    cutoff = _today_et() - timedelta(days=days + 5)
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
    today = _today_iso()
    floor = (_today_et() - timedelta(days=days)).isoformat()
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
    cutoff = _today_et() - timedelta(days=days + 5)
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
    today = _today_iso()
    floor = (_today_et() - timedelta(days=days)).isoformat()
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


async def daily_total_vs_spy_curve(days: int = 90) -> dict[str, Any]:
    """Compare total terminal performance against SPY as the S&P 500 proxy.

    The terminal line blends the available equity signal curve and options
    proxy curve by date. SPY is normalized to the first available close in the
    same chart window.
    """
    stock_curve = await daily_pnl_curve(days=days)
    options_curve = await daily_options_pnl_curve(days=days)
    if not stock_curve and not options_curve:
        return {"benchmark": "SPY", "curve": [], "source": pricer.source_label()}

    by_date: dict[str, dict[str, Any]] = {}
    for row in stock_curve:
        by_date.setdefault(row["date"], {})["stock_gain_pct"] = row.get("avg_gain_pct")
        by_date[row["date"]]["stock_positions"] = row.get("positions", 0)
    for row in options_curve:
        by_date.setdefault(row["date"], {})["options_gain_pct"] = row.get("avg_gain_pct")
        by_date[row["date"]]["options_positions"] = row.get("positions", 0)

    dates = sorted(by_date.keys())
    if not dates:
        return {"benchmark": "SPY", "curve": [], "source": pricer.source_label()}

    spy_history = await pricer.get_history("SPY", days=days + 10)
    spy_dates = sorted(d for d in spy_history.keys() if dates[0] <= d <= dates[-1])
    spy_base = spy_history.get(spy_dates[0]) if spy_dates else None

    out: list[dict[str, Any]] = []
    latest_spy_close = None
    for d in dates:
        if d in spy_history:
            latest_spy_close = spy_history[d]
        vals = [
            v for v in [
                by_date[d].get("stock_gain_pct"),
                by_date[d].get("options_gain_pct"),
            ]
            if v is not None
        ]
        total = sum(vals) / len(vals) if vals else None
        spy_return = (
            ((latest_spy_close - spy_base) / spy_base * 100.0)
            if spy_base and latest_spy_close
            else None
        )
        out.append({
            "date": d,
            "terminal_total_pct": round(total, 2) if total is not None else None,
            "spy_return_pct": round(spy_return, 2) if spy_return is not None else None,
            "relative_pct": round(total - spy_return, 2) if total is not None and spy_return is not None else None,
            "stock_gain_pct": by_date[d].get("stock_gain_pct"),
            "options_gain_pct": by_date[d].get("options_gain_pct"),
            "stock_positions": by_date[d].get("stock_positions", 0),
            "options_positions": by_date[d].get("options_positions", 0),
        })

    return {
        "benchmark": "SPY",
        "source": pricer.source_label(),
        "days": days,
        "curve": out,
    }


async def signals_tracker_summary(limit: int = 200) -> list[dict[str, Any]]:
    """v5.1 — adds hold-window tracking + peak-gain-within-window.
    Peak Gain is capped at the recommended hold window's end. After the window
    closes (or option expiry, whichever is first), tracking stops permanently.
    Rolling extension: if the same ticker fires again BEFORE the window closes,
    the window extends to the furthest end across all active signals.
    """
    db = get_db()
    rows = await db.signal_first_seen.find({}, {"_id": 0}).sort("first_seen_date", -1).to_list(limit)
    out: list[dict[str, Any]] = []
    tickers = [r["ticker"] for r in rows]
    if not tickers:
        return out

    cur_prices = await pricer.batch_latest_closes(tickers)
    today = datetime.now(timezone.utc).date()

    for r in rows:
        t = r["ticker"]
        entry = r.get("first_seen_price")
        current = cur_prices.get(t)
        first_seen_iso = r.get("first_seen_date")
        # Determine recommended hold window — derived from signal type
        hold_days = r.get("recommended_hold_days")
        if not hold_days:
            sigs = r.get("first_signals") or []
            if "upcoming_earnings" in sigs:
                hold_days = 14
            elif "CONTRACT_SURGE" in sigs or "MOMENTUM_STACK" in sigs:
                hold_days = 45
            else:
                hold_days = 30
        first_seen_dt = None
        if first_seen_iso:
            try:
                first_seen_dt = datetime.fromisoformat(first_seen_iso).date()
            except Exception:
                first_seen_dt = None
        hold_end = (first_seen_dt + timedelta(days=hold_days)) if first_seen_dt else None
        # Rolling extension: if any other signal_first_seen row for same
        # ticker was seen WITHIN current window, extend to its hold_end.
        try:
            others = await db.signal_first_seen.find(
                {"ticker": t}, {"_id": 0, "first_seen_date": 1, "recommended_hold_days": 1, "first_signals": 1},
            ).to_list(10)
            for o in others:
                o_date_str = o.get("first_seen_date")
                if not o_date_str or o_date_str == first_seen_iso:
                    continue
                try:
                    o_dt = datetime.fromisoformat(o_date_str).date()
                except Exception:
                    continue
                if hold_end is None or o_dt > hold_end:
                    continue  # outside current window
                o_hold = o.get("recommended_hold_days") or 30
                o_end = o_dt + timedelta(days=o_hold)
                if hold_end is None or o_end > hold_end:
                    hold_end = o_end
        except Exception:
            pass
        is_active = bool(hold_end and hold_end >= today)

        # Peak gain — strictly within window
        peak_gain = None
        if first_seen_dt and entry:
            # Bound: from first_seen to MIN(today, hold_end)
            cutoff = min(today, hold_end) if hold_end else today
            try:
                def _peak_in_window():
                    import yfinance as yf
                    df = yf.Ticker(t).history(start=first_seen_iso, end=(cutoff + timedelta(days=1)).isoformat())
                    if df is None or df.empty:
                        return None
                    peak_high = float(df["High"].max())
                    return round((peak_high - entry) / entry * 100, 2)
                pg = await asyncio.get_event_loop().run_in_executor(None, _peak_in_window)
                if pg is not None:
                    peak_gain = pg
            except Exception:
                pass

        gain_pct = None
        gain_abs = None
        if entry and current and entry > 0:
            gain_pct = round((current - entry) / entry * 100, 2)
            gain_abs = round(current - entry, 2)
        delta = r.get("first_options_delta") or 0.0
        opt_premium = r.get("first_options_premium") or 0.0
        opt_proxy_pct = None
        # Options proxy uses peak gain when window is active
        ref_gain = peak_gain if peak_gain is not None else gain_pct
        if ref_gain is not None and delta and opt_premium > 0 and entry:
            premium_delta = (entry * ref_gain / 100.0) * delta
            opt_proxy_pct = round(premium_delta / opt_premium * 100, 2)
        out.append({
            "ticker": t,
            "first_seen_date": r.get("first_seen_date"),
            "first_seen_price": entry,
            "current_price": current,
            "gain_pct": gain_pct,
            "gain_abs": gain_abs,
            "peak_gain_pct": peak_gain,
            "recommended_hold_days": hold_days,
            "hold_end_date": hold_end.isoformat() if hold_end else None,
            "is_active": is_active,
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
            "options_peak_return_pct": opt_proxy_pct,
            "risk_level": r.get("first_risk_level"),
        })
    return out


async def options_alpha_gap_summary(limit: int = 300, threshold_pct: float = 100.0) -> dict[str, Any]:
    """Compare equity signal outcomes against theoretical option proxy gains.

    The proxy is useful for spotting missed convexity, but it is not a filled
    trade record. This summary makes that distinction explicit and attaches the
    latest Options Desk blocker reasons where available.
    """
    db = get_db()
    rows = await signals_tracker_summary(limit=limit)
    today = datetime.now(timezone.utc).date()
    closed: list[dict[str, Any]] = []
    active: list[dict[str, Any]] = []
    for row in rows:
        end = row.get("hold_end_date")
        is_active = True
        if end:
            try:
                is_active = datetime.fromisoformat(str(end)).date() >= today
            except Exception:
                is_active = bool(row.get("is_active"))
        if is_active:
            active.append(row)
        else:
            closed.append(row)

    option_proxy_rows = [r for r in rows if r.get("options_return_proxy_pct") is not None]
    closed_option_proxy = [r for r in closed if r.get("options_return_proxy_pct") is not None]
    closed_equity = [r for r in closed if r.get("gain_pct") is not None]
    threshold = max(0.0, float(threshold_pct or 100.0))
    huge_proxy = [r for r in option_proxy_rows if float(r.get("options_return_proxy_pct") or 0) >= threshold]
    tickers = sorted({str(r.get("ticker") or "").upper() for r in huge_proxy if r.get("ticker")})

    latest_candidates = await db.options_desk_candidates.find(
        {"ticker": {"$in": tickers}}, {"_id": 0}
    ).sort("generated_at", -1).to_list(500) if tickers else []
    candidate_by_ticker: dict[str, dict[str, Any]] = {}
    for c in latest_candidates:
        candidate_by_ticker.setdefault(str(c.get("ticker") or "").upper(), c)

    order_rows = await db.options_desk_orders.find(
        {"ticker": {"$in": tickers}}, {"_id": 0}
    ).sort("created_at", -1).to_list(500) if tickers else []
    order_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for order in order_rows:
        order_by_ticker.setdefault(str(order.get("ticker") or "").upper(), []).append(order)
    trade_rows = await db.options_desk_trades.find(
        {"ticker": {"$in": tickers}}, {"_id": 0}
    ).sort("closed_at", -1).to_list(500) if tickers else []
    trade_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for trade in trade_rows:
        trade_by_ticker.setdefault(str(trade.get("ticker") or "").upper(), []).append(trade)

    def _avg(items: list[dict[str, Any]], key: str) -> float | None:
        vals = [float(x[key]) for x in items if x.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else None

    missed = []
    status_counts = Counter()
    blocker_counts = Counter()
    captured_100pct_plus = 0
    sorted_huge_proxy = sorted(huge_proxy, key=lambda r: float(r.get("options_return_proxy_pct") or 0), reverse=True)
    for row in sorted_huge_proxy:
        ticker = str(row.get("ticker") or "").upper()
        candidate = candidate_by_ticker.get(ticker) or {}
        orders = order_by_ticker.get(ticker) or []
        trades = trade_by_ticker.get(ticker) or []
        blocked = candidate.get("blocked_reasons") or []
        if orders or trades:
            status = "CAPTURED_OR_ATTEMPTED"
            reason = f"{len(orders)} options order record(s), {len(trades)} trade record(s) exist"
        elif blocked:
            status = "BLOCKED_BY_OPTIONS_DESK"
            reason = "; ".join(str(x) for x in blocked[:4])
        elif not row.get("options_premium_at_entry"):
            status = "NO_EXECUTABLE_OPTION_SNAPSHOT"
            reason = "Signal tracker has no entry premium/contract snapshot"
        else:
            status = "NOT_ROUTED_OR_NOT_BUILT"
            reason = "No Options Desk candidate/order record found for this ticker"
        status_counts[status] += 1
        if status == "CAPTURED_OR_ATTEMPTED" and float(row.get("options_return_proxy_pct") or 0) >= 100:
            captured_100pct_plus += 1
        for blocker in blocked or [status]:
            blocker_counts[str(blocker)] += 1
        if len(missed) >= 25:
            continue
        missed.append({
            "ticker": ticker,
            "first_seen_date": row.get("first_seen_date"),
            "equity_gain_pct": row.get("gain_pct"),
            "equity_peak_gain_pct": row.get("peak_gain_pct"),
            "options_proxy_pct": row.get("options_return_proxy_pct"),
            "options_strategy": row.get("options_strategy"),
            "options_premium_at_entry": row.get("options_premium_at_entry"),
            "latest_candidate_ready": candidate.get("manual_fire_ready"),
            "latest_blockers": blocked,
            "latest_candidate_provider": candidate.get("data_provider"),
            "latest_candidate_route": candidate.get("route"),
            "status": status,
            "reason": reason,
        })

    return {
        "ok": True,
        "basis": "option_proxy_not_filled_trade",
        "threshold_pct": threshold,
        "tracked_rows": len(rows),
        "closed_rows": len(closed),
        "active_rows": len(active),
        "closed_equity_avg_pct": _avg(closed_equity, "gain_pct"),
        "closed_option_proxy_avg_pct": _avg(closed_option_proxy, "options_return_proxy_pct"),
        "option_proxy_rows": len(option_proxy_rows),
        "option_proxy_threshold_plus": len(huge_proxy),
        "option_proxy_100pct_plus": len([r for r in option_proxy_rows if float(r.get("options_return_proxy_pct") or 0) >= 100]),
        "captured_or_attempted_threshold_plus": status_counts.get("CAPTURED_OR_ATTEMPTED", 0),
        "captured_or_attempted_100pct_plus": captured_100pct_plus,
        "status_counts": dict(status_counts),
        "blocker_counts": dict(blocker_counts.most_common(12)),
        "top_missed_or_blocked": missed,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


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
