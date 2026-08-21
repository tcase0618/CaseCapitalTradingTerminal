"""PHARMA — fully standalone biotech intelligence pipeline.

Zero interaction with the main signal/learning engine. Own data, own
collections, own scoring. Triggers:
  • Runs in parallel with every main scan
  • Has its own /api/pharma/scan endpoint for standalone execution

Data sources (all free):
  • FDA PDUFA calendar — merged free public PDUFA calendars with source confidence
  • ClinicalTrials.gov v2 API (https://clinicaltrials.gov/api/v2/studies)
  • OpenInsider — biotech insider buying inside 60d of PDUFA
  • Finviz — short interest
  • yfinance — IV rank
  • NIH/CDC curated prevalence map (US population percentages)

Binary Event Score / 100:
  • Phase 3 endpoint quality  25
  • Insider cluster buying    20
  • Short interest squeeze    15
  • IV rank cheapness         15
  • AdCom vote (neutral 7)    15
  • Clean application history 10

Tiers:  STRONG ≥80 (auto-enter) · WATCH ≥65 · NEUTRAL ≥40 · WEAK <40.
Telegram alerts fire on score ≥ 70.
"""
from __future__ import annotations
import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from calendar import monthrange

import httpx
from bs4 import BeautifulSoup

from . import options_engine, pricer
from .db import get_db, log_activity, stamped
from .scrapers import (
    fetch_finviz_short_for_ticker,
    fetch_openinsider_for_ticker,
)

logger = logging.getLogger(__name__)

US_POPULATION = 333_000_000  # US Census 2024 rounded
PDUFA_CACHE_HOURS = int(os.environ.get("PHARMA_PDUFA_CACHE_HOURS", "24") or 24)
AUTO_ENTER_SCORE = 80
TELEGRAM_THRESHOLD = 70
CATALYST_SHOCK_THRESHOLD = 75
PHARMA_OPTION_SNAPSHOT_THRESHOLD = 70
PHARMA_OPTION_SNAPSHOT_BUDGET = 500.0
PHARMA_PM_ROUTE_THRESHOLD = 70


async def _mirror_pg(collection: str, doc: dict[str, Any]) -> None:
    try:
        from . import postgres_store
        await postgres_store.mirror_document(collection, doc, source="pharma")
    except Exception:
        pass

PHARMA_COMPANY_ALIASES: dict[str, str] = {
    "MODERNA": "MRNA",
    "BIONTECH": "BNTX",
    "REGENERON": "REGN",
    "VERTEX": "VRTX",
    "ALNYLAM": "ALNY",
    "BIOMARIN": "BMRN",
    "SAREPTA": "SRPT",
    "NOVAVAX": "NVAX",
    "GILEAD": "GILD",
    "MERCK": "MRK",
    "BRISTOL MYERS": "BMY",
    "ELI LILLY": "LLY",
    "PFIZER": "PFE",
    "ASTRAZENECA": "AZN",
    "AMGEN": "AMGN",
    "INCYTE": "INCY",
    "AXSOME": "AXSM",
}

PHARMA_CATALYST_WEIGHTS: dict[str, int] = {
    "phase 3": 20,
    "phase iii": 20,
    "pivotal": 18,
    "primary endpoint": 18,
    "met endpoint": 16,
    "statistically significant": 16,
    "clinical trial": 12,
    "trial results": 14,
    "vaccine": 10,
    "cancer": 10,
    "oncology": 10,
    "fda approval": 22,
    "approved": 16,
    "breakthrough therapy": 15,
    "fast track": 10,
    "orphan drug": 8,
}

PHARMA_BEARISH_CATALYST_WEIGHTS: dict[str, int] = {
    "failed to meet": 26,
    "failed": 24,
    "fails": 24,
    "missed endpoint": 22,
    "did not meet": 22,
    "complete response letter": 22,
    "response letter": 18,
    "crl": 18,
    "halted": 16,
    "hold": 14,
}


# ─────── Disease prevalence map — NIH / CDC sourced static percentages ───────
# Each value is the share of US population affected (point prevalence or
# lifetime depending on condition). Sources documented per-row.
# Extend as new drugs land. Unknown conditions fall back to 0.1% (rare disease).
PREVALENCE_MAP: dict[str, float] = {
    # NIH/CDC (cdc.gov/nchs, nih.gov)
    "hypertension":            48.0,   # CDC 2024
    "obesity":                 41.9,   # CDC NHANES
    "type 2 diabetes":         11.6,
    "diabetes":                11.6,
    "depression":              8.4,
    "major depressive":        8.4,
    "asthma":                  7.8,
    "copd":                    4.6,
    "atrial fibrillation":     3.0,
    "alzheimer":               2.0,
    "schizophrenia":           1.1,
    "psoriasis":               3.0,
    "rheumatoid arthritis":    1.3,
    "crohn":                   0.24,
    "ulcerative colitis":      0.28,
    "multiple sclerosis":      0.27,
    "parkinson":               0.3,
    "epilepsy":                1.2,
    "lupus":                   0.07,
    "hiv":                     0.36,
    "hepatitis c":             0.7,
    # Oncology (CDC SEER)
    "breast cancer":           1.0,
    "prostate cancer":         1.1,
    "lung cancer":             0.7,
    "colorectal cancer":       0.4,
    "pancreatic cancer":       0.02,
    "leukemia":                0.13,
    "lymphoma":                0.25,
    "melanoma":                0.4,
    "multiple myeloma":        0.03,
    # Rare / orphan (NIH GARD)
    "sickle cell":             0.03,
    "cystic fibrosis":         0.012,
    "hemophilia":              0.005,
    "duchenne":                0.002,
    "huntington":              0.0014,
    "als":                     0.005,
    "amyloidosis":             0.004,
    "narcolepsy":              0.06,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today_iso() -> str:
    return _now().date().isoformat()


def lookup_prevalence(indication: str) -> dict[str, Any]:
    """Returns {pct, patient_count, source} for a disease string. Falls back
    to 0.1% rare-disease default if no curated match."""
    if not indication:
        return {"pct": 0.1, "patient_count": int(US_POPULATION * 0.001), "source": "default_rare"}
    s = indication.lower()
    best_pct = None
    best_key = None
    for key, pct in PREVALENCE_MAP.items():
        if key in s:
            if best_pct is None or pct > best_pct:
                best_pct = pct
                best_key = key
    if best_pct is None:
        return {"pct": 0.1, "patient_count": int(US_POPULATION * 0.001),
                "source": "default_rare", "matched": None}
    return {
        "pct": best_pct,
        "patient_count": int(US_POPULATION * best_pct / 100.0),
        "source": "NIH/CDC",
        "matched": best_key,
    }


def _article_text(article: dict[str, Any]) -> str:
    return f"{article.get('title', '')} {article.get('summary', '')}".strip()


def _map_pharma_article_tickers(article: dict[str, Any]) -> list[str]:
    text = _article_text(article)
    upper = text.upper()
    mapped = {
        str(t).upper().strip()
        for t in (article.get("tickers") or [])
        if str(t or "").strip()
    }
    for alias, ticker in PHARMA_COMPANY_ALIASES.items():
        if alias in upper:
            mapped.add(ticker)
    for match in re.findall(r"\$([A-Z][A-Z0-9.]{0,5})\b", text):
        mapped.add(match.upper().strip("."))
    return sorted(t for t in mapped if 1 < len(t) <= 6)


def _has_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text) is not None


def _score_catalyst_shock(article: dict[str, Any], ticker: str, price: float | None = None) -> dict[str, Any]:
    text = _article_text(article).lower()
    bullish_hits = [term for term in PHARMA_CATALYST_WEIGHTS if _has_phrase(text, term)]
    bearish_hits = [term for term in PHARMA_BEARISH_CATALYST_WEIGHTS if _has_phrase(text, term)]
    bullish_score = sum(PHARMA_CATALYST_WEIGHTS[t] for t in bullish_hits)
    bearish_score = sum(PHARMA_BEARISH_CATALYST_WEIGHTS[t] for t in bearish_hits)
    age = article.get("age_minutes")
    try:
        age = float(age) if age is not None else None
    except (TypeError, ValueError):
        age = None
    recency = 18 if age is not None and age <= 90 else 12 if age is not None and age <= 360 else 7
    ticker_weight = 15 if ticker else 0
    source_score = int(article.get("score") or 0)
    news_quality = 12 if source_score >= 80 else 8 if source_score >= 65 else 4
    score = max(0, min(100, 28 + bullish_score + recency + ticker_weight + news_quality - bearish_score))
    direction = "BEARISH" if bearish_score > bullish_score else "BULLISH" if bullish_score else "WATCH"
    return {
        "shock_score": round(score, 1),
        "direction": direction,
        "tier": "BREAKOUT" if score >= 85 else "WATCH" if score >= CATALYST_SHOCK_THRESHOLD else "MONITOR",
        "bullish_terms": bullish_hits[:8],
        "bearish_terms": bearish_hits[:8],
        "age_minutes": age,
        "current_price": price,
    }


# ─────── FDA PDUFA Calendar ───────
async def fetch_pdufa_calendar() -> list[dict[str, Any]]:
    """Scrape upcoming PDUFA dates. Tries multiple free public sources in
    order — biopharmcatalyst → streetinsider → drugs.com new drugs feed.
    Returns list of {ticker, company, drug, indication, pdufa_date, type}."""
    headers = {
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }
    sources = [
        ("rttnews",          "https://www.rttnews.com/CorpInfo/FDACalendar.aspx"),
        ("biopharmcatalyst", "https://www.biopharmcatalyst.com/calendars/fda-calendar"),
        ("streetinsider",    "https://www.streetinsider.com/Sect/AllStocks/PDUFA+Dates/0.html"),
    ]
    for name, url in sources:
        try:
            async with httpx.AsyncClient(timeout=40.0, follow_redirects=True) as client:
                r = await client.get(url, headers=headers)
                if r.status_code != 200:
                    logger.warning("PDUFA fetch [%s] HTTP %s", name, r.status_code)
                    continue
                rows = _parse_pdufa_html(r.text, name)
                if rows:
                    logger.info("PDUFA source %s — %d entries", name, len(rows))
                    return rows
        except Exception as e:
            logger.warning("PDUFA source %s exception: %s", name, e)
            continue
    # Last-resort curated seed (kept current with major upcoming PDUFAs).
    # Operator-editable from MongoDB if biopharmcatalyst/streetinsider stay blocked.
    logger.warning("PDUFA all live sources failed — falling back to seed list")
    return _seed_pdufa()


def _seed_pdufa() -> list[dict[str, Any]]:
    """Curated seed list of known upcoming PDUFA dates. Updated quarterly.
    Used only when all live sources are blocked. Operator can override by
    inserting docs directly into pharma_pdufa_cache.entries in MongoDB."""
    rows = [
        {"ticker": "LLY",   "drug": "Donanemab",       "indication": "Early Alzheimer's disease", "pdufa_date": "2026-07-08", "type": "PDUFA"},
        {"ticker": "BMY",   "drug": "Iberdomide",      "indication": "Multiple myeloma",            "pdufa_date": "2026-08-15", "type": "PDUFA"},
        {"ticker": "MRK",   "drug": "Keytruda + Lenvima","indication": "Endometrial cancer",         "pdufa_date": "2026-09-22", "type": "sBLA"},
        {"ticker": "VRTX",  "drug": "Vanzacaftor triple","indication": "Cystic fibrosis",            "pdufa_date": "2026-04-30", "type": "NDA"},
        {"ticker": "BIIB",  "drug": "BIIB080",         "indication": "Alzheimer's disease",         "pdufa_date": "2026-06-18", "type": "BLA"},
        {"ticker": "GILD",  "drug": "Lenacapavir",     "indication": "HIV prevention",              "pdufa_date": "2026-06-19", "type": "sNDA"},
        {"ticker": "REGN",  "drug": "Linvoseltamab",   "indication": "Multiple myeloma",            "pdufa_date": "2026-08-22", "type": "BLA"},
        {"ticker": "ALNY",  "drug": "Vutrisiran (ATTR)","indication": "Amyloidosis cardiomyopathy",  "pdufa_date": "2026-04-04", "type": "sNDA"},
        {"ticker": "NVAX",  "drug": "NVX-CoV2373 next-gen","indication": "COVID-19 booster",         "pdufa_date": "2026-05-15", "type": "EUA"},
        {"ticker": "MRNA",  "drug": "mRNA-1083",       "indication": "Combo flu/COVID vaccine",      "pdufa_date": "2026-09-30", "type": "BLA"},
        {"ticker": "AXSM",  "drug": "Auvelity",        "indication": "Major depressive disorder",    "pdufa_date": "2026-07-12", "type": "sNDA"},
        {"ticker": "SRPT",  "drug": "Elevidys",        "indication": "Duchenne muscular dystrophy",  "pdufa_date": "2026-06-21", "type": "sBLA"},
        {"ticker": "INCY",  "drug": "Ruxolitinib XR",  "indication": "Atopic dermatitis",            "pdufa_date": "2026-05-08", "type": "sNDA"},
    ]
    for row in rows:
        row["source"] = "curated_seed"
        row["data_quality"] = "fallback_calendar"
    return rows


def _parse_pdufa_html(html_text: str, source: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        soup = BeautifulSoup(html_text, "html.parser")
        for table in soup.find_all("table"):
            for row in table.find_all("tr")[1:]:
                cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
                if len(cells) < 3:
                    continue
                # Find a ticker pattern in the cells
                ticker = None
                for c in cells[:3]:
                    m = re.match(r"^[\$]?([A-Z]{1,5})(?:\s|$)", c)
                    if m:
                        ticker = m.group(1)
                        break
                if not ticker:
                    continue
                # Find a date pattern in any cell
                date_iso = None
                for c in cells:
                    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y"):
                        try:
                            date_iso = datetime.strptime(c, fmt).date().isoformat()
                            break
                        except Exception:
                            continue
                    if date_iso:
                        break
                if not date_iso:
                    continue
                drug = cells[1] if len(cells) > 1 else ""
                indication = cells[2] if len(cells) > 2 else ""
                out.append({
                    "ticker": ticker,
                    "drug": drug[:80],
                    "indication": indication[:120],
                    "pdufa_date": date_iso,
                    "type": "PDUFA",
                    "source": source,
                })
    except Exception as e:
        logger.warning("PDUFA parse failed (%s): %s", source, e)
    return out


# ─────── ClinicalTrials.gov ───────
def _clean_pdufa_cell(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()


def _extract_pdufa_date(text: str) -> str | None:
    text = _clean_pdufa_cell(text)
    candidates = [text]
    candidates.extend(re.findall(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", text))
    candidates.extend(re.findall(r"\b\d{4}-\d{1,2}-\d{1,2}\b", text))
    candidates.extend(re.findall(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b", text, re.I))
    candidates.extend(re.findall(r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}\b", text, re.I))
    for raw in candidates:
        normalized = raw.replace("Sept", "Sep").replace(".", "").replace(",", "")
        for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y"):
            try:
                return datetime.strptime(normalized, fmt).date().isoformat()
            except Exception:
                continue
    return None


def _pdufa_cell_by_header(cells: list[str], headers: list[str], *needles: str) -> str:
    lower_headers = [h.lower() for h in headers]
    for needle in needles:
        for i, header in enumerate(lower_headers):
            if needle in header and i < len(cells):
                return cells[i]
    return ""


def _extract_pdufa_ticker(cells: list[str], source: str) -> str | None:
    blocked = {"FDA", "PDUFA", "PMDA", "NDA", "BLA", "SBLA", "SNDA", "IND", "PHASE", "TRIAL"}
    for cell in cells[:3]:
        first = _clean_pdufa_cell(cell).replace("$", "").replace(".", "").split(" ")[0].strip()
        if re.fullmatch(r"[A-Z][A-Z0-9]{1,5}", first) and first not in blocked:
            return first.upper()
    joined = " ".join(cells)
    for pattern in (r"\$([A-Z][A-Z0-9.]{0,5})\b", r"\(([A-Z][A-Z0-9.]{0,5})\)"):
        match = re.search(pattern, joined)
        if match:
            ticker = match.group(1).replace(".", "").upper()
            if ticker not in blocked:
                return ticker
    for cell in cells[:4]:
        clean = _clean_pdufa_cell(cell).replace("$", "").replace(".", "")
        if re.fullmatch(r"[A-Z][A-Z0-9]{1,5}", clean) and clean not in blocked:
            return clean.upper()
    if source == "drugs_com":
        return None
    return None


def _best_pdufa_text_cell(cells: list[str], headers: list[str], *needles: str) -> str:
    value = _pdufa_cell_by_header(cells, headers, *needles)
    if value:
        return value
    ranked = sorted(cells, key=len, reverse=True)
    return ranked[0] if ranked else ""


def _extract_pdufa_event_type(cells: list[str]) -> str:
    joined = " ".join(cells).lower()
    if "adcom" in joined or "advisory committee" in joined:
        return "ADCOM"
    if "sbla" in joined:
        return "sBLA"
    if "snda" in joined:
        return "sNDA"
    if "bla" in joined:
        return "BLA"
    if "nda" in joined:
        return "NDA"
    if "pdufa" in joined or "fda action" in joined:
        return "PDUFA"
    return "FDA"


def _parse_pdufa_html(html_text: str, source: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        soup = BeautifulSoup(html_text, "html.parser")
        for table in soup.find_all("table"):
            headers: list[str] = []
            first_row = table.find("tr")
            if first_row:
                headers = [_clean_pdufa_cell(c.get_text(" ", strip=True)) for c in first_row.find_all(["th", "td"])]
            for row in table.find_all("tr")[1:]:
                cells = [_clean_pdufa_cell(c.get_text(" ", strip=True)) for c in row.find_all(["td", "th"])]
                cells = [c for c in cells if c]
                if len(cells) < 2:
                    continue
                date_iso = None
                for cell in cells:
                    date_iso = _extract_pdufa_date(cell)
                    if date_iso:
                        break
                if not date_iso:
                    continue
                ticker = _extract_pdufa_ticker(cells, source)
                company = _pdufa_cell_by_header(cells, headers, "company", "sponsor")
                drug = _best_pdufa_text_cell(cells, headers, "drug", "product", "therapy", "candidate")
                indication = _pdufa_cell_by_header(cells, headers, "indication", "disease", "condition")
                if not indication:
                    indication = next((c for c in reversed(cells) if not _extract_pdufa_date(c)), "")
                if not ticker and not company:
                    continue
                out.append({
                    "ticker": ticker or "MANUAL",
                    "company": company[:100],
                    "drug": drug[:100],
                    "indication": indication[:160],
                    "pdufa_date": date_iso,
                    "type": _extract_pdufa_event_type(cells),
                    "source": source,
                    "source_list": [source],
                    "source_count": 1,
                    "source_confidence": 70,
                    "data_quality": "live_calendar",
                })
    except Exception as e:
        logger.warning("PDUFA parse failed (%s): %s", source, e)
    return out


def _dedupe_pdufa_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "MANUAL").upper().strip()
        date = str(row.get("pdufa_date") or "")[:10]
        drug_key = re.sub(r"[^a-z0-9]+", "", str(row.get("drug") or row.get("company") or "").lower())[:28]
        if not date or not drug_key:
            continue
        key = (ticker, date, drug_key)
        existing = merged.get(key)
        if not existing:
            row = dict(row)
            row["ticker"] = ticker
            row["source_list"] = sorted(set(row.get("source_list") or [row.get("source") or "unknown"]))
            row["source_count"] = len(row["source_list"])
            merged[key] = row
            continue
        sources = sorted(set((existing.get("source_list") or []) + (row.get("source_list") or [row.get("source") or "unknown"])))
        existing["source_list"] = sources
        existing["source_count"] = len(sources)
        for field in ("company", "drug", "indication", "type"):
            if len(str(row.get(field) or "")) > len(str(existing.get(field) or "")):
                existing[field] = row.get(field)
    out = list(merged.values())
    for row in out:
        count = int(row.get("source_count") or 1)
        row["source_confidence"] = min(98, 62 + count * 18)
        row["data_quality"] = "cross_checked_calendar" if count > 1 else "live_calendar"
        row["source"] = "+".join(row.get("source_list") or [row.get("source") or "unknown"])
    out.sort(key=lambda r: (str(r.get("pdufa_date") or "9999-99-99"), -int(r.get("source_count") or 0), str(r.get("ticker") or "")))
    return out


async def fetch_pdufa_calendar() -> list[dict[str, Any]]:
    headers = {
        "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/124.0.0.0 Safari/537.36"),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }
    sources = [
        ("rttnews", "https://www.rttnews.com/CorpInfo/FDACalendar.aspx"),
        ("biopharmcatalyst", "https://www.biopharmcatalyst.com/calendars/fda-calendar"),
        ("benzinga", "https://www.benzinga.com/fda-calendar"),
        ("marketbeat", "https://www.marketbeat.com/fda-calendar/"),
        ("streetinsider", "https://www.streetinsider.com/Sect/AllStocks/PDUFA+Dates/0.html"),
        ("drugs_com", "https://www.drugs.com/newdrugs.html"),
    ]
    rows: list[dict[str, Any]] = []
    source_errors: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=40.0, follow_redirects=True) as client:
        for name, url in sources:
            try:
                response = await client.get(url, headers=headers)
                if response.status_code != 200:
                    logger.warning("PDUFA fetch [%s] HTTP %s", name, response.status_code)
                    source_errors.append({"source": name, "status": response.status_code})
                    continue
                parsed = _parse_pdufa_html(response.text, name)
                logger.info("PDUFA source %s - %d parsed entries", name, len(parsed))
                rows.extend(parsed)
            except Exception as e:
                logger.warning("PDUFA source %s exception: %s", name, e)
                source_errors.append({"source": name, "error": str(e)[:160]})
    deduped = _dedupe_pdufa_rows(rows)
    if deduped:
        for row in deduped:
            row["source_errors"] = source_errors[:6]
        return deduped
    logger.warning("PDUFA all live sources failed - falling back to seed list")
    fallback = _seed_pdufa()
    for row in fallback:
        row["source_list"] = ["curated_seed"]
        row["source_count"] = 0
        row["source_confidence"] = 25
        row["source_errors"] = source_errors[:6]
    return fallback


def _pdufa_rows_are_fallback(rows: list[dict[str, Any]]) -> bool:
    if not rows:
        return False
    return all(
        int(row.get("source_count") or 0) <= 0
        or "curated_seed" in {str(s) for s in (row.get("source_list") or [])}
        for row in rows
    )


def _pdufa_source_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    source_counts: dict[str, int] = {}
    quality_counts: dict[str, int] = {}
    source_errors: list[dict[str, Any]] = []
    seen_errors: set[str] = set()
    for row in rows:
        quality = str(row.get("data_quality") or "unknown")
        quality_counts[quality] = quality_counts.get(quality, 0) + 1
        for source in row.get("source_list") or [row.get("source") or "unknown"]:
            source = str(source or "unknown")
            source_counts[source] = source_counts.get(source, 0) + 1
        for err in row.get("source_errors") or []:
            key = f"{err.get('source')}:{err.get('status') or err.get('error')}"
            if key not in seen_errors:
                source_errors.append(err)
                seen_errors.add(key)
    return {
        "source_counts": source_counts,
        "quality_counts": quality_counts,
        "source_errors": source_errors[:12],
        "fallback_used": _pdufa_rows_are_fallback(rows),
    }


async def import_fda_calendar(
    *,
    persist: bool = True,
    allow_fallback: bool = False,
    triggered_by: str = "cli",
) -> dict[str, Any]:
    """Fetch, validate, and optionally import public FDA/PDUFA calendar rows.

    This intentionally refuses to write the curated seed fallback unless
    allow_fallback=True. The calendar UI should know when it is showing live
    public-calendar data versus backup seed data.
    """
    fetched_at = _now().isoformat()
    rows = await fetch_pdufa_calendar()
    rows = _dedupe_pdufa_rows(rows) if rows else []
    summary = _pdufa_source_summary(rows)
    blocked = bool(rows and summary["fallback_used"] and not allow_fallback)
    if blocked:
        await log_activity(
            "FDA calendar import blocked - live sources unavailable and fallback disabled",
            "warn",
            {"rows": len(rows), "triggered_by": triggered_by, "source_errors": summary["source_errors"]},
        )
        return {
            "ok": False,
            "imported": 0,
            "rows": rows,
            "count": len(rows),
            "fetched_at": fetched_at,
            "persisted": False,
            "blocked": True,
            "reason": "live_sources_unavailable_fallback_not_imported",
            **summary,
        }

    imported = 0
    if persist and rows:
        db = get_db()
        await db.pharma_pdufa_cache.update_one(
            {"_id": "calendar"},
            {"$set": {
                "entries": rows,
                "fetched_at": fetched_at,
                "imported_at": fetched_at,
                "imported_by": triggered_by,
                "source_summary": summary,
            }},
            upsert=True,
        )
        for row in rows:
            doc = {
                **row,
                "calendar_imported_at": fetched_at,
                "calendar_imported_by": triggered_by,
                "calendar_source_summary": summary,
            }
            await db.pharma_pdufa.update_one(
                {
                    "ticker": doc.get("ticker"),
                    "pdufa_date": doc.get("pdufa_date"),
                    "drug": doc.get("drug"),
                },
                {"$set": stamped(doc)},
                upsert=True,
            )
            await _mirror_pg("pharma_pdufa", doc)
            imported += 1
        await log_activity(
            f"FDA calendar import complete - {imported} row(s)",
            "success",
            {"triggered_by": triggered_by, "quality_counts": summary["quality_counts"]},
        )

    return {
        "ok": bool(rows),
        "imported": imported,
        "rows": rows,
        "count": len(rows),
        "fetched_at": fetched_at,
        "persisted": bool(persist and rows),
        "blocked": False,
        **summary,
    }


async def fetch_clinical_trial(drug: str, ticker: str | None = None) -> dict[str, Any] | None:
    """Fetch latest Phase 3 study for a drug from ClinicalTrials.gov v2 API.
    Strategy:
      1. Try `query.intr` (intervention search) — most accurate for drug names
      2. Filter to Phase 3 status: RECRUITING, ACTIVE_NOT_RECRUITING, COMPLETED
      3. If no Phase 3 hit, fall back to highest-phase study
      4. As last resort, try `query.term` keyword search (catches NCT IDs +
         brand vs generic names)"""
    if not drug:
        return None
    base = "https://clinicaltrials.gov/api/v2/studies"
    # Strip parenthetical/qualifier text from drug name (e.g. "Vutrisiran (ATTR)" → "Vutrisiran")
    clean = re.sub(r"\s*\([^)]+\)\s*", " ", drug).strip()
    primary = clean.split()[0] if clean else drug
    try_queries = [
        {"query.intr": primary, "pageSize": 10},
        {"query.term": clean, "pageSize": 10},
    ]
    if ticker:
        try_queries.append({"query.term": f"{clean} {ticker}", "pageSize": 10})

    studies: list[dict] = []
    # ClinicalTrials.gov is fronted by Cloudflare which blocks plain httpx
    # via TLS fingerprinting. curl_cffi impersonates real Chrome and gets
    # through reliably. Run in a thread executor to keep this async.
    def _fetch_sync() -> list[dict]:
        from curl_cffi import requests as cc_requests
        for params in try_queries:
            params = {**params, "filter.overallStatus":
                        "RECRUITING|ACTIVE_NOT_RECRUITING|COMPLETED|TERMINATED"}
            try:
                r = cc_requests.get(base, params=params, impersonate="chrome120",
                                     timeout=20)
            except Exception:
                continue
            if r.status_code != 200:
                logger.debug("ClinicalTrials (cffi) %s -> %s", params, r.status_code)
                continue
            try:
                data = r.json()
            except Exception:
                continue
            st = data.get("studies") or []
            if st:
                return st
        return []
    try:
        loop = asyncio.get_event_loop()
        studies = await loop.run_in_executor(None, _fetch_sync)
    except Exception as e:
        logger.debug("ClinicalTrials fetch failed for %s: %s", drug, e)
        return None
    if not studies:
        return None
    # Rank: prefer Phase 3 active/completed with highest enrollment
    def rank(s: dict) -> tuple[int, int]:
        proto = s.get("protocolSection") or {}
        phases = " ".join((proto.get("designModule") or {}).get("phases") or []).upper()
        status = ((proto.get("statusModule") or {}).get("overallStatus") or "").upper()
        enroll = ((proto.get("designModule") or {}).get("enrollmentInfo") or {}).get("count") or 0
        phase_rank = 3 if ("PHASE3" in phases or "PHASE_3" in phases) else \
                       2 if ("PHASE2" in phases or "PHASE_2" in phases) else 1
        status_rank = 2 if status in ("ACTIVE_NOT_RECRUITING", "COMPLETED") else 1
        return (phase_rank, status_rank, int(enroll))
    best = max(studies, key=rank)
    proto = best.get("protocolSection") or {}
    return {
        "nct_id": (proto.get("identificationModule") or {}).get("nctId"),
        "title": (proto.get("identificationModule") or {}).get("briefTitle"),
        "phases": (proto.get("designModule") or {}).get("phases") or [],
        "status": (proto.get("statusModule") or {}).get("overallStatus"),
        "enrollment": ((proto.get("designModule") or {}).get("enrollmentInfo") or {}).get("count"),
        "primary_completion": ((proto.get("statusModule") or {}).get(
                                  "primaryCompletionDateStruct") or {}).get("date"),
    }


# ─────── Scoring ───────
def score_phase3_endpoint(trial: dict | None) -> tuple[float, str]:
    """25 pts. Phase 3 active+enrollment≥300 = full. Phase 3 = 18. Phase 2 = 10."""
    if not trial:
        return 0.0, "no trial data"
    phases = " ".join(trial.get("phases", [])).upper()
    enroll = trial.get("enrollment") or 0
    status = (trial.get("status") or "").upper()
    if "PHASE3" in phases or "PHASE_3" in phases:
        if enroll >= 300 and status in ("COMPLETED", "ACTIVE_NOT_RECRUITING"):
            return 25.0, f"Phase 3 complete · n={enroll}"
        if enroll >= 150:
            return 20.0, f"Phase 3 active · n={enroll}"
        return 15.0, "Phase 3 early"
    if "PHASE2" in phases or "PHASE_2" in phases:
        return 10.0, "Phase 2 only"
    return 5.0, "early stage"


def score_insider(insider_summary: dict | None) -> tuple[float, str]:
    """20 pts. Cluster of 3+ insider buys in 60d = full."""
    if not insider_summary:
        return 0.0, "no insider activity"
    n = insider_summary.get("buy_count") or 0
    val = insider_summary.get("total_value_usd") or 0
    if n >= 3 and val >= 500_000:
        return 20.0, f"{n} insider buys · ${val/1e6:.1f}M"
    if n >= 2:
        return 14.0, f"{n} insider buys"
    if n >= 1:
        return 8.0, f"{n} insider buy"
    return 0.0, "no insider activity"


def score_short(short_pct: float | None) -> tuple[float, str]:
    """15 pts. Higher short = more squeeze fuel."""
    try:
        sp = float(short_pct) if short_pct is not None else None
    except (TypeError, ValueError):
        sp = None
    if sp is None:
        return 0.0, "no short data"
    if sp >= 25:
        return 15.0, f"{sp:.1f}% short — heavy squeeze fuel"
    if sp >= 15:
        return 10.0, f"{sp:.1f}% short"
    if sp >= 8:
        return 5.0, f"{sp:.1f}% short"
    return 0.0, f"{sp:.1f}% short — low"


def score_iv(iv_rank: float | None) -> tuple[float, str]:
    """15 pts. Cheaper IV → more upside on binary."""
    if iv_rank is None:
        return 6.0, "IV rank unknown"
    if iv_rank < 25:
        return 15.0, f"IV rank {iv_rank:.0f} · cheap"
    if iv_rank < 40:
        return 10.0, f"IV rank {iv_rank:.0f}"
    if iv_rank < 60:
        return 5.0, f"IV rank {iv_rank:.0f}"
    return 0.0, f"IV rank {iv_rank:.0f} · pricey"


def score_adcom(adcom: dict | None) -> tuple[float, str]:
    """15 pts. Positive AdCom vote = 15. No AdCom = neutral 7. Negative = 0."""
    if not adcom:
        return 7.0, "no AdCom (neutral)"
    vote = (adcom.get("vote") or "").upper()
    if "POSITIVE" in vote or "FAVORABLE" in vote or "YES" in vote:
        return 15.0, f"AdCom positive ({adcom.get('date','?')})"
    if "NEGATIVE" in vote or "UNFAVORABLE" in vote or "NO" in vote:
        return 0.0, f"AdCom negative ({adcom.get('date','?')})"
    return 7.0, "AdCom split (neutral)"


def score_application_history(history: dict | None) -> tuple[float, str]:
    """10 pts. Clean application = 10. CRL prior = 4. Multiple CRLs = 0."""
    if not history:
        return 7.0, "no application history (default clean)"
    crls = history.get("crl_count") or 0
    if crls == 0:
        return 10.0, "clean application history"
    if crls == 1:
        return 4.0, "1 prior CRL"
    return 0.0, f"{crls} prior CRLs"


def compute_binary_event_score(
    trial: dict | None,
    insider_summary: dict | None,
    short_pct: float | None,
    iv_rank: float | None,
    adcom: dict | None,
    history: dict | None,
) -> dict[str, Any]:
    p3 = score_phase3_endpoint(trial)
    ins = score_insider(insider_summary)
    sh = score_short(short_pct)
    iv = score_iv(iv_rank)
    ac = score_adcom(adcom)
    hist = score_application_history(history)
    total = round(p3[0] + ins[0] + sh[0] + iv[0] + ac[0] + hist[0], 1)
    tier = ("STRONG" if total >= 80 else
            "WATCH" if total >= 65 else
            "NEUTRAL" if total >= 40 else
            "WEAK")
    return {
        "score": total,
        "tier": tier,
        "components": {
            "phase3":      {"points": p3[0],   "note": p3[1],   "max": 25},
            "insider":     {"points": ins[0],  "note": ins[1],  "max": 20},
            "short":       {"points": sh[0],   "note": sh[1],   "max": 15},
            "iv":          {"points": iv[0],   "note": iv[1],   "max": 15},
            "adcom":       {"points": ac[0],   "note": ac[1],   "max": 15},
            "application": {"points": hist[0], "note": hist[1], "max": 10},
        },
    }


async def build_option_snapshot(row: dict[str, Any], *, persist: bool = True) -> dict[str, Any]:
    """Create an auditable option thesis for a pharma alert.

    Research only: this captures the contract and quote evidence that existed
    at alert time. It never submits, stages, modifies, or cancels orders.
    """
    ticker = str(row.get("ticker") or "").upper().strip()
    pdufa_date = str(row.get("pdufa_date") or "").strip()
    drug = str(row.get("drug") or "").strip()
    base = {
        "ticker": ticker,
        "drug": drug,
        "pdufa_date": pdufa_date,
        "alert_score": row.get("binary_event_score"),
        "alert_price": row.get("current_price"),
        "snapshot_at": _now().isoformat(),
        "budget": PHARMA_OPTION_SNAPSHOT_BUDGET,
        "authority": "RESEARCH_ONLY_NO_EXECUTION",
        "strategy": "LONG_CALL",
        "direction": "BULL",
    }

    if not ticker:
        snap = {**base, "ok": False, "status": "NO_TICKER", "reason": "missing ticker"}
    else:
        try:
            chain = await options_engine.get_options_data(ticker, catalyst_date=pdufa_date or None)
            if not chain:
                snap = {**base, "ok": False, "status": "NO_CHAIN", "reason": "no options chain available"}
            else:
                contract = options_engine.find_best_contract(chain, "BULL", budget=PHARMA_OPTION_SNAPSHOT_BUDGET)
                chain_meta = {
                    "spot": chain.get("price"),
                    "expiration_selected": chain.get("expiration"),
                    "iv_rank": chain.get("iv_rank"),
                    "iv_label": chain.get("iv_label"),
                    "atm_iv": chain.get("atm_iv"),
                    "data_provider": chain.get("data_provider"),
                    "data_feed": chain.get("data_feed"),
                    "data_quality": chain.get("data_quality"),
                    "snapshot_count": chain.get("snapshot_count"),
                    "expiration_window": chain.get("expiration_window"),
                    "strike_window": chain.get("strike_window"),
                }
                if contract:
                    spread = float(contract.get("spread") or 0)
                    premium = float(contract.get("premium") or 0)
                    spread_pct = round((spread / premium) * 100, 2) if premium > 0 else None
                    snap = {
                        **base,
                        "ok": True,
                        "status": "CONTRACT_SNAPSHOT",
                        "reason": "validated research contract captured",
                        "chain": chain_meta,
                        "contract": contract,
                        "liquidity": contract.get("liquidity"),
                        "spread_pct": spread_pct,
                        "max_loss": contract.get("max_loss"),
                        "contracts_at_budget": contract.get("contracts_at_budget"),
                        "tradeability": (
                            "CLEAN" if contract.get("liquidity") == "GOOD"
                            else "WATCH" if contract.get("liquidity") == "WARN"
                            else "RESEARCH_ONLY"
                        ),
                    }
                else:
                    snap = {
                        **base,
                        "ok": False,
                        "status": "NO_VALID_CONTRACT",
                        "reason": "chain loaded but no premium/delta/liquidity-valid contract selected",
                        "chain": chain_meta,
                    }
        except Exception as exc:
            logger.warning("Pharma option snapshot failed for %s: %s", ticker, exc)
            snap = {**base, "ok": False, "status": "SNAPSHOT_ERROR", "reason": str(exc)}

    if persist and ticker:
        try:
            db = get_db()
            await db.pharma_option_snapshots.update_one(
                {"ticker": ticker, "pdufa_date": pdufa_date, "drug": drug},
                {"$set": stamped(snap)},
                upsert=True,
            )
            await _mirror_pg("pharma_option_snapshots", snap)
        except Exception as exc:
            logger.warning("Persist pharma option snapshot failed for %s: %s", ticker, exc)
            snap["persist_error"] = str(exc)
    return snap


def _component_points(row: dict[str, Any], key: str) -> float:
    try:
        return float(((row.get("score_components") or {}).get(key) or {}).get("points") or 0)
    except (TypeError, ValueError):
        return 0.0


def build_pm_candidate(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a pharma catalyst row into the PM's normal scan-row shape."""
    ticker = str(row.get("ticker") or "").upper().strip()
    score = float(row.get("binary_event_score") or 0)
    price = float(row.get("current_price") or 0)
    target = round(price * 1.40, 2) if price > 0 else 0.0
    stop = round(price * 0.85, 2) if price > 0 else 0.0
    signals = ["PHARMA_PDUFA", "BINARY_FDA_CATALYST"]
    if _component_points(row, "phase3") >= 15:
        signals.append("PHARMA_PHASE_3")
    if _component_points(row, "insider") >= 8:
        signals.append("PHARMA_INSIDER_BUYING")
    if _component_points(row, "short") >= 10:
        signals.append("PHARMA_SHORT_SQUEEZE")
    if _component_points(row, "iv") >= 10:
        signals.append("PHARMA_CHEAP_IV")

    snap = row.get("option_snapshot") or {}
    chain = snap.get("chain") or {}
    contract = snap.get("contract") or {}
    if snap.get("ok") and contract:
        opts = {
            "strategy": "LONG_CALL",
            "direction": "BULL",
            "strategy_reason": "Pharma binary catalyst with captured contract snapshot; PM retains final authority.",
            "data_provider": chain.get("data_provider") or contract.get("data_provider"),
            "data_feed": chain.get("data_feed") or contract.get("data_feed"),
            "data_quality": chain.get("data_quality") or contract.get("data_quality"),
            "iv_rank": chain.get("iv_rank"),
            "iv_label": chain.get("iv_label"),
            "atm_iv": chain.get("atm_iv"),
            "spot": chain.get("spot") or price,
            "expiration": contract.get("expiration") or chain.get("expiration_selected"),
            "contract": contract,
            "crush_risk": "HIGH" if float(chain.get("iv_rank") or row.get("iv_rank") or 50) >= 70 else "MODERATE",
            "crush_recommendation": "Defined premium only; reassess before catalyst and never hold through FDA binary without PM approval.",
        }
    else:
        opts = {
            "strategy": "AVOID_OPTIONS",
            "direction": "NONE",
            "strategy_reason": f"No validated pharma option snapshot: {snap.get('reason') or 'not captured'}",
            "iv_rank": row.get("iv_rank"),
            "iv_label": "UNKNOWN",
            "contract": None,
        }

    risk_score = 55.0
    if row.get("data_quality") == "fallback_calendar":
        risk_score += 8.0
    if not snap.get("ok"):
        risk_score += 8.0
    if float(row.get("iv_rank") or 50) >= 70:
        risk_score += 8.0
    risk_score = min(85.0, risk_score)

    return {
        "ticker": ticker,
        "source": "pharma",
        "source_type": "PHARMA_PDUFA",
        "price": price,
        "target_blended": target,
        "target_high": target,
        "stop_loss": stop,
        "entry_low": round(price * 0.99, 2) if price > 0 else None,
        "entry_high": round(price * 1.01, 2) if price > 0 else None,
        "signals": signals,
        "signal_score": min(10.0, score / 10.0),
        "trade_score": min(40.0, score / 1.75),
        "learning_score": 0.0,
        "sector": "Healthcare",
        "risk": {"score": risk_score, "level": "HIGH", "stop_loss": stop},
        "options": opts,
        "time_target": {"target_date": row.get("pdufa_date"), "days_remaining": row.get("days_until") or 30},
        "pharma": {
            "drug": row.get("drug"),
            "indication": row.get("indication"),
            "pdufa_date": row.get("pdufa_date"),
            "binary_event_score": score,
            "tier": row.get("tier"),
            "prevalence": row.get("prevalence"),
            "trial": row.get("trial"),
            "data_quality": row.get("data_quality"),
        },
    }


async def route_to_pm(row: dict[str, Any], *, persist: bool = True) -> dict[str, Any]:
    """Ask PM for a ruling on a pharma candidate without executing anything."""
    from . import portfolio_manager

    candidate = build_pm_candidate(row)
    decisions = portfolio_manager.evaluate_rows(
        [candidate],
        equity=portfolio_manager.DEFAULT_EQUITY,
        mode="BALANCED",
    )
    decision = decisions[0] if decisions else {}
    docket = {
        "ticker": candidate.get("ticker"),
        "drug": row.get("drug"),
        "pdufa_date": row.get("pdufa_date"),
        "routed_at": _now().isoformat(),
        "authority": "PM_DISCRETION_NO_PHARMA_EXECUTION",
        "candidate": candidate,
        "decision": decision,
        "option_snapshot": row.get("option_snapshot"),
    }
    if persist and candidate.get("ticker"):
        try:
            db = get_db()
            await db.pharma_pm_decisions.update_one(
                {
                    "ticker": candidate["ticker"],
                    "pdufa_date": row.get("pdufa_date"),
                    "drug": row.get("drug"),
                },
                {"$set": stamped(docket)},
                upsert=True,
            )
            await _mirror_pg("pharma_pm_decisions", docket)
        except Exception as exc:
            logger.warning("Persist pharma PM decision failed for %s: %s", candidate.get("ticker"), exc)
            docket["persist_error"] = str(exc)
    return docket


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in (None, ""):
            return default
        n = float(value)
        return n if n == n else default
    except (TypeError, ValueError):
        return default


def _date_from_iso(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        try:
            return datetime.fromisoformat(str(value)[:10])
        except Exception:
            return None


def pharma_data_gate(row: dict[str, Any]) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    neutralized: list[str] = []
    sources: list[dict[str, Any]] = []

    ticker = str(row.get("ticker") or "").upper().strip()
    if not ticker:
        blockers.append("missing_ticker")
    sources.append({"key": "ticker_match", "status": "PASS" if ticker else "BLOCK", "detail": ticker or "-"})

    pdufa_date = str(row.get("pdufa_date") or "").strip()
    parsed_pdufa = _date_from_iso(pdufa_date)
    if not parsed_pdufa:
        blockers.append("missing_pdufa_date")
    else:
        days_until = (parsed_pdufa.date() - _now().date()).days
        if days_until < 0:
            warnings.append("pdufa_date_elapsed")
        if days_until > 365:
            warnings.append("pdufa_date_far_out")
    source_name = row.get("source") or row.get("data_quality") or "unknown"
    source_status = "WARN" if row.get("data_quality") == "fallback_calendar" else "PASS" if source_name != "unknown" else "WARN"
    sources.append({"key": "fda_calendar", "status": source_status, "detail": source_name})
    if source_status == "WARN":
        warnings.append("calendar_source_fallback")

    price = _as_float(row.get("current_price"))
    if price is None or price <= 0:
        blockers.append("missing_live_price")
    sources.append({"key": "price", "status": "PASS" if price and price > 0 else "BLOCK", "detail": price})

    trial = row.get("trial") or {}
    has_trial = bool(trial.get("nct_id") or trial.get("status") or trial.get("phases"))
    sources.append({"key": "clinical_trials", "status": "PASS" if has_trial else "NEUTRAL", "detail": trial.get("nct_id") or trial.get("status") or "-"})
    if not has_trial:
        neutralized.append("clinical_trials")

    insider = row.get("insider_summary")
    sources.append({"key": "insider_cluster", "status": "PASS" if insider else "NEUTRAL", "detail": (insider or {}).get("summary") if isinstance(insider, dict) else "-"})
    if not insider:
        neutralized.append("insider_cluster")

    short_pct = _as_float(row.get("short_pct"))
    sources.append({"key": "short_interest", "status": "PASS" if short_pct is not None else "NEUTRAL", "detail": short_pct})
    if short_pct is None:
        neutralized.append("short_interest")

    iv_rank = _as_float(row.get("iv_rank"))
    sources.append({"key": "implied_volatility", "status": "PASS" if iv_rank is not None else "NEUTRAL", "detail": iv_rank})
    if iv_rank is None:
        neutralized.append("implied_volatility")

    snap = row.get("option_snapshot") or {}
    snap_ok = bool(snap.get("ok") and snap.get("contract"))
    if _as_float(row.get("binary_event_score"), 0) >= PHARMA_OPTION_SNAPSHOT_THRESHOLD and not snap_ok:
        warnings.append("no_validated_option_snapshot")
    sources.append({
        "key": "option_snapshot",
        "status": "PASS" if snap_ok else "WARN" if snap else "NEUTRAL",
        "detail": snap.get("status") or snap.get("reason") or "-",
    })

    evaluated_at = _date_from_iso(row.get("evaluated_at") or row.get("updated_at") or row.get("created_at"))
    age_hours = None
    if evaluated_at:
        if evaluated_at.tzinfo is None:
            evaluated_at = evaluated_at.replace(tzinfo=timezone.utc)
        age_hours = round((_now() - evaluated_at).total_seconds() / 3600, 2)
        if age_hours > 24:
            warnings.append("pharma_row_stale_gt_24h")
    else:
        warnings.append("missing_evaluation_timestamp")
    sources.append({"key": "freshness", "status": "PASS" if age_hours is not None and age_hours <= 24 else "WARN", "detail": age_hours})

    decision = "BLOCK" if blockers else "WATCH" if warnings else "PASS"
    score = 100 - (len(blockers) * 25) - (len(warnings) * 6) - (len(neutralized) * 2)
    return {
        "decision": decision,
        "score": max(0, min(100, round(score, 1))),
        "blockers": blockers,
        "warnings": warnings,
        "neutralized_exhibits": sorted(set(neutralized)),
        "sources": sources,
        "age_hours": age_hours,
    }


def pharma_strategy_read(row: dict[str, Any]) -> dict[str, Any]:
    score = _as_float(row.get("binary_event_score"), 0) or 0
    days = _as_float(row.get("days_until"), 999) or 999
    iv_rank = _as_float(row.get("iv_rank"), 50) or 50
    snap = row.get("option_snapshot") or {}
    contract = snap.get("contract") or {}
    data_gate = row.get("data_gate") or pharma_data_gate(row)
    if data_gate.get("decision") == "BLOCK":
        lane = "DATA_BLOCK"
        strategy = "PASS_UNTIL_DATA_CLEAN"
    elif days <= 7:
        lane = "BINARY_WINDOW"
        strategy = "DEFINED_PREMIUM_ONLY"
    elif iv_rank >= 70:
        lane = "EXPENSIVE_VOL"
        strategy = "DEBIT_SPREAD_OR_PASS"
    elif score >= 80 and snap.get("ok") and contract:
        lane = "CATALYST_CALL"
        strategy = "LONG_CALL_RESEARCH_CANDIDATE"
    elif score >= 70:
        lane = "PM_REVIEW"
        strategy = "WATCH_FOR_OPTION_SNAPSHOT"
    else:
        lane = "MONITOR"
        strategy = "NO_TRADE"
    return {
        "lane": lane,
        "strategy": strategy,
        "hold_through_binary": False,
        "reason": "PM must explicitly approve any binary FDA hold; Pharma only supplies evidence.",
    }


def pharma_scenario_model(row: dict[str, Any]) -> dict[str, Any]:
    score = _as_float(row.get("binary_event_score"), 0) or 0
    prevalence_pct = _as_float(((row.get("prevalence") or {}).get("pct")), 0.1) or 0.1
    short_pct = _as_float(row.get("short_pct"), 0) or 0
    iv_rank = _as_float(row.get("iv_rank"), 50) or 50
    approval_proxy = max(15, min(75, 30 + (score - 50) * 0.55 + min(prevalence_pct, 10) * 0.8))
    squeeze_boost = min(15, short_pct * 0.55)
    iv_drag = max(0, (iv_rank - 55) * 0.25)
    return {
        "approval_probability_proxy": round(approval_proxy, 1),
        "bull_move_pct": round(25 + squeeze_boost + max(0, score - 70) * 0.7, 1),
        "base_move_pct": round((approval_proxy - 45) * 0.55 - iv_drag, 1),
        "bear_move_pct": round(-18 - max(0, 70 - score) * 0.35 - iv_drag, 1),
        "iv_crush_risk": "HIGH" if iv_rank >= 70 else "MEDIUM" if iv_rank >= 45 else "LOW",
        "model": "heuristic_research_not_prediction",
    }


def _hydrate_pharma_row(row: dict[str, Any]) -> dict[str, Any]:
    hydrated = dict(row)
    hydrated["data_gate"] = pharma_data_gate(hydrated)
    hydrated["strategy_read"] = pharma_strategy_read(hydrated)
    hydrated["scenario"] = pharma_scenario_model(hydrated)
    pm = hydrated.get("pm_decision") or {}
    decision = pm.get("decision") or {}
    hydrated["pm_summary"] = {
        "action": decision.get("action") or "NOT_ROUTED",
        "score": decision.get("pm_score") or decision.get("score"),
        "risk_reward": decision.get("risk_reward"),
        "authority": pm.get("authority") or "PM_PENDING",
        "routed_at": pm.get("routed_at"),
    }
    snap = hydrated.get("option_snapshot") or {}
    contract = snap.get("contract") or {}
    hydrated["option_summary"] = {
        "status": snap.get("status") or "NO_SNAPSHOT",
        "ok": bool(snap.get("ok") and contract),
        "contract": contract.get("symbol") or contract.get("contractSymbol"),
        "expiration": contract.get("expiration") or (snap.get("chain") or {}).get("expiration_selected"),
        "strike": contract.get("strike"),
        "premium": contract.get("premium"),
        "spread_pct": snap.get("spread_pct"),
        "tradeability": snap.get("tradeability") or "UNKNOWN",
        "reason": snap.get("reason"),
    }
    return hydrated


async def get_fda_calendar_month(year: int | None = None, month: int | None = None, force_refresh: bool = False) -> dict[str, Any]:
    now = _now()
    year = int(year or now.year)
    month = max(1, min(12, int(month or now.month)))
    start = datetime(year, month, 1, tzinfo=timezone.utc).date()
    end = datetime(year, month, monthrange(year, month)[1], tzinfo=timezone.utc).date()

    db = get_db()
    if force_refresh:
        await import_fda_calendar(
            persist=True,
            allow_fallback=False,
            triggered_by="fda_calendar_refresh",
        )

    rows = await db.pharma_pdufa.find(
        {"pdufa_date": {"$gte": start.isoformat(), "$lte": end.isoformat()}},
        {"_id": 0},
    ).sort("pdufa_date", 1).to_list(500)
    hydrated = [_hydrate_pharma_row(r) for r in rows]
    days: list[dict[str, Any]] = []
    for day in range(1, end.day + 1):
        d = datetime(year, month, day, tzinfo=timezone.utc).date()
        events = [r for r in hydrated if str(r.get("pdufa_date"))[:10] == d.isoformat()]
        hot = [e for e in events if _as_float(e.get("binary_event_score"), 0) >= TELEGRAM_THRESHOLD]
        pm_ready = [e for e in events if (e.get("pm_summary") or {}).get("action") not in {None, "NOT_ROUTED"}]
        blocked = [e for e in events if (e.get("data_gate") or {}).get("decision") == "BLOCK"]
        best = max((_as_float(e.get("binary_event_score"), 0) or 0 for e in events), default=None)
        days.append({
            "date": d.isoformat(),
            "day": day,
            "events": events,
            "event_count": len(events),
            "hot_count": len(hot),
            "pm_ready_count": len(pm_ready),
            "blocked_count": len(blocked),
            "best_score": best,
            "status": "BLOCK" if blocked else "HOT" if hot else "EVENT" if events else "EMPTY",
        })

    year_rows = await db.pharma_pdufa.find({}, {"_id": 0, "pdufa_date": 1}).to_list(1000)
    available_years = sorted({
        int(str(r.get("pdufa_date", "0000"))[:4])
        for r in year_rows
        if str(r.get("pdufa_date", ""))[:4].isdigit()
    }, reverse=True)
    source_counts: dict[str, int] = {}
    quality_counts: dict[str, int] = {}
    for row in hydrated:
        quality = str(row.get("data_quality") or "unknown")
        quality_counts[quality] = quality_counts.get(quality, 0) + 1
        for source in row.get("source_list") or [row.get("source") or "unknown"]:
            source = str(source or "unknown")
            source_counts[source] = source_counts.get(source, 0) + 1

    return {
        "ok": True,
        "year": year,
        "month": month,
        "month_start": start.isoformat(),
        "month_end": end.isoformat(),
        "days": days,
        "events": hydrated,
        "summary": {
            "events": len(hydrated),
            "hot": sum(1 for r in hydrated if _as_float(r.get("binary_event_score"), 0) >= TELEGRAM_THRESHOLD),
            "pm_ready": sum(1 for r in hydrated if (r.get("pm_summary") or {}).get("action") not in {None, "NOT_ROUTED"}),
            "option_ready": sum(1 for r in hydrated if (r.get("option_summary") or {}).get("ok")),
            "blocked": sum(1 for r in hydrated if (r.get("data_gate") or {}).get("decision") == "BLOCK"),
            "cross_checked_calendar": quality_counts.get("cross_checked_calendar", 0),
            "live_calendar": quality_counts.get("live_calendar", 0),
            "fallback_calendar": quality_counts.get("fallback_calendar", 0),
            "source_counts": source_counts,
        },
        "available_years": available_years or [year, year + 1],
        "generated_at": now.isoformat(),
    }


# ─────── Master pharma scan ───────
async def _cached_pdufa(force_refresh: bool = False) -> list[dict[str, Any]]:
    """Return cached PDUFA list, refreshing on the configured cadence."""
    db = get_db()
    cache = await db.pharma_pdufa_cache.find_one({"_id": "calendar"})
    if cache and not force_refresh:
        try:
            age = (_now() - datetime.fromisoformat(cache["fetched_at"])).total_seconds() / 3600
        except Exception:
            age = 9999
        if age < PDUFA_CACHE_HOURS:
            return cache.get("entries") or []
    entries = await fetch_pdufa_calendar()
    if entries:
        await db.pharma_pdufa_cache.update_one(
            {"_id": "calendar"},
            {"$set": {"entries": entries, "fetched_at": _now().isoformat()}},
            upsert=True,
        )
    return entries or (cache.get("entries") if cache else [])


async def run_pharma_scan(triggered_by: str = "manual", force_calendar_refresh: bool = False) -> dict[str, Any]:
    """Complete pharma pipeline. Returns enriched PDUFA entries with scores +
    persists to MongoDB."""
    started = _now()
    await log_activity(f"Pharma scan started ({triggered_by})", "info")

    pdufa = await _cached_pdufa(force_refresh=force_calendar_refresh)
    # Normal pharma scans stay focused on the next 90 days. A manual FDA
    # calendar refresh stores a wider calendar horizon so the calendar tab can
    # show credible future PDUFA rows without making every row trade-routable.
    today = _now().date()
    horizon = today + timedelta(days=540 if force_calendar_refresh else 90)
    upcoming = []
    for p in pdufa:
        try:
            d = datetime.fromisoformat(p["pdufa_date"]).date()
        except Exception:
            continue
        if today <= d <= horizon:
            p["days_until"] = (d - today).days
            upcoming.append(p)
    if not upcoming:
        await log_activity("Pharma scan — no PDUFA in next 90d", "warn")
        return {
            "started_at": started.isoformat(),
            "finished_at": _now().isoformat(),
            "results": [],
            "triggered_by": triggered_by,
        }

    # Gather supporting data once for all tickers — per-ticker for accuracy
    biotech_tickers = sorted({p["ticker"] for p in upcoming})

    insider_sem = asyncio.Semaphore(3)
    short_sem = asyncio.Semaphore(3)
    iv_sem = asyncio.Semaphore(3)

    async def _ins(t: str):
        async with insider_sem:
            try:
                return await fetch_openinsider_for_ticker(t, days=60)
            except Exception:
                return None
    async def _sh(t: str):
        async with short_sem:
            try:
                return await fetch_finviz_short_for_ticker(t)
            except Exception:
                return None
    async def _iv(t: str):
        async with iv_sem:
            try:
                iv = await options_engine.calculate_iv_rank(t)
                return iv.get("iv_rank") if iv else None
            except Exception:
                return None

    insider_results, short_results, iv_results = await asyncio.gather(
        asyncio.gather(*[_ins(t) for t in biotech_tickers], return_exceptions=True),
        asyncio.gather(*[_sh(t)  for t in biotech_tickers], return_exceptions=True),
        asyncio.gather(*[_iv(t)  for t in biotech_tickers], return_exceptions=True),
    )
    insider_by_t = {t: (v if not isinstance(v, Exception) else None)
                     for t, v in zip(biotech_tickers, insider_results)}
    short_by_t = {t: (v if not isinstance(v, Exception) else None)
                   for t, v in zip(biotech_tickers, short_results)}
    iv_by_t = {t: (v if not isinstance(v, Exception) else None)
                for t, v in zip(biotech_tickers, iv_results)}

    # Clinical trial lookups per drug
    ct_tasks = [fetch_clinical_trial(p["drug"], p["ticker"]) for p in upcoming]
    cts = await asyncio.gather(*ct_tasks, return_exceptions=True)
    cts = [c if not isinstance(c, Exception) else None for c in cts]

    # Current prices
    prices = await pricer.batch_latest_closes(biotech_tickers)

    db = get_db()
    enriched: list[dict[str, Any]] = []
    for p, trial in zip(upcoming, cts):
        t = p["ticker"]
        insider = insider_by_t.get(t)
        short_pct = short_by_t.get(t)
        iv_rank = iv_by_t.get(t)
        prevalence = lookup_prevalence(p.get("indication", ""))
        # AdCom + application history: not freely scraped — leave None for now,
        # scoring fallbacks handle missing data gracefully
        score = compute_binary_event_score(
            trial=trial, insider_summary=insider, short_pct=short_pct,
            iv_rank=iv_rank, adcom=None, history=None,
        )
        row = {
            **p,
            "trial": trial,
            "insider_summary": insider,
            "short_pct": short_pct,
            "iv_rank": iv_rank,
            "prevalence": prevalence,
            "current_price": prices.get(t),
            "binary_event_score": score["score"],
            "tier": score["tier"],
            "score_components": score["components"],
            "auto_entered": score["score"] >= AUTO_ENTER_SCORE,
            "evaluated_at": _now().isoformat(),
        }
        row["data_gate"] = pharma_data_gate(row)
        row["strategy_read"] = pharma_strategy_read(row)
        row["scenario"] = pharma_scenario_model(row)
        enriched.append(row)
        # Persist to pdufa collection
        await db.pharma_pdufa.update_one(
            {"ticker": t, "pdufa_date": p["pdufa_date"], "drug": p["drug"]},
            {"$set": stamped(row)},
            upsert=True,
        )
        await _mirror_pg("pharma_pdufa", row)
        # Auto-enter into active plays if score >= 80
        if row["auto_entered"]:
            active_play = {
                "ticker": t,
                "pdufa_date": p["pdufa_date"],
                "drug": p["drug"],
                "indication": p.get("indication"),
                "entry_score": row["binary_event_score"],
                "entry_price": prices.get(t),
                "entry_date": _today_iso(),
                "source": "auto",
                "tier": row["tier"],
                "prevalence_pct": prevalence["pct"],
            }
            await db.pharma_active_plays.update_one(
                {"ticker": t, "pdufa_date": p["pdufa_date"]},
                {"$setOnInsert": stamped(active_play)},
                upsert=True,
            )
            await _mirror_pg("pharma_active_plays", active_play)

    enriched.sort(key=lambda x: -x["binary_event_score"])

    hot_for_options = [r for r in enriched if r["binary_event_score"] >= PHARMA_OPTION_SNAPSHOT_THRESHOLD]
    if hot_for_options:
        option_sem = asyncio.Semaphore(2)

        async def _snap(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
            async with option_sem:
                return row, await build_option_snapshot(row, persist=True)

        snapshot_pairs = await asyncio.gather(*[_snap(r) for r in hot_for_options], return_exceptions=True)
        for pair in snapshot_pairs:
            if isinstance(pair, Exception):
                logger.warning("Pharma option snapshot gather failed: %s", pair)
                continue
            row, snap = pair
            row["option_snapshot"] = snap
            row["data_gate"] = pharma_data_gate(row)
            row["strategy_read"] = pharma_strategy_read(row)
            row["scenario"] = pharma_scenario_model(row)
            try:
                await db.pharma_pdufa.update_one(
                    {"ticker": row["ticker"], "pdufa_date": row["pdufa_date"], "drug": row["drug"]},
                    {"$set": {
                        "option_snapshot": snap,
                        "option_snapshot_at": snap.get("snapshot_at"),
                        "data_gate": row["data_gate"],
                        "strategy_read": row["strategy_read"],
                        "scenario": row["scenario"],
                    }},
                    upsert=True,
                )
                await _mirror_pg("pharma_pdufa", row)
            except Exception as exc:
                logger.warning("Attach pharma option snapshot failed for %s: %s", row.get("ticker"), exc)

    hot_for_pm = [
        r for r in enriched
        if r["binary_event_score"] >= PHARMA_PM_ROUTE_THRESHOLD
        and (r.get("data_gate") or {}).get("decision") != "BLOCK"
    ]
    if hot_for_pm:
        pm_sem = asyncio.Semaphore(4)

        async def _pm(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
            async with pm_sem:
                return row, await route_to_pm(row, persist=True)

        pm_pairs = await asyncio.gather(*[_pm(r) for r in hot_for_pm], return_exceptions=True)
        for pair in pm_pairs:
            if isinstance(pair, Exception):
                logger.warning("Pharma PM route gather failed: %s", pair)
                continue
            row, docket = pair
            row["pm_decision"] = docket
            try:
                await db.pharma_pdufa.update_one(
                    {"ticker": row["ticker"], "pdufa_date": row["pdufa_date"], "drug": row["drug"]},
                    {"$set": {"pm_decision": docket, "pm_routed_at": docket.get("routed_at")}},
                    upsert=True,
                )
                await _mirror_pg("pharma_pdufa", row)
            except Exception as exc:
                logger.warning("Attach pharma PM decision failed for %s: %s", row.get("ticker"), exc)

    finished = _now()
    duration = round((finished - started).total_seconds(), 2)
    await log_activity(
        f"Pharma scan complete — {len(enriched)} PDUFA in 90d, "
        f"{sum(1 for r in enriched if r['auto_entered'])} auto-entered ({duration}s)",
        "success",
    )

    # Fire telegram alerts for score ≥ 70
    try:
        from . import telegram_events
        hot = [r for r in enriched if r["binary_event_score"] >= TELEGRAM_THRESHOLD]
        if hot:
            await telegram_events.dispatch_pharma_alerts(hot, triggered_by=triggered_by)
    except Exception as e:
        logger.warning("Pharma telegram dispatch failed: %s", e)

    return {
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_sec": duration,
        "results": enriched,
        "triggered_by": triggered_by,
    }


async def run_catalyst_shock_scan(triggered_by: str = "manual", force_refresh: bool = True) -> dict[str, Any]:
    """Detect same-day clinical/FDA catalyst shocks outside the PDUFA calendar."""
    started = _now()
    await log_activity(f"Pharma catalyst shock scan started ({triggered_by})", "info")
    from . import news_intel

    intel = await news_intel.latest(force_refresh=force_refresh, lane="discovery", limit=120)
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for article in intel.get("articles") or []:
        text = _article_text(article).lower()
        if not any(_has_phrase(text, term) for term in [*PHARMA_CATALYST_WEIGHTS, *PHARMA_BEARISH_CATALYST_WEIGHTS]):
            continue
        for ticker in _map_pharma_article_tickers(article):
            key = (ticker, str(article.get("id") or article.get("url") or article.get("title") or ""))
            if key in seen:
                continue
            seen.add(key)
            price = await pricer.get_latest_close(ticker, force=True)
            score = _score_catalyst_shock(article, ticker, price)
            if score["shock_score"] < 55:
                continue
            row = stamped({
                "ticker": ticker,
                "event_type": "PHARMA_CATALYST_SHOCK",
                "source": article.get("source"),
                "source_key": article.get("source_key"),
                "title": article.get("title"),
                "summary": article.get("summary"),
                "url": article.get("url"),
                "published_at": article.get("published_at"),
                "detected_at": _now().isoformat(),
                "triggered_by": triggered_by,
                "news_score": article.get("score"),
                "bias": article.get("bias"),
                **score,
            })
            candidates.append(row)

    candidates.sort(key=lambda row: (row.get("shock_score") or 0, -(row.get("age_minutes") or 999999)), reverse=True)
    db = get_db()
    for row in candidates:
        await db.pharma_catalyst_shocks.update_one(
            {"ticker": row["ticker"], "url": row.get("url")},
            {"$set": row},
            upsert=True,
        )
        await _mirror_pg("pharma_catalyst_shocks", row)

    hot = [r for r in candidates if (r.get("shock_score") or 0) >= CATALYST_SHOCK_THRESHOLD]
    try:
        if hot:
            from . import telegram_events
            await telegram_events.dispatch_pharma_shock_alerts(hot, triggered_by=triggered_by)
    except Exception as exc:
        logger.warning("Pharma shock telegram dispatch failed: %s", exc)

    finished = _now()
    duration = round((finished - started).total_seconds(), 2)
    await log_activity(
        f"Pharma catalyst shock scan complete - {len(candidates)} candidates, {len(hot)} hot ({duration}s)",
        "success" if hot else "info",
    )
    return {
        "ok": True,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_sec": duration,
        "triggered_by": triggered_by,
        "source": "news_intel.discovery",
        "candidate_count": len(candidates),
        "hot_count": len(hot),
        "results": candidates[:50],
    }


def format_pharma_alert(r: dict[str, Any]) -> str:
    """Per-spec Telegram format for score ≥ 70 pharma plays."""
    from . import telegram_service as ts
    score = r["binary_event_score"]
    tier_emoji = "🟢" if score >= 80 else "🟡" if score >= 70 else "⚪"
    prev = r.get("prevalence") or {}
    prev_str = f"{prev.get('pct', 0):.1f}% US ({prev.get('patient_count', 0):,})"
    sigs = []
    comp = r.get("score_components") or {}
    if (comp.get("phase3") or {}).get("points", 0) >= 15:
        sigs.append("🧬 PHASE_3")
    if (comp.get("insider") or {}).get("points", 0) >= 8:
        sigs.append("👥 INSIDER")
    if (comp.get("short") or {}).get("points", 0) >= 10:
        sigs.append("📉 SHORT_SQUEEZE")
    if (comp.get("iv") or {}).get("points", 0) >= 10:
        sigs.append("🟢 CHEAP_IV")
    cur = r.get("current_price") or 0
    target = cur * 1.40 if cur else 0
    upside = 40.0
    snap = r.get("option_snapshot") or {}
    contract = snap.get("contract") or {}
    contract_symbol = contract.get("symbol") or contract.get("contractSymbol")
    if snap.get("ok") and contract_symbol:
        option_line = (
            f"OPTIONS SNAPSHOT - {ts._esc(str(contract_symbol))} - "
            f"{ts._esc(str(contract.get('expiration') or '?'))} - "
            f"{contract.get('strike')}C - mid ${contract.get('premium')} - "
            f"bid/ask ${contract.get('bid')}/${contract.get('ask')} - "
            f"IV {contract.get('iv')} - {ts._esc(str(contract.get('liquidity') or 'UNKNOWN'))}"
        )
    else:
        option_line = (
            f"OPTIONS SNAPSHOT - NO VALIDATED CONTRACT - "
            f"{ts._esc(str(snap.get('status') or 'NOT_CAPTURED'))}: "
            f"{ts._esc(str(snap.get('reason') or 'snapshot unavailable'))}"
        )
    pm = (r.get("pm_decision") or {}).get("decision") or {}
    pm_line = (
        f"PM RULING - {ts._esc(str(pm.get('action') or 'PENDING'))} - "
        f"score {pm.get('pm_score', '-')} - RR {pm.get('risk_reward', '-')} - "
        f"{ts._esc(str(pm.get('option_view') or 'NO_VIEW'))}"
    )
    return (
        f"1. <b>${ts._esc(r['ticker'])}</b> · <code>{score:.0f}/100</code> · 🔴H · "
        f"CASE SCORE <b>{score:.0f}</b> {tier_emoji}\n"
        f"{' '.join(sigs) or '🧬 PHARMA'}\n"
        f"<i>Binary FDA catalyst — {ts._esc(r.get('drug', '?'))} · "
        f"{ts._esc(r.get('indication', ''))} · prevalence {prev_str}</i>\n"
        f"💰 ${cur:.2f} → ${target:.2f} +{upside:.1f}% · "
        f"{ts._esc(r['pdufa_date'])} · 1–{r.get('days_until', 30)}d\n"
        f"🛑 Stop ${cur * 0.85:.2f} · ⚠️ Regulatory binary volatility\n"
        f"{option_line}\n"
        f"{pm_line}\n"
        f"🎰 {ts._esc(r['tier'])} {score:.0f}/100 · P(approval): 70% · "
        f"P(rejection): 30% · Max: $500"
    )


# ─────── Query helpers for the React tab ───────
def format_pharma_shock_alert(r: dict[str, Any]) -> str:
    """Telegram card for same-day clinical/FDA news shocks."""
    from . import telegram_service as ts
    score = r.get("shock_score") or 0
    ticker = r.get("ticker") or "?"
    direction = r.get("direction") or "WATCH"
    terms = [*r.get("bullish_terms", []), *r.get("bearish_terms", [])][:4]
    price = r.get("current_price")
    price_line = f"${float(price):.2f}" if isinstance(price, (int, float)) and price > 0 else "-"
    return (
        f"<b>CASE CAPITAL | PHARMA CATALYST SHOCK</b>\n"
        f"<code>{datetime.now(timezone.utc).astimezone().strftime('%b %d %H:%M')}</code>\n\n"
        f"<b>${ts._esc(ticker)}</b> - <code>{float(score):.0f}/100</code> - <b>{ts._esc(direction)}</b>\n"
        f"{ts._esc(r.get('title') or 'Clinical/FDA catalyst detected')}\n\n"
        f"Price: <b>{ts._esc(price_line)}</b>\n"
        f"Evidence: {ts._esc(', '.join(terms) or 'pharma catalyst terms')}\n"
        f"Source: {ts._esc(r.get('source') or 'news')}\n\n"
        f"<i>Research alert only: clinical shocks require PM confirmation before execution.</i>"
        + (f"\n<a href=\"{ts._esc(r.get('url'))}\">Open source</a>" if r.get("url") else "")
    )


async def get_pdufa_within_days(days: int = 90) -> list[dict[str, Any]]:
    db = get_db()
    horizon = (_now().date() + timedelta(days=days)).isoformat()
    today = _today_iso()
    rows = await db.pharma_pdufa.find(
        {"pdufa_date": {"$gte": today, "$lte": horizon}},
        {"_id": 0},
    ).sort("binary_event_score", -1).to_list(500)
    for row in rows:
        row.setdefault("source", "curated_seed")
        row.setdefault("data_quality", "fallback_calendar")
    return [_hydrate_pharma_row(row) for row in rows]


async def get_option_snapshots(limit: int = 100, ticker: str | None = None) -> list[dict[str, Any]]:
    db = get_db()
    safe_limit = max(1, min(int(limit or 100), 500))
    query: dict[str, Any] = {}
    if ticker:
        query["ticker"] = str(ticker).upper().strip()
    return await db.pharma_option_snapshots.find(query, {"_id": 0}).sort("snapshot_at", -1).to_list(safe_limit)


async def get_pm_decisions(limit: int = 100, ticker: str | None = None) -> list[dict[str, Any]]:
    db = get_db()
    safe_limit = max(1, min(int(limit or 100), 500))
    query: dict[str, Any] = {}
    if ticker:
        query["ticker"] = str(ticker).upper().strip()
    return await db.pharma_pm_decisions.find(query, {"_id": 0}).sort("routed_at", -1).to_list(safe_limit)


async def get_catalyst_shocks(limit: int = 100) -> list[dict[str, Any]]:
    db = get_db()
    safe_limit = max(1, min(int(limit or 100), 250))
    return await db.pharma_catalyst_shocks.find(
        {},
        {"_id": 0},
    ).sort("detected_at", -1).to_list(safe_limit)


async def get_active_plays() -> list[dict[str, Any]]:
    db = get_db()
    rows = await db.pharma_active_plays.find({}, {"_id": 0}).sort("entry_date", -1).to_list(200)
    # Attach current price for each
    if not rows:
        return []
    tickers = sorted({r["ticker"] for r in rows})
    prices = await pricer.batch_latest_closes(tickers)
    for r in rows:
        cur = prices.get(r["ticker"])
        if cur and r.get("entry_price"):
            r["current_price"] = cur
            r["gain_pct"] = round((cur - r["entry_price"]) / r["entry_price"] * 100, 2)
        else:
            r["current_price"] = cur
            r["gain_pct"] = None
    return rows


async def add_manual_play(ticker: str, drug: str | None, pdufa_date: str | None,
                          entry_price: float | None, notes: str | None = None) -> dict[str, Any]:
    """User-added play (score < 80)."""
    db = get_db()
    cur = entry_price
    if cur is None:
        cur = await pricer.get_latest_close(ticker)
    doc = stamped({
        "ticker": ticker.upper(),
        "drug": drug or "",
        "pdufa_date": pdufa_date,
        "indication": "",
        "entry_score": None,
        "entry_price": cur,
        "entry_date": _today_iso(),
        "source": "manual",
        "tier": "MANUAL",
        "notes": notes or "",
    })
    await db.pharma_active_plays.update_one(
        {"ticker": doc["ticker"], "pdufa_date": pdufa_date or "manual"},
        {"$set": doc},
        upsert=True,
    )
    return doc


async def track_record() -> dict[str, Any]:
    """Closed/settled pharma plays (track record fully isolated from main)."""
    db = get_db()
    rows = await db.pharma_track_record.find({}, {"_id": 0}).sort("settled_at", -1).to_list(500)
    plays = await get_active_plays()
    realized = [r for r in rows if r.get("realized_pct") is not None]
    winners = [r for r in realized if (r["realized_pct"] or 0) > 0]
    unrealized = [p["gain_pct"] for p in plays if p.get("gain_pct") is not None]
    return {
        "settled": len(realized),
        "winners": len(winners),
        "open": len(plays),
        "hit_rate": round(len(winners) / len(realized), 3) if realized else None,
        "avg_realized_pct": round(sum(r["realized_pct"] for r in realized) / len(realized), 2) if realized else None,
        "avg_unrealized_pct": round(sum(unrealized) / len(unrealized), 2) if unrealized else None,
        "history": rows,
    }


async def close_play(ticker: str, pdufa_date: str, exit_price: float | None) -> bool:
    """Move active play → track record."""
    db = get_db()
    play = await db.pharma_active_plays.find_one(
        {"ticker": ticker.upper(), "pdufa_date": pdufa_date}, {"_id": 0},
    )
    if not play:
        return False
    exit_p = exit_price if exit_price is not None else await pricer.get_latest_close(ticker)
    entry = play.get("entry_price")
    realized_pct = None
    if entry and exit_p:
        realized_pct = round((exit_p - entry) / entry * 100, 2)
    await db.pharma_track_record.insert_one(stamped({
        **play,
        "exit_price": exit_p,
        "realized_pct": realized_pct,
        "settled_at": _now().isoformat(),
    }))
    await db.pharma_active_plays.delete_one({"ticker": ticker.upper(), "pdufa_date": pdufa_date})
    return True
