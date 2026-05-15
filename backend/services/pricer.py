"""Unified price fetcher — Massive API (Polygon.io rebrand) primary,
yfinance fallback. All functions are async and never raise.

Massive endpoints used (work on the free plan we have):
- /v2/aggs/ticker/{ticker}/prev        → previous trading day OHLC (latest close)
- /v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}  → daily aggregates

Caching:
- price_cache (Mongo) — 10-minute TTL for latest closes
- price_history_cache (Mongo) — 24h TTL for daily series

Public API:
- get_latest_close(ticker)
- batch_latest_closes(tickers)
- get_close_on_date(ticker, date_iso)
- get_history(ticker, days)
- get_history_range(ticker, from_iso, to_iso)
- batch_history(tickers, days)   → {ticker: {date_iso: close}}
- refresh_price(ticker)          → force-refresh single ticker
- clear_cache()
"""
from __future__ import annotations
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .db import get_db

logger = logging.getLogger(__name__)

MASSIVE_KEY = os.environ.get("MASSIVE_API_KEY", "").strip()
MASSIVE_BASE = "https://api.polygon.io"

LATEST_TTL_MIN = 10
HISTORY_TTL_HR = 24
_SOURCE = "massive" if MASSIVE_KEY else "yfinance"

# Shared HTTP client (created lazily)
_client: httpx.AsyncClient | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today_iso() -> str:
    return _now().date().isoformat()


async def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=15.0, limits=httpx.Limits(max_keepalive_connections=20))
    return _client


def has_massive() -> bool:
    return bool(MASSIVE_KEY)


def source_label() -> str:
    return _SOURCE


# ─────────────────────────── Massive primitives ───────────────────────────
async def _massive_prev_close(ticker: str) -> float | None:
    """Latest available close (previous trading day on free tier)."""
    if not MASSIVE_KEY:
        return None
    try:
        c = await _get_client()
        r = await c.get(
            f"{MASSIVE_BASE}/v2/aggs/ticker/{ticker}/prev",
            params={"adjusted": "true", "apiKey": MASSIVE_KEY},
        )
        if r.status_code != 200:
            return None
        data = r.json()
        results = data.get("results") or []
        if not results:
            return None
        return float(results[0].get("c") or 0) or None
    except Exception as e:
        logger.debug("massive prev_close %s failed: %s", ticker, e)
        return None


async def _massive_range(ticker: str, from_iso: str, to_iso: str) -> dict[str, float]:
    """Daily aggregates → {date_iso: close}."""
    if not MASSIVE_KEY:
        return {}
    try:
        c = await _get_client()
        r = await c.get(
            f"{MASSIVE_BASE}/v2/aggs/ticker/{ticker}/range/1/day/{from_iso}/{to_iso}",
            params={"adjusted": "true", "sort": "asc", "limit": 5000, "apiKey": MASSIVE_KEY},
        )
        if r.status_code != 200:
            return {}
        data = r.json()
        results = data.get("results") or []
        out: dict[str, float] = {}
        for row in results:
            t_ms = row.get("t")
            close = row.get("c")
            if not t_ms or close is None:
                continue
            d = datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc).date().isoformat()
            out[d] = float(close)
        return out
    except Exception as e:
        logger.debug("massive range %s failed: %s", ticker, e)
        return {}


# ─────────────────────────── yfinance fallback ───────────────────────────
async def _yf_latest_close(ticker: str) -> float | None:
    try:
        import yfinance as yf

        def _sync():
            t = yf.Ticker(ticker)
            h = t.history(period="5d")
            if not len(h):
                return None
            return float(h["Close"].dropna().iloc[-1])
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync)
    except Exception as e:
        logger.debug("yf latest %s failed: %s", ticker, e)
        return None


async def _yf_range(ticker: str, from_iso: str, to_iso: str) -> dict[str, float]:
    try:
        import yfinance as yf

        def _sync():
            t = yf.Ticker(ticker)
            h = t.history(start=from_iso, end=to_iso)
            if not len(h):
                return {}
            out: dict[str, float] = {}
            for ts, v in h["Close"].dropna().items():
                out[ts.date().isoformat()] = float(v)
            return out
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync)
    except Exception as e:
        logger.debug("yf range %s failed: %s", ticker, e)
        return {}


# ───────────────────────────── Cache helpers ─────────────────────────────
async def _cached_latest(ticker: str) -> float | None:
    db = get_db()
    doc = await db.price_cache.find_one({"ticker": ticker}, {"_id": 0})
    if not doc:
        return None
    try:
        ts = datetime.fromisoformat(doc["fetched_at"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if (_now() - ts) <= timedelta(minutes=LATEST_TTL_MIN) and doc.get("price"):
            return float(doc["price"])
    except Exception:
        pass
    return None


async def _store_latest(ticker: str, price: float, src: str) -> None:
    db = get_db()
    await db.price_cache.update_one(
        {"ticker": ticker},
        {"$set": {
            "ticker": ticker, "price": float(price),
            "fetched_at": _now().isoformat(), "source": src,
        }},
        upsert=True,
    )


async def _cached_history(ticker: str) -> dict[str, float] | None:
    db = get_db()
    doc = await db.price_history_cache.find_one({"ticker": ticker}, {"_id": 0})
    if not doc:
        return None
    try:
        ts = datetime.fromisoformat(doc["fetched_at"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if (_now() - ts) <= timedelta(hours=HISTORY_TTL_HR):
            closes = doc.get("closes") or {}
            if closes:
                return {k: float(v) for k, v in closes.items()}
    except Exception:
        pass
    return None


async def _store_history(ticker: str, closes: dict[str, float], src: str) -> None:
    if not closes:
        return
    db = get_db()
    await db.price_history_cache.update_one(
        {"ticker": ticker},
        {"$set": {
            "ticker": ticker, "closes": closes,
            "fetched_at": _now().isoformat(), "source": src,
        }},
        upsert=True,
    )


# ───────────────────────────── Public API ─────────────────────────────────
async def get_latest_close(ticker: str, force: bool = False) -> float | None:
    ticker = (ticker or "").upper().strip()
    if not ticker:
        return None
    if not force:
        c = await _cached_latest(ticker)
        if c is not None:
            return c
    price = await _massive_prev_close(ticker)
    if price is not None:
        await _store_latest(ticker, price, "massive")
        return price
    price = await _yf_latest_close(ticker)
    if price is not None:
        await _store_latest(ticker, price, "yfinance")
    return price


async def batch_latest_closes(tickers: list[str], force: bool = False,
                                concurrency: int = 8) -> dict[str, float | None]:
    """Concurrent latest-close fetcher."""
    tickers = [t.upper().strip() for t in tickers if t]
    if not tickers:
        return {}
    sem = asyncio.Semaphore(concurrency)

    async def _one(t: str) -> tuple[str, float | None]:
        async with sem:
            return t, await get_latest_close(t, force=force)
    results = await asyncio.gather(*[_one(t) for t in tickers])
    return dict(results)


async def get_history(ticker: str, days: int = 120,
                       force: bool = False) -> dict[str, float]:
    """Daily closes for the last N days. {date_iso: close}."""
    ticker = (ticker or "").upper().strip()
    if not ticker:
        return {}
    if not force:
        cached = await _cached_history(ticker)
        if cached:
            return cached
    to_d = _now().date()
    from_d = to_d - timedelta(days=days + 10)
    closes = await _massive_range(ticker, from_d.isoformat(), to_d.isoformat())
    src = "massive"
    if not closes:
        closes = await _yf_range(ticker, from_d.isoformat(), to_d.isoformat())
        src = "yfinance"
    if closes:
        await _store_history(ticker, closes, src)
    return closes


async def get_history_range(ticker: str, from_iso: str, to_iso: str,
                              force: bool = False) -> dict[str, float]:
    """Daily closes between two ISO dates. Bypasses TTL cache when from/to is custom."""
    ticker = (ticker or "").upper().strip()
    if not ticker:
        return {}
    closes = await _massive_range(ticker, from_iso, to_iso)
    if not closes:
        closes = await _yf_range(ticker, from_iso, to_iso)
    return closes


async def get_close_on_date(ticker: str, date_iso: str) -> float | None:
    """Close on a specific date, or the nearest prior trading day."""
    ticker = (ticker or "").upper().strip()
    if not ticker or not date_iso:
        return None
    # Pull a small window around the target date — guards against weekends/holidays
    try:
        target = datetime.fromisoformat(date_iso).date()
    except Exception:
        return None
    from_d = (target - timedelta(days=10)).isoformat()
    to_d = (target + timedelta(days=2)).isoformat()
    closes = await get_history_range(ticker, from_d, to_d)
    if not closes:
        return None
    # Prefer exact match, else nearest <= target, else nearest >= target
    if date_iso in closes:
        return closes[date_iso]
    sorted_d = sorted(closes.keys())
    leq = [d for d in sorted_d if d <= date_iso]
    if leq:
        return closes[leq[-1]]
    return closes[sorted_d[0]]


async def batch_history(tickers: list[str], days: int = 120,
                          force: bool = False,
                          concurrency: int = 6) -> dict[str, dict[str, float]]:
    tickers = [t.upper().strip() for t in tickers if t]
    sem = asyncio.Semaphore(concurrency)

    async def _one(t: str) -> tuple[str, dict[str, float]]:
        async with sem:
            return t, await get_history(t, days=days, force=force)
    res = await asyncio.gather(*[_one(t) for t in tickers])
    return dict(res)


async def clear_cache() -> dict[str, int]:
    db = get_db()
    a = await db.price_cache.delete_many({})
    b = await db.price_history_cache.delete_many({})
    return {"latest_cleared": a.deleted_count, "history_cleared": b.deleted_count}


async def refresh_price(ticker: str) -> float | None:
    return await get_latest_close(ticker, force=True)
