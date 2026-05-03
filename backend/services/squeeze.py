"""4-dimensional squeeze probability score (0-100). Pure Python. Zero Claude tokens.

Inputs (all from existing data we already fetch):
  - Short interest %  (Finviz)
  - Days-to-cover   (Yahoo: shortRatio or short_pct * float_shares / avg_volume)
  - Borrow-rate proxy (delta in short_pct over 30d; >20% growth = elevated)
  - Rate of change in short positions (Finviz repeat reads, persisted in Mongo)

Score formula:
  base = (short_pct/100 * 0.35) + (min(dtc,30)/30 * 0.30)
       + (borrow_rate_score * 0.20) + (rate_of_change_score * 0.15)
  squeeze_score = min(100, base * 100)
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import get_db


def _normalize(v: float | None, lo: float, hi: float) -> float:
    if v is None:
        return 0.0
    v = max(lo, min(hi, v))
    return (v - lo) / (hi - lo) if hi > lo else 0.0


async def record_short_observation(ticker: str, short_pct: float | None) -> None:
    """Persist short_pct sample so we can compute 30d rate-of-change."""
    if short_pct is None:
        return
    db = get_db()
    await db.short_history.insert_one({
        "ticker": ticker.upper(),
        "short_pct": float(short_pct),
        "ts": datetime.now(timezone.utc).isoformat(),
    })
    # Trim old samples beyond 60 days
    cutoff = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    await db.short_history.delete_many({"ticker": ticker.upper(), "ts": {"$lt": cutoff}})


async def _short_30d_change(ticker: str, current_pct: float | None) -> float | None:
    if current_pct is None:
        return None
    db = get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    doc = await db.short_history.find_one(
        {"ticker": ticker.upper(), "ts": {"$lte": cutoff}},
        sort=[("ts", -1)],
    )
    if not doc:
        return None
    old = float(doc.get("short_pct", 0))
    if old <= 0:
        return None
    return (current_pct - old) / old  # fraction


def _band(score: int) -> tuple[str, str]:
    if score >= 86:
        return "extreme", "🔥"
    if score >= 66:
        return "high", "🟠"
    if score >= 41:
        return "medium", "🟡"
    return "low", "⚪"


async def compute_squeeze(ticker: str, short_pct: float | None, fund: dict[str, Any]) -> dict[str, Any]:
    """Returns {score:int, band:str, emoji:str, components:{...}}."""
    avg_vol = (fund or {}).get("avg_volume") or 0
    float_shares = (fund or {}).get("float_shares") or 0

    # Days-to-cover: prefer yfinance shortRatio; else compute from short_pct + float / avg_vol
    dtc = (fund or {}).get("short_ratio")
    if not dtc and short_pct and avg_vol and float_shares:
        short_shares = float_shares * (short_pct / 100.0)
        dtc = short_shares / max(1, avg_vol)

    # Rate-of-change in short %
    roc = await _short_30d_change(ticker, short_pct)

    # Borrow-rate proxy (1.0 if short_pct grew >20% in 30d, else linear)
    if roc is None:
        borrow_score = 0.3 if (short_pct and short_pct >= 20) else 0.0
    else:
        borrow_score = min(1.0, max(0.0, roc / 0.5))

    si_norm = _normalize(short_pct, 5.0, 50.0)
    dtc_norm = _normalize(dtc, 1.0, 20.0)
    roc_norm = max(0.0, min(1.0, (roc or 0) / 0.5)) if roc is not None else 0.0

    base = (si_norm * 0.35) + (dtc_norm * 0.30) + (borrow_score * 0.20) + (roc_norm * 0.15)
    score = int(round(min(100, base * 100)))
    band, emoji = _band(score)

    return {
        "score": score,
        "band": band,
        "emoji": emoji,
        "components": {
            "short_pct": short_pct,
            "days_to_cover": round(dtc, 1) if dtc else None,
            "rate_of_change_30d": round(roc * 100, 1) if roc is not None else None,
            "borrow_score": round(borrow_score, 2),
        },
    }


async def squeeze_leaderboard(limit: int = 10) -> list[dict[str, Any]]:
    """Top tickers by squeeze score from latest scan."""
    db = get_db()
    last = await db.scan_results.find_one({}, {"_id": 0, "results": 1}, sort=[("finished_at", -1)])
    if not last:
        return []
    rows = []
    for r in last.get("results", []):
        sq = r.get("squeeze") or {}
        if sq.get("score") is not None:
            rows.append({
                "ticker": r["ticker"],
                "score": sq["score"],
                "band": sq.get("band"),
                "emoji": sq.get("emoji"),
                "components": sq.get("components"),
                "price": r.get("price"),
            })
    rows.sort(key=lambda x: x["score"], reverse=True)
    return rows[:limit]
