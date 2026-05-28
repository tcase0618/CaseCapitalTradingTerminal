"""AXIOM Max Conviction + Narrative Lock detector.

Both are post-scan calculations that take the final scan results and
overlay Dark Horse / X Factor / unusual flow / lottery scores.

Max Conviction scoring (per spec):
  • Dark Horse active        +3
  • X Factor active          +3
  • Unusual flow             +2
  • Insider cluster buy      +2
  • Squeeze score >70        +2
  • Earnings this week       +2
  • Appearing in scan        +1
  • Congressional buy        +1

Top 3 by conviction → MAX_CONVICTION designation.

Narrative Lock fires when ALL THREE are present on the SAME ticker:
  • Dark Horse
  • X Factor
  • Unusual options flow score > 75
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any

from .db import get_db, log_activity, stamped


def _now():
    return datetime.now(timezone.utc)


def conviction_score(result: dict[str, Any], dark_horse: dict | None,
                       x_factor: dict | None, lottery_score: float | None,
                       earnings_this_week: bool) -> dict[str, Any]:
    """Compute max-conviction score for one ticker. Returns {score, signals_aligned, components}."""
    sigs = set(result.get("signals") or [])
    flow_score = (result.get("flow") or {}).get("score") or 0
    sq_score = (result.get("squeeze") or {}).get("score") or 0

    parts: list[tuple[str, int]] = []
    if dark_horse:
        parts.append(("DARK_HORSE", 3))
    if x_factor:
        parts.append(("X_FACTOR", 3))
    if "UNUSUAL_FLOW" in sigs or flow_score >= 75:
        parts.append(("UNUSUAL_FLOW", 2))
    if "insider_cluster_buy" in sigs:
        parts.append(("INSIDER", 2))
    if sq_score > 70:
        parts.append(("SQUEEZE_70", 2))
    if earnings_this_week:
        parts.append(("EARNINGS_WEEK", 2))
    # Always 1 for appearing in scan
    parts.append(("SCAN_HIT", 1))
    if "CONGRESSIONAL_BUY" in sigs:
        parts.append(("CONGRESSIONAL", 1))

    total = sum(p[1] for p in parts)
    return {
        "score": total,
        "components": parts,
        "signals_aligned": len(parts),
    }


def detect_narrative_lock(result: dict[str, Any], dark_horse: dict | None,
                            x_factor: dict | None) -> bool:
    if not dark_horse or not x_factor:
        return False
    flow_score = (result.get("flow") or {}).get("score") or 0
    if "UNUSUAL_FLOW" in (result.get("signals") or []):
        return True
    return flow_score >= 75


async def compute_for_scan(scan_results: list[dict[str, Any]],
                              dark_horse_by_ticker: dict[str, dict],
                              x_factor_by_ticker: dict[str, dict],
                              earnings_tickers: set[str],
                              lottery_by_ticker: dict[str, float]) -> dict[str, Any]:
    """Compute Max Conviction picks + Narrative Locks for a scan.
    Returns {top3, narrative_locks, all_scored}.
    Persists max_conviction_picks + narrative_lock_alerts collections.
    """
    if not scan_results:
        return {"top3": [], "narrative_locks": [], "all_scored": []}
    scored: list[dict[str, Any]] = []
    locks: list[dict[str, Any]] = []
    for r in scan_results:
        t = r.get("ticker")
        if not t:
            continue
        dh = dark_horse_by_ticker.get(t)
        xf = x_factor_by_ticker.get(t)
        cs = conviction_score(r, dh, xf, lottery_by_ticker.get(t),
                              t in earnings_tickers)
        nl = detect_narrative_lock(r, dh, xf)
        row = {
            "ticker": t,
            "conviction_score": cs["score"],
            "signals_aligned": cs["signals_aligned"],
            "components": [c[0] for c in cs["components"]],
            "axiom_score": r.get("signal_score"),
            "price": r.get("price"),
            "thesis": r.get("thesis") or r.get("axiom_thesis"),
            "narrative_lock": nl,
            "dark_horse": bool(dh),
            "x_factor": bool(xf),
            "lottery_score": lottery_by_ticker.get(t),
            "earnings_this_week": t in earnings_tickers,
        }
        scored.append(row)
        if nl:
            locks.append(row)
    scored.sort(key=lambda x: (-x["conviction_score"], -(x["axiom_score"] or 0)))
    top3 = scored[:3]
    for p in top3:
        p["is_max_conviction"] = True

    # Persist
    db = get_db()
    now_iso = _now().isoformat()
    for p in top3:
        await db.max_conviction_picks.update_one(
            {"ticker": p["ticker"], "date": now_iso[:10]},
            {"$set": stamped({**p, "logged_at": now_iso})},
            upsert=True,
        )
    for nl in locks:
        await db.narrative_lock_alerts.update_one(
            {"ticker": nl["ticker"], "date": now_iso[:10]},
            {"$set": stamped({**nl, "fired_at": now_iso})},
            upsert=True,
        )
    if top3:
        await log_activity(
            f"Max Conviction: {len(top3)} top picks · {len(locks)} Narrative Locks",
            "info",
        )
    return {"top3": top3, "narrative_locks": locks, "all_scored": scored}


async def latest_top3() -> list[dict[str, Any]]:
    db = get_db()
    today = _now().date().isoformat()
    return await db.max_conviction_picks.find(
        {"date": today}, {"_id": 0},
    ).sort("conviction_score", -1).to_list(3)


async def recent_locks(days: int = 14) -> list[dict[str, Any]]:
    from datetime import timedelta
    db = get_db()
    cutoff = (_now() - timedelta(days=days)).date().isoformat()
    return await db.narrative_lock_alerts.find(
        {"date": {"$gte": cutoff}}, {"_id": 0},
    ).sort("date", -1).to_list(50)
