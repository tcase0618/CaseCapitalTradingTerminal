"""Bounded regime recovery without disguising missing data as market risk."""
from __future__ import annotations

import asyncio
import html
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .db import get_db

logger = logging.getLogger(__name__)
FETCH_TIMEOUT_SECONDS = 2.5
STATE_TIMEOUT_SECONDS = 0.5
LAST_GOOD_MAX_AGE_SECONDS = 900
SNAPSHOT_ID = "market_regime_last_good_v1"
ALERT_ID = "market_regime_alert_v1"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def history_values(history: dict[str, Any]) -> list[float]:
    """Reject invalid/future/old price series rather than cache false regimes."""
    today = _now().astimezone(ZoneInfo("America/New_York")).date()
    points = sorted((datetime.fromisoformat(str(day)[:10]).date(), float(value))
                    for day, value in history.items())
    if len(points) < 2 or not all(math.isfinite(value) and value > 0 for _, value in points):
        return []
    if points[-1][0] > today or points[-1][0] < today - timedelta(days=4):
        return []
    return [value for _, value in points]


def _verified(snapshot: dict[str, Any]) -> bool:
    try:
        prices = [float(snapshot[key]) for key in ("spy_last", "spy_ema200")]
        change = float(snapshot["spy_day_change_pct"])
        return (snapshot.get("status") in {"green", "downtrend", "red", "doomsday"}
                and all(math.isfinite(price) and price > 0 for price in prices)
                and math.isfinite(change)
                and bool(snapshot.get("halt_new_entries")) == (snapshot["status"] == "doomsday"))
    except (KeyError, TypeError, ValueError):
        return False


async def _last_good() -> dict[str, Any] | None:
    try:
        row = await asyncio.wait_for(get_db().bot_state.find_one({"_id": SNAPSHOT_ID}), STATE_TIMEOUT_SECONDS)
        snapshot = (row or {}).get("snapshot") or {}
        observed = datetime.fromisoformat(snapshot["checked_at"].replace("Z", "+00:00"))
        if observed.tzinfo is None:
            return None
        age = (_now() - observed).total_seconds()
        if 0 <= age <= LAST_GOOD_MAX_AGE_SECONDS and _verified(snapshot):
            return {**snapshot, "data_state": "LAST_GOOD", "data_age_seconds": round(age, 2),
                    "evaluated_at": _now().isoformat(), "warning": "regime_live_refresh_failed"}
    except Exception:
        logger.warning("Regime last-good snapshot unavailable", exc_info=True)
    return None


async def resolve(fetch) -> dict[str, Any]:
    last_result = None
    for force in (False, True):
        try:
            result = await asyncio.wait_for(fetch(force=force), FETCH_TIMEOUT_SECONDS)
            last_result = result
            if _verified(result):
                snapshot = {**result, "data_state": "AVAILABLE"}
                try:
                    await asyncio.wait_for(get_db().bot_state.update_one(
                        {"_id": SNAPSHOT_ID}, {"$set": {"snapshot": snapshot}}, upsert=True), STATE_TIMEOUT_SECONDS)
                except Exception:
                    logger.warning("Could not persist regime snapshot", exc_info=True)
                return snapshot
        except Exception:
            logger.warning("Regime history attempt failed (force=%s)", force, exc_info=True)
    cached = await _last_good()
    if cached:
        return cached
    return {"status": "unknown", "weather": "UNKNOWN", "vix": None, "spy_last": None,
            "spy_ema200": None, "spy_day_change_pct": None, "halt_new_entries": True,
            "reason": "regime_data_unavailable", "playbook": "DATA_FAIL_SAFE",
            "source": "primary_market_data_unavailable", "data_state": "UNAVAILABLE",
            "checked_at": _now().isoformat(),
            "last_fetch_reason": (last_result or {}).get("reason")}


def alert_state(snapshot: dict[str, Any]) -> str:
    if snapshot.get("status") == "unknown":
        return "data_unavailable"
    if snapshot.get("halt_new_entries"):
        return "market_restriction"
    return "cached_data" if snapshot.get("data_state") == "LAST_GOOD" else "clear"


def alert_text(snapshot: dict[str, Any], previous: str | None) -> str | None:
    state = alert_state(snapshot)
    if state == previous or (state == "clear" and previous is None):
        return None
    timestamp = _now().astimezone(ZoneInfo("America/New_York")).strftime("%b %d %H:%M ET")
    if state == "data_unavailable":
        detail = ("Status: <b>REGIME DATA UNAVAILABLE</b>\n"
                  "SPY history unavailable after retry; no recent validated fallback.\n"
                  "PM new sizing is paused by this data check, not a confirmed market crash.")
    elif state == "market_restriction":
        detail = (f"Status: <b>MARKET-RISK ENTRY RESTRICTION</b>\n"
                  f"Reason: {html.escape(str(snapshot.get('reason')))}\n"
                  f"SPY session change: {snapshot.get('spy_day_change_pct')}%")
        if snapshot.get("data_state") == "LAST_GOOD":
            detail += "\nBased on a recent last-good assessment; live refresh failed."
    elif state == "cached_data":
        detail = ("Status: <b>REGIME REFRESH DEGRADED</b>\n"
                  f"Using validated assessment from {html.escape(str(snapshot.get('checked_at')))} "
                  "(maximum age 15 minutes).\nNo new market halt inferred from this fetch failure.")
    else:
        detail = ("Status: <b>REGIME CHECK RECOVERED</b>\n"
                  f"Current regime: {html.escape(str(snapshot.get('status')).upper())}.\n"
                  "This regime check no longer blocks new sizing; other PM/execution checks still apply.")
    return (f"<b>CASE CAPITAL | REGIME GATE</b>\n<code>{timestamp}</code>\n\n{detail}\n"
            "Global trading switch unchanged. This check does not disable exits.")


async def notify_transition(snapshot: dict[str, Any]) -> None:
    from . import telegram_service

    collection = get_db().bot_state
    previous = await collection.find_one({"_id": ALERT_ID}) or {}
    text = alert_text(snapshot, previous.get("state"))
    if text and not await telegram_service.send_message(text):
        return
    await collection.update_one({"_id": ALERT_ID}, {"$set": {
        "state": alert_state(snapshot), "checked_at": _now().isoformat(),
        "reason": snapshot.get("reason"), "data_state": snapshot.get("data_state"),
    }}, upsert=True)
