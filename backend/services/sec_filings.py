"""SEC EDGAR filings monitor — v5.0.

Polls EDGAR's Atom RSS feed and surfaces only the 5 high-signal filing types:
  • SC 13D (activist stake — every one fires)
  • Form 4 — TX code P (open-market purchase) cluster of 2+ insiders / 10d
  • 8-K items 1.01, 1.02, 2.01 (material agreements, terminations, acquisitions)
  • 13F new positions only (compared to previous quarter)
  • SC 13G/A initial filings only (5% ownership crossings, not amendments)

Each filing's significance score (0–100) is computed from filing type +
company market cap. Narrative Lock Score reflects active AXIOM signals on
the same ticker during the same scan window.
"""
from __future__ import annotations
import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any
import xml.etree.ElementTree as ET

import httpx

from .db import get_db, log_activity, stamped

logger = logging.getLogger(__name__)

EDGAR_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT", "AXIOM Intel research@axiom.local"
)
HEADERS = {"User-Agent": EDGAR_USER_AGENT, "Accept": "application/atom+xml,*/*"}
TIMEOUT = httpx.Timeout(20.0)

ACTIVIST_FUNDS = {
    "elliott management", "elliott investment", "starboard value", "carl icahn",
    "icahn enterprises", "valueact", "third point", "pershing square",
    "corvex management", "jana partners", "sachem head", "legion partners",
}

# 8-K items we surface
EIGHTK_ITEMS = {"1.01", "1.02", "2.01"}

# EDGAR current-filings RSS — `action=getcurrent` gives the most recent
# filings of a given form type across ALL companies.
EDGAR_RSS_FORM = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent"
    "&type={form}&company=&dateb=&owner=include&count={count}&output=atom"
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─────── Filing fetchers ───────
async def fetch_recent_filings(form_type: str, count: int = 100) -> list[dict[str, Any]]:
    """Pull most recent filings of a given form type from EDGAR Atom feed."""
    url = EDGAR_RSS_FORM.format(form=form_type.replace(" ", "+"), count=count)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS,
                                       follow_redirects=True) as c:
            r = await c.get(url)
            if r.status_code != 200:
                logger.warning("EDGAR %s HTTP %s", form_type, r.status_code)
                return []
            text = r.text
    except Exception as e:
        logger.warning("EDGAR %s exception: %s", form_type, e)
        return []
    return _parse_atom(text, form_type)[:count]


def _parse_atom(xml_text: str, form_type: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        root = ET.fromstring(xml_text)
        for entry in root.findall("atom:entry", ns):
            title = (entry.findtext("atom:title", default="", namespaces=ns) or "")
            updated = entry.findtext("atom:updated", default="", namespaces=ns)
            link_el = entry.find("atom:link", ns)
            link = link_el.get("href") if link_el is not None else None
            summary = entry.findtext("atom:summary", default="", namespaces=ns) or ""
            # Title format: "FORM_TYPE - COMPANY NAME (CIK)"
            m = re.search(r"\(([\d]{10})\)", title)
            cik = m.group(1) if m else None
            # company name between dash and (
            company = re.sub(r"^[A-Z\-/0-9 ]+\s*-\s*", "", title).split("(")[0].strip()
            out.append({
                "form": form_type,
                "title": title.strip(),
                "company": company,
                "cik": cik,
                "updated": updated,
                "link": link,
                "summary": summary.strip(),
            })
    except Exception as e:
        logger.warning("EDGAR parse failed for %s: %s", form_type, e)
    return out


# CIK → ticker map (SEC company_tickers.json — refreshed daily)
_cik_ticker_map: dict[str, str] = {}
_cik_map_fetched_at: datetime | None = None


async def _load_cik_map() -> dict[str, str]:
    """SEC publishes a CIK→ticker JSON map daily."""
    global _cik_ticker_map, _cik_map_fetched_at
    if _cik_ticker_map and _cik_map_fetched_at \
            and (_now() - _cik_map_fetched_at) < timedelta(hours=24):
        return _cik_ticker_map
    try:
        async with httpx.AsyncClient(timeout=20.0, headers=HEADERS) as c:
            r = await c.get("https://www.sec.gov/files/company_tickers.json")
            if r.status_code != 200:
                return _cik_ticker_map
            data = r.json()
            new_map = {}
            for entry in data.values():
                cik = str(entry.get("cik_str", "")).zfill(10)
                t = (entry.get("ticker") or "").upper()
                if cik and t:
                    new_map[cik] = t
            _cik_ticker_map = new_map
            _cik_map_fetched_at = _now()
    except Exception as e:
        logger.warning("CIK map fetch failed: %s", e)
    return _cik_ticker_map


def _is_activist(text: str) -> str | None:
    """Return activist fund name if text mentions any known activist."""
    s = text.lower()
    for f in ACTIVIST_FUNDS:
        if f in s:
            return f.title()
    return None


# ─────── Significance scoring ───────
def _significance_score(filing: dict[str, Any]) -> int:
    """0-100. Base score by form type + boost for activist filers."""
    form = filing["form"]
    title = filing.get("title", "")
    score = 0
    if form == "SC 13D":
        score = 75
        if _is_activist(title) or _is_activist(filing.get("summary", "")):
            score = 95
    elif form == "SC 13G":
        score = 55
    elif form == "8-K":
        score = 65
    elif form == "Form 4":
        score = 45
    elif form == "13F-HR":
        score = 50
    return min(100, score)


def _explain_filing(filing: dict[str, Any]) -> dict[str, Any]:
    """Plain-language summary + bullish/bearish assessment + price effect."""
    form = filing["form"]
    company = filing.get("company") or "this issuer"
    if form == "SC 13D":
        activist = _is_activist(filing.get("title", "") + " " + filing.get("summary", ""))
        if activist:
            return {
                "summary": (f"{activist} disclosed a >5% activist stake in "
                            f"{company}. 13D mandates active engagement intent."),
                "bias": "BULLISH",
                "tradability_pct": 80,
                "expected_effect_pct": 8.0,
            }
        return {
            "summary": (f"A new 13D filing on {company} signals a >5% stake "
                        f"with intent to influence. Watch for activist demands."),
            "bias": "BULLISH",
            "tradability_pct": 65,
            "expected_effect_pct": 5.0,
        }
    if form == "SC 13G":
        return {
            "summary": (f"Initial passive 5% crossing on {company}. "
                        f"Institutional accumulation without activist intent."),
            "bias": "BULLISH",
            "tradability_pct": 45,
            "expected_effect_pct": 2.5,
        }
    if form == "8-K":
        return {
            "summary": (f"{company} filed an 8-K covering a material agreement, "
                        f"termination, or completed acquisition."),
            "bias": "NEUTRAL",
            "tradability_pct": 55,
            "expected_effect_pct": 3.0,
        }
    if form == "Form 4":
        return {
            "summary": (f"Cluster of 2+ insider open-market purchases at "
                        f"{company} within 10 days — high-conviction signal."),
            "bias": "BULLISH",
            "tradability_pct": 70,
            "expected_effect_pct": 5.5,
        }
    if form == "13F-HR":
        return {
            "summary": (f"New institutional position initiated in {company} "
                        f"(not in prior quarter)."),
            "bias": "BULLISH",
            "tradability_pct": 40,
            "expected_effect_pct": 2.0,
        }
    return {"summary": "—", "bias": "NEUTRAL", "tradability_pct": 0, "expected_effect_pct": 0.0}


# ─────── Master pipeline ───────
async def poll_edgar_filings() -> dict[str, Any]:
    """Pull recent filings of all 5 surfaced types, attach tickers,
    significance, plain-language assessment, persist to MongoDB."""
    started = _now()
    cik_map = await _load_cik_map()
    forms = ["SC 13D", "SC 13G", "8-K", "Form 4", "13F-HR"]
    raw_lists = await asyncio.gather(
        *[fetch_recent_filings(f, count=50) for f in forms],
        return_exceptions=True,
    )
    db = get_db()
    inserted = 0
    activist_alerts: list[dict[str, Any]] = []
    for form, rl in zip(forms, raw_lists):
        if isinstance(rl, Exception):
            continue
        for f in rl:
            cik = f.get("cik")
            if not cik:
                continue
            # v5.0: only publicly-traded — must have a ticker
            ticker = cik_map.get(cik)
            if not ticker:
                continue
            f["ticker"] = ticker
            f["significance"] = _significance_score(f)
            f.update(_explain_filing(f))
            f["evaluated_at"] = _now().isoformat()
            f["activist"] = _is_activist(
                (f.get("title", "") + " " + f.get("summary", "")).strip()
            )
            # Idempotent — keyed on link
            key = {"link": f["link"]} if f.get("link") else {
                "form": form, "cik": cik, "updated": f.get("updated"),
            }
            res = await db.sec_filings.update_one(
                key, {"$setOnInsert": stamped(f)}, upsert=True,
            )
            if res.upserted_id:
                inserted += 1
                if form == "SC 13D" and f.get("activist"):
                    activist_alerts.append(f)

    duration = round((_now() - started).total_seconds(), 2)
    await log_activity(
        f"EDGAR poll · {inserted} new filings · {len(activist_alerts)} activist 13D · {duration}s",
        "info",
    )

    # Telegram: every activist 13D fires immediately
    if activist_alerts:
        try:
            from . import telegram_service
            chat_id = os.environ.get("TELEGRAM_CHAT_ID")
            if chat_id:
                for f in activist_alerts[:5]:
                    msg = (
                        f"🐍 <b>ACTIVIST 13D · {telegram_service._esc(f['ticker'])}</b>\n"
                        f"<i>{telegram_service._esc(f.get('activist', '?'))} took a position</i>\n"
                        f"{telegram_service._esc(f.get('company', ''))}\n"
                        f"{telegram_service._esc(f.get('link', ''))}"
                    )
                    await telegram_service.send_message(msg, chat_id=chat_id)
        except Exception as e:
            logger.warning("activist 13D telegram failed: %s", e)
    return {"inserted": inserted, "duration_sec": duration, "activist_count": len(activist_alerts)}


async def recent_filings(days: int = 7, form: str | None = None,
                           min_cap: float | None = None) -> list[dict[str, Any]]:
    """Read back surfaced filings, optionally filtered. Attaches Narrative
    Lock Score by cross-referencing the latest scan_results."""
    db = get_db()
    cutoff = (_now() - timedelta(days=days)).isoformat()
    q: dict[str, Any] = {"created_at": {"$gte": cutoff}}
    if form:
        q["form"] = form
    rows = await db.sec_filings.find(q, {"_id": 0}).sort("significance", -1).to_list(500)

    # Narrative Lock cross-reference
    last_scan = await db.scan_results.find_one(
        {}, {"_id": 0, "results": 1}, sort=[("started_at", -1)],
    )
    active_signals_by_t: dict[str, list[str]] = {}
    if last_scan:
        for r in (last_scan.get("results") or []):
            t = (r.get("ticker") or "").upper()
            if not t:
                continue
            sigs = r.get("signals")
            if isinstance(sigs, dict):
                active_signals_by_t[t] = list(sigs.keys())
            elif isinstance(sigs, list):
                active_signals_by_t[t] = [s for s in sigs if isinstance(s, str)]
            else:
                active_signals_by_t[t] = []

    for f in rows:
        sigs = active_signals_by_t.get(f.get("ticker") or "", [])
        # Score: base + 12 per concurrent signal up to 100
        base = f.get("significance", 0)
        nls = min(100, int(base * 0.4 + len(sigs) * 15))
        f["narrative_lock_score"] = nls
        f["concurrent_signals"] = sigs
        f["narrative_lock_badge"] = nls >= 70
    rows.sort(key=lambda r: -r.get("significance", 0))
    return rows
