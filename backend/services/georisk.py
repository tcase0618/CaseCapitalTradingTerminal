from __future__ import annotations

import asyncio
import hashlib
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote_plus, urlparse

import httpx

from .db import get_db

GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
FEATURE_VERSION = "2.1"

RSS_SOURCES = [
    {
        "key": "fox_world",
        "name": "Fox News World",
        "url": "https://feeds.foxnews.com/foxnews/world?format=xml",
        "source": "Fox News RSS",
    },
    {
        "key": "wsj_world",
        "name": "WSJ World",
        "url": "https://feeds.a.dj.com/rss/RSSWorldNews.xml",
        "source": "WSJ RSS",
    },
    {
        "key": "bloomberg_free_world",
        "name": "Bloomberg Free World",
        "url": "https://news.google.com/rss/search?q="
        + quote_plus("site:bloomberg.com (world OR geopolitics OR war OR sanctions OR conflict OR energy)")
        + "&hl=en-US&gl=US&ceid=US:en",
        "source": "Bloomberg via Google News RSS",
    },
    {
        "key": "fox_us",
        "name": "Fox News U.S.",
        "url": "https://feeds.foxnews.com/foxnews/national?format=xml",
        "source": "Fox News RSS",
        "lane": "stateside_rss",
    },
    {
        "key": "wsj_us",
        "name": "WSJ U.S.",
        "url": "https://feeds.a.dj.com/rss/WSJcomUSBusiness.xml",
        "source": "WSJ RSS",
        "lane": "stateside_rss",
    },
    {
        "key": "bloomberg_free_us",
        "name": "Bloomberg Free U.S.",
        "url": "https://news.google.com/rss/search?q="
        + quote_plus("site:bloomberg.com (Federal Reserve OR Congress OR SEC OR FTC OR antitrust OR tariff OR strike OR outage OR wildfire OR hurricane)")
        + "&hl=en-US&gl=US&ceid=US:en",
        "source": "Bloomberg via Google News RSS",
        "lane": "stateside_rss",
    },
]

US_LOCATION_COORDS: dict[str, tuple[float, float]] = {
    "washington": (38.9, -77.0),
    "washington dc": (38.9, -77.0),
    "new york": (40.7, -74.0),
    "wall street": (40.7, -74.0),
    "chicago": (41.9, -87.6),
    "los angeles": (34.1, -118.2),
    "san francisco": (37.8, -122.4),
    "silicon valley": (37.4, -122.0),
    "seattle": (47.6, -122.3),
    "houston": (29.8, -95.4),
    "dallas": (32.8, -96.8),
    "atlanta": (33.7, -84.4),
    "miami": (25.8, -80.2),
    "detroit": (42.3, -83.0),
    "boston": (42.4, -71.1),
    "philadelphia": (40.0, -75.2),
    "phoenix": (33.4, -112.1),
    "las vegas": (36.2, -115.1),
    "denver": (39.7, -105.0),
    "minneapolis": (45.0, -93.3),
    "california": (36.8, -119.4),
    "texas": (31.0, -99.0),
    "florida": (27.8, -81.7),
    "new york state": (42.9, -75.0),
    "georgia": (32.7, -83.4),
    "michigan": (44.3, -85.6),
    "ohio": (40.4, -82.8),
    "pennsylvania": (41.0, -77.7),
    "illinois": (40.0, -89.2),
    "arizona": (34.0, -111.1),
    "nevada": (39.5, -117.0),
    "colorado": (39.0, -105.5),
    "minnesota": (46.7, -94.7),
    "louisiana": (31.2, -92.3),
    "gulf coast": (29.3, -90.7),
    "west coast": (37.2, -122.2),
    "east coast": (39.5, -74.6),
}

COUNTRY_COORDS: dict[str, tuple[float, float]] = {
    "united states": (39.8, -98.6),
    "usa": (39.8, -98.6),
    "china": (35.9, 104.2),
    "taiwan": (23.7, 121.0),
    "russia": (61.5, 105.3),
    "ukraine": (49.0, 31.0),
    "israel": (31.0, 35.0),
    "iran": (32.4, 53.7),
    "iraq": (33.2, 43.7),
    "syria": (35.0, 38.5),
    "lebanon": (33.9, 35.9),
    "yemen": (15.6, 48.5),
    "saudi arabia": (24.0, 45.0),
    "qatar": (25.4, 51.2),
    "egypt": (26.8, 30.8),
    "turkey": (39.0, 35.2),
    "pakistan": (30.4, 69.3),
    "india": (22.6, 79.0),
    "north korea": (40.3, 127.5),
    "south korea": (36.5, 127.8),
    "japan": (36.2, 138.2),
    "philippines": (12.9, 122.8),
    "venezuela": (6.4, -66.6),
    "mexico": (23.6, -102.5),
    "panama": (8.5, -80.0),
    "brazil": (-10.8, -53.1),
    "argentina": (-38.4, -63.6),
    "united kingdom": (55.0, -3.4),
    "france": (46.2, 2.2),
    "germany": (51.2, 10.5),
    "poland": (52.0, 19.1),
    "norway": (60.5, 8.5),
    "sweden": (60.1, 18.6),
    "finland": (64.0, 26.0),
    "nigeria": (9.1, 8.7),
    "south africa": (-30.6, 22.9),
    "sudan": (12.9, 30.2),
    "ethiopia": (9.1, 40.5),
    "somalia": (5.1, 46.2),
    "libya": (26.3, 17.2),
    "mali": (17.6, -3.9),
    "niger": (17.6, 8.1),
    "myanmar": (21.9, 95.9),
}

REGION_COORDS: dict[str, tuple[float, float]] = {
    "red sea": (18.0, 39.5),
    "suez": (30.6, 32.3),
    "strait of hormuz": (26.6, 56.3),
    "persian gulf": (27.0, 51.0),
    "taiwan strait": (24.4, 119.8),
    "south china sea": (12.0, 114.0),
    "black sea": (43.3, 34.0),
    "panama canal": (9.1, -79.7),
    "arctic": (72.0, 30.0),
    "gaza": (31.4, 34.3),
    "west bank": (31.9, 35.2),
    "korean peninsula": (38.1, 127.4),
}

CHOKEPOINTS = [
    {
        "key": "hormuz",
        "name": "Strait of Hormuz",
        "lat": 26.6,
        "lon": 56.3,
        "assets": ["Oil", "LNG", "Shipping", "Inflation"],
        "tickers": ["XOM", "CVX", "OXY", "LNG", "USO"],
        "watch_terms": ["hormuz", "persian gulf", "iran", "tanker"],
    },
    {
        "key": "red_sea",
        "name": "Red Sea / Bab el-Mandeb",
        "lat": 14.7,
        "lon": 43.1,
        "assets": ["Shipping", "Oil", "Retail", "Airlines"],
        "tickers": ["ZIM", "FRO", "XOM", "DAL", "UAL"],
        "watch_terms": ["red sea", "bab el", "houthi", "yemen", "suez"],
    },
    {
        "key": "suez",
        "name": "Suez Canal",
        "lat": 30.6,
        "lon": 32.3,
        "assets": ["Shipping", "Retail", "Europe trade"],
        "tickers": ["ZIM", "FRO", "WMT", "TGT"],
        "watch_terms": ["suez", "egypt", "canal"],
    },
    {
        "key": "taiwan_strait",
        "name": "Taiwan Strait",
        "lat": 24.4,
        "lon": 119.8,
        "assets": ["Semiconductors", "Hardware", "Defense"],
        "tickers": ["TSM", "NVDA", "AMD", "ASML", "LMT"],
        "watch_terms": ["taiwan strait", "taiwan", "china", "pla"],
    },
    {
        "key": "south_china_sea",
        "name": "South China Sea",
        "lat": 12.0,
        "lon": 114.0,
        "assets": ["Shipping", "Semiconductors", "Energy"],
        "tickers": ["TSM", "NVDA", "ZIM", "XOM"],
        "watch_terms": ["south china sea", "philippines", "china coast guard"],
    },
    {
        "key": "black_sea",
        "name": "Black Sea",
        "lat": 43.3,
        "lon": 34.0,
        "assets": ["Wheat", "Energy", "Shipping"],
        "tickers": ["WEAT", "CORN", "XOM", "CVX"],
        "watch_terms": ["black sea", "ukraine", "russia", "grain"],
    },
    {
        "key": "panama",
        "name": "Panama Canal",
        "lat": 9.1,
        "lon": -79.7,
        "assets": ["Shipping", "Retail", "Industrials"],
        "tickers": ["ZIM", "WMT", "TGT", "CAT"],
        "watch_terms": ["panama canal", "panama", "drought"],
    },
]

THEMES = [
    {
        "key": "conflict",
        "label": "Conflict / escalation",
        "query": '(war OR missile OR drone OR attack OR conflict OR escalation OR invasion OR strike) sourcelang:English',
        "terms": ["war", "missile", "drone", "attack", "conflict", "escalation", "invasion", "strike"],
        "sectors": ["Defense", "Energy", "Shipping"],
        "tickers": ["LMT", "RTX", "NOC", "GD", "XOM", "CVX"],
        "weight": 28,
    },
    {
        "key": "sanctions",
        "label": "Sanctions / export controls",
        "query": '(sanctions OR sanction OR export controls OR blacklist OR tariff OR embargo) sourcelang:English',
        "terms": ["sanctions", "sanction", "export controls", "blacklist", "tariff", "embargo"],
        "sectors": ["Semiconductors", "Banks", "Energy", "Industrials"],
        "tickers": ["NVDA", "AMD", "TSM", "ASML", "JPM", "XOM"],
        "weight": 24,
    },
    {
        "key": "shipping",
        "label": "Shipping chokepoint",
        "query": '("Red Sea" OR Suez OR "Strait of Hormuz" OR "Panama Canal" OR "Black Sea" OR "South China Sea" OR shipping OR tanker) sourcelang:English',
        "terms": ["red sea", "suez", "strait of hormuz", "panama canal", "black sea", "south china sea", "shipping", "tanker"],
        "sectors": ["Shipping", "Energy", "Airlines", "Retail"],
        "tickers": ["FRO", "ZIM", "XOM", "CVX", "DAL", "UAL"],
        "weight": 26,
    },
    {
        "key": "energy",
        "label": "Energy supply shock",
        "query": '(oil OR crude OR natural gas OR pipeline OR refinery OR LNG OR uranium) (disruption OR attack OR sanctions OR outage OR shortage) sourcelang:English',
        "terms": ["oil", "crude", "natural gas", "pipeline", "refinery", "lng", "uranium", "disruption", "outage"],
        "sectors": ["Energy", "Uranium", "Utilities"],
        "tickers": ["XOM", "CVX", "OXY", "LNG", "CCJ", "URA"],
        "weight": 25,
    },
    {
        "key": "cyber",
        "label": "Cyber / infrastructure",
        "query": '(cyberattack OR ransomware OR hack OR hacked OR malware OR infrastructure attack) sourcelang:English',
        "terms": ["cyberattack", "ransomware", "hack", "hacked", "malware", "infrastructure"],
        "sectors": ["Cybersecurity", "Defense", "Financials"],
        "tickers": ["CRWD", "PANW", "ZS", "FTNT", "RTX"],
        "weight": 22,
    },
    {
        "key": "food",
        "label": "Food / agriculture shock",
        "query": '(wheat OR grain OR corn OR fertilizer OR drought OR food security) (war OR sanctions OR shortage OR disruption OR export) sourcelang:English',
        "terms": ["wheat", "grain", "corn", "fertilizer", "drought", "food security"],
        "sectors": ["Agriculture", "Fertilizer", "Food"],
        "tickers": ["WEAT", "CORN", "MOS", "CF", "ADM"],
        "weight": 18,
    },
]

US_THEMES = [
    {
        "key": "us_policy",
        "label": "U.S. policy / regulation",
        "query": '(United States OR Washington OR Congress OR White House OR SEC OR FTC OR DOJ) (tariff OR regulation OR antitrust OR lawsuit OR investigation OR shutdown OR bill OR executive order) sourcelang:English',
        "terms": ["congress", "white house", "sec", "ftc", "doj", "tariff", "regulation", "antitrust", "lawsuit", "investigation", "shutdown", "executive order"],
        "sectors": ["Banks", "Semiconductors", "Big Tech", "Industrials", "Defense"],
        "tickers": ["SPY", "QQQ", "JPM", "NVDA", "AAPL", "MSFT", "LMT"],
        "weight": 22,
    },
    {
        "key": "us_macro",
        "label": "U.S. macro / Fed",
        "query": '(Federal Reserve OR Fed OR Treasury OR CPI OR inflation OR jobs report OR unemployment OR GDP OR yields) (United States OR U.S. OR Wall Street OR Washington) sourcelang:English',
        "terms": ["federal reserve", "fed", "treasury", "cpi", "inflation", "jobs report", "unemployment", "gdp", "yields"],
        "sectors": ["Indexes", "Banks", "Homebuilders", "Retail", "Rate-sensitive growth"],
        "tickers": ["SPY", "QQQ", "IWM", "TLT", "JPM", "XHB", "HD"],
        "weight": 24,
    },
    {
        "key": "us_infrastructure",
        "label": "U.S. infrastructure / outage",
        "query": '(United States OR U.S. OR California OR Texas OR New York OR Florida) (power outage OR grid OR port strike OR rail strike OR refinery outage OR pipeline outage OR cyberattack OR ransomware) sourcelang:English',
        "terms": ["power outage", "grid", "port strike", "rail strike", "refinery outage", "pipeline outage", "cyberattack", "ransomware"],
        "sectors": ["Utilities", "Energy", "Rails", "Cybersecurity", "Retail"],
        "tickers": ["XLU", "XLE", "UNP", "CSX", "CRWD", "PANW", "WMT"],
        "weight": 25,
    },
    {
        "key": "us_weather",
        "label": "U.S. weather / disaster",
        "query": '(United States OR U.S. OR Florida OR Texas OR California OR Gulf Coast) (hurricane OR wildfire OR flood OR tornado OR heat wave OR winter storm OR drought) sourcelang:English',
        "terms": ["hurricane", "wildfire", "flood", "tornado", "heat wave", "winter storm", "drought"],
        "sectors": ["Insurance", "Utilities", "Energy", "Home improvement", "Agriculture"],
        "tickers": ["KRE", "XLU", "XLE", "HD", "LOW", "ADM", "MOS"],
        "weight": 20,
    },
]

ALL_THEMES = THEMES + US_THEMES


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_float(value: Any) -> float | None:
    try:
        f = float(value)
        return f if math.isfinite(f) else None
    except Exception:
        return None


def _event_location(text: str) -> dict[str, Any]:
    lower = text.lower()
    for name, coords in US_LOCATION_COORDS.items():
        if re.search(rf"\b{re.escape(name)}\b", lower):
            return {"name": name.title(), "lat": coords[0], "lon": coords[1], "match_type": "us_location"}
    for name, coords in REGION_COORDS.items():
        if name in lower:
            return {"name": name.title(), "lat": coords[0], "lon": coords[1], "match_type": "region"}
    for name, coords in COUNTRY_COORDS.items():
        if re.search(rf"\b{re.escape(name)}\b", lower):
            return {"name": name.title(), "lat": coords[0], "lon": coords[1], "match_type": "country"}
    return {"name": "Global", "lat": 20.0, "lon": 0.0, "match_type": "fallback"}


def _score_event(theme: dict[str, Any], title: str, domain: str | None, seen_date: str | None) -> int:
    lower = title.lower()
    term_hits = sum(1 for term in theme["terms"] if term in lower)
    score = 35 + int(theme["weight"]) + term_hits * 6
    if any(w in lower for w in ["missile", "attack", "strike", "war", "sanction", "disruption", "cyberattack"]):
        score += 10
    if domain and any(d in domain.lower() for d in ["reuters", "apnews", "bbc", "ft.com", "wsj", "bloomberg"]):
        score += 6
    if seen_date:
        score += 5
    return max(20, min(100, score))


def _severity(score: int) -> str:
    if score >= 82:
        return "CRITICAL"
    if score >= 68:
        return "HIGH"
    if score >= 50:
        return "WATCH"
    return "LOW"


def _market_bias(theme_key: str, score: int) -> str:
    if theme_key == "us_policy":
        return "Domestic policy/regulatory headline; check exposed sectors and index beta before sizing."
    if theme_key == "us_macro":
        return "Rates and index-volatility impulse; watch SPY/QQQ/IWM, banks, homebuilders, and TLT."
    if theme_key == "us_infrastructure":
        return "U.S. outage/logistics risk; utilities, energy, rails, cyber, and retail margins can react."
    if theme_key == "us_weather":
        return "Weather shock watch; insurance, utilities, energy, home improvement, and ag inputs may move."
    if theme_key == "conflict":
        return "Defense/energy bid; airlines and high-beta risk-off watch."
    if theme_key == "sanctions":
        return "Supply-chain rerating risk; semis/energy/banks require exposure checks."
    if theme_key == "shipping":
        return "Freight and oil risk higher; retail and airlines margin pressure."
    if theme_key == "energy":
        return "Crude/gas-sensitive equities move first; inflation impulse risk if sustained."
    if theme_key == "cyber":
        return "Cybersecurity bid; affected financial/infrastructure names need containment check."
    if theme_key == "food":
        return "Ag inputs and grain ETFs on watch; emerging-market inflation risk."
    return "Monitor cross-asset reaction."


def _event_id(source_key: str, url: str, title: str) -> str:
    basis = f"{source_key}:{url or title}".encode("utf-8", errors="ignore")
    return f"{source_key}:{hashlib.sha1(basis).hexdigest()[:16]}"


def _clean_title(title: str) -> str:
    title = re.sub(r"\s+", " ", title or "").strip()
    return title[:260]


def _best_theme_for_title(title: str) -> dict[str, Any]:
    lower = title.lower()
    ranked = []
    for theme in ALL_THEMES:
        hits = sum(1 for term in theme["terms"] if term in lower)
        if hits:
            ranked.append((hits, int(theme["weight"]), theme))
    if ranked:
        return sorted(ranked, key=lambda row: (row[0], row[1]), reverse=True)[0][2]
    return THEMES[0]


def _source_from_url(url: str, fallback: str | None = None) -> str:
    if fallback:
        return fallback
    try:
        host = urlparse(url).netloc.replace("www.", "")
        return host or "News feed"
    except Exception:
        return "News feed"


def _parse_feed_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat()
    except Exception:
        return value


def _impact_notes(event: dict[str, Any]) -> list[str]:
    theme = event.get("theme")
    severity = event.get("severity")
    notes = []
    if theme == "conflict":
        notes.append("Defense and energy can catch bids if escalation is sustained.")
        notes.append("Airlines, travel, and high-beta growth can weaken during risk-off tape.")
    elif theme == "shipping":
        notes.append("Freight rates and delivery times can move before equity analysts update estimates.")
        notes.append("Retail and airline margin pressure rises if fuel or logistics costs climb.")
    elif theme == "sanctions":
        notes.append("Export-control headlines can reprice supply chains and China-linked revenue.")
        notes.append("Semiconductors, industrials, banks, and energy need exposure checks.")
    elif theme == "energy":
        notes.append("Oil and gas shocks can flow into inflation expectations and rate-sensitive assets.")
        notes.append("Energy equities usually move before second-order consumer weakness shows.")
    elif theme == "cyber":
        notes.append("Cybersecurity vendors can see sympathy demand after infrastructure incidents.")
        notes.append("Named victims need operational containment and disclosure checks.")
    elif theme == "food":
        notes.append("Grain and fertilizer stress can affect food inflation and emerging markets.")
        notes.append("Agriculture inputs and commodity ETFs belong on the watchlist.")
    elif theme == "us_policy":
        notes.append("Regulatory or fiscal headlines can rotate sector leadership without a global catalyst.")
        notes.append("Check targeted tickers plus SPY/QQQ beta before acting on the first headline.")
    elif theme == "us_macro":
        notes.append("Rates, yields, and index volatility can react first, then sector dispersion follows.")
        notes.append("Homebuilders, banks, small caps, and long-duration growth need exposure checks.")
    elif theme == "us_infrastructure":
        notes.append("Domestic outage and logistics headlines can hit supply chains before earnings estimates update.")
        notes.append("Utilities, rails, energy, cyber, and retailers deserve a quick sympathy scan.")
    elif theme == "us_weather":
        notes.append("Severe weather can move insurers, utilities, energy infrastructure, and home repair demand.")
        notes.append("Track duration and affected region before treating the move as more than a headline spike.")
    if severity in {"CRITICAL", "HIGH"}:
        notes.append("Treat this as a scanner trigger, not an automatic trade.")
    return notes[:4]


def _battle_card(event: dict[str, Any]) -> dict[str, Any]:
    title = event.get("title") or "No headline available."
    return {
        "synopsis": title,
        "why_it_matters": event.get("market_bias") or "Monitor cross-asset reaction and headline follow-through.",
        "impact_notes": _impact_notes(event),
        "next_scan": {
            "conflict": "Defense bid + oil shock scan",
            "shipping": "Shipping chokepoint + freight squeeze scan",
            "sanctions": "Supply-chain exposure scan",
            "energy": "Energy shock + inflation impulse scan",
            "cyber": "Cybersecurity sympathy scan",
            "food": "Agriculture inflation scan",
            "us_policy": "U.S. policy exposure scan",
            "us_macro": "Fed/rates + index-volatility scan",
            "us_infrastructure": "Domestic outage + logistics scan",
            "us_weather": "Weather impact + insurance/utilities scan",
        }.get(event.get("theme"), "Cross-asset risk scan"),
    }


def _timeline(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = sorted(
        events,
        key=lambda e: str(e.get("seen_at") or ""),
        reverse=True,
    )
    return [
        {
            "id": event.get("id"),
            "time": event.get("seen_at"),
            "location": event.get("location"),
            "severity": event.get("severity"),
            "theme": event.get("theme_label"),
            "title": event.get("title"),
            "source": event.get("source_name") or event.get("source"),
        }
        for event in rows[:25]
    ]


def _source_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        source = event.get("source_name") or event.get("source") or "Unknown"
        counts[source] = counts.get(source, 0) + 1
    return counts


def _chokepoint_status(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for point in CHOKEPOINTS:
        active = []
        terms = point["watch_terms"]
        for event in events:
            title = str(event.get("title") or "").lower()
            if any(term in title for term in terms):
                active.append(event)
        max_score = max([int(e.get("score", 0)) for e in active] or [0])
        out.append({
            **point,
            "score": max_score,
            "severity": _severity(max_score) if max_score else "LOW",
            "active_events": len(active),
            "top_event": active[0] if active else None,
        })
    return sorted(out, key=lambda row: row["score"], reverse=True)


async def _gdelt_theme_events(theme: dict[str, Any], limit: int = 25) -> list[dict[str, Any]]:
    is_stateside = str(theme.get("key", "")).startswith("us_")
    params = {
        "query": theme["query"],
        "mode": "ArtList",
        "format": "json",
        "timespan": "24h",
        "maxrecords": str(limit),
        "sort": "HybridRel",
    }
    try:
        async with httpx.AsyncClient(timeout=16.0, follow_redirects=True) as client:
            r = await client.get(GDELT_DOC_URL, params=params)
        if r.status_code != 200:
            return []
        data = r.json() or {}
    except Exception:
        return []

    rows = []
    for article in data.get("articles") or []:
        title = str(article.get("title") or "").strip()
        url = article.get("url")
        if not title or not url:
            continue
        loc = _event_location(title)
        score = _score_event(theme, title, article.get("domain"), article.get("seendate"))
        rows.append({
            "id": _event_id(f"gdelt_{theme['key']}", str(url), title),
            "title": title,
            "url": url,
            "domain": article.get("domain"),
            "seen_at": article.get("seendate"),
            "source_country": article.get("sourceCountry"),
            "theme": theme["key"],
            "theme_label": theme["label"],
            "location": loc["name"],
            "lat": loc["lat"],
            "lon": loc["lon"],
            "location_match_type": loc["match_type"],
            "score": score,
            "severity": _severity(score),
            "sectors": theme["sectors"],
            "tickers": theme["tickers"],
            "market_bias": _market_bias(theme["key"], score),
            "source": "GDELT 2.1 DOC",
            "source_name": _source_from_url(str(url), article.get("domain")),
            "data_lane": "stateside_gdelt" if is_stateside else "gdelt",
        })
    return rows


async def _rss_source_events(source: dict[str, str], limit: int = 35) -> list[dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=14.0, follow_redirects=True) as client:
            r = await client.get(
                source["url"],
                headers={"User-Agent": "CaseCapitalTerminal/1.0 (+local research terminal)"},
            )
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.text)
    except Exception:
        return []

    rows = []
    for item in root.findall(".//item")[:limit]:
        title = _clean_title(item.findtext("title") or "")
        url = (item.findtext("link") or "").strip()
        pub_date = _parse_feed_date(item.findtext("pubDate"))
        if not title or not url:
            continue
        theme = _best_theme_for_title(title)
        loc = _event_location(title)
        score = _score_event(theme, title, _source_from_url(url), pub_date)
        event = {
            "id": _event_id(source["key"], url, title),
            "title": title,
            "url": url,
            "domain": _source_from_url(url),
            "seen_at": pub_date,
            "source_country": None,
            "theme": theme["key"],
            "theme_label": theme["label"],
            "location": loc["name"],
            "lat": loc["lat"],
            "lon": loc["lon"],
            "location_match_type": loc["match_type"],
            "score": score,
            "severity": _severity(score),
            "sectors": theme["sectors"],
            "tickers": theme["tickers"],
            "market_bias": _market_bias(theme["key"], score),
            "source": source["source"],
            "source_name": source["name"],
            "data_lane": source.get("lane", "rss"),
        }
        event["battle_card"] = _battle_card(event)
        rows.append(event)
    return rows


def _dedupe_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_titles: set[str] = set()
    out: list[dict[str, Any]] = []
    for event in sorted(events, key=lambda e: e.get("score", 0), reverse=True):
        normalized = re.sub(r"\W+", " ", event.get("title", "").lower()).strip()[:90]
        if normalized in seen_titles:
            continue
        seen_titles.add(normalized)
        event["battle_card"] = event.get("battle_card") or _battle_card(event)
        out.append(event)
    return out


async def live_georisk(max_age_minutes: int = 20) -> dict[str, Any]:
    db = get_db()
    latest = None
    try:
        latest = await db.georisk_snapshots.find_one(
            {"georisk_feature_version": FEATURE_VERSION},
            {"_id": 0},
            sort=[("created_at", -1)],
        )
        if latest and latest.get("created_at"):
            created = datetime.fromisoformat(str(latest["created_at"]).replace("Z", "+00:00"))
            age = (_now() - created).total_seconds() / 60.0
            if age <= max_age_minutes:
                latest["cache_status"] = "HIT"
                latest["cache_age_minutes"] = round(age, 1)
                return latest
            if latest.get("events"):
                latest["cache_status"] = "STALE"
                latest["cache_age_minutes"] = round(age, 1)
                asyncio.create_task(_refresh_snapshot(db))
                return latest
    except Exception:
        pass

    return await _refresh_snapshot(db)


async def _refresh_snapshot(db) -> dict[str, Any]:
    tasks = [_gdelt_theme_events(theme) for theme in ALL_THEMES]
    tasks.extend(_rss_source_events(source) for source in RSS_SOURCES)
    results = await asyncio.gather(*tasks)
    events = _dedupe_events([event for group in results for event in group])[:110]
    counts: dict[str, int] = {}
    severity_counts: dict[str, int] = {}
    for event in events:
        counts[event["theme_label"]] = counts.get(event["theme_label"], 0) + 1
        severity_counts[event["severity"]] = severity_counts.get(event["severity"], 0) + 1

    snapshot = {
        "feature_version": FEATURE_VERSION,
        "georisk_feature_version": FEATURE_VERSION,
        "generated_at": _now().isoformat(),
        "cache_status": "MISS",
        "cache_age_minutes": 0,
        "total": len(events),
        "events": events,
        "theme_counts": counts,
        "severity_counts": severity_counts,
        "source_counts": _source_counts(events),
        "timeline": _timeline(events),
        "chokepoints": _chokepoint_status(events),
        "top_events": events[:12],
        "source_note": "Free headline sources only: GDELT, Fox/WSJ world + U.S. RSS, and Bloomberg-linked Google News RSS. Locations are inferred from headline region, country, state, and city mentions.",
    }
    try:
        await db.georisk_snapshots.insert_one({
            **snapshot,
            "created_at": _now().isoformat(),
        })
    except Exception:
        pass
    return snapshot
