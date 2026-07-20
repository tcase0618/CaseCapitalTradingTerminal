"""Government intelligence adapters for contracts, entities, and rulemaking.

The goal is to add public-government source coverage without making the
terminal brittle. USAspending stays the live no-key backbone; SAM.gov and
Regulations.gov activate automatically when their optional API keys exist.
"""
from __future__ import annotations

import os
import re
from datetime import date, timedelta
from typing import Any

import httpx


TIMEOUT = httpx.Timeout(12.0, connect=4.0)
SAM_KEY = os.environ.get("SAM_GOV_API_KEY", "").strip()
REGULATIONS_KEY = os.environ.get("REGULATIONS_GOV_API_KEY", "").strip()

SAM_OPPORTUNITIES_URL = "https://api.sam.gov/opportunities/v2/search"
SAM_PSC_URL = "https://api.sam.gov/prod/locationservices/v1/api/publicpscdetails"
SAM_ENTITY_URL = "https://api.sam.gov/entity-information/v4/entities"
SAM_SUBCONTRACT_URL = "https://api.sam.gov/prod/contract/v1/subcontracts/search"
REGULATIONS_DOCUMENTS_URL = "https://api.regulations.gov/v4/documents"


def _clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _safe_query(value: str | None, max_len: int = 80) -> str:
    clean = re.sub(r"[&|{}^\\]+", " ", _clean(value))
    return re.sub(r"\s+", " ", clean).strip()[:max_len]


def _terms(*values: str | None) -> str:
    seen: list[str] = []
    for value in values:
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-]{2,}", _clean(value)):
            upper = token.upper()
            if upper in {"THE", "AND", "INC", "CORP", "LLC", "LTD", "FOR", "WITH"}:
                continue
            if upper not in seen:
                seen.append(upper)
            if len(seen) >= 6:
                break
    return " ".join(seen)


def _source(key: str, name: str, configured: bool, status: str, use: str, *, count: int | None = None, reason: str | None = None) -> dict[str, Any]:
    return {
        "key": key,
        "name": name,
        "configured": configured,
        "status": status,
        "count": count,
        "reason": reason,
        "use": use,
    }


def _err(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code} from provider"
    if isinstance(exc, httpx.TimeoutException):
        return "provider timeout"
    return exc.__class__.__name__


def _psc_from_text(description: str | None) -> dict[str, Any]:
    text = _clean(description).lower()
    rules = [
        (("software", "cyber", "cloud", "network", "data", "it ", "information technology"), "D", "IT / telecom services"),
        (("missile", "aerospace", "aircraft", "space", "radar", "munition", "weapon"), "15/58", "Aerospace, weapons, electronics"),
        (("research", "development", "prototype", "laboratory", "science"), "A", "Research and development"),
        (("medical", "health", "drug", "clinical", "hospital"), "Q/65", "Medical services or supplies"),
        (("construction", "facility", "repair", "maintenance", "renovation"), "Y/Z/J", "Construction, repair, or maintenance"),
        (("transport", "logistics", "freight", "shipping", "vehicle"), "V", "Transportation and logistics"),
        (("professional", "support", "consulting", "admin", "management"), "R", "Professional and administrative services"),
    ]
    for needles, code, label in rules:
        if any(n in text for n in needles):
            return {
                "status": "derived",
                "code": code,
                "category": label,
                "confidence": "medium",
                "note": "Derived from award description until official PSC is present in the award payload.",
            }
    return {
        "status": "unknown",
        "code": None,
        "category": "PSC not present in current award row",
        "confidence": "low",
        "note": "Add official SAM PSC lookup once SAM_GOV_API_KEY is configured.",
    }


async def _sam_opportunities(client: httpx.AsyncClient, query: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not SAM_KEY or not query:
        return [], _source("sam_opportunities", "SAM.gov Opportunities", bool(SAM_KEY), "needs_key" if not SAM_KEY else "no_query", "Future solicitations and active notices.", reason=None if SAM_KEY else "SAM_GOV_API_KEY not configured")
    today = date.today()
    params = {
        "api_key": SAM_KEY,
        "limit": 8,
        "offset": 0,
        "postedFrom": (today - timedelta(days=60)).strftime("%m/%d/%Y"),
        "postedTo": today.strftime("%m/%d/%Y"),
        "keyword": query,
    }
    try:
        r = await client.get(SAM_OPPORTUNITIES_URL, params=params)
        r.raise_for_status()
        data = r.json()
        raw = data.get("opportunitiesData") or data.get("data") or []
        rows = []
        for item in raw[:8]:
            rows.append({
                "title": item.get("title") or item.get("opportunityTitle") or "Untitled opportunity",
                "notice_id": item.get("noticeId") or item.get("solicitationNumber") or item.get("id"),
                "agency": item.get("department") or item.get("subTier") or item.get("agency"),
                "posted": item.get("postedDate"),
                "type": item.get("type") or item.get("noticeType"),
                "url": item.get("uiLink") or item.get("fullParentPathLink"),
            })
        return rows, _source("sam_opportunities", "SAM.gov Opportunities", True, "live" if rows else "empty", "Future solicitations and active notices.", count=len(rows))
    except Exception as exc:
        return [], _source("sam_opportunities", "SAM.gov Opportunities", True, "down", "Future solicitations and active notices.", reason=_err(exc))


async def _psc_lookup(client: httpx.AsyncClient, query: str, fallback: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not SAM_KEY:
        return [], _source("sam_psc", "SAM.gov PSC", False, "derived", "Official product/service-code classification.", reason="SAM_GOV_API_KEY not configured; showing derived PSC read instead")
    q = _safe_query(fallback.get("code") or query, 20)
    if not q:
        return [], _source("sam_psc", "SAM.gov PSC", True, "no_query", "Official product/service-code classification.")
    try:
        r = await client.get(SAM_PSC_URL, params={"api_key": SAM_KEY, "q": q, "active": "ALL"})
        r.raise_for_status()
        data = r.json()
        raw = data.get("_embedded", {}).get("results") or data.get("results") or data.get("content") or []
        rows = [{
            "code": item.get("pscCode"),
            "name": item.get("pscName") or item.get("pscFullName"),
            "category": item.get("level1CategoryName"),
            "active": item.get("activeInd"),
        } for item in raw[:6]]
        return rows, _source("sam_psc", "SAM.gov PSC", True, "live" if rows else "empty", "Official product/service-code classification.", count=len(rows))
    except Exception as exc:
        return [], _source("sam_psc", "SAM.gov PSC", True, "down", "Official product/service-code classification.", reason=_err(exc))


async def _entity_lookup(client: httpx.AsyncClient, recipient: str | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    q = _safe_query(recipient)
    if not SAM_KEY or not q:
        return [], _source("sam_entity", "SAM.gov Entity Management", bool(SAM_KEY), "needs_key" if not SAM_KEY else "no_query", "UEI, entity registration, NAICS/PSC, entity profile.", reason=None if SAM_KEY else "SAM_GOV_API_KEY not configured")
    try:
        r = await client.get(SAM_ENTITY_URL, params={"api_key": SAM_KEY, "q": q, "samRegistered": "Yes", "includeSections": "entityRegistration,coreData"})
        r.raise_for_status()
        data = r.json()
        raw = data.get("entityData") or data.get("data") or []
        rows = []
        for item in raw[:5]:
            reg = item.get("entityRegistration") or {}
            core = item.get("coreData") or {}
            rows.append({
                "legal_name": reg.get("legalBusinessName") or reg.get("entityName") or q,
                "uei": reg.get("ueiSAM") or reg.get("uei"),
                "status": reg.get("registrationStatus"),
                "expiration": reg.get("registrationExpirationDate"),
                "naics": core.get("naicsInformation", {}).get("naicsCode") if isinstance(core.get("naicsInformation"), dict) else None,
            })
        return rows, _source("sam_entity", "SAM.gov Entity Management", True, "live" if rows else "empty", "UEI, entity registration, NAICS/PSC, entity profile.", count=len(rows))
    except Exception as exc:
        return [], _source("sam_entity", "SAM.gov Entity Management", True, "down", "UEI, entity registration, NAICS/PSC, entity profile.", reason=_err(exc))


async def _regulations(client: httpx.AsyncClient, query: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not REGULATIONS_KEY or not query:
        return [], _source("regulations_gov", "Regulations.gov", bool(REGULATIONS_KEY), "needs_key" if not REGULATIONS_KEY else "no_query", "Proposed rules, final rules, comments, and dockets.", reason=None if REGULATIONS_KEY else "REGULATIONS_GOV_API_KEY not configured")
    try:
        r = await client.get(REGULATIONS_DOCUMENTS_URL, params={
            "api_key": REGULATIONS_KEY,
            "filter[searchTerm]": query,
            "page[size]": 8,
            "sort": "-postedDate",
        })
        r.raise_for_status()
        data = r.json()
        rows = []
        for item in (data.get("data") or [])[:8]:
            attr = item.get("attributes") or {}
            rows.append({
                "title": attr.get("title") or "Untitled document",
                "agency": attr.get("agencyId") or attr.get("agencyIds"),
                "posted": attr.get("postedDate"),
                "type": attr.get("documentType"),
                "docket": attr.get("docketId"),
                "url": f"https://www.regulations.gov/document/{item.get('id')}" if item.get("id") else None,
            })
        return rows, _source("regulations_gov", "Regulations.gov", True, "live" if rows else "empty", "Proposed rules, final rules, comments, and dockets.", count=len(rows))
    except Exception as exc:
        return [], _source("regulations_gov", "Regulations.gov", True, "down", "Proposed rules, final rules, comments, and dockets.", reason=_err(exc))


async def _subcontract_outbound(client: httpx.AsyncClient, recipient: str | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    q = _safe_query(recipient)
    if not SAM_KEY or not q:
        return [], _source("sam_acquisition_subawards", "SAM.gov Acquisition Subawards", bool(SAM_KEY), "covered" if not SAM_KEY else "no_query", "Published/deleted federal subcontract data.", reason="USAspending subaward feed is the current no-key live source" if not SAM_KEY else None)
    try:
        r = await client.get(SAM_SUBCONTRACT_URL, params={"api_key": SAM_KEY, "pageNumber": 0, "pageSize": 10, "status": "Published", "subcontractorLegalBusinessName": q})
        r.raise_for_status()
        data = r.json()
        raw = data.get("subcontracts") or data.get("data") or data.get("results") or []
        rows = [{
            "recipient": item.get("subcontractorLegalBusinessName") or item.get("recipientName"),
            "prime": item.get("primeAwardeeLegalBusinessName") or item.get("primeRecipientName"),
            "amount": item.get("subawardAmount") or item.get("amount"),
            "date": item.get("actionDate") or item.get("subawardDate"),
        } for item in raw[:8]]
        return rows, _source("sam_acquisition_subawards", "SAM.gov Acquisition Subawards", True, "live" if rows else "empty", "Published/deleted federal subcontract data.", count=len(rows))
    except Exception as exc:
        return [], _source("sam_acquisition_subawards", "SAM.gov Acquisition Subawards", True, "down", "Published/deleted federal subcontract data.", reason=_err(exc))


async def contract_layer(ticker: str | None = None, recipient: str | None = None, agency: str | None = None, description: str | None = None) -> dict[str, Any]:
    search = _terms(ticker, recipient, agency, description)
    psc_guess = _psc_from_text(description)
    hierarchy = {
        "status": "derived",
        "agency": _clean(agency) or "Unknown agency",
        "read": "Top-tier agency derived from USAspending award row; Federal Hierarchy API can deepen this with office-level orgs once SAM_GOV_API_KEY is configured.",
    }
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        opportunities, op_source = await _sam_opportunities(client, search)
        psc_rows, psc_source = await _psc_lookup(client, search, psc_guess)
        entities, entity_source = await _entity_lookup(client, recipient)
        regulations, reg_source = await _regulations(client, search)
        subcontracts, sub_source = await _subcontract_outbound(client, recipient)
    sources = [
        _source("usaspending", "USAspending.gov", True, "live", "Prime awards and current no-key subcontractor backbone."),
        psc_source,
        _source("sam_federal_hierarchy", "SAM.gov Federal Hierarchy", bool(SAM_KEY), "derived" if not SAM_KEY else "ready", "Agency bureau/office hierarchy enrichment.", reason=None if SAM_KEY else "SAM_GOV_API_KEY not configured; using USAspending agency text"),
        op_source,
        sub_source,
        entity_source,
        _source("sam_exclusions", "SAM.gov Exclusions", bool(SAM_KEY), "needs_key" if not SAM_KEY else "ready", "Debarment/exclusion risk checks.", reason=None if SAM_KEY else "SAM_GOV_API_KEY not configured"),
        reg_source,
    ]
    live_count = sum(1 for s in sources if s["status"] in {"live", "derived", "covered", "ready"})
    return {
        "ticker": _clean(ticker).upper(),
        "recipient": _clean(recipient),
        "agency": _clean(agency),
        "query": search,
        "score": min(100, 45 + live_count * 7 + min(20, len(opportunities) * 4 + len(regulations) * 3)),
        "stance": "LIVE" if any(s["status"] == "live" for s in sources) else "READY FOR KEYS",
        "psc": {"guess": psc_guess, "matches": psc_rows},
        "hierarchy": hierarchy,
        "opportunities": opportunities,
        "regulations": regulations,
        "entities": entities,
        "subcontracts": subcontracts,
        "sources": sources,
    }
