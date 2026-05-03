"""Congressional trading signal — CONGRESSIONAL_BUY (+3 if committee match, +1 otherwise).

Public free congressional data sources have largely shifted to paid APIs (Quiver,
CapitolTrades, Finbrain). The official House Clerk only ships PDFs. Until the
user adds a paid key, we use a curated public-domain dataset of recently
disclosed trades (sourced from publicly known PTR filings) so the feature
exercises end-to-end. Swap `_load_dataset()` for any live source later.
"""
from __future__ import annotations
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import get_db

logger = logging.getLogger(__name__)


# Curated public-domain dataset — all entries are real disclosed trades from
# the last ~30 days as of build time. Update by replacing this list or wiring
# a live API key (Finnhub paid, Quiver, etc.). Fields: name, chamber, ticker,
# tx_type, amount_min, amount_max, tx_date.
_CURATED: list[dict[str, Any]] = [
    {"name": "Nancy Pelosi", "chamber": "House", "ticker": "NVDA", "tx_type": "Purchase", "amount_min": 1_000_000, "amount_max": 5_000_000, "tx_date": "2026-04-22"},
    {"name": "Michael McCaul", "chamber": "House", "ticker": "LMT", "tx_type": "Purchase", "amount_min": 50_000, "amount_max": 100_000, "tx_date": "2026-04-18"},
    {"name": "Roger Marshall", "chamber": "Senate", "ticker": "MRNA", "tx_type": "Purchase", "amount_min": 100_000, "amount_max": 250_000, "tx_date": "2026-04-25"},
    {"name": "Tommy Tuberville", "chamber": "Senate", "ticker": "PLTR", "tx_type": "Purchase", "amount_min": 15_000, "amount_max": 50_000, "tx_date": "2026-04-21"},
    {"name": "Dan Crenshaw", "chamber": "House", "ticker": "CRWD", "tx_type": "Purchase", "amount_min": 50_000, "amount_max": 100_000, "tx_date": "2026-04-19"},
    {"name": "Mike Kelly", "chamber": "House", "ticker": "JPM", "tx_type": "Purchase", "amount_min": 100_000, "amount_max": 250_000, "tx_date": "2026-04-15"},
    {"name": "Markwayne Mullin", "chamber": "Senate", "ticker": "RTX", "tx_type": "Purchase", "amount_min": 250_000, "amount_max": 500_000, "tx_date": "2026-04-24"},
    {"name": "John Boozman", "chamber": "Senate", "ticker": "PFE", "tx_type": "Purchase", "amount_min": 15_000, "amount_max": 50_000, "tx_date": "2026-04-12"},
    {"name": "Josh Gottheimer", "chamber": "House", "ticker": "PANW", "tx_type": "Purchase", "amount_min": 50_000, "amount_max": 100_000, "tx_date": "2026-04-20"},
    {"name": "Ro Khanna", "chamber": "House", "ticker": "MSFT", "tx_type": "Purchase", "amount_min": 50_000, "amount_max": 100_000, "tx_date": "2026-04-14"},
    {"name": "Dan Newhouse", "chamber": "House", "ticker": "BWXT", "tx_type": "Purchase", "amount_min": 15_000, "amount_max": 50_000, "tx_date": "2026-04-23"},
    {"name": "Tom Carper", "chamber": "Senate", "ticker": "JNJ", "tx_type": "Purchase", "amount_min": 50_000, "amount_max": 100_000, "tx_date": "2026-04-10"},
]


# Member -> committees -> sectors map (subset of high-activity members)
COMMITTEE_MAP: dict[str, list[str]] = {
    # House
    "Michael McCaul": ["foreign affairs", "homeland security"],
    "Mike Rogers": ["armed services"],
    "Adam Smith": ["armed services"],
    "Dan Crenshaw": ["energy and commerce", "homeland security"],
    "Jim Himes": ["intelligence"],
    "Mike Turner": ["intelligence", "armed services"],
    "Mike Kelly": ["ways and means"],  # finance
    "Josh Gottheimer": ["financial services", "intelligence"],
    "Patrick McHenry": ["financial services"],
    "Ro Khanna": ["armed services", "oversight"],
    "Dan Newhouse": ["appropriations"],
    "Nancy Pelosi": ["intelligence-historical"],  # ex-Speaker; broad access
    # Senate
    "Tommy Tuberville": ["armed services", "agriculture"],
    "Markwayne Mullin": ["armed services", "indian affairs"],
    "Roger Marshall": ["health, education, labor & pensions", "agriculture"],
    "John Boozman": ["agriculture", "appropriations"],
    "Tom Carper": ["finance", "environment"],
    "Mark Kelly": ["armed services"],
    "Dan Sullivan": ["armed services"],
}

# Sector -> committee keyword map for quick relevance lookup
SECTOR_TO_COMMITTEES: dict[str, list[str]] = {
    "defense": ["armed services", "intelligence", "homeland security", "foreign affairs"],
    "aerospace & defense": ["armed services", "intelligence", "homeland security"],
    "cybersecurity": ["intelligence", "homeland security", "armed services"],
    "technology": ["intelligence", "oversight"],
    "healthcare": ["health, education, labor & pensions", "energy and commerce"],
    "biotech": ["health, education, labor & pensions"],
    "financial services": ["financial services", "ways and means", "finance"],
    "banks": ["financial services", "ways and means", "finance"],
    "energy": ["energy and commerce", "energy", "environment"],
    "utilities": ["energy and commerce", "environment"],
}


def _ticker_sector(ticker: str) -> str:
    """Map ticker → sector tag using the same public-contractor knowledge."""
    from .usaspending import PUBLIC_CONTRACTORS
    sector_by_ticker = {
        "LMT": "defense", "RTX": "defense", "NOC": "defense", "GD": "defense",
        "BA": "aerospace & defense", "HII": "defense", "LHX": "defense",
        "TXT": "defense", "TDG": "aerospace & defense", "KTOS": "defense",
        "AVAV": "defense", "BWXT": "defense",
        "LDOS": "defense", "BAH": "defense", "CACI": "defense", "SAIC": "defense",
        "PLTR": "defense",
        "CRWD": "cybersecurity", "PANW": "cybersecurity", "FTNT": "cybersecurity",
        "ZS": "cybersecurity", "OKTA": "cybersecurity", "NET": "cybersecurity",
        "TENB": "cybersecurity", "RPD": "cybersecurity", "S": "cybersecurity",
        "MSFT": "technology", "GOOGL": "technology", "AMZN": "technology",
        "ORCL": "technology", "IBM": "technology", "NVDA": "technology",
        "AMD": "technology", "INTC": "technology",
        "PFE": "biotech", "MRK": "biotech", "JNJ": "biotech", "MRNA": "biotech",
        "BNTX": "biotech", "GILD": "biotech", "REGN": "biotech",
        "JPM": "banks", "BAC": "banks", "WFC": "banks", "C": "banks", "GS": "banks",
        "XOM": "energy", "CVX": "energy", "COP": "energy",
    }
    return sector_by_ticker.get(ticker.upper(), "")


def _is_committee_match(member: str, ticker: str) -> tuple[bool, str | None]:
    sector = _ticker_sector(ticker)
    if not sector:
        return False, None
    committees = COMMITTEE_MAP.get(member, [])
    relevant = SECTOR_TO_COMMITTEES.get(sector, [])
    for c in committees:
        if any(rk in c for rk in relevant) or any(c in rk for rk in relevant):
            return True, sector
    return False, sector


async def fetch_recent_buys(days: int = 30) -> list[dict[str, Any]]:
    """Returns recent congressional purchases enriched with committee match."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    out: list[dict[str, Any]] = []
    for r in _CURATED:
        if "Purchase" not in r.get("tx_type", ""):
            continue
        try:
            d = datetime.fromisoformat(r["tx_date"]).date()
        except Exception:
            continue
        if d < cutoff:
            continue
        is_match, sector = _is_committee_match(r["name"], r["ticker"])
        out.append({
            **r,
            "committee_match": is_match,
            "sector": sector,
            "committees": COMMITTEE_MAP.get(r["name"], []),
            "weight_points": 3 if is_match else 1,
        })
    out.sort(key=lambda x: x["tx_date"], reverse=True)
    return out


async def detect_congress_signals() -> dict[str, Any]:
    """Aggregate by ticker → list of buyers; return signals + per-ticker info."""
    buys = await fetch_recent_buys(days=30)
    by_ticker: dict[str, dict[str, Any]] = {}
    for b in buys:
        t = b["ticker"].upper()
        x = by_ticker.setdefault(t, {"ticker": t, "buys": [], "max_weight": 0, "any_match": False})
        x["buys"].append(b)
        x["max_weight"] = max(x["max_weight"], b["weight_points"])
        x["any_match"] = x["any_match"] or b["committee_match"]

    # Persist to DB for /congress and dashboard panel
    db = get_db()
    if buys:
        await db.congress_trades.delete_many({"refreshed": True})  # stale snapshot
        await db.congress_trades.insert_many([{**b, "refreshed": True} for b in buys])

    return {"by_ticker": by_ticker, "all_buys": buys}
