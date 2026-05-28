"""X Factor Alerts — retail sentiment surge detector.

Sources:
  • StockTwits — public REST API, no auth. Per-ticker stream of recent messages
    with bullish/bearish tags.
  • Google Trends — pytrends, free. Search-interest score 0-100.
  • Reddit (degraded mode): public JSON requires OAuth as of 2023 — disabled
    in this build. Easily reactivated when a Reddit client_id/secret is added.

Triggers (per spec — fires if ANY one is true):
  1. StockTwits mentions hit 10× the 7-day average
  2. StockTwits bullish flips from <40% to >65%
  3. Reddit mentions hit 15× the 7-day average  (disabled if no OAuth)
  4. Google Trends spikes from baseline <20 to current >70
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .db import get_db, log_activity, stamped

logger = logging.getLogger(__name__)

STOCKTWITS_URL = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
UA = "AxiomBot/3.2 (compatible; market-intel)"

MENTION_SPIKE_X = 10.0          # StockTwits 10× avg
REDDIT_SPIKE_X = 15.0
SENT_FLIP_LOW = 0.40
SENT_FLIP_HIGH = 0.65
TREND_SPIKE_BASELINE = 20
TREND_SPIKE_CURRENT = 70


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ────────────────────────── StockTwits ──────────────────────────
async def fetch_stocktwits(ticker: str) -> dict[str, Any] | None:
    """Returns {mentions_24h, bullish_pct, total} or None if rate-limited/empty."""
    try:
        async with httpx.AsyncClient(timeout=10.0, headers={"User-Agent": UA}) as c:
            r = await c.get(STOCKTWITS_URL.format(symbol=ticker.upper()))
            if r.status_code != 200:
                return None
            data = r.json()
    except Exception as e:
        logger.debug("stocktwits %s: %s", ticker, e)
        return None

    msgs = data.get("messages") or []
    if not msgs:
        return None
    cutoff = _now() - timedelta(hours=24)
    mentions = 0
    bull = 0
    bear = 0
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
            basis = sent.get("basic")
            if basis == "Bullish":
                bull += 1
            elif basis == "Bearish":
                bear += 1
    tagged = bull + bear
    return {
        "mentions_24h": mentions,
        "bullish_pct": round(bull / tagged, 3) if tagged else None,
        "bull": bull, "bear": bear,
        "total_msgs": len(msgs),
    }


# ────────────────────────── Google Trends ──────────────────────────
async def fetch_google_trends(ticker: str) -> dict[str, Any] | None:
    """Returns {current, baseline, ratio} — interest 0-100.
    pytrends is sync → run in executor."""
    def _sync():
        try:
            from pytrends.request import TrendReq
            pt = TrendReq(hl="en-US", tz=300, timeout=(5, 10))
            # 7-day window vs 30-day baseline
            pt.build_payload([ticker], cat=0, timeframe="now 7-d", geo="US")
            df = pt.interest_over_time()
            if df is None or len(df) == 0 or ticker not in df.columns:
                return None
            series = df[ticker].dropna()
            if len(series) < 5:
                return None
            current = float(series.iloc[-1])
            baseline = float(series.iloc[:-2].mean())
            return {"current": current, "baseline": baseline,
                    "ratio": round(current / baseline, 2) if baseline > 0 else None}
        except Exception as e:
            logger.debug("pytrends %s: %s", ticker, e)
            return None
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _sync)


# ────────────────────────── Aggregator ──────────────────────────
async def baseline_for_ticker(ticker: str) -> dict[str, float]:
    """7-day rolling mentions baseline from `x_factor_history`.
    Updated on every check so the trigger thresholds learn over time."""
    db = get_db()
    cutoff = (_now() - timedelta(days=7)).isoformat()
    docs = await db.x_factor_history.find(
        {"ticker": ticker.upper(), "ts": {"$gte": cutoff}},
        {"_id": 0, "stocktwits_mentions": 1, "stocktwits_bullish_pct": 1},
    ).to_list(200)
    if not docs:
        return {"avg_mentions": 0.0, "avg_bullish_pct": 0.5}
    mentions = [d.get("stocktwits_mentions") for d in docs if d.get("stocktwits_mentions") is not None]
    bulls = [d.get("stocktwits_bullish_pct") for d in docs if d.get("stocktwits_bullish_pct") is not None]
    return {
        "avg_mentions": (sum(mentions) / len(mentions)) if mentions else 0.0,
        "avg_bullish_pct": (sum(bulls) / len(bulls)) if bulls else 0.5,
    }


async def evaluate_x_factor(ticker: str, *, fast: bool = False) -> dict[str, Any] | None:
    """Evaluate all triggers for a single ticker. Records history regardless
    of whether an alert fires (so baseline learns).
    `fast=True` skips Google Trends (Reddit-rate-limited pytrends adds ~5s)
    — used during scans to keep latency bounded."""
    db = get_db()
    if fast:
        twits = await fetch_stocktwits(ticker)
        trends = None
    else:
        twits, trends = await asyncio.gather(
            fetch_stocktwits(ticker),
            fetch_google_trends(ticker),
        )
    baseline = await baseline_for_ticker(ticker)

    # Record snapshot
    snap = {
        "ticker": ticker.upper(),
        "ts": _now().isoformat(),
        "stocktwits_mentions": twits.get("mentions_24h") if twits else None,
        "stocktwits_bullish_pct": twits.get("bullish_pct") if twits else None,
        "google_trends_current": trends.get("current") if trends else None,
        "google_trends_baseline": trends.get("baseline") if trends else None,
    }
    await db.x_factor_history.insert_one(stamped(snap))

    # Evaluate triggers
    triggers: list[dict[str, Any]] = []
    if twits and twits.get("mentions_24h") and baseline["avg_mentions"] > 1:
        ratio = twits["mentions_24h"] / baseline["avg_mentions"]
        if ratio >= MENTION_SPIKE_X:
            triggers.append({
                "platform": "STOCKTWITS",
                "type": "MENTION_SPIKE",
                "spike_x": round(ratio, 1),
                "mentions": twits["mentions_24h"],
                "baseline": round(baseline["avg_mentions"], 1),
                "bullish_pct": round((twits.get("bullish_pct") or 0) * 100, 0),
            })
    if twits and twits.get("bullish_pct") is not None:
        if baseline["avg_bullish_pct"] < SENT_FLIP_LOW and twits["bullish_pct"] > SENT_FLIP_HIGH:
            triggers.append({
                "platform": "STOCKTWITS",
                "type": "SENTIMENT_FLIP",
                "from_pct": round(baseline["avg_bullish_pct"] * 100, 0),
                "to_pct": round(twits["bullish_pct"] * 100, 0),
                "mentions": twits.get("mentions_24h", 0),
                "bullish_pct": round(twits["bullish_pct"] * 100, 0),
            })
    if trends and trends.get("baseline") is not None and trends["baseline"] < TREND_SPIKE_BASELINE \
            and trends.get("current", 0) > TREND_SPIKE_CURRENT:
        triggers.append({
            "platform": "GOOGLE_TRENDS",
            "type": "SEARCH_SPIKE",
            "current": int(trends["current"]),
            "baseline": int(trends["baseline"]),
            "ratio": trends["ratio"],
        })

    if not triggers:
        return None

    alert = {
        "ticker": ticker.upper(),
        "fired_at": _now().isoformat(),
        "triggers": triggers,
        "trigger_count": len(triggers),
        "primary_trigger": triggers[0],
        "stocktwits": twits,
        "google_trends": trends,
    }
    await db.x_factor_alerts.update_one(
        {"ticker": ticker.upper(), "fired_at": alert["fired_at"]},
        {"$set": stamped(alert)},
        upsert=True,
    )
    return alert


async def batch_evaluate(tickers: list[str], concurrency: int = 3,
                          per_ticker_timeout: float = 8.0) -> list[dict[str, Any]]:
    """Evaluate X Factor for many tickers. Each ticker is capped at
    `per_ticker_timeout` seconds to keep scan latency bounded. Slow ones
    (Google Trends rate limits) silently skip."""
    if not tickers:
        return []
    sem = asyncio.Semaphore(concurrency)

    async def _one(t: str):
        async with sem:
            try:
                return await asyncio.wait_for(
                    evaluate_x_factor(t, fast=True), timeout=per_ticker_timeout,
                )
            except asyncio.TimeoutError:
                logger.debug("x_factor %s: timeout", t)
                return None
            except Exception as e:
                logger.warning("x_factor %s failed: %s", t, e)
                return None
    results = await asyncio.gather(*[_one(t) for t in tickers])
    alerts = [r for r in results if r]
    if alerts:
        await log_activity(f"X Factor: {len(alerts)} alerts fired", "info")
    return alerts


async def recent_alerts(days: int = 7) -> list[dict[str, Any]]:
    db = get_db()
    cutoff = (_now() - timedelta(days=days)).isoformat()
    rows = await db.x_factor_alerts.find(
        {"fired_at": {"$gte": cutoff}}, {"_id": 0},
    ).sort("fired_at", -1).to_list(200)
    return rows
