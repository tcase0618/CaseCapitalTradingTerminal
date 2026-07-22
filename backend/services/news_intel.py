"""Free RSS-backed news intelligence for the Intel Feed.

This is intentionally provider-light: no paid API key, no LLM, and no fabricated
market impact. It gives the terminal a deduped, ticker-mapped newswire tape from
public RSS sources and marks source failures explicitly.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote_plus

import httpx

from .db import get_db, stamped


HEADERS = {
    "User-Agent": "CaseCapitalTerminal/1.0 (+https://casecapitalterminal.local)",
    "Accept": "application/rss+xml,application/xml,text/xml,*/*",
}
TIMEOUT = httpx.Timeout(10.0, connect=4.0)
CACHE_TTL_SECONDS = 300

MARKET_FEEDS = [
    {
        "key": "marketwatch_top",
        "source": "MarketWatch",
        "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        "lane": "market",
    },
    {
        "key": "cnbc_finance",
        "source": "CNBC Markets",
        "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "lane": "market",
    },
    {
        "key": "yahoo_spy",
        "source": "Yahoo Finance",
        "url": "https://feeds.finance.yahoo.com/rss/2.0/headline?s=SPY&region=US&lang=en-US",
        "lane": "market",
    },
]

URGENT_TERMS = {
    "halt": 18,
    "sec": 10,
    "probe": 12,
    "investigation": 14,
    "lawsuit": 10,
    "guidance": 13,
    "raises": 10,
    "cuts": 10,
    "downgrade": 12,
    "upgrade": 10,
    "acquisition": 14,
    "merger": 14,
    "contract": 10,
    "earnings": 9,
    "fda": 14,
    "approval": 12,
    "recall": 14,
    "bankruptcy": 18,
    "offering": 13,
}

BULLISH_TERMS = {
    "beats", "beat", "raises", "raised", "upgrade", "upgraded", "approval",
    "approved", "contract", "award", "acquisition", "buyout", "surges", "jumps",
}
BEARISH_TERMS = {
    "misses", "miss", "cuts", "cut", "downgrade", "downgraded", "probe",
    "investigation", "lawsuit", "recall", "bankruptcy", "offering", "falls", "drops",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _clean_text(value: str | None) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _ticker_terms(ticker: str, company: str | None = None) -> str:
    terms = [ticker.upper(), "stock"]
    if company:
        cleaned = re.sub(r"[^A-Za-z0-9 &.-]", " ", company)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned:
            terms.insert(1, f'"{cleaned}"')
    return " OR ".join(terms)


def _rss_items(xml_text: str, *, source: str, source_key: str, lane: str, ticker_hint: str | None = None) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return []

    rows: list[dict[str, Any]] = []
    for item in root.findall(".//item")[:40]:
        title = _clean_text(item.findtext("title"))
        if not title:
            continue
        link = _clean_text(item.findtext("link"))
        summary = _clean_text(item.findtext("description"))
        published = _parse_dt(item.findtext("pubDate") or item.findtext("{http://purl.org/dc/elements/1.1/}date"))
        rows.append({
            "id": hashlib.sha1(f"{source}|{title}|{link}".encode("utf-8")).hexdigest()[:16],
            "title": title,
            "summary": summary[:360],
            "url": link,
            "published_at": published.isoformat() if published else None,
            "source": source,
            "source_key": source_key,
            "lane": lane,
            "ticker_hint": ticker_hint,
        })
    return rows


async def _fetch_feed(client: httpx.AsyncClient, feed: dict[str, Any]) -> dict[str, Any]:
    try:
        response = await client.get(feed["url"])
        ok = response.status_code == 200 and "<item" in response.text.lower()
        if not ok:
            return {**feed, "ok": False, "reason": f"http_{response.status_code}", "articles": []}
        return {
            **feed,
            "ok": True,
            "articles": _rss_items(
                response.text,
                source=feed["source"],
                source_key=feed["key"],
                lane=feed.get("lane") or "ticker",
                ticker_hint=feed.get("ticker"),
            ),
        }
    except Exception as exc:
        return {**feed, "ok": False, "reason": str(exc)[:160], "articles": []}


async def _active_universe(limit: int = 20) -> list[dict[str, str]]:
    db = get_db()
    tickers: dict[str, str] = {}

    latest_scan = await db.scan_results.find_one({}, {"_id": 0}, sort=[("finished_at", -1)])
    for row in (latest_scan or {}).get("results", [])[:limit]:
        ticker = str(row.get("ticker") or "").upper().strip()
        if ticker:
            tickers[ticker] = row.get("company") or row.get("name") or ""

    pm = await db.pm_decisions.find({}, {"_id": 0, "ticker": 1, "company": 1}).sort("created_at", -1).to_list(25)
    for row in pm:
        ticker = str(row.get("ticker") or "").upper().strip()
        if ticker:
            tickers.setdefault(ticker, row.get("company") or "")

    tf = await db.tf_trades.find({}, {"_id": 0, "ticker": 1, "company": 1}).sort("created_at", -1).to_list(25)
    for row in tf:
        ticker = str(row.get("ticker") or "").upper().strip()
        if ticker:
            tickers.setdefault(ticker, row.get("company") or "")

    for ticker in ["SPY", "QQQ", "IWM"]:
        tickers.setdefault(ticker, "")

    return [{"ticker": ticker, "company": company} for ticker, company in list(tickers.items())[:limit]]


def _build_feeds(universe: list[dict[str, str]]) -> list[dict[str, Any]]:
    feeds = list(MARKET_FEEDS)
    for item in universe[:14]:
        ticker = item["ticker"]
        company = item.get("company") or ""
        feeds.append({
            "key": f"yahoo_{ticker.lower()}",
            "source": "Yahoo Finance",
            "url": f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US",
            "lane": "ticker",
            "ticker": ticker,
        })
        feeds.append({
            "key": f"google_{ticker.lower()}",
            "source": "Google News",
            "url": f"https://news.google.com/rss/search?q={quote_plus(_ticker_terms(ticker, company))}%20when:1d&hl=en-US&gl=US&ceid=US:en",
            "lane": "ticker",
            "ticker": ticker,
        })
    return feeds


def _map_tickers(article: dict[str, Any], universe: list[dict[str, str]]) -> list[str]:
    haystack = f"{article.get('title', '')} {article.get('summary', '')}".upper()
    mapped: set[str] = set()
    if article.get("ticker_hint"):
        mapped.add(str(article["ticker_hint"]).upper())
    for item in universe:
        ticker = item["ticker"].upper()
        company = (item.get("company") or "").upper()
        if re.search(rf"(?<![A-Z]){re.escape(ticker)}(?![A-Z])", haystack):
            mapped.add(ticker)
        elif company and len(company) > 4 and company in haystack:
            mapped.add(ticker)
    return sorted(mapped)


def _score_article(article: dict[str, Any], mapped: list[str]) -> dict[str, Any]:
    text = f"{article.get('title', '')} {article.get('summary', '')}".lower()
    age_min = None
    published = _parse_dt(article.get("published_at"))
    if published:
        age_min = max(0.0, (_now() - published).total_seconds() / 60.0)

    urgency = sum(points for term, points in URGENT_TERMS.items() if term in text)
    recency = 22 if age_min is not None and age_min <= 60 else 14 if age_min is not None and age_min <= 240 else 6
    ticker_weight = 18 if mapped else 0
    score = max(0, min(100, 28 + urgency + recency + ticker_weight))

    bull = sum(1 for term in BULLISH_TERMS if term in text)
    bear = sum(1 for term in BEARISH_TERMS if term in text)
    if bull > bear:
        bias = "BULLISH"
    elif bear > bull:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"

    lane = "URGENT" if score >= 78 else "WATCH" if mapped else "MARKET"
    return {
        "score": score,
        "bias": bias,
        "urgency_terms": [term for term in URGENT_TERMS if term in text][:6],
        "age_minutes": round(age_min, 1) if age_min is not None else None,
        "intel_lane": lane,
    }


def _dedupe(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    rows = []
    for article in articles:
        key = re.sub(r"[^a-z0-9]+", "", article.get("title", "").lower())[:140]
        if not key or key in seen:
            continue
        seen.add(key)
        rows.append(article)
    return rows


async def latest(force_refresh: bool = False, limit: int = 60) -> dict[str, Any]:
    db = get_db()
    cached = await db.bot_state.find_one({"_id": "news_intel_latest"}, {"_id": 0})
    if cached and not force_refresh:
        generated = _parse_dt(cached.get("generated_at"))
        if generated and (_now() - generated).total_seconds() < CACHE_TTL_SECONDS:
            return {**cached, "cache": "hit"}

    universe = await _active_universe()
    feeds = _build_feeds(universe)
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS, follow_redirects=True) as client:
        feed_results = await asyncio.gather(*[_fetch_feed(client, feed) for feed in feeds])

    articles = []
    for result in feed_results:
        articles.extend(result.get("articles") or [])

    deduped = _dedupe(articles)
    for article in deduped:
        mapped = _map_tickers(article, universe)
        scored = _score_article(article, mapped)
        article.update(scored)
        article["tickers"] = mapped

    deduped.sort(key=lambda row: (row.get("score") or 0, -(row.get("age_minutes") or 999999)), reverse=True)
    rows = deduped[: max(1, min(int(limit or 60), 120))]
    live_sources = [r for r in feed_results if r.get("ok")]
    failed_sources = [{k: r.get(k) for k in ("key", "source", "reason")} for r in feed_results if not r.get("ok")]

    payload = {
        "ok": True,
        "generated_at": _now().isoformat(),
        "cache": "refresh",
        "provider": "free_rss",
        "source_note": "Free RSS only: Yahoo Finance ticker feeds, Google News ticker searches, CNBC, MarketWatch.",
        "universe": universe,
        "source_count": len(feeds),
        "live_source_count": len(live_sources),
        "failed_source_count": len(failed_sources),
        "failed_sources": failed_sources[:12],
        "summary": {
            "articles": len(rows),
            "urgent": sum(1 for row in rows if row.get("intel_lane") == "URGENT"),
            "ticker_mapped": sum(1 for row in rows if row.get("tickers")),
            "bullish": sum(1 for row in rows if row.get("bias") == "BULLISH"),
            "bearish": sum(1 for row in rows if row.get("bias") == "BEARISH"),
        },
        "articles": rows,
    }

    await db.bot_state.update_one({"_id": "news_intel_latest"}, {"$set": payload}, upsert=True)
    await db.news_intel_snapshots.insert_one(stamped(payload))
    return payload

