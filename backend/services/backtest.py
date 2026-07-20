"""Backtesting Engine — two modes:

1) Forward-looking — every scan adds rows to signal_performance via pnl_tracker;
   over weeks, this gives real backtest data. See pnl_tracker.refresh_due_returns.

2) Synthetic — replays the curated congressional dataset against historical
   yfinance prices to seed initial backtest data immediately. Each curated
   trade becomes a "signal_performance" row with real 7/30/90d returns
   computed retroactively from yfinance history.
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from .congress import _CURATED, _is_committee_match
from .db import get_db, log_activity, stamped

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _fetch_close_on_or_after(ticker: str, target_date: datetime) -> float | None:
    """Get the closing price on `target_date` or the next trading day."""
    try:
        from . import london_strategic_edge as lse_svc

        if lse_svc.configured():
            payload = await lse_svc.candles(
                ticker,
                timeframe="1d",
                start=(target_date - timedelta(days=2)).date().isoformat(),
                end=(target_date + timedelta(days=10)).date().isoformat(),
                limit=20,
                order="asc",
            )
            for row in payload.get("rows") or []:
                raw_ts = row.get("timestamp") or row.get("time") or row.get("date") or row.get("datetime")
                if not raw_ts:
                    continue
                try:
                    row_date = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00")).date()
                except Exception:
                    row_date = datetime.fromisoformat(str(raw_ts)[:10]).date()
                if row_date >= target_date.date():
                    close = row.get("close") or row.get("c") or row.get("Close")
                    if close is not None:
                        return float(close)
    except Exception as e:
        logger.warning("LSE close lookup %s @ %s failed: %s", ticker, target_date, e)

    try:
        import yfinance as yf

        def _sync():
            t = yf.Ticker(ticker)
            start = (target_date - timedelta(days=2)).date()
            end = (target_date + timedelta(days=10)).date()
            h = t.history(start=start.isoformat(), end=end.isoformat())
            if len(h) == 0:
                return None
            # Find first row >= target_date (UTC-naive comparison via date)
            for ts, row in h.iterrows():
                if ts.date() >= target_date.date():
                    return float(row["Close"])
            return float(h["Close"].iloc[-1])
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync)
    except Exception as e:
        logger.warning("close lookup %s @ %s failed: %s", ticker, target_date, e)
        return None


async def synthetic_congress_backtest() -> dict[str, Any]:
    """Replay the curated congressional buys against yfinance historical
    prices. Writes signal_performance rows with real returns.
    Idempotent — keys on (ticker, date)."""
    db = get_db()
    written = 0
    skipped = 0
    for r in _CURATED:
        if "Purchase" not in r.get("tx_type", ""):
            continue
        try:
            tx_date = datetime.fromisoformat(r["tx_date"]).replace(tzinfo=timezone.utc)
        except Exception:
            continue
        ticker = r["ticker"].upper()
        is_match, sector = _is_committee_match(r["name"], ticker)

        # Skip if already exists
        existing = await db.signal_performance.find_one(
            {"ticker": ticker, "date": tx_date.date().isoformat(), "synthetic": True},
            projection={"_id": 0},
        )
        if existing and existing.get("return_30d") is not None:
            skipped += 1
            continue

        # Entry close
        entry = await _fetch_close_on_or_after(ticker, tx_date)
        if not entry:
            continue

        signals = ["CONGRESSIONAL_BUY"]

        ret7 = ret30 = ret90 = None
        for delta_days, key in [(7, "ret7"), (30, "ret30"), (90, "ret90")]:
            future = tx_date + timedelta(days=delta_days)
            if future > _now():
                continue  # not yet known
            cur = await _fetch_close_on_or_after(ticker, future)
            if cur and entry > 0:
                pct = (cur - entry) / entry * 100.0
                if key == "ret7": ret7 = round(pct, 2)
                if key == "ret30": ret30 = round(pct, 2)
                if key == "ret90": ret90 = round(pct, 2)

        await db.signal_performance.update_one(
            {"ticker": ticker, "date": tx_date.date().isoformat(), "synthetic": True},
            {"$set": stamped({
                "ticker": ticker,
                "date": tx_date.date().isoformat(),
                "ts": tx_date.isoformat(),
                "signals": signals,
                "signal_score": 4 if is_match else 3,
                "entry_price": round(entry, 2),
                "risk_level": "MEDIUM",
                "synthetic": True,
                "synthetic_source": "congress_curated",
                "synthetic_meta": {
                    "name": r["name"], "chamber": r["chamber"],
                    "committee_match": is_match, "sector": sector,
                },
                "return_7d": ret7,
                "return_30d": ret30,
                "return_90d": ret90,
            })},
            upsert=True,
        )
        written += 1
    await log_activity(f"Synthetic backtest seeded {written} congress rows", "info")
    return {"written": written, "skipped": skipped, "source": "congress_curated"}


async def backtest_summary() -> dict[str, Any]:
    """Return forward + synthetic combined performance summary."""
    db = get_db()
    rows = await db.signal_performance.find({}, {"_id": 0}).to_list(5000)
    forward = [r for r in rows if not r.get("synthetic")]
    synthetic = [r for r in rows if r.get("synthetic")]

    def _agg(items: list[dict]) -> dict:
        from collections import defaultdict
        by_combo: dict[str, list[float]] = defaultdict(list)
        for it in items:
            key = " + ".join(sorted(it.get("signals", [])))
            if it.get("return_30d") is not None:
                by_combo[key].append(it["return_30d"])
        out = []
        for k, vs in by_combo.items():
            wins = [v for v in vs if v > 0]
            out.append({
                "combo": k, "n": len(vs),
                "avg_30d": round(sum(vs) / len(vs), 2),
                "win_rate_30d": round(len(wins) / len(vs) * 100, 1),
                "best": round(max(vs), 2),
                "worst": round(min(vs), 2),
            })
        out.sort(key=lambda x: -x["avg_30d"])
        return out

    return {
        "forward": _agg(forward),
        "synthetic": _agg(synthetic),
        "forward_count": len(forward),
        "synthetic_count": len(synthetic),
    }
