"""Unified price fetcher — Alpaca extended/24h feeds primary,
with provider fallbacks. All functions are async and never raise.

Fallback endpoints used when Alpaca is unavailable:
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

FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "").strip()
FINNHUB_BASE = "https://finnhub.io/api/v1"
FINNHUB_RATE_PER_MIN = 60  # free tier hard limit

ALPACA_KEY = os.environ.get("APCA_API_KEY_ID", "").strip()
ALPACA_SECRET = os.environ.get("APCA_API_SECRET_KEY", "").strip()
ALPACA_DATA_BASE = "https://data.alpaca.markets/v2"

LATEST_TTL_MIN = 10
HISTORY_TTL_HR = 24
_SOURCE = (
    "alpaca+finnhub+massive" if (ALPACA_KEY and FINNHUB_KEY and MASSIVE_KEY)
    else "alpaca+finnhub" if (ALPACA_KEY and FINNHUB_KEY)
    else "alpaca" if ALPACA_KEY
    else "finnhub+massive" if FINNHUB_KEY and MASSIVE_KEY
    else "finnhub" if FINNHUB_KEY
    else "massive" if MASSIVE_KEY
    else "yfinance"
)


def _public_configured() -> bool:
    try:
        from . import public_api
        return public_api.configured()
    except Exception:
        return False


def _parse_provider_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
    except Exception:
        return None


def _age_seconds(provider_ts: Any) -> float | None:
    ts = _parse_provider_ts(provider_ts)
    if not ts:
        return None
    return max(0.0, (_now() - ts).total_seconds())


def _live_price_max_age_seconds() -> int:
    try:
        return int(os.environ.get("SCANNER_LIVE_PRICE_MAX_AGE_SECONDS", "1800"))
    except Exception:
        return 1800


def _scanner_alpaca_feeds() -> list[str | None]:
    """Feed order for scanner live pricing.

    Alpaca 24/5 overnight data uses `overnight` on Basic or `boats` on Algo
    Trader Plus. SIP/IEX cover regular and premarket/after-hours depending on
    account permissions. An empty item means "try Alpaca default feed".
    """
    raw = os.environ.get("ALPACA_SCANNER_FEEDS", "overnight,boats,delayed_sip,sip,iex,").split(",")
    feeds: list[str | None] = []
    for item in raw:
        feed = item.strip().lower()
        value = feed or None
        if value not in feeds:
            feeds.append(value)
    return feeds


async def _alpaca_trade_meta(ticker: str, *, feed: str | None = None) -> dict[str, Any] | None:
    """Alpaca latest trade with provider timestamp.

    The scanner uses this to distinguish a real overnight/premarket mark from
    a stale last-regular-session print. That distinction matters more than the
    price itself for the 00:00 and 08:00 scans.
    """
    if not (ALPACA_KEY and ALPACA_SECRET):
        return None
    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            params: dict[str, str] = {}
            if feed:
                params["feed"] = feed
            r = await c.get(
                f"{ALPACA_DATA_BASE}/stocks/{ticker}/trades/latest",
                params=params,
                headers={
                    "APCA-API-KEY-ID": ALPACA_KEY,
                    "APCA-API-SECRET-KEY": ALPACA_SECRET,
                },
            )
            if r.status_code != 200:
                return None
            data = r.json()
            trade = data.get("trade") or {}
            price = trade.get("p")
            if not price:
                return None
            age_s = _age_seconds(trade.get("t"))
            is_delayed = (feed or "").lower() == "delayed_sip"
            max_age = int(os.environ.get("SCANNER_DELAYED_PRICE_MAX_AGE_SECONDS", "1200")) if is_delayed else _live_price_max_age_seconds()
            source = "alpaca_latest_trade"
            if feed:
                source = f"{source}_{feed}"
            return {
                "price": float(price),
                "source": source,
                "provider_ts": trade.get("t"),
                "age_seconds": round(age_s, 2) if age_s is not None else None,
                "fresh": bool(age_s is not None and age_s <= max_age),
                "max_age_seconds": max_age,
                "delayed": is_delayed,
                "execution_eligible": bool(not is_delayed and age_s is not None and age_s <= max_age),
                "raw": trade,
            }
    except Exception:
        return None


async def _alpaca_quote(ticker: str) -> float | None:
    """Alpaca latest trade price — primary source per v5.0 spec."""
    meta = await _alpaca_trade_meta(ticker, feed=os.environ.get("ALPACA_STOCK_FEED") or None)
    if not meta:
        meta = await _alpaca_trade_meta(ticker, feed="iex")
    return float(meta["price"]) if meta and meta.get("price") else None


async def live_price_meta(ticker: str) -> dict[str, Any]:
    """Return best scanner/live price metadata without hiding stale sources."""
    ticker = (ticker or "").upper().strip()
    empty = {
        "ticker": ticker,
        "price": None,
        "source": "unavailable",
        "provider_ts": None,
        "age_seconds": None,
        "fresh": False,
        "premarket_confirmed": False,
        "warning": "no_live_price",
    }
    if not ticker:
        return empty

    # Public is the preferred actionable quote source.  Alpaca and the other
    # providers remain fallbacks so a Public outage does not erase research.
    if _public_configured():
        try:
            from . import public_api
            async with public_api.PublicAPIClient() as client:
                payload = await client.quotes([ticker])
            quote_rows = payload.get("quotes") or payload.get("results") or payload.get("data") or []
            if isinstance(quote_rows, dict):
                quote = quote_rows.get(ticker) or quote_rows.get(ticker.upper()) or quote_rows
            else:
                quote = next((row for row in quote_rows if isinstance(row, dict) and str(row.get("symbol") or row.get("ticker") or "").upper() == ticker), None)
            quote = quote if isinstance(quote, dict) else {}
            price = quote.get("lastPrice") or quote.get("last") or quote.get("price") or quote.get("close")
            if price is not None and float(price) > 0:
                provider_ts = quote.get("updatedAt") or quote.get("timestamp") or quote.get("quoteTime")
                age_s = _age_seconds(provider_ts) if provider_ts else 0
                max_age = _live_price_max_age_seconds()
                return {
                    "ticker": ticker,
                    "price": float(price),
                    "source": "public_quote",
                    "provider_ts": provider_ts or _now().isoformat(),
                    "age_seconds": round(age_s, 2) if age_s is not None else 0,
                    "fresh": bool(age_s is None or age_s <= max_age),
                    "premarket_confirmed": True,
                    "delayed": False,
                    "execution_eligible": bool(age_s is None or age_s <= max_age),
                    "warning": None if age_s is None or age_s <= max_age else "public_quote_timestamp_stale",
                    "raw": quote,
                }
        except Exception as exc:
            logger.debug("public quote %s failed; falling back: %s", ticker, exc)

    preferred_feed = (os.environ.get("ALPACA_STOCK_FEED") or "").strip().lower() or None
    feeds = []
    if preferred_feed:
        feeds.append(preferred_feed)
    feeds.extend(_scanner_alpaca_feeds())
    tried_feeds: list[str | None] = []
    stale_alpaca: dict[str, Any] | None = None
    for feed in feeds:
        if feed in tried_feeds:
            continue
        tried_feeds.append(feed)
        meta = await _alpaca_trade_meta(ticker, feed=feed)
        if not meta:
            continue
        meta.update({
            "ticker": ticker,
            "premarket_confirmed": bool(meta.get("fresh") and not meta.get("delayed")),
            "warning": "alpaca_delayed_sip_15m" if meta.get("delayed") else (None if meta.get("fresh") else "alpaca_trade_timestamp_stale"),
        })
        if meta.get("fresh"):
            return meta
        if stale_alpaca is None:
            stale_alpaca = meta

    if stale_alpaca is not None:
        return stale_alpaca

    fh_price = await _finnhub_quote(ticker)
    if fh_price is not None:
        return {
            "ticker": ticker,
            "price": float(fh_price),
            "source": "finnhub_quote",
            "provider_ts": _now().isoformat(),
            "age_seconds": 0,
            "fresh": True,
            "premarket_confirmed": False,
            "delayed": True,
            "execution_eligible": False,
            "warning": "not_confirmed_24h_market",
        }

    yf_price = await _yf_latest_close(ticker)
    if yf_price is not None:
        return {
            "ticker": ticker,
            "price": float(yf_price),
            "source": "yfinance_latest_close",
            "provider_ts": None,
            "age_seconds": None,
            "fresh": False,
            "premarket_confirmed": False,
            "warning": "fallback_close_not_24h_market",
        }
    return empty


async def batch_live_price_meta(
    tickers: list[str],
    *,
    concurrency: int = 8,
) -> dict[str, dict[str, Any]]:
    """Fetch live scanner price metadata for a list of tickers.

    This intentionally bypasses the 10-minute close cache so scheduled scans
    can detect whether the market-data evidence actually advanced.
    """
    clean = list(dict.fromkeys(t.upper().strip() for t in tickers if t))
    if not clean:
        return {}
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _one(t: str) -> tuple[str, dict[str, Any]]:
        async with sem:
            return t, await live_price_meta(t)

    pairs = await asyncio.gather(*[_one(t) for t in clean])
    db = get_db()
    now_iso = _now().isoformat()
    for t, meta in pairs:
        if meta.get("price") is None:
            continue
        try:
            await db.price_cache.update_one(
                {"ticker": t},
                {"$set": {
                    "ticker": t,
                    "price": float(meta["price"]),
                    "fetched_at": now_iso,
                    "source": meta.get("source"),
                    "provider_ts": meta.get("provider_ts"),
                    "age_seconds": meta.get("age_seconds"),
                    "fresh": meta.get("fresh"),
                    "premarket_confirmed": meta.get("premarket_confirmed"),
                    "warning": meta.get("warning"),
                }},
                upsert=True,
            )
        except Exception:
            pass
    return {t: meta for t, meta in pairs}

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


def has_finnhub() -> bool:
    return bool(FINNHUB_KEY)


def source_label() -> str:
    if _public_configured():
        fallback = "alpaca" if ALPACA_KEY else "finnhub" if FINNHUB_KEY else "massive" if MASSIVE_KEY else "yfinance"
        return f"public+{fallback}"
    return _SOURCE


# ─────────────────────────── Finnhub primitives ───────────────────────────
# Free tier: 60 requests/min. We use a rolling-window throttle to stay under.
_finnhub_calls: list[datetime] = []
_finnhub_lock = asyncio.Lock()


async def _finnhub_throttle() -> None:
    """Block if we'd exceed 60 calls in the last 60 seconds."""
    if not FINNHUB_KEY:
        return
    async with _finnhub_lock:
        now = _now()
        cutoff = now - timedelta(seconds=60)
        # Drop old call timestamps
        _finnhub_calls[:] = [t for t in _finnhub_calls if t > cutoff]
        if len(_finnhub_calls) >= FINNHUB_RATE_PER_MIN:
            wait_for = 60 - (now - _finnhub_calls[0]).total_seconds() + 0.5
            if wait_for > 0:
                await asyncio.sleep(wait_for)
                # Re-prune after wait
                now2 = _now()
                _finnhub_calls[:] = [t for t in _finnhub_calls if t > now2 - timedelta(seconds=60)]
        _finnhub_calls.append(_now())


async def _finnhub_quote(ticker: str) -> float | None:
    """Real-time quote → returns current price `c`.
    `c=0` means Finnhub doesn't recognize the ticker."""
    if not FINNHUB_KEY:
        return None
    await _finnhub_throttle()
    try:
        c = await _get_client()
        r = await c.get(
            f"{FINNHUB_BASE}/quote",
            params={"symbol": ticker, "token": FINNHUB_KEY},
        )
        if r.status_code != 200:
            return None
        data = r.json()
        price = data.get("c")
        if price is None or price == 0:
            return None
        return float(price)
    except Exception as e:
        logger.debug("finnhub quote %s failed: %s", ticker, e)
        return None


async def _finnhub_batch(tickers: list[str], concurrency: int = 8) -> dict[str, float]:
    """Concurrent Finnhub quote fetch. Throttled to 60/min globally."""
    if not FINNHUB_KEY or not tickers:
        return {}
    sem = asyncio.Semaphore(concurrency)

    async def _one(t: str) -> tuple[str, float | None]:
        async with sem:
            return t, await _finnhub_quote(t)
    res = await asyncio.gather(*[_one(t) for t in tickers])
    return {t: p for t, p in res if p is not None and p > 0}


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


# Module-level grouped cache: date_iso → {ticker: close}
_grouped_cache: dict[str, dict[str, float]] = {}
_grouped_cache_at: dict[str, datetime] = {}
_grouped_lock = asyncio.Lock()


async def _massive_grouped(date_iso: str) -> dict[str, float]:
    """ONE call returns close for every US stock on `date_iso`.
    Free-tier-friendly: 1 request handles 12,000+ tickers.
    Skips weekends/holidays by walking back up to 5 calendar days."""
    if not MASSIVE_KEY:
        return {}
    # Memory cache (1h TTL) — grouped data for a finished day never changes
    ts = _grouped_cache_at.get(date_iso)
    if ts and (_now() - ts) <= timedelta(hours=1):
        return _grouped_cache.get(date_iso, {})

    async with _grouped_lock:
        # Re-check under lock
        ts = _grouped_cache_at.get(date_iso)
        if ts and (_now() - ts) <= timedelta(hours=1):
            return _grouped_cache.get(date_iso, {})
        try:
            c = await _get_client()
            r = await c.get(
                f"{MASSIVE_BASE}/v2/aggs/grouped/locale/us/market/stocks/{date_iso}",
                params={"adjusted": "true", "apiKey": MASSIVE_KEY},
                timeout=30.0,
            )
            if r.status_code != 200:
                _grouped_cache[date_iso] = {}
                _grouped_cache_at[date_iso] = _now()
                return {}
            data = r.json()
            results = data.get("results") or []
            out = {row["T"]: float(row["c"]) for row in results if row.get("T") and row.get("c") is not None}
            _grouped_cache[date_iso] = out
            _grouped_cache_at[date_iso] = _now()
            return out
        except Exception as e:
            logger.debug("massive grouped %s failed: %s", date_iso, e)
            return {}


async def grouped_latest() -> tuple[str, dict[str, float]]:
    """Return (effective_date, {ticker: close}) for the most recent trading day
    that has grouped data. Walks back up to 5 calendar days for weekends/holidays."""
    today = _now().date()
    # Start at yesterday — markets settle T+0 daily close after 16:00 ET, but
    # safest is to walk from yesterday.
    for offset in range(1, 7):
        d = (today - timedelta(days=offset)).isoformat()
        data = await _massive_grouped(d)
        if data:
            return d, data
    return today.isoformat(), {}


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
    """Single-ticker latest price. v5.0 order: Alpaca → Finnhub → yfinance.
    Massive (EOD only) kept as ultra-fallback for grouped daily backfills."""
    ticker = (ticker or "").upper().strip()
    if not ticker:
        return None
    if not force:
        c = await _cached_latest(ticker)
        if c is not None:
            return c
    # 1) Alpaca real-time (v5.0 primary)
    price = await _alpaca_quote(ticker)
    if price is not None:
        await _store_latest(ticker, price, "alpaca")
        return price
    # 2) Finnhub
    price = await _finnhub_quote(ticker)
    if price is not None:
        await _store_latest(ticker, price, "finnhub")
        return price
    # 3) yfinance
    price = await _yf_latest_close(ticker)
    if price is not None:
        await _store_latest(ticker, price, "yfinance")
        return price
    # 4) Massive grouped EOD as last resort
    if MASSIVE_KEY:
        _, grouped = await grouped_latest()
        if grouped and ticker in grouped:
            p = grouped[ticker]
            await _store_latest(ticker, p, "massive")
            return p
    return None


async def _yf_batch_latest(tickers: list[str]) -> dict[str, float]:
    """Single yfinance call that returns latest intraday close for many
    tickers in one shot. Returns {ticker: price}. yfinance intraday data
    is ~15-min delayed but shows real movement (Massive free tier is EOD
    only, so daily close = entry price for same-day signals = 0% gain
    forever — yfinance fixes that)."""
    if not tickers:
        return {}
    try:
        import yfinance as yf

        def _sync():
            data = yf.download(
                tickers=" ".join(tickers), period="2d", interval="1d",
                progress=False, threads=True, group_by="ticker", auto_adjust=True,
            )
            if data is None or len(data) == 0:
                return {}
            out: dict[str, float] = {}
            if len(tickers) == 1:
                t = tickers[0]
                try:
                    out[t] = float(data["Close"].dropna().iloc[-1])
                except Exception:
                    pass
                return out
            for t in tickers:
                try:
                    series = data[t]["Close"].dropna()
                    if len(series):
                        out[t] = float(series.iloc[-1])
                except Exception:
                    continue
            return out
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync)
    except Exception as e:
        logger.debug("yf batch latest failed: %s", e)
        return {}


async def batch_latest_closes(tickers: list[str], force: bool = False,
                                concurrency: int = 8) -> dict[str, float | None]:
    """Latest price for many tickers.

    Strategy (Massive first, Finnhub for real-time freshness, yfinance fallback):
      1. **Massive grouped daily** — 1 HTTP call returns yesterday's close for
         all 12,000+ US stocks. Free, instant, no rate-limit pain.
      2. **Finnhub /quote** — overrides Massive's EOD close with TODAY's
         intraday quote (real-time, 60 req/min). Throttled internally.
      3. **yfinance batch** — fallback for anything both APIs missed
         (delisted tickers, etc.).

    All results cached in `price_cache` for `LATEST_TTL_MIN` minutes."""
    tickers = [t.upper().strip() for t in tickers if t]
    if not tickers:
        return {}

    # Honor existing cache unless force
    result: dict[str, float | None] = {}
    if not force:
        db = get_db()
        cached = await db.price_cache.find(
            {"ticker": {"$in": tickers}}, {"_id": 0},
        ).to_list(len(tickers))
        fresh_after = _now() - timedelta(minutes=LATEST_TTL_MIN)
        for c in cached:
            try:
                ts = datetime.fromisoformat(c["fetched_at"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= fresh_after and c.get("price"):
                    result[c["ticker"]] = float(c["price"])
            except Exception:
                pass

    missing = [t for t in tickers if t not in result]
    if not missing:
        return result

    now_iso = _now().isoformat()
    db = get_db()

    # 1) Massive grouped — single call gets yesterday's close for everything
    if MASSIVE_KEY:
        _, grouped = await grouped_latest()
        if grouped:
            for t in missing:
                p = grouped.get(t)
                if p is not None:
                    result[t] = float(p)
                    await db.price_cache.update_one(
                        {"ticker": t},
                        {"$set": {"ticker": t, "price": float(p),
                                  "fetched_at": now_iso, "source": "massive"}},
                        upsert=True,
                    )

    # 2) Finnhub — override with real-time quote for every ticker we want fresh
    #    (overwrites Massive's EOD close with today's intraday price)
    if FINNHUB_KEY:
        fh_data = await _finnhub_batch(missing, concurrency=concurrency)
        for t, p in fh_data.items():
            result[t] = float(p)
            await db.price_cache.update_one(
                {"ticker": t},
                {"$set": {"ticker": t, "price": float(p),
                          "fetched_at": now_iso, "source": "finnhub"}},
                upsert=True,
            )

    # 3) yfinance backfill — anything we still don't have a price for
    still_missing = [t for t in missing if t not in result]
    if still_missing:
        yf_data = await _yf_batch_latest(still_missing)
        for t, p in yf_data.items():
            result[t] = float(p)
            await db.price_cache.update_one(
                {"ticker": t},
                {"$set": {"ticker": t, "price": float(p),
                          "fetched_at": now_iso, "source": "yfinance"}},
                upsert=True,
            )

    # Any leftovers stay None
    for t in tickers:
        result.setdefault(t, None)
    return result


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
    if _public_configured():
        try:
            from . import public_api
            async with public_api.PublicAPIClient() as client:
                payload = await client.bars(ticker, days=days)
            closes = {}
            for bar in payload.get("bars") or []:
                timestamp = bar.get("timestamp") or bar.get("time")
                close = bar.get("close") or bar.get("c")
                if timestamp and close is not None:
                    closes[str(timestamp)[:10]] = float(close)
            if closes:
                closes = dict(sorted(closes.items())[-max(1, days):])
                await _store_history(ticker, closes, "public")
                return closes
        except Exception as exc:
            logger.debug("public history %s failed; falling back: %s", ticker, exc)
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
    if _public_configured():
        try:
            from . import public_api
            start = datetime.fromisoformat(from_iso).date()
            end = datetime.fromisoformat(to_iso).date()
            async with public_api.PublicAPIClient() as client:
                payload = await client.bars(ticker, days=max(1, (end - start).days + 10))
            closes = {}
            for bar in payload.get("bars") or []:
                timestamp = bar.get("timestamp") or bar.get("time")
                close = bar.get("close") or bar.get("c")
                day = str(timestamp)[:10] if timestamp else ""
                if day and close is not None and from_iso <= day <= to_iso:
                    closes[day] = float(close)
            if closes:
                return closes
        except Exception as exc:
            logger.debug("public history range %s failed; falling back: %s", ticker, exc)
    closes = await _massive_range(ticker, from_iso, to_iso)
    if not closes:
        closes = await _yf_range(ticker, from_iso, to_iso)
    return closes


async def get_close_on_date(ticker: str, date_iso: str) -> float | None:
    """Close on a specific date, or the nearest prior trading day.
    Uses Massive's grouped daily endpoint — one cached request covers
    every ticker on that date."""
    ticker = (ticker or "").upper().strip()
    if not ticker or not date_iso:
        return None
    try:
        target = datetime.fromisoformat(date_iso).date()
    except Exception:
        return None
    # Walk back up to 7 calendar days to handle weekends/holidays
    if MASSIVE_KEY:
        for offset in range(8):
            d = target - timedelta(days=offset)
            if d.weekday() >= 5:
                continue
            grouped = await _massive_grouped(d.isoformat())
            if grouped and ticker in grouped:
                return grouped[ticker]
        # If we got here, Massive has no data for this ticker in the window
        # — fall through to yfinance
    # yfinance fallback (per-ticker range)
    from_d = (target - timedelta(days=10)).isoformat()
    to_d = (target + timedelta(days=2)).isoformat()
    closes = await _yf_range(ticker, from_d, to_d)
    if not closes:
        return None
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
    """Returns {ticker: {date_iso: close}} for all tickers across `days`.
    Uses Massive's GROUPED endpoint per-day — 1 request per trading day
    regardless of ticker count. Massively cheaper than per-ticker range calls
    on the free tier and immune to rate limits beyond ~5 days back."""
    tickers = [t.upper().strip() for t in tickers if t]
    if not tickers:
        return {}

    # Massive grouped path — N trading days = N requests, cached for 1h each
    if MASSIVE_KEY:
        today = _now().date()
        out: dict[str, dict[str, float]] = {t: {} for t in tickers}
        ticker_set = set(tickers)
        # Walk back day-by-day. Skip weekends (Sat=5, Sun=6).
        offset = 0
        trading_days_collected = 0
        max_calendar_days = days + 20  # buffer for holidays
        while offset < max_calendar_days and trading_days_collected < days:
            d = today - timedelta(days=offset)
            offset += 1
            if d.weekday() >= 5:
                continue
            data = await _massive_grouped(d.isoformat())
            if not data:
                # Could be holiday — keep walking
                continue
            trading_days_collected += 1
            for t in ticker_set:
                px = data.get(t)
                if px is not None:
                    out[t][d.isoformat()] = float(px)
        # Drop tickers with no data (delisted) — yfinance fallback for those
        missing = [t for t in tickers if not out[t]]
        if missing:
            sem = asyncio.Semaphore(concurrency)

            async def _one(t: str) -> tuple[str, dict[str, float]]:
                async with sem:
                    return t, await get_history(t, days=days, force=force)
            yres = await asyncio.gather(*[_one(t) for t in missing])
            for t, series in yres:
                if series:
                    out[t] = series
        return out

    # No Massive: per-ticker range with bounded concurrency
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
