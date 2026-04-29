"""Scanner: fetch all signals -> aggregate -> pre-filter (2+) -> Claude batch."""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Any

from . import claude_service
from .db import get_db, log_activity
from .scrapers import collect_all_signals

logger = logging.getLogger(__name__)


def _aggregate_candidates(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Aggregate by ticker; keep only those with 2+ distinct signal sources."""
    by_ticker: dict[str, dict[str, Any]] = {}

    for c in raw.get("insider_clusters", []):
        t = c["ticker"]
        x = by_ticker.setdefault(t, {"ticker": t, "signals": [], "company": c.get("company")})
        x["signals"].append("insider_cluster_buy")
        x["insider_summary"] = {
            "insider_count": c.get("insider_count"),
            "buy_count": c.get("buy_count"),
            "total_value_usd": c.get("total_value_usd"),
            "latest_filing": c.get("latest_filing"),
        }

    for s in raw.get("high_short_interest", []):
        t = s["ticker"]
        x = by_ticker.setdefault(t, {"ticker": t, "signals": []})
        if "high_short_interest" not in x["signals"]:
            x["signals"].append("high_short_interest")
        x["short_summary"] = {"short_float_pct": s.get("short_float_pct")}

    for e in raw.get("upcoming_earnings", []):
        t = e["ticker"]
        x = by_ticker.setdefault(t, {"ticker": t, "signals": []})
        if "upcoming_earnings" not in x["signals"]:
            x["signals"].append("upcoming_earnings")
        x["earnings_summary"] = {"earnings_date": e.get("earnings_date")}

    # Pre-filter: 2+ distinct signals
    candidates = [v for v in by_ticker.values() if len(set(v["signals"])) >= 2]
    candidates.sort(key=lambda v: len(set(v["signals"])), reverse=True)
    return candidates


async def run_scan(triggered_by: str = "manual") -> dict[str, Any]:
    """Full scan flow. Returns scan summary doc."""
    started = datetime.now(timezone.utc)
    await log_activity(f"Scan started ({triggered_by})", "info")

    raw = await collect_all_signals()
    candidates = _aggregate_candidates(raw)
    pre_filter_count = len(candidates)

    await log_activity(
        f"Aggregated signals: {len(raw['insider_clusters'])} insider, "
        f"{len(raw['high_short_interest'])} short, "
        f"{len(raw['upcoming_earnings'])} earnings -> "
        f"{pre_filter_count} candidates passed 2+ signals",
        "info",
    )

    # Token-efficient batch Claude (cache-aware)
    analyses = await claude_service.analyze_batch(candidates)
    cache_hits = sum(1 for a in analyses if a.get("cached"))
    fresh_calls = len(analyses) - cache_hits

    finished = datetime.now(timezone.utc)
    db = get_db()
    scan_doc = {
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_sec": round((finished - started).total_seconds(), 2),
        "triggered_by": triggered_by,
        "raw_counts": {
            "insider_clusters": len(raw["insider_clusters"]),
            "high_short_interest": len(raw["high_short_interest"]),
            "upcoming_earnings": len(raw["upcoming_earnings"]),
        },
        "pre_filter_passed": pre_filter_count,
        "claude_calls_made": fresh_calls,
        "claude_cache_hits": cache_hits,
        "results": analyses,
    }
    await db.scan_results.insert_one(dict(scan_doc))
    await db.bot_state.update_one(
        {"_id": "state"},
        {"$set": {
            "last_scan_at": finished.isoformat(),
            "last_scan_summary": {
                "pre_filter_passed": pre_filter_count,
                "results_count": len(analyses),
                "claude_calls_made": fresh_calls,
                "claude_cache_hits": cache_hits,
            },
        }},
        upsert=True,
    )
    await log_activity(
        f"Scan complete: {len(analyses)} analyses ({fresh_calls} fresh, "
        f"{cache_hits} cached) in {scan_doc['duration_sec']}s",
        "success",
    )
    # Return without _id
    scan_doc.pop("_id", None)
    return scan_doc


async def latest_scan() -> dict[str, Any] | None:
    db = get_db()
    return await db.scan_results.find_one({}, {"_id": 0}, sort=[("finished_at", -1)])
