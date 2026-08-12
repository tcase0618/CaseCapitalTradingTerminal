"""Macro Pulse.

ForexFactory/Fair Economy is the source of truth for the U.S. macro-event
countdown. FRED utilities remain available for other macro work, but the
top-of-terminal countdown must not fall back to stale FRED calendar rows.
"""
from __future__ import annotations

import logging
import os
import xml.etree.ElementTree as XmlTree
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .db import get_db, log_activity

logger = logging.getLogger(__name__)

FRED_KEY = os.environ.get("FRED_API_KEY", "").strip()
FRED_BASE = "https://api.stlouisfed.org/fred"
CACHE_TTL_HR = 4
FOREX_FACTORY_CACHE_TTL_MIN = 15
ET = ZoneInfo("America/New_York")
UTC = timezone.utc
FOREX_FACTORY_THIS_WEEK = "https://nfs.faireconomy.media/ff_calendar_thisweek.xml"

RELEASES = {
    101: {"name": "FOMC Meeting", "tag": "FOMC", "warns_sectors": ["TECH", "REAL_ESTATE"], "boosts_sectors": ["FINANCIALS"]},
    10: {"name": "CPI (Consumer Price Index)", "tag": "CPI", "warns_sectors": ["TECH", "CONSUMER_DISCRETIONARY"], "boosts_sectors": ["ENERGY", "FINANCIALS"]},
    14: {"name": "PPI (Producer Price Index)", "tag": "PPI", "warns_sectors": ["TECH"], "boosts_sectors": ["ENERGY"]},
    50: {"name": "Employment Situation", "tag": "JOBS", "warns_sectors": [], "boosts_sectors": ["FINANCIALS", "INDUSTRIALS"]},
    53: {"name": "GDP", "tag": "GDP", "warns_sectors": [], "boosts_sectors": ["FINANCIALS", "INDUSTRIALS"]},
    18: {"name": "Retail Sales", "tag": "RETAIL", "warns_sectors": [], "boosts_sectors": ["CONSUMER_DISCRETIONARY"]},
}

EVENT_TIMES_ET = {
    "FOMC": "14:00",
    "CPI": "08:30",
    "PPI": "08:30",
    "JOBS": "08:30",
    "GDP": "08:30",
    "RETAIL": "08:30",
}

FOMC_DECISION_DATES_2026 = [
    "2026-01-28",
    "2026-03-18",
    "2026-04-29",
    "2026-06-17",
    "2026-07-29",
    "2026-09-16",
    "2026-10-28",
    "2026-12-09",
]

INDUSTRY_TO_SECTOR = {
    "technology": "TECH",
    "software": "TECH",
    "semiconductor": "TECH",
    "communication": "TECH",
    "real estate": "REAL_ESTATE",
    "consumer cyclical": "CONSUMER_DISCRETIONARY",
    "consumer discretionary": "CONSUMER_DISCRETIONARY",
    "energy": "ENERGY",
    "oil": "ENERGY",
    "financial": "FINANCIALS",
    "bank": "FINANCIALS",
    "industrial": "INDUSTRIALS",
    "aerospace": "INDUSTRIALS",
    "defense": "INDUSTRIALS",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _event_text(event: XmlTree.Element, field: str) -> str | None:
    child = event.find(field)
    if child is None or child.text is None:
        return None
    text = child.text.strip()
    return text or None


def _macro_tag_from_title(title: str | None) -> str:
    text = (title or "").lower()
    if "fomc" in text or "federal funds" in text or "fed interest" in text:
        return "FOMC"
    if "cpi" in text or "consumer price" in text or "inflation" in text:
        return "CPI"
    if "ppi" in text or "producer price" in text:
        return "PPI"
    if "non-farm" in text or "nonfarm" in text or "employment" in text or "unemployment" in text or "average hourly" in text:
        return "JOBS"
    if "gdp" in text:
        return "GDP"
    if "retail sales" in text:
        return "RETAIL"
    if "ism" in text:
        return "ISM"
    if "pmi" in text:
        return "PMI"
    if "jobless claims" in text or "unemployment claims" in text:
        return "CLAIMS"
    if "crude oil" in text or "oil inventories" in text:
        return "OIL"
    return "USD"


def _macro_impact_model(tag: str) -> dict[str, list[str]]:
    if tag in {"FOMC", "CPI", "PPI"}:
        return {
            "warns_sectors": ["TECH", "REAL_ESTATE", "CONSUMER_DISCRETIONARY"],
            "boosts_sectors": ["FINANCIALS", "ENERGY"],
        }
    if tag in {"JOBS", "GDP", "RETAIL", "ISM", "PMI"}:
        return {
            "warns_sectors": [],
            "boosts_sectors": ["FINANCIALS", "INDUSTRIALS", "CONSUMER_DISCRETIONARY"],
        }
    if tag == "OIL":
        return {"warns_sectors": ["AIRLINES", "TRANSPORTS"], "boosts_sectors": ["ENERGY"]}
    return {"warns_sectors": [], "boosts_sectors": []}


def _parse_forex_factory_datetime(date_text: str | None, time_text: str | None) -> datetime | None:
    if not date_text:
        return None
    try:
        d = datetime.strptime(date_text.strip(), "%m-%d-%Y").date()
    except Exception:
        return None
    raw_time = (time_text or "").strip().lower()
    if not raw_time or raw_time in {"all day", "tentative"} or raw_time.startswith("day "):
        return datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET)
    try:
        parsed = datetime.strptime(raw_time, "%I:%M%p").time()
    except Exception:
        try:
            parsed = datetime.strptime(raw_time, "%H:%M").time()
        except Exception:
            return datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET)
    # Fair Economy/ForexFactory XML event times are UTC.
    utc_dt = datetime(d.year, d.month, d.day, parsed.hour, parsed.minute, tzinfo=UTC)
    return utc_dt.astimezone(ET)


def _parse_forex_factory_us_events(xml_text: str, *, now_utc: datetime, days_ahead: int) -> list[dict[str, Any]]:
    root = XmlTree.fromstring(xml_text)
    end_utc = now_utc + timedelta(days=days_ahead)
    events: list[dict[str, Any]] = []
    for event in root.findall(".//event"):
        country = (_event_text(event, "country") or "").upper()
        if country != "USD":
            continue
        dt_et = _parse_forex_factory_datetime(_event_text(event, "date"), _event_text(event, "time"))
        if not dt_et:
            continue
        dt_utc = dt_et.astimezone(UTC)
        if dt_utc < now_utc or dt_utc > end_utc:
            continue
        title = _event_text(event, "title") or "U.S. macro event"
        tag = _macro_tag_from_title(title)
        impact_model = _macro_impact_model(tag)
        diff = dt_utc - now_utc
        events.append({
            "date": dt_et.date().isoformat(),
            "time_et": dt_et.strftime("%H:%M"),
            "datetime_et": dt_et.isoformat(),
            "days_until": max(0, diff.days),
            "hours_until": round(diff.total_seconds() / 3600, 2),
            "tag": tag,
            "name": title,
            "country": country,
            "impact": _event_text(event, "impact"),
            "forecast": _event_text(event, "forecast"),
            "previous": _event_text(event, "previous"),
            "actual": _event_text(event, "actual"),
            "source": "ForexFactory/FairEconomy XML",
            "source_url": FOREX_FACTORY_THIS_WEEK,
            "warns_sectors": impact_model["warns_sectors"],
            "boosts_sectors": impact_model["boosts_sectors"],
            "is_imminent": diff.total_seconds() <= 48 * 3600,
        })
    events.sort(key=lambda row: (row["hours_until"], row["tag"], row["name"]))
    return events


async def _fetch_forex_factory_us_events(days_ahead: int = 14) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    now_utc = _now()
    async with httpx.AsyncClient(timeout=18.0, headers={"User-Agent": "CaseCapitalTerminal/1.0 macro-pulse"}) as c:
        response = await c.get(FOREX_FACTORY_THIS_WEEK)
    if response.status_code != 200:
        raise RuntimeError(f"ForexFactory/FairEconomy returned HTTP {response.status_code}")
    events = _parse_forex_factory_us_events(response.text, now_utc=now_utc, days_ahead=days_ahead)
    return events, {
        "source": "ForexFactory/FairEconomy XML",
        "source_url": FOREX_FACTORY_THIS_WEEK,
        "country_filter": "USD",
        "fetched_at": now_utc.isoformat(),
        "events_count": len(events),
    }


def _event_datetime_et(date_str: str, tag: str) -> datetime:
    hhmm = EVENT_TIMES_ET.get(tag, "09:30")
    hour, minute = [int(x) for x in hhmm.split(":", 1)]
    d = datetime.fromisoformat(date_str).date()
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=ET)


def _build_event(date_str: str, release_id: int, now_utc: datetime) -> dict[str, Any] | None:
    meta = RELEASES[release_id]
    tag = meta["tag"]
    dt_et = _event_datetime_et(date_str, tag)
    diff = dt_et.astimezone(UTC) - now_utc
    if diff.total_seconds() < 0:
        return None
    days_until = diff.days
    hours_until = round(diff.total_seconds() / 3600, 2)
    return {
        "date": date_str,
        "time_et": EVENT_TIMES_ET.get(tag, "09:30"),
        "datetime_et": dt_et.isoformat(),
        "days_until": days_until,
        "hours_until": hours_until,
        "tag": tag,
        "name": meta["name"],
        "warns_sectors": meta["warns_sectors"],
        "boosts_sectors": meta["boosts_sectors"],
        "is_imminent": hours_until <= 48,
        "release_id": release_id,
        "source": "FRED/FOMC fallback",
    }


def has_fred() -> bool:
    return bool(FRED_KEY)


async def _fetch_release_dates(days_ahead: int = 14) -> list[dict[str, Any]]:
    if not FRED_KEY:
        return []
    today = _now().date()
    end = today + timedelta(days=days_ahead)
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(
                f"{FRED_BASE}/releases/dates",
                params={
                    "api_key": FRED_KEY,
                    "file_type": "json",
                    "realtime_start": today.isoformat(),
                    "realtime_end": end.isoformat(),
                    "include_release_dates_with_no_data": "true",
                    "limit": 200,
                    "sort_order": "asc",
                },
            )
            if r.status_code != 200:
                logger.warning("FRED returned %s", r.status_code)
                return []
            data = r.json()
    except Exception as exc:
        logger.warning("FRED fetch failed: %s", exc)
        return []
    return data.get("release_dates") or []


async def upcoming_events(days_ahead: int = 14, force: bool = False) -> list[dict[str, Any]]:
    """Upcoming U.S. macro releases, sorted by countdown.

    This path is intentionally ForexFactory-only because it drives the terminal
    top bar countdown.
    """
    db = get_db()
    if not force:
        cached = await db.macro_cache.find_one({"_id": "events"}, {"_id": 0})
        if cached:
            try:
                source = cached.get("source")
                if source not in {"forex_factory", "fred_fallback"}:
                    raise ValueError("legacy macro cache missing source marker")
                ts = datetime.fromisoformat(cached["fetched_at"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
                ttl_minutes = FOREX_FACTORY_CACHE_TTL_MIN if source == "forex_factory" else CACHE_TTL_HR * 60
                if (_now() - ts) <= timedelta(minutes=ttl_minutes):
                    return cached.get("events") or []
            except Exception:
                pass

    ok = True
    try:
        events, meta = await _fetch_forex_factory_us_events(days_ahead=days_ahead)
    except Exception as exc:
        logger.warning("ForexFactory macro fetch failed; no fallback used for countdown: %s", exc)
        events = []
        ok = False
        meta = {
            "source": "ForexFactory/FairEconomy XML",
            "source_url": FOREX_FACTORY_THIS_WEEK,
            "country_filter": "USD",
            "error": str(exc)[:220],
        }
    await db.macro_cache.update_one(
        {"_id": "events"},
        {"$set": {"events": events, "fetched_at": _now().isoformat(), "source": "forex_factory", "meta": meta}},
        upsert=True,
    )
    await log_activity(
        f"Macro Pulse refreshed from ForexFactory/FairEconomy - {len(events)} USD events",
        "info" if ok else "warn",
    )
    return events


async def source_status() -> dict[str, Any]:
    db = get_db()
    cached = await db.macro_cache.find_one({"_id": "events"}, {"_id": 0})
    if not cached:
        return {"source": "none", "fresh": False, "events_count": 0}
    fetched_at = cached.get("fetched_at")
    age_minutes = None
    fresh = False
    try:
        ts = datetime.fromisoformat(fetched_at)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        age_minutes = round((_now() - ts).total_seconds() / 60, 1)
        ttl_minutes = FOREX_FACTORY_CACHE_TTL_MIN if cached.get("source") == "forex_factory" else CACHE_TTL_HR * 60
        fresh = age_minutes <= ttl_minutes
    except Exception:
        pass
    return {
        "source": cached.get("source") or "unknown",
        "fresh": fresh,
        "age_minutes": age_minutes,
        "events_count": len(cached.get("events") or []),
        "meta": cached.get("meta") or {},
    }


async def imminent_warnings() -> list[dict[str, Any]]:
    events = await upcoming_events()
    return [e for e in events if e.get("is_imminent") and e.get("warns_sectors")]


def map_industry_to_sector(industry: str | None) -> str | None:
    if not industry:
        return None
    s = industry.lower()
    for needle, sector in INDUSTRY_TO_SECTOR.items():
        if needle in s:
            return sector
    return None


async def should_block_long_call(industry: str | None) -> tuple[bool, str | None]:
    sector = map_industry_to_sector(industry)
    if not sector:
        return False, None
    warnings = await imminent_warnings()
    for w in warnings:
        if sector in (w.get("warns_sectors") or []):
            return True, f"{w['tag']} in {w['days_until']}D - avoid {sector} long calls"
    return False, None
