"""Optional EdgarTools enrichment for SEC battle cards.

The existing SEC feed remains the live event source. EdgarTools adds deeper
company context from EDGAR with a short Mongo cache so UI clicks do not turn
into repeated SEC requests.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import get_db

logger = logging.getLogger(__name__)

CACHE_TTL = timedelta(hours=6)
CACHE_SCHEMA = 2
IDENTITY = os.environ.get("SEC_USER_AGENT", "Case Capital Terminal research@casecapital.local")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stringify(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _numeric(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if hasattr(value, "value"):
            value = value.value
        return float(str(value).replace(",", ""))
    except Exception:
        return None


def _metric(value: Any) -> dict[str, Any] | None:
    amount = _numeric(value)
    if amount is None:
        return None
    return {
        "value": amount,
        "concept": _stringify(getattr(value, "concept", None)),
        "periods": _stringify(getattr(value, "periods", None)),
    }


def _filing_dict(filing: Any) -> dict[str, Any]:
    return {
        "form": _stringify(getattr(filing, "form", None)),
        "filing_date": _stringify(getattr(filing, "filing_date", None)),
        "accession": _stringify(getattr(filing, "accession_no", None) or getattr(filing, "accession_number", None)),
        "company": _stringify(getattr(filing, "company", None)),
        "description": _stringify(getattr(filing, "description", None)),
        "url": _stringify(getattr(filing, "filing_url", None) or getattr(filing, "url", None)),
    }


def _read_company_snapshot(ticker: str, filing_limit: int) -> dict[str, Any]:
    from edgar import Company, set_identity

    set_identity(IDENTITY)
    company = Company(ticker)
    if getattr(company, "not_found", False):
        return {"ok": False, "provider": "edgartools", "reason": "Company not found", "ticker": ticker}

    filings: list[dict[str, Any]] = []
    try:
        entity_filings = company.get_filings(
            form=["10-K", "10-Q", "8-K", "DEF 14A", "4"],
            amendments=False,
        ).head(filing_limit)
        filings = [_filing_dict(f) for f in entity_filings]
    except Exception as exc:
        logger.debug("EdgarTools filing history unavailable for %s: %s", ticker, exc)

    flags = []
    for label, attr in (
        ("large accelerated filer", "is_large_accelerated_filer"),
        ("accelerated filer", "is_accelerated_filer"),
        ("smaller reporting company", "is_smaller_reporting_company"),
        ("foreign issuer", "is_foreign"),
        ("operating company", "is_operating_company"),
        ("fund", "is_fund"),
    ):
        try:
            if bool(getattr(company, attr, False)):
                flags.append(label)
        except Exception:
            continue

    snapshot = {
        "ok": True,
        "provider": "edgartools",
        "quality": "live_sec_edgar",
        "ticker": ticker,
        "company": _stringify(getattr(company, "name", None) or getattr(company, "display_name", None)),
        "cik": _stringify(getattr(company, "cik", None)),
        "entity": {
            "industry": _stringify(getattr(company, "industry", None)),
            "sic": _stringify(getattr(company, "sic", None)),
            "filer_category": _stringify(getattr(company, "filer_category", None)),
            "fiscal_year_end": _stringify(getattr(company, "fiscal_year_end", None)),
            "tickers": list(getattr(company, "tickers", []) or []),
            "flags": flags,
        },
        "fundamentals": {
            "public_float": _numeric(getattr(company, "public_float", None)),
            "shares_outstanding": _numeric(getattr(company, "shares_outstanding", None)),
            "ttm_revenue": _metric(company.get_ttm_revenue()),
            "ttm_net_income": _metric(company.get_ttm_net_income()),
        },
        "latest_filings": filings,
        "cache_schema": CACHE_SCHEMA,
        "fetched_at": _now().isoformat(),
        "source": "EdgarTools SEC EDGAR",
    }
    return snapshot


async def company_snapshot(ticker: str, filing_limit: int = 8) -> dict[str, Any]:
    t = (ticker or "").upper().strip()
    if not t:
        return {"ok": False, "provider": "edgartools", "reason": "Missing ticker"}

    db = get_db()
    try:
        cached = await db.edgartools_company_cache.find_one({"ticker": t}, {"_id": 0})
        if cached and cached.get("cache_schema") == CACHE_SCHEMA:
            fetched_at = cached.get("fetched_at")
            parsed = datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00")) if fetched_at else None
            if parsed and _now() - parsed <= CACHE_TTL:
                cached["cache"] = "hit"
                return cached
    except Exception as exc:
        logger.debug("EdgarTools cache read skipped for %s: %s", t, exc)

    try:
        snapshot = await asyncio.wait_for(asyncio.to_thread(_read_company_snapshot, t, filing_limit), timeout=18)
    except ImportError as exc:
        snapshot = {"ok": False, "provider": "edgartools", "reason": f"Package unavailable: {exc}", "ticker": t}
    except Exception as exc:
        logger.warning("EdgarTools snapshot failed for %s: %s", t, exc)
        snapshot = {"ok": False, "provider": "edgartools", "reason": str(exc), "ticker": t}

    if snapshot.get("ok"):
        try:
            await db.edgartools_company_cache.update_one({"ticker": t}, {"$set": snapshot}, upsert=True)
        except Exception as exc:
            logger.debug("EdgarTools cache write skipped for %s: %s", t, exc)
        snapshot["cache"] = "miss"

    return snapshot
