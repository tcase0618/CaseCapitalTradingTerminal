"""Macro Pulse — upcoming econ calendar from FRED.

Pulls major release calendars (FOMC, CPI, PPI, NFP, GDP, Retail Sales)
and maps each event type to sector impact. When a major event is within
48 hours, the dashboard sidebar surfaces a warning, and long-call
recommendations on affected sectors are blocked by the recommendation
gate (`should_block_long_call`).
"""
from __future__ import annotations
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any

import httpx

from .db import get_db, log_activity

logger = logging.getLogger(__name__)

FRED_KEY = os.environ.get("FRED_API_KEY", "").strip()
FRED_BASE = "https://api.stlouisfed.org/fred"
CACHE_TTL_HR = 4
ET = ZoneInfo("America/New_York")

# Release ID → display + sector impact.
# Reference: https://api.stlouisfed.org/fred/releases (release_id is stable)
RELEASES = {
    101: {"name": "FOMC Meeting",       "tag": "FOMC", "warns_sectors": ["TECH", "REAL_ESTATE"], "boosts_sectors": ["FINANCIALS"]},
    10:  {"name": "CPI (Consumer Price Index)", "tag": "CPI", "warns_sectors": ["TECH", "CONSUMER_DISCRETIONARY"], "boosts_sectors": ["ENERGY", "FINANCIALS"]},
    14:  {"name": "PPI (Producer Price Index)", "tag": "PPI", "warns_sectors": ["TECH"], "boosts_sectors": ["ENERGY"]},
    50:  {"name": "Employment Situation", "tag": "JOBS", "warns_sectors": [], "boosts_sectors": ["FINANCIALS", "INDUSTRIALS"]},
    53:  {"name": "GDP",  "tag": "GDP",  "warns_sectors": [], "boosts_sectors": ["FINANCIALS", "INDUSTRIALS"]},
    18:  {"name": "Retail Sales", "tag": "RETAIL", "warns_sectors": [], "boosts_sectors": ["CONSUMER_DISCRETIONARY"]},
}

EVENT_TIMES_ET = {
    "FOMC": "14:00",
    "CPI": "08:30",
    "PPI": "08:30",
    "JOBS": "08:30",
    "GDP": "08:30",
    "RETAIL": "08:30",
}

# FRED release_id=101 can surface non-decision placeholder rows. Use the
# official Federal Reserve FOMC meeting decision dates for countdowns.
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

# Industry → sector tag mapping (loose match — keys are lowercase substrings)
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
    return datetime.now(timezone.utc)


def _event_datetime_et(date_str: str, tag: str) -> datetime:
    hhmm = EVENT_TIMES_ET.get(tag, "09:30")
    hour, minute = [int(x) for x in hhmm.split(":", 1)]
    d = datetime.fromisoformat(date_str).date()
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=ET)


def _build_event(date_str: str, release_id: int, now_utc: datetime) -> dict[str, Any] | None:
    meta = RELEASES[release_id]
    tag = meta["tag"]
    dt_et = _event_datetime_et(date_str, tag)
    diff = dt_et.astimezone(timezone.utc) - now_utc
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
    }


def has_fred() -> bool:
    return bool(FRED_KEY)


async def _fetch_release_dates(days_ahead: int = 14) -> list[dict[str, Any]]:
    """Query FRED for release dates within the window."""
    if not FRED_KEY:
        return []
    today = _now().date()
    end = today + timedelta(days=days_ahead)
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.get(
                f"{FRED_BASE}/releases/dates",
                params={
                    "api_key": FRED_KEY, "file_type": "json",
                    "realtime_start": today.isoformat(),
                    "realtime_end": end.isoformat(),
                    "include_release_dates_with_no_data": "true",
                    "limit": 200, "sort_order": "asc",
                },
            )
            if r.status_code != 200:
                logger.warning("FRED returned %s", r.status_code)
                return []
            data = r.json()
    except Exception as e:
        logger.warning("FRED fetch failed: %s", e)
        return []
    return data.get("release_dates") or []


async def upcoming_events(days_ahead: int = 14, force: bool = False) -> list[dict[str, Any]]:
    """Returns upcoming MAJOR releases (FOMC, CPI, PPI, JOBS, GDP, RETAIL)
    sorted by date. Cached in `macro_cache`."""
    db = get_db()
    if not force:
        cached = await db.macro_cache.find_one({"_id": "events"}, {"_id": 0})
        if cached:
            try:
                ts = datetime.fromisoformat(cached["fetched_at"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if (_now() - ts) <= timedelta(hours=CACHE_TTL_HR):
                    return cached.get("events") or []
            except Exception:
                pass

    raw = await _fetch_release_dates(days_ahead=days_ahead)
    now_utc = _now()
    today = now_utc.date()
    events: list[dict[str, Any]] = []
    end = today + timedelta(days=days_ahead)

    for date_str in FOMC_DECISION_DATES_2026:
        try:
            d = datetime.fromisoformat(date_str).date()
        except Exception:
            continue
        if today <= d <= end:
            ev = _build_event(date_str, 101, now_utc)
            if ev:
                events.append(ev)

    # Dedup by (release_id, year-month). FRED returns daily release rows for
    # releases that update with each new data point — for our purposes we want
    # at most one event per release per calendar month (the actual print date).
    seen: set[tuple[int, str]] = set()
    for r in raw:
        rid = r.get("release_id")
        date_str = r.get("date")
        if rid not in RELEASES or not date_str:
            continue
        if rid == 101:
            continue
        ym = date_str[:7]  # YYYY-MM
        key = (rid, ym)
        if key in seen:
            continue
        seen.add(key)
        try:
            d = datetime.fromisoformat(date_str).date()
        except Exception:
            continue
        if d < today:
            continue
        ev = _build_event(date_str, rid, now_utc)
        if ev:
            events.append(ev)
    events.sort(key=lambda x: (x["hours_until"], x["tag"]))

    await db.macro_cache.update_one(
        {"_id": "events"},
        {"$set": {"events": events, "fetched_at": _now().isoformat()}},
        upsert=True,
    )
    await log_activity(f"Macro Pulse refreshed — {len(events)} events", "info")
    return events


async def imminent_warnings() -> list[dict[str, Any]]:
    """Events within 48h that carry a sector warning."""
    events = await upcoming_events()
    return [e for e in events if e["is_imminent"] and e["warns_sectors"]]


def map_industry_to_sector(industry: str | None) -> str | None:
    if not industry:
        return None
    s = industry.lower()
    for needle, sector in INDUSTRY_TO_SECTOR.items():
        if needle in s:
            return sector
    return None


async def should_block_long_call(industry: str | None) -> tuple[bool, str | None]:
    """True if a macro event in the next 48h warns against this sector's
    long-call recommendations."""
    sector = map_industry_to_sector(industry)
    if not sector:
        return False, None
    warnings = await imminent_warnings()
    for w in warnings:
        if sector in w["warns_sectors"]:
            return True, f"{w['tag']} in {w['days_until']}D · avoid {sector} long calls"
    return False, None
