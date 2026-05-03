"""USASpending.gov integration. Public, no API key.

Maps award recipient names → public-company tickers via curated static map of
the largest US-listed government contractors. Computes 5 new signal types:
  - CONTRACT_SURGE        +2  (30d total >= 1.4× prior 90d avg, awards > $10M)
  - NEW_WINNER            +2  (first award from agency in last 12 months, > $5M)
  - CONCENTRATION_WIN     +3  (single contract > $20M to mkt-cap < $2B issuer)
  - MOMENTUM_STACK        +2  (>=3 distinct agencies in 30 days, cum > $20M)
  - BUDGET_SURGE          +2  (agency monthly obligations >= 1.5× 3mo avg;
                                attribute to top exposed public contractors)
"""
from __future__ import annotations
import asyncio
import logging
import re
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE = "https://api.usaspending.gov/api/v2"
TIMEOUT = httpx.Timeout(40.0, connect=15.0)
HEADERS = {"User-Agent": "stock-intel-bot/1.0", "Content-Type": "application/json"}

# ---------- Public contractor map: normalized recipient substring -> ticker ----------
# Keys are uppercase substrings that should appear in the official recipient name.
# Order matters: more specific patterns first.
PUBLIC_CONTRACTORS: list[tuple[str, str]] = [
    # Defense primes
    ("LOCKHEED MARTIN", "LMT"),
    ("RAYTHEON TECHNOLOGIES", "RTX"),
    ("RAYTHEON COMPANY", "RTX"),
    ("RTX CORPORATION", "RTX"),
    ("NORTHROP GRUMMAN", "NOC"),
    ("GENERAL DYNAMICS", "GD"),
    ("BOEING COMPANY", "BA"),
    ("THE BOEING", "BA"),
    ("HUNTINGTON INGALLS", "HII"),
    ("L3HARRIS", "LHX"),
    ("L-3 COMMUNICATIONS", "LHX"),
    ("TEXTRON", "TXT"),
    ("TRANSDIGM", "TDG"),
    ("BAE SYSTEMS", "BAESY"),
    # Defense mid/small
    ("KRATOS DEFENSE", "KTOS"),
    ("MERCURY SYSTEMS", "MRCY"),
    ("AEROVIRONMENT", "AVAV"),
    ("CURTISS-WRIGHT", "CW"),
    ("HEICO", "HEI"),
    ("MOOG INC", "MOG.A"),
    ("PARSONS CORPORATION", "PSN"),
    ("V2X", "VVX"),
    ("VECTRUS", "VVX"),
    ("KBR INC", "KBR"),
    ("KBR, INC", "KBR"),
    ("BWX TECHNOLOGIES", "BWXT"),
    ("BABCOCK & WILCOX", "BW"),
    # IT services (federal)
    ("LEIDOS", "LDOS"),
    ("BOOZ ALLEN HAMILTON", "BAH"),
    ("CACI INTERNATIONAL", "CACI"),
    ("CACI, INC", "CACI"),
    ("SAIC", "SAIC"),
    ("SCIENCE APPLICATIONS INTERNATIONAL", "SAIC"),
    ("MANTECH", "MANT"),
    ("PERSPECTA", "PRSP"),
    ("MAXIMUS", "MMS"),
    ("ICF INTERNATIONAL", "ICFI"),
    ("UNISYS", "UIS"),
    ("DXC TECHNOLOGY", "DXC"),
    ("CGI FEDERAL", "GIB"),
    ("ACCENTURE FEDERAL", "ACN"),
    ("ACCENTURE LLP", "ACN"),
    # Big tech (cloud / AI)
    ("PALANTIR", "PLTR"),
    ("AMAZON WEB SERVICES", "AMZN"),
    ("AMAZON.COM", "AMZN"),
    ("MICROSOFT CORPORATION", "MSFT"),
    ("GOOGLE LLC", "GOOGL"),
    ("ORACLE AMERICA", "ORCL"),
    ("ORACLE CORPORATION", "ORCL"),
    ("INTERNATIONAL BUSINESS MACHINES", "IBM"),
    ("IBM CORPORATION", "IBM"),
    ("DELL FEDERAL", "DELL"),
    ("DELL TECHNOLOGIES", "DELL"),
    ("HEWLETT PACKARD ENTERPRISE", "HPE"),
    ("CISCO SYSTEMS", "CSCO"),
    ("VMWARE", "VMW"),
    ("SERVICENOW", "NOW"),
    ("SALESFORCE", "CRM"),
    ("SNOWFLAKE", "SNOW"),
    ("DATABRICKS", None),  # private
    # Semis (defense / hyperscale buyers)
    ("INTEL FEDERAL", "INTC"),
    ("INTEL CORPORATION", "INTC"),
    ("NVIDIA CORPORATION", "NVDA"),
    ("ADVANCED MICRO DEVICES", "AMD"),
    ("QUALCOMM", "QCOM"),
    ("MICRON TECHNOLOGY", "MU"),
    # Healthcare
    ("MCKESSON", "MCK"),
    ("CARDINAL HEALTH", "CAH"),
    ("HUMANA", "HUM"),
    ("UNITEDHEALTH GROUP", "UNH"),
    ("CIGNA", "CI"),
    ("ELEVANCE HEALTH", "ELV"),
    ("ANTHEM", "ELV"),
    ("MOLINA HEALTHCARE", "MOH"),
    ("CENTENE", "CNC"),
    ("CVS HEALTH", "CVS"),
    ("HCA HEALTHCARE", "HCA"),
    ("LABORATORY CORPORATION", "LH"),
    ("QUEST DIAGNOSTICS", "DGX"),
    # Pharma
    ("PFIZER", "PFE"),
    ("MERCK", "MRK"),
    ("JOHNSON & JOHNSON", "JNJ"),
    ("MODERNA", "MRNA"),
    ("BIONTECH", "BNTX"),
    ("GILEAD SCIENCES", "GILD"),
    ("REGENERON", "REGN"),
    ("EMERGENT BIOSOLUTIONS", "EBS"),
    # Industrial / aerospace
    ("HONEYWELL", "HON"),
    ("GENERAL ELECTRIC", "GE"),
    ("3M COMPANY", "MMM"),
    ("EATON CORPORATION", "ETN"),
    ("EMERSON ELECTRIC", "EMR"),
    ("CATERPILLAR", "CAT"),
    ("DEERE & COMPANY", "DE"),
    ("OSHKOSH", "OSK"),
    ("AECOM", "ACM"),
    ("FLUOR", "FLR"),
    ("JACOBS ENGINEERING", "J"),
    ("JACOBS SOLUTIONS", "J"),
    ("BECHTEL", None),  # private
    # Energy
    ("EXXON MOBIL", "XOM"),
    ("CHEVRON", "CVX"),
    ("CONOCOPHILLIPS", "COP"),
    ("PHILLIPS 66", "PSX"),
    ("VALERO", "VLO"),
    # Telecom
    ("AT&T MOBILITY", "T"),
    ("AT&T CORP", "T"),
    ("VERIZON BUSINESS", "VZ"),
    ("VERIZON COMMUNICATIONS", "VZ"),
    ("T-MOBILE", "TMUS"),
    ("LUMEN TECHNOLOGIES", "LUMN"),
    ("CENTURYLINK", "LUMN"),
    # Aerospace / space
    ("ROCKET LAB", "RKLB"),
    ("VIRGIN GALACTIC", "SPCE"),
    ("MAXAR", "MAXR"),
    ("PLANET LABS", "PL"),
    ("INTUITIVE MACHINES", "LUNR"),
    # Cybersecurity
    ("CROWDSTRIKE", "CRWD"),
    ("PALO ALTO NETWORKS", "PANW"),
    ("FORTINET", "FTNT"),
    ("ZSCALER", "ZS"),
    ("OKTA", "OKTA"),
    ("CLOUDFLARE", "NET"),
    ("TENABLE", "TENB"),
    ("RAPID7", "RPD"),
    # Misc
    ("GENERAL MOTORS", "GM"),
    ("FORD MOTOR", "F"),
    ("UNITED PARCEL SERVICE", "UPS"),
    ("FEDEX", "FDX"),
    ("VEEVA", "VEEV"),
    ("APPLIED MATERIALS", "AMAT"),
    ("LAM RESEARCH", "LRCX"),
]


def _normalize(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").upper()).strip()


def name_to_ticker(name: str) -> str | None:
    n = _normalize(name)
    for needle, ticker in PUBLIC_CONTRACTORS:
        if ticker and needle in n:
            return ticker
    return None


# ---------- Low-level USASpending fetchers ----------
async def _post(client: httpx.AsyncClient, path: str, payload: dict) -> dict | None:
    try:
        r = await client.post(f"{BASE}{path}", json=payload, headers=HEADERS)
        if r.status_code != 200:
            logger.warning("USASpending %s -> %s", path, r.status_code)
            return None
        return r.json()
    except Exception as e:
        logger.warning("USASpending %s exception: %s", path, e)
        return None


async def fetch_awards(start: date, end: date, min_amount: float, limit: int = 100,
                       page: int = 1) -> list[dict[str, Any]]:
    payload = {
        "filters": {
            "award_type_codes": ["A", "B", "C", "D"],
            "time_period": [{"start_date": start.isoformat(), "end_date": end.isoformat()}],
            "award_amounts": [{"lower_bound": min_amount}],
        },
        "fields": ["Award ID", "Recipient Name", "Awarding Agency", "Award Amount",
                   "Period of Performance Start Date", "recipient_id"],
        "page": page, "limit": limit, "sort": "Award Amount", "order": "desc",
    }
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        data = await _post(client, "/search/spending_by_award/", payload)
    return (data or {}).get("results", []) if data else []


async def fetch_recipient_history(recipient_name: str, lookback_days: int = 365,
                                   min_amount: float = 0, limit: int = 200) -> list[dict[str, Any]]:
    end = date.today()
    start = end - timedelta(days=lookback_days)
    payload = {
        "filters": {
            "award_type_codes": ["A", "B", "C", "D"],
            "time_period": [{"start_date": start.isoformat(), "end_date": end.isoformat()}],
            "recipient_search_text": [recipient_name],
            **({"award_amounts": [{"lower_bound": min_amount}]} if min_amount > 0 else {}),
        },
        "fields": ["Award ID", "Recipient Name", "Awarding Agency", "Award Amount",
                   "Period of Performance Start Date"],
        "page": 1, "limit": limit, "sort": "Award Amount", "order": "desc",
    }
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        data = await _post(client, "/search/spending_by_award/", payload)
    return (data or {}).get("results", []) if data else []


async def fetch_agency_obligations_by_month(months_back: int = 4) -> dict[str, list[dict[str, Any]]]:
    """For BUDGET_SURGE: per-agency obligations in last N months. Uses
    /search/spending_by_category/awarding_agency endpoint."""
    today = date.today()
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for i in range(months_back):
            # walk months: this month, prev, prev-1, prev-2
            month_end = (today.replace(day=1) - timedelta(days=1)) if i > 0 else today
            for _ in range(i - 1):
                month_end = month_end.replace(day=1) - timedelta(days=1)
            month_start = month_end.replace(day=1)
            payload = {
                "category": "awarding_agency",
                "filters": {
                    "award_type_codes": ["A", "B", "C", "D"],
                    "time_period": [{"start_date": month_start.isoformat(),
                                       "end_date": month_end.isoformat()}],
                },
                "limit": 30, "page": 1,
            }
            data = await _post(client, "/search/spending_by_category/", payload)
            for r in (data or {}).get("results", []):
                out[r.get("name", "?")].append({
                    "month": month_start.isoformat()[:7],
                    "amount": float(r.get("amount", 0) or 0),
                })
    return dict(out)


# ---------- Signal computation ----------
async def detect_gov_signals() -> dict[str, Any]:
    """Returns {ticker: {signals: [...], contracts: [...], gov_summary: {...}}}
    for tickers that match any of the 5 gov signals."""
    today = date.today()
    last30 = today - timedelta(days=30)
    last90 = today - timedelta(days=90)

    # Pull 30d awards > $5M; lots of pages won't be needed because we stop early.
    awards_30d = await fetch_awards(last30, today, min_amount=5_000_000, limit=100)
    # Pull 90d awards > $10M for surge baseline (already includes 30d window)
    awards_90d = await fetch_awards(last90, today, min_amount=10_000_000, limit=100)

    # Index by ticker
    per_ticker_30d: dict[str, list[dict]] = defaultdict(list)
    per_ticker_90d: dict[str, list[dict]] = defaultdict(list)
    public_recipients: dict[str, str] = {}  # ticker -> recipient name (canonical)

    def _row(r: dict) -> dict:
        return {
            "award_id": r.get("Award ID"),
            "recipient": r.get("Recipient Name"),
            "agency": r.get("Awarding Agency"),
            "amount": float(r.get("Award Amount") or 0),
            "start": r.get("Period of Performance Start Date"),
        }

    for r in awards_30d:
        t = name_to_ticker(r.get("Recipient Name", ""))
        if not t:
            continue
        public_recipients.setdefault(t, r.get("Recipient Name", ""))
        per_ticker_30d[t].append(_row(r))

    for r in awards_90d:
        t = name_to_ticker(r.get("Recipient Name", ""))
        if not t:
            continue
        public_recipients.setdefault(t, r.get("Recipient Name", ""))
        per_ticker_90d[t].append(_row(r))

    # Compute per-ticker signals
    out: dict[str, dict[str, Any]] = {}

    for ticker in set(list(per_ticker_30d.keys()) + list(per_ticker_90d.keys())):
        signals: list[str] = []
        rows30 = per_ticker_30d.get(ticker, [])
        rows90 = per_ticker_90d.get(ticker, [])
        total_30 = sum(r["amount"] for r in rows30)
        # 90d-baseline excludes the trailing 30d
        rows_60_to_90 = [r for r in rows90 if r not in rows30]  # rough
        total_60_90 = sum(r["amount"] for r in rows_60_to_90)
        # average per 30-day equivalent
        prior_avg_30 = total_60_90 / 2.0 if total_60_90 > 0 else 0

        # CONTRACT_SURGE
        if total_30 >= 1.4 * prior_avg_30 and total_30 >= 10_000_000 and prior_avg_30 > 0:
            signals.append("CONTRACT_SURGE")

        # CONCENTRATION_WIN — single award > $20M (mkt-cap check happens later in scanner)
        big_single = next((r for r in rows30 if r["amount"] >= 20_000_000), None)
        if big_single:
            signals.append("CONCENTRATION_WIN_PROVISIONAL")  # finalized after mkt-cap check

        # MOMENTUM_STACK — >=3 distinct agencies in 30d, cum > $20M
        agencies_30 = {r["agency"] for r in rows30 if r.get("agency")}
        if len(agencies_30) >= 3 and total_30 >= 20_000_000:
            signals.append("MOMENTUM_STACK")

        if not signals and not big_single and total_30 == 0:
            continue

        contracts = sorted(rows30, key=lambda r: r["amount"], reverse=True)[:3]

        out[ticker] = {
            "signals": signals,
            "contracts": contracts,
            "gov_summary": {
                "total_30d": total_30,
                "prior_30d_avg": prior_avg_30,
                "agencies_30d": sorted(list(agencies_30)),
                "biggest_award": big_single,
                "recipient_name": public_recipients.get(ticker),
            },
        }

    # NEW_WINNER detection — for each (ticker, agency) in last 30d, check if
    # the company had ANY prior award from that agency in the prior 12 months.
    # Use a single batch call per ticker rather than per agency to limit HTTP.
    async def _check_new_agencies(ticker: str, recipient: str, recent: list[dict]):
        # Gather agencies seen in last 30d
        recent_agencies = {r["agency"] for r in recent if r.get("agency") and r["amount"] >= 5_000_000}
        if not recent_agencies:
            return []
        # Pull 12-month award history for this recipient (>= $5M)
        try:
            hist = await fetch_recipient_history(recipient, lookback_days=365,
                                                  min_amount=5_000_000, limit=200)
        except Exception:
            return []
        hist_agencies_old: set[str] = set()
        cutoff = date.today() - timedelta(days=30)
        for h in hist:
            agency = h.get("Awarding Agency")
            start = h.get("Period of Performance Start Date") or ""
            try:
                d_start = date.fromisoformat(start[:10]) if start else None
            except Exception:
                d_start = None
            # consider as "old" if start before cutoff (i.e., before last 30d)
            if d_start and d_start < cutoff and agency:
                hist_agencies_old.add(agency)
        new_ones = recent_agencies - hist_agencies_old
        return list(new_ones)

    tasks = []
    targets = []
    for ticker, info in out.items():
        recipient = info["gov_summary"].get("recipient_name") or ticker
        recent_rows = info["contracts"]
        targets.append(ticker)
        tasks.append(_check_new_agencies(ticker, recipient, recent_rows))
    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for ticker, res in zip(targets, results):
            if isinstance(res, Exception) or not res:
                continue
            if "NEW_WINNER" not in out[ticker]["signals"]:
                out[ticker]["signals"].append("NEW_WINNER")
            out[ticker]["gov_summary"]["new_agencies"] = res

    # BUDGET_SURGE
    try:
        per_agency = await fetch_agency_obligations_by_month(months_back=4)
        budget_surges: list[dict[str, Any]] = []
        for agency, rows in per_agency.items():
            rows_sorted = sorted(rows, key=lambda r: r["month"])
            if len(rows_sorted) < 4:
                continue
            current = rows_sorted[-1]["amount"]
            prior = [r["amount"] for r in rows_sorted[:-1]]
            avg = sum(prior) / max(1, len(prior))
            if avg > 0 and current >= 1.5 * avg and current >= 100_000_000:
                budget_surges.append({"agency": agency, "current": current, "prior_avg": avg,
                                       "pct_increase": round((current - avg) / avg * 100, 1)})
        # For each surging agency, attribute to the public contractors with the
        # most exposure to that agency in the last 12 months.
        if budget_surges:
            for bs in budget_surges:
                agency = bs["agency"]
                # Find which tickers in our set won from this agency lately
                exposed: list[str] = []
                for ticker, info in out.items():
                    if agency in (info["gov_summary"].get("agencies_30d") or []):
                        exposed.append(ticker)
                bs["exposed_tickers"] = exposed
                for ticker in exposed:
                    if "BUDGET_SURGE" not in out[ticker]["signals"]:
                        out[ticker]["signals"].append("BUDGET_SURGE")
                    out[ticker]["gov_summary"].setdefault("budget_surge_agencies", []).append(agency)
    except Exception as e:
        logger.warning("BUDGET_SURGE detection failed: %s", e)
        budget_surges = []

    return {
        "by_ticker": out,
        "budget_surges": budget_surges,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "raw_count_30d": len(awards_30d),
        "raw_count_90d": len(awards_90d),
    }


async def list_recent_contracts_for_tickers(limit: int = 5) -> list[dict[str, Any]]:
    """Top recent gov contracts to public companies (for /contracts command)."""
    rows = await fetch_awards(date.today() - timedelta(days=14),
                                date.today(), min_amount=5_000_000, limit=100)
    out: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for r in rows:
        t = name_to_ticker(r.get("Recipient Name", ""))
        if not t:
            continue
        key = (t, r.get("Award ID"))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "ticker": t,
            "recipient": r.get("Recipient Name"),
            "agency": r.get("Awarding Agency"),
            "amount": float(r.get("Award Amount") or 0),
            "award_id": r.get("Award ID"),
            "start": r.get("Period of Performance Start Date"),
        })
        if len(out) >= limit:
            break
    return out


async def awards_for_agency(agency_name: str, days: int = 30, limit: int = 50) -> list[dict[str, Any]]:
    """For /agency [name] — public companies that won from that agency."""
    payload = {
        "filters": {
            "award_type_codes": ["A", "B", "C", "D"],
            "time_period": [
                {"start_date": (date.today() - timedelta(days=days)).isoformat(),
                  "end_date": date.today().isoformat()}
            ],
            "agencies": [{"type": "awarding", "tier": "toptier", "name": agency_name}],
            "award_amounts": [{"lower_bound": 1_000_000}],
        },
        "fields": ["Award ID", "Recipient Name", "Awarding Agency", "Award Amount"],
        "page": 1, "limit": 100, "sort": "Award Amount", "order": "desc",
    }
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        data = await _post(client, "/search/spending_by_award/", payload)
    rows = (data or {}).get("results", []) if data else []
    out = []
    for r in rows:
        t = name_to_ticker(r.get("Recipient Name", ""))
        if not t:
            continue
        out.append({
            "ticker": t,
            "recipient": r.get("Recipient Name"),
            "agency": r.get("Awarding Agency"),
            "amount": float(r.get("Award Amount") or 0),
        })
        if len(out) >= limit:
            break
    return out


async def recent_wins_for_ticker(ticker: str, days: int = 7, min_amount: float = 1_000_000) -> list[dict[str, Any]]:
    """For /watchlist_contracts — recent wins for a specific ticker."""
    # Reverse map: ticker → recipient pattern. Use first matching pattern.
    recipient_pattern = None
    for needle, t in PUBLIC_CONTRACTORS:
        if t == ticker:
            recipient_pattern = needle
            break
    if not recipient_pattern:
        return []
    end = date.today()
    start = end - timedelta(days=days)
    payload = {
        "filters": {
            "award_type_codes": ["A", "B", "C", "D"],
            "time_period": [{"start_date": start.isoformat(), "end_date": end.isoformat()}],
            "recipient_search_text": [recipient_pattern.title()],
            "award_amounts": [{"lower_bound": min_amount}],
        },
        "fields": ["Award ID", "Recipient Name", "Awarding Agency", "Award Amount"],
        "page": 1, "limit": 50, "sort": "Award Amount", "order": "desc",
    }
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        data = await _post(client, "/search/spending_by_award/", payload)
    rows = (data or {}).get("results", []) if data else []
    return [{
        "ticker": ticker,
        "recipient": r.get("Recipient Name"),
        "agency": r.get("Awarding Agency"),
        "amount": float(r.get("Award Amount") or 0),
        "award_id": r.get("Award ID"),
    } for r in rows]
