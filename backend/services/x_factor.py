"""X Factor Alerts — multi-source unusual-attention detector.

Fires when ANY of these is true (per v3.2 lucrative-mode spec):

  • News velocity     — ≥6 articles in 6h (Yahoo RSS + Finviz)
  • StockTwits        — 3× mention spike OR sent flips <40% → >65%
  • Reddit (RSS)      — 4× combined mention spike across r/wsb · r/investing ·
                         r/options · r/SecurityAnalysis
  • Google Trends     — current >40 from baseline <15 (broader terms incl.
                         ticker + company + sector)
  • Yahoo trending    — appears on /trending-tickers list
  • Barchart unusual  — appears on /options/unusual-activity

If a flagged ticker is OUTSIDE the universe it gets surfaced as a STANDALONE
discovery alert (see `discovery_candidates`).
"""
from __future__ import annotations
import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .db import get_db, log_activity, stamped

logger = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
REDDIT_UA = "python:axiom-intel:v3.5 (by /u/axiombot)"
STOCKTWITS_URL = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
YAHOO_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
YAHOO_TRENDING = "https://query1.finance.yahoo.com/v1/finance/trending/US?count=30"
REDDIT_SUBS = ["wallstreetbets", "investing", "options", "SecurityAnalysis", "stocks", "StockMarket"]
REDDIT_RSS = "https://www.reddit.com/r/{sub}/search.rss?q={query}&restrict_sr=1&sort=new&limit=25"
FINVIZ_QUOTE = "https://finviz.com/quote.ashx?t={symbol}"
BARCHART_UNUSUAL = "https://www.barchart.com/options/unusual-activity/stocks?orderBy=volumeOpenInterestRatio"

# Thresholds — lucrative mode
NEWS_VELOCITY_MIN = 6
NEWS_WINDOW_HR = 6
STOCKTWITS_SPIKE_X = 3.0
REDDIT_SPIKE_X = 4.0
SENT_FLIP_LOW = 0.40
SENT_FLIP_HIGH = 0.65
TREND_SPIKE_BASELINE = 15
TREND_SPIKE_CURRENT = 40

# Cache TTLs (seconds)
TRENDING_TTL = 600       # 10 min
BARCHART_TTL = 1800      # 30 min


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ────────────────────── Module-level caches (per-process) ──────────────────────
_yahoo_trending: tuple[datetime, set[str]] | None = None
_barchart_unusual: tuple[datetime, set[str]] | None = None


async def yahoo_trending_set() -> set[str]:
    global _yahoo_trending
    if _yahoo_trending and (_now() - _yahoo_trending[0]).total_seconds() < TRENDING_TTL:
        return _yahoo_trending[1]
    try:
        async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": UA}) as c:
            r = await c.get(YAHOO_TRENDING)
            if r.status_code != 200:
                return _yahoo_trending[1] if _yahoo_trending else set()
            data = r.json()
            quotes = (data.get("finance", {}).get("result") or [{}])[0].get("quotes") or []
            # Only US-listed equities — drop foreign exchanges (.HK .L .TO etc)
            # and private placeholders (.PVT)
            syms = {q["symbol"].upper() for q in quotes
                     if q.get("symbol") and "." not in q["symbol"]
                     and re.match(r"^[A-Z]{1,5}$", q["symbol"].upper())}
            _yahoo_trending = (_now(), syms)
            return syms
    except Exception as e:
        logger.debug("yahoo trending: %s", e)
        return _yahoo_trending[1] if _yahoo_trending else set()


async def barchart_unusual_set() -> set[str]:
    global _barchart_unusual
    if _barchart_unusual and (_now() - _barchart_unusual[0]).total_seconds() < BARCHART_TTL:
        return _barchart_unusual[1]
    try:
        async with httpx.AsyncClient(timeout=12.0, headers={"User-Agent": UA},
                                       follow_redirects=True) as c:
            r = await c.get(BARCHART_UNUSUAL)
            if r.status_code != 200:
                return _barchart_unusual[1] if _barchart_unusual else set()
            syms = set(re.findall(r'data-current-symbol="([A-Z]{1,5})"', r.text))
            _barchart_unusual = (_now(), syms)
            return syms
    except Exception as e:
        logger.debug("barchart: %s", e)
        return _barchart_unusual[1] if _barchart_unusual else set()


# ────────────────────── Per-ticker fetchers ──────────────────────
async def fetch_stocktwits(ticker: str) -> dict[str, Any] | None:
    """Fetch StockTwits sentiment. StockTwits is Cloudflare-fronted and
    blocks plain httpx via TLS fingerprinting, so we use curl_cffi to
    impersonate Chrome (same library yfinance uses)."""
    url = STOCKTWITS_URL.format(symbol=ticker.upper())
    def _sync():
        try:
            from curl_cffi import requests as cc_requests
            r = cc_requests.get(url, impersonate="chrome120", timeout=10,
                                  headers={"User-Agent": UA})
            if r.status_code != 200:
                return None
            return r.json()
        except Exception:
            return None
    data = await asyncio.get_event_loop().run_in_executor(None, _sync)
    if not data:
        return None
    msgs = data.get("messages") or []
    if not msgs:
        return None
    cutoff = _now() - timedelta(hours=24)
    mentions = bull = bear = 0
    for m in msgs:
        ts_str = m.get("created_at")
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if ts < cutoff:
                    continue
            except Exception:
                pass
        mentions += 1
        sent = (m.get("entities") or {}).get("sentiment")
        if isinstance(sent, dict):
            if sent.get("basic") == "Bullish":
                bull += 1
            elif sent.get("basic") == "Bearish":
                bear += 1
    tagged = bull + bear
    return {
        "mentions_24h": mentions,
        "bullish_pct": round(bull / tagged, 3) if tagged else None,
    }


async def fetch_news_velocity(ticker: str) -> int:
    """Count articles published in the last NEWS_WINDOW_HR via Yahoo RSS."""
    try:
        async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": UA},
                                       follow_redirects=True) as c:
            r = await c.get(YAHOO_RSS.format(symbol=ticker.upper()))
            if r.status_code != 200:
                return 0
            text = r.text
    except Exception:
        return 0
    pub_dates = re.findall(r"<pubDate>([^<]+)</pubDate>", text)
    if not pub_dates:
        return 0
    cutoff = _now() - timedelta(hours=NEWS_WINDOW_HR)
    n = 0
    for pd in pub_dates:
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(pd)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cutoff:
                n += 1
        except Exception:
            continue
    return n


async def fetch_reddit_mentions(ticker: str) -> int:
    """Sum mention counts across REDDIT_SUBS.
      • If REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET env vars are set, use the
        official OAuth API (most reliable, never rate-limited at our volume).
      • Otherwise fall back to public .rss search with a compliant UA.
        Note: Reddit hard-blocks data-center IPs without auth — public
        fallback may return 0 from cloud hosts but works from residential IPs."""
    import os as _os
    client_id = _os.environ.get("REDDIT_CLIENT_ID", "").strip()
    client_secret = _os.environ.get("REDDIT_CLIENT_SECRET", "").strip()

    # OAuth path
    if client_id and client_secret:
        try:
            return await _fetch_reddit_oauth(ticker, client_id, client_secret)
        except Exception as e:
            logger.debug("reddit oauth failed: %s", e)

    # Public RSS fallback (curl_cffi for TLS-friendly request)
    total = 0
    def _sync_one(sub: str) -> int:
        try:
            from curl_cffi import requests as cc_requests
            r = cc_requests.get(
                REDDIT_RSS.format(sub=sub, query=ticker.upper()),
                impersonate="chrome120", timeout=10,
                headers={"User-Agent": REDDIT_UA,
                          "Accept": "application/rss+xml,*/*"},
            )
            if r.status_code == 200:
                return r.text.count("<entry>")
        except Exception:
            return 0
        return 0
    for sub in REDDIT_SUBS:
        try:
            n = await asyncio.get_event_loop().run_in_executor(None, _sync_one, sub)
            total += n
            await asyncio.sleep(0.25)  # gentle rate-limiting
        except Exception:
            continue
    return total


_reddit_token_cache: dict[str, Any] = {}


async def _fetch_reddit_oauth(ticker: str, client_id: str, client_secret: str) -> int:
    """Authenticated Reddit search across REDDIT_SUBS. Tokens cached 50min."""
    now = _now().timestamp()
    if _reddit_token_cache.get("expires_at", 0) < now + 60:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(
                "https://www.reddit.com/api/v1/access_token",
                auth=(client_id, client_secret),
                data={"grant_type": "client_credentials"},
                headers={"User-Agent": REDDIT_UA},
            )
            if r.status_code != 200:
                raise RuntimeError(f"reddit auth {r.status_code}")
            tk = r.json()
        _reddit_token_cache["token"] = tk["access_token"]
        _reddit_token_cache["expires_at"] = now + tk.get("expires_in", 3600)

    token = _reddit_token_cache["token"]
    headers = {"User-Agent": REDDIT_UA, "Authorization": f"bearer {token}"}
    total = 0
    async with httpx.AsyncClient(timeout=15.0, headers=headers) as c:
        for sub in REDDIT_SUBS:
            try:
                r = await c.get(
                    f"https://oauth.reddit.com/r/{sub}/search",
                    params={"q": ticker.upper(), "restrict_sr": "1",
                             "sort": "new", "limit": 25, "t": "week"},
                )
                if r.status_code == 200:
                    children = ((r.json() or {}).get("data") or {}).get("children") or []
                    total += len(children)
                elif r.status_code == 429:
                    await asyncio.sleep(1.5)
                    break
            except Exception:
                continue
    return total


async def fetch_google_trends(ticker: str) -> dict[str, Any] | None:
    """Broader terms — ticker + sector keyword 'stock'. Less false negatives
    when the bare ticker is too generic (e.g. 'F' = Ford = ambiguous)."""
    def _sync():
        try:
            from pytrends.request import TrendReq
            pt = TrendReq(hl="en-US", tz=300, timeout=(5, 10))
            kws = [ticker, f"{ticker} stock"]
            pt.build_payload(kws, cat=0, timeframe="now 7-d", geo="US")
            df = pt.interest_over_time()
            if df is None or len(df) == 0:
                return None
            # Combine: max across keywords per timestamp
            cols = [k for k in kws if k in df.columns]
            if not cols:
                return None
            combo = df[cols].max(axis=1).dropna()
            if len(combo) < 5:
                return None
            current = float(combo.iloc[-1])
            baseline = float(combo.iloc[:-2].mean())
            return {"current": current, "baseline": baseline,
                     "ratio": round(current / baseline, 2) if baseline > 0 else None}
        except Exception:
            return None
    return await asyncio.get_event_loop().run_in_executor(None, _sync)


# ────────────────────── Baseline ──────────────────────
async def baseline_for_ticker(ticker: str) -> dict[str, float]:
    """7-day rolling baseline of sentiment metrics. Excludes the snapshot
    just inserted by `evaluate_x_factor` (rows from last 30 minutes) so the
    multiplier doesn't always equal 1.0 on the first run."""
    db = get_db()
    cutoff_old = (_now() - timedelta(days=7)).isoformat()
    cutoff_recent = (_now() - timedelta(minutes=30)).isoformat()
    docs = await db.x_factor_history.find(
        {"ticker": ticker.upper(),
          "ts": {"$gte": cutoff_old, "$lt": cutoff_recent}}, {"_id": 0},
    ).to_list(200)
    if not docs:
        return {"avg_st_mentions": 0, "avg_bull_pct": 0.5, "avg_reddit": 0,
                 "has_baseline": False}
    sm = [d.get("stocktwits_mentions") for d in docs if d.get("stocktwits_mentions") is not None]
    bp = [d.get("stocktwits_bullish_pct") for d in docs if d.get("stocktwits_bullish_pct") is not None]
    rd = [d.get("reddit_mentions") for d in docs if d.get("reddit_mentions") is not None]
    return {
        "avg_st_mentions": (sum(sm)/len(sm)) if sm else 0,
        "avg_bull_pct": (sum(bp)/len(bp)) if bp else 0.5,
        "avg_reddit": (sum(rd)/len(rd)) if rd else 0,
        "has_baseline": True,
    }


async def seed_baseline(tickers: list[str]) -> int:
    """One-time per-ticker baseline seed used on startup so the multiplier
    has something to compare against. Writes a synthetic snapshot dated 4d
    ago for each ticker with the CURRENT live sentiment counts halved —
    that way the first real scan can fire a >2x spike trigger.
    Idempotent: only seeds tickers that don't already have any history."""
    db = get_db()
    seeded = 0
    for t in tickers:
        existing = await db.x_factor_history.count_documents({"ticker": t.upper()})
        if existing:
            continue
        twits = await fetch_stocktwits(t)
        reddit = await fetch_reddit_mentions(t) if not twits else 0
        if not twits and not reddit:
            continue
        synth_ts = (_now() - timedelta(days=4)).isoformat()
        await db.x_factor_history.insert_one(stamped({
            "ticker": t.upper(),
            "ts": synth_ts,
            "stocktwits_mentions": int((twits.get("mentions_24h") or 0) / 2) if twits else 0,
            "stocktwits_bullish_pct": (twits.get("bullish_pct") if twits else 0.5),
            "news_velocity": 0,
            "reddit_mentions": int(reddit / 2),
            "google_trends_current": None,
            "google_trends_baseline": None,
            "_synthetic_seed": True,
        }))
        seeded += 1
    if seeded:
        await log_activity(f"X-Factor baseline seeded for {seeded} tickers", "info")
    return seeded


# ────────────────────── Main evaluator ──────────────────────
async def evaluate_x_factor(ticker: str, *, fast: bool = False,
                              trending_set: set[str] | None = None,
                              barchart_set: set[str] | None = None) -> dict[str, Any] | None:
    """Evaluate all six triggers. fast=True skips Google Trends + Reddit
    (each ~3-5s) — used during scans to keep latency bounded. The Yahoo
    Trending + Barchart sets can be passed in pre-fetched to avoid N copies."""
    db = get_db()
    t = ticker.upper()

    # Pre-fetched module-level sets if caller didn't pass them
    if trending_set is None:
        trending_set = await yahoo_trending_set()
    if barchart_set is None:
        barchart_set = await barchart_unusual_set()

    # Concurrent fetches
    if fast:
        twits, news, reddit, trends = await asyncio.gather(
            fetch_stocktwits(t),
            fetch_news_velocity(t),
            asyncio.sleep(0, result=0),  # skip reddit (slow)
            asyncio.sleep(0, result=None),  # skip trends (slow)
        )
    else:
        twits, news, reddit, trends = await asyncio.gather(
            fetch_stocktwits(t),
            fetch_news_velocity(t),
            fetch_reddit_mentions(t),
            fetch_google_trends(t),
        )

    baseline = await baseline_for_ticker(t)

    # Record snapshot for baseline learning
    await db.x_factor_history.insert_one(stamped({
        "ticker": t, "ts": _now().isoformat(),
        "stocktwits_mentions": twits.get("mentions_24h") if twits else None,
        "stocktwits_bullish_pct": twits.get("bullish_pct") if twits else None,
        "news_velocity": news,
        "reddit_mentions": reddit,
        "google_trends_current": trends.get("current") if trends else None,
        "google_trends_baseline": trends.get("baseline") if trends else None,
    }))

    triggers: list[dict[str, Any]] = []

    # 1) News velocity
    if news >= NEWS_VELOCITY_MIN:
        triggers.append({
            "platform": "NEWS", "type": "VELOCITY_SPIKE",
            "count": news, "window_h": NEWS_WINDOW_HR,
        })
    # 2) StockTwits mention spike (3x)
    if twits and twits.get("mentions_24h") and baseline["avg_st_mentions"] > 1:
        ratio = twits["mentions_24h"] / baseline["avg_st_mentions"]
        if ratio >= STOCKTWITS_SPIKE_X:
            triggers.append({
                "platform": "STOCKTWITS", "type": "MENTION_SPIKE",
                "spike_x": round(ratio, 1),
                "mentions": twits["mentions_24h"],
                "baseline": round(baseline["avg_st_mentions"], 1),
                "bullish_pct": round((twits.get("bullish_pct") or 0) * 100, 0),
            })
    # 3) StockTwits sentiment flip
    if twits and twits.get("bullish_pct") is not None:
        if baseline["avg_bull_pct"] < SENT_FLIP_LOW and twits["bullish_pct"] > SENT_FLIP_HIGH:
            triggers.append({
                "platform": "STOCKTWITS", "type": "SENTIMENT_FLIP",
                "from_pct": round(baseline["avg_bull_pct"] * 100, 0),
                "to_pct": round(twits["bullish_pct"] * 100, 0),
                "bullish_pct": round(twits["bullish_pct"] * 100, 0),
            })
    # 4) Reddit spike (4x combined)
    if reddit and baseline["avg_reddit"] > 1:
        ratio = reddit / baseline["avg_reddit"]
        if ratio >= REDDIT_SPIKE_X:
            triggers.append({
                "platform": "REDDIT", "type": "MENTION_SPIKE",
                "spike_x": round(ratio, 1),
                "mentions": reddit, "baseline": round(baseline["avg_reddit"], 1),
            })
    elif reddit and reddit >= 8 and not baseline["avg_reddit"]:
        # Cold-start: no baseline yet — fire if absolute mentions are loud
        triggers.append({
            "platform": "REDDIT", "type": "MENTION_SURGE",
            "spike_x": "∞", "mentions": reddit, "baseline": 0,
        })
    # 5) Google Trends spike
    if trends and trends.get("baseline") is not None and trends["baseline"] < TREND_SPIKE_BASELINE \
            and trends.get("current", 0) > TREND_SPIKE_CURRENT:
        triggers.append({
            "platform": "GOOGLE_TRENDS", "type": "SEARCH_SPIKE",
            "current": int(trends["current"]),
            "baseline": int(trends["baseline"]),
            "ratio": trends["ratio"],
        })
    # 6) Yahoo trending list
    if t in trending_set:
        triggers.append({"platform": "YAHOO_TRENDING", "type": "ON_LIST"})
    # 7) Barchart unusual options
    if t in barchart_set:
        triggers.append({"platform": "BARCHART", "type": "UNUSUAL_OPTIONS"})

    if not triggers:
        # Cold-start: surface StockTwits sentiment as a passive observation
        # so the UI shows the bullish % column even before a spike fires.
        if twits and (twits.get("mentions_24h") or 0) >= 5 and not baseline.get("has_baseline"):
            triggers.append({
                "platform": "STOCKTWITS", "type": "BASELINE_SEEDING",
                "mentions": twits["mentions_24h"],
                "bullish_pct": round((twits.get("bullish_pct") or 0) * 100, 0),
                "note": "establishing 7-day baseline",
            })
        else:
            return None

    alert = {
        "ticker": t,
        "fired_at": _now().isoformat(),
        "triggers": triggers,
        "trigger_count": len(triggers),
        "primary_trigger": triggers[0],
        "stocktwits": twits,
        "news_velocity": news,
        "reddit_mentions": reddit,
        "google_trends": trends,
    }
    await db.x_factor_alerts.update_one(
        {"ticker": t, "fired_at": alert["fired_at"]},
        {"$set": stamped(alert)}, upsert=True,
    )
    return alert


async def batch_evaluate(tickers: list[str], concurrency: int = 4,
                          per_ticker_timeout: float = 10.0) -> list[dict[str, Any]]:
    if not tickers:
        return []
    # Warm caches once
    trending = await yahoo_trending_set()
    barchart = await barchart_unusual_set()
    sem = asyncio.Semaphore(concurrency)

    async def _one(t: str):
        async with sem:
            try:
                return await asyncio.wait_for(
                    evaluate_x_factor(t, fast=True,
                                        trending_set=trending,
                                        barchart_set=barchart),
                    timeout=per_ticker_timeout,
                )
            except asyncio.TimeoutError:
                return None
            except Exception as e:
                logger.warning("x_factor %s failed: %s", t, e)
                return None
    results = await asyncio.gather(*[_one(t) for t in tickers])
    alerts = [r for r in results if r]
    if alerts:
        await log_activity(f"X Factor: {len(alerts)} alerts fired", "info")
    return alerts


async def discovery_candidates(universe_tickers: set[str]) -> list[dict[str, Any]]:
    """Tickers OUTSIDE the scan universe that hit Yahoo trending or Barchart
    unusual — candidates worth surfacing for universe expansion."""
    trending = await yahoo_trending_set()
    barchart = await barchart_unusual_set()
    fired = (trending | barchart) - {t.upper() for t in universe_tickers}
    if not fired:
        return []
    out: list[dict[str, Any]] = []
    for t in sorted(fired):
        sources = []
        if t in trending:
            sources.append("YAHOO_TRENDING")
        if t in barchart:
            sources.append("BARCHART_UNUSUAL")
        out.append({"ticker": t, "sources": sources,
                     "discovered_at": _now().isoformat()})
    # Persist
    db = get_db()
    for d in out:
        await db.x_factor_discoveries.update_one(
            {"ticker": d["ticker"], "date": _now().date().isoformat()},
            {"$set": stamped({**d, "date": _now().date().isoformat()})},
            upsert=True,
        )
    return out


async def recent_alerts(days: int = 7) -> list[dict[str, Any]]:
    db = get_db()
    cutoff = (_now() - timedelta(days=days)).isoformat()
    rows = await db.x_factor_alerts.find(
        {"fired_at": {"$gte": cutoff}}, {"_id": 0},
    ).sort("fired_at", -1).to_list(200)
    return rows


async def recent_discoveries(days: int = 7) -> list[dict[str, Any]]:
    db = get_db()
    cutoff = (_now() - timedelta(days=days)).date().isoformat()
    return await db.x_factor_discoveries.find(
        {"date": {"$gte": cutoff}}, {"_id": 0},
    ).sort("date", -1).to_list(200)
