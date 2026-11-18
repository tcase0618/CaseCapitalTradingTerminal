"""Dated options approval journal; Public-only weekly marks, never orders."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .db import get_db

ET = ZoneInfo("America/New_York")
_export_lock = asyncio.Lock()


def research_only() -> bool:
    return os.environ.get("OPTIONS_RESEARCH_ONLY", "false").strip().lower() in {"1", "true", "yes", "on"}


def _number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _timestamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.tzinfo else None
    except (TypeError, ValueError):
        return None


def approval_record(ticket: dict) -> dict | None:
    if ticket.get("route") not in {"OPTION", "BOTH"} or ticket.get("pm_action") not in {"STARTER", "ACCUMULATE"}:
        return None
    if not ticket.get("cycle_id") or not ticket.get("candidate_id"):
        return None
    observed = _timestamp(ticket.get("generated_at"))
    if observed is None:
        return None
    research = ticket.get("research_options") or {}
    # Keep the contract the PM saw, not a subsequently rebuilt broker ticket.
    if research:
        instrument = {"kind": "spread", **research["spread"]} if research.get("spread") else {
            "kind": "single_leg", **(research.get("contract") or {})}
    else:
        instrument = dict(ticket.get("instrument") or {})
    symbol = instrument.get("symbol") or instrument.get("contractSymbol")
    bid, ask = _number(instrument.get("bid")), _number(instrument.get("ask"))
    ts = _timestamp(instrument.get("quote_time") or instrument.get("quoteTime"))
    age = (observed - ts).total_seconds() if ts else None
    valid = bool(symbol and instrument.get("kind") == "single_leg" and bid is not None
                 and ask is not None and 0 < bid <= ask and age is not None and 0 <= age <= 120)
    key = f"{ticket['cycle_id']}:{ticket['candidate_id']}"
    return {
        "_id": hashlib.sha256(key.encode()).hexdigest(),
        "cycle_id": ticket["cycle_id"], "candidate_id": ticket["candidate_id"],
        "observed_at": observed.isoformat(), "day_et": observed.astimezone(ET).date().isoformat(),
        "ticker": ticket.get("ticker"), "pm_action": ticket.get("pm_action"),
        "pm_score": ticket.get("pm_score"), "strategy_lane": ticket.get("strategy_lane"),
        "instrument": instrument, "symbol": symbol, "expiration": instrument.get("expiration"),
        "provider": research.get("data_provider") or ticket.get("data_provider"),
        "quote_time": ts.isoformat() if ts else None, "entry_ask": ask if valid else None,
        "entry_status": "QUOTED_NOT_FILLED" if valid else "UNPRICED_OR_UNSUPPORTED",
        "blocked_reasons": ticket.get("blocked_reasons") or [],
        "mode": "RESEARCH_ONLY", "review": None,
    }


def _directory() -> Path:
    default = Path(__file__).resolve().parents[1] / "data" / "options-research"
    return Path(os.environ.get("OPTIONS_RESEARCH_DIR") or default)


def _write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


async def export_day(day: str):
    async with _export_lock:
        rows = await get_db().options_research_approvals.find({"day_et": day}, {"_id": 0}).sort("observed_at", 1).to_list(10001)
        if len(rows) > 10000:
            raise RuntimeError("options_research_export_capacity_exceeded")
        content = (
            f"# Options research approvals - {day} ET\n\n"
            "No orders. Each record is a dated PM approval, not a fill. Repeated approvals are not independent trades.\n"
            "Returns use original fresh quoted ask to review quoted bid, exclude fees, and are not realized P&L.\n"
            "Missing quotes and unsupported structures remain unpriced. Sunday marks may be Friday observations.\n\n"
            + "\n\n".join("```json\n" + json.dumps(row, indent=2, default=str) + "\n```" for row in rows)
        )
        await asyncio.to_thread(_write, _directory() / f"{day}.md", content)
    return len(rows)


async def record_candidates(tickets: list[dict]) -> dict:
    days = set()
    count = 0
    for ticket in tickets:
        record = approval_record(ticket)
        if record is None:
            continue
        await get_db().options_research_approvals.update_one(
            {"_id": record["_id"]}, {"$setOnInsert": record}, upsert=True)
        days.add(record["day_et"])
        count += 1
    for day in sorted(days):
        await export_day(day)
    return {"mode": "RESEARCH_ONLY", "approved": count, "days": sorted(days)}


def review_mark(record: dict, quote: dict | None, checked_at: datetime) -> dict:
    quote = quote or {}
    bid = _number(quote.get("bid") if quote.get("bid") is not None else quote.get("bidPrice"))
    ts = _timestamp(quote.get("bidTimestamp"))
    entry = _number(record.get("entry_ask"))
    age = (checked_at - ts).total_seconds() if ts else None
    observed = _timestamp(record.get("observed_at"))
    valid = bool(entry and entry > 0 and bid is not None and bid >= 0 and ts and observed
                 and ts >= observed and age is not None and 0 <= age <= 3 * 86400)
    return {"checked_at": checked_at.isoformat(), "status": "QUOTED_NOT_REALIZED" if valid else "UNAVAILABLE",
            "mark_bid": bid if valid else None, "quote_time": ts.isoformat() if ts else None,
            "quote_age_seconds": age, "return_pct": round((bid / entry - 1) * 100, 4) if valid else None}


async def weekly_review() -> dict:
    from . import public_api

    now = datetime.now(timezone.utc)
    rows = await get_db().options_research_approvals.find({}, {"_id": 1, "ticker": 1, "symbol": 1,
        "expiration": 1, "entry_ask": 1, "observed_at": 1, "day_et": 1, "review": 1}).to_list(10001)
    if len(rows) > 10000:
        raise RuntimeError("options_research_review_capacity_exceeded")
    groups = {}
    for row in rows:
        prior = row.get("review") or {}
        # Preserve the last available observation after expiry; never assume expiry value zero.
        if row.get("expiration") and row["expiration"] < now.astimezone(ET).date().isoformat() and prior.get("status") == "QUOTED_NOT_REALIZED":
            continue
        groups.setdefault((row.get("ticker"), row.get("expiration")), []).append(row)
    available = unavailable = 0
    days = set()
    async with public_api.PublicAPIClient() as client:
        for (ticker, expiration), records in groups.items():
            quotes = {}
            error = None
            if ticker and expiration:
                try:
                    chain = await client.option_chain(ticker, expiration=expiration)
                    quotes = {q.get("symbol") or q.get("contractSymbol"): q for q in chain.get("options", [])}
                except Exception as exc:
                    error = exc.__class__.__name__
            for row in records:
                mark = review_mark(row, quotes.get(row.get("symbol")), datetime.now(timezone.utc))
                mark["provider_error_type"] = error
                await get_db().options_research_approvals.update_one({"_id": row["_id"]}, {"$set": {"review": mark}})
                available += mark["status"] == "QUOTED_NOT_REALIZED"
                unavailable += mark["status"] == "UNAVAILABLE"
                days.add(row["day_et"])
    for day in sorted(days):
        await export_day(day)
    summary = {"checked_at": now.isoformat(), "mode": "RESEARCH_ONLY", "marked": available, "unavailable": unavailable}
    await get_db().bot_state.update_one({"_id": "options_research_weekly"}, {"$set": summary}, upsert=True)
    await asyncio.to_thread(_write, _directory() / "latest-sunday-review.md",
                            "# Options weekly review\n\nNo orders or policy changes.\n\n```json\n" + json.dumps(summary, indent=2) + "\n```\n")
    return summary
