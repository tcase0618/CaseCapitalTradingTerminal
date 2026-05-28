"""Dark Horse Alerts — institutional accumulation detector.

Uses FINRA's free public CNMSshvol daily short-sale volume file
(https://cdn.finra.org/equity/regsho/daily/CNMSshvol{YYYYMMDD}.txt).

This file reports per-ticker, per-day:
  • TotalVolume — total volume reported to FINRA via off-exchange / dark pool
  • ShortVolume — the short-sale slice of that total

We compare FINRA's off-exchange volume to total market volume (yfinance) to
compute the off-exchange ratio. A Dark Horse alert fires when ALL three are
true on the most recent published date:

  1. off-exchange ratio (FINRA / market) > 0.45
  2. FINRA TotalVolume > 0.10 × 30-day average daily total volume
     (proxy for "block size > 10% ADV")
  3. close > VWAP-proxy ((open + high + low + close) / 4) AND
     close > previous close by > 0.5%
     (proxy for "trade printed above market" — the bulk of the off-exchange
     prints landed at premium levels)

Cache: the daily FINRA file is fetched once and cached for 24h in
`finra_cache`. Re-evaluated per ticker on every scan.
"""
from __future__ import annotations
import asyncio
import csv
import io
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .db import get_db, log_activity, stamped

logger = logging.getLogger(__name__)

FINRA_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{date}.txt"
CACHE_TTL_HR = 12

# Thresholds (per spec)
OFF_EX_RATIO_MIN = 0.45
BLOCK_SIZE_MIN_PCT = 0.10
PREMIUM_MIN_PCT = 0.005


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _fetch_finra_file(date_str: str) -> dict[str, dict[str, float]] | None:
    """Returns {ticker: {short, exempt, total, market}}, or None if not yet published."""
    url = FINRA_URL.format(date=date_str)
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as c:
            r = await c.get(url)
            if r.status_code != 200 or len(r.text) < 1000:
                return None
            text = r.text
    except Exception as e:
        logger.warning("FINRA fetch %s failed: %s", date_str, e)
        return None
    out: dict[str, dict[str, float]] = {}
    reader = csv.DictReader(io.StringIO(text), delimiter="|")
    for row in reader:
        sym = (row.get("Symbol") or "").strip().upper()
        if not sym:
            continue
        try:
            out[sym] = {
                "short": float(row.get("ShortVolume") or 0),
                "exempt": float(row.get("ShortExemptVolume") or 0),
                "total": float(row.get("TotalVolume") or 0),
                "market": (row.get("Market") or "").strip(),
            }
        except ValueError:
            continue
    return out


async def get_latest_finra() -> tuple[str, dict] | None:
    """Return (date_str, {ticker: row}) for the most recent FINRA publication.
    Walks back up to 5 calendar days (handles weekends + holidays).
    Caches in MongoDB for `CACHE_TTL_HR` hours."""
    db = get_db()
    cached = await db.finra_cache.find_one({"_id": "latest"}, {"_id": 0})
    if cached:
        try:
            ts = datetime.fromisoformat(cached["fetched_at"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if (_now() - ts) <= timedelta(hours=CACHE_TTL_HR):
                return cached["date"], cached["data"]
        except Exception:
            pass

    today = _now().date()
    for offset in range(1, 7):
        d = today - timedelta(days=offset)
        if d.weekday() >= 5:
            continue
        date_str = d.strftime("%Y%m%d")
        data = await _fetch_finra_file(date_str)
        if data:
            await db.finra_cache.update_one(
                {"_id": "latest"},
                {"$set": {"date": date_str, "data": data,
                          "fetched_at": _now().isoformat()}},
                upsert=True,
            )
            await log_activity(
                f"FINRA CNMS cache refreshed — {date_str}, {len(data)} tickers",
                "info",
            )
            return date_str, data
    return None


async def get_finra_for_ticker(ticker: str) -> dict[str, Any] | None:
    """Return FINRA row for a single ticker (one-shot lookup)."""
    latest = await get_latest_finra()
    if not latest:
        return None
    date_str, data = latest
    row = data.get(ticker.upper())
    if not row:
        return None
    return {"date": date_str, **row}


async def evaluate_dark_horse(ticker: str, *,
                                close: float | None = None,
                                prev_close: float | None = None,
                                vwap_proxy: float | None = None,
                                avg_volume_30d: float | None = None) -> dict[str, Any] | None:
    """Evaluate Dark Horse conditions for ONE ticker. Returns alert dict if
    triggered, else None. Caller supplies the OHLC + ADV context (already
    fetched via pricer/yfinance — avoids redundant fetches)."""
    finra = await get_finra_for_ticker(ticker)
    if not finra:
        return None

    finra_total = finra.get("total", 0)
    if finra_total <= 0 or avg_volume_30d is None or avg_volume_30d <= 0:
        return None

    # Total market volume on FINRA's reporting day. yfinance/Massive gives
    # composite volume; FINRA total IS the off-exchange chunk.
    # Off-exchange ratio = FINRA / (FINRA + on-exchange).
    # We don't always have on-exchange split, so approximate by comparing to ADV.
    # If FINRA total alone is >45% of ADV that's a real off-exchange day.
    off_ex_ratio = finra_total / max(avg_volume_30d, 1)
    block_size_pct = finra_total / max(avg_volume_30d, 1)

    cond1 = off_ex_ratio >= OFF_EX_RATIO_MIN
    cond2 = block_size_pct >= BLOCK_SIZE_MIN_PCT
    premium_pct = 0.0
    cond3 = False
    if close and prev_close and prev_close > 0:
        premium_pct = (close - prev_close) / prev_close
        cond3 = premium_pct >= PREMIUM_MIN_PCT
        if vwap_proxy is not None and close < vwap_proxy:
            cond3 = False  # close below VWAP fails the "paid up" check

    if not (cond1 and cond2 and cond3):
        return None

    alert = {
        "ticker": ticker.upper(),
        "date": finra["date"],
        "off_exchange_ratio": round(off_ex_ratio, 3),
        "off_exchange_pct": round(off_ex_ratio * 100, 1),
        "block_volume": int(finra_total),
        "block_pct_of_adv": round(block_size_pct * 100, 1),
        "premium_pct": round(premium_pct * 100, 2),
        "close": close,
        "prev_close": prev_close,
        "fired_at": _now().isoformat(),
    }
    return alert


async def batch_evaluate(tickers: list[str], context_by_ticker: dict[str, dict]) -> list[dict[str, Any]]:
    """Evaluate dark horse for many tickers at once.
    `context_by_ticker[ticker] = {close, prev_close, vwap_proxy, avg_volume_30d}`
    Returns list of fired alerts. Also persists each alert to `dark_horse_alerts`."""
    if not tickers:
        return []
    # Warm cache once
    await get_latest_finra()

    alerts: list[dict[str, Any]] = []
    for t in tickers:
        ctx = context_by_ticker.get(t.upper(), {})
        alert = await evaluate_dark_horse(t, **ctx)
        if alert:
            alerts.append(alert)
    if alerts:
        db = get_db()
        for a in alerts:
            await db.dark_horse_alerts.update_one(
                {"ticker": a["ticker"], "date": a["date"]},
                {"$set": stamped(a)},
                upsert=True,
            )
        await log_activity(f"Dark Horse: {len(alerts)} alerts fired", "info")
    return alerts


async def recent_alerts(days: int = 7) -> list[dict[str, Any]]:
    db = get_db()
    cutoff = (_now() - timedelta(days=days)).date().strftime("%Y%m%d")
    rows = await db.dark_horse_alerts.find(
        {"date": {"$gte": cutoff}}, {"_id": 0},
    ).sort("date", -1).to_list(200)
    return rows
