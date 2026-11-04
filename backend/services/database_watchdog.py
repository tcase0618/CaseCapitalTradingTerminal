"""DB-independent, process-local notification for an unavailable ledger.

At most one delivery attempt per outage per process. A timed-out send can have
been accepted, so it is not retried until two healthy probes span 60 seconds.
This cannot promise cross-process or restart-persistent exactly-once delivery.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from .db import database_failure_reason, get_db

logger = logging.getLogger(__name__)


def _now() -> float:
    return time.monotonic()


async def _send_outage_alert() -> dict:
    from . import telegram_service
    enabled = os.environ.get("TELEGRAM_EVENTS_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled or not telegram_service._standalone_delivery_allowed("ops_alert"):
        return {"attempted": False, "sent": False, "reason": "disabled"}
    if not telegram_service._has_token() or not telegram_service._default_chat_id():
        return {"attempted": False, "sent": False, "reason": "not_configured"}
    # This fixed critical notice alone bypasses DB-backed delivery guards: the
    # ordinary send_message path cannot acquire those guards during this outage.
    text = ("CASE CAPITAL | PORTFOLIO DATABASE OUTAGE — CRITICAL\n"
            "Ledger reads failed. Terminal exit protection is UNVERIFIED.\n"
            "Check PostgreSQL/backend logs and broker-side protection. "
            "Database recovery and position coverage have not been confirmed.")
    payload = {"chat_id": telegram_service._default_chat_id(), "text": text, "disable_web_page_preview": True}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(telegram_service._api_url("sendMessage"), json=payload)
        sent = response.status_code == 200 and response.json().get("ok") is True
        return {"attempted": True, "sent": sent, "reason": "sent" if sent else "delivery_unverified"}
    except Exception:
        # Never log the token-bearing URL, chat identifier or raw exception.
        return {"attempted": True, "sent": False, "reason": "delivery_unverified"}


class OutageNotifier:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._attempted = False
        self._healthy_since: float | None = None

    async def observe(self, available: bool) -> dict:
        async with self._lock:
            if available:
                if self._healthy_since is None:
                    self._healthy_since = _now()
                elif _now() - self._healthy_since >= 60:
                    self._attempted = False
                return {"attempted": False, "sent": False, "reason": "healthy_probe"}
            self._healthy_since = None
            if self._attempted:
                return {"attempted": False, "sent": False, "reason": "outage_already_attempted"}
            self._attempted = True
            result = await _send_outage_alert()
            if not result["attempted"]:
                self._attempted = False
            else:
                logger.critical("Database outage: terminal protection unverified; notification=%s", result["reason"])
            return result


_notifier = OutageNotifier()


async def _read_database() -> None:
    db = get_db()
    await db.bot_state.find_one({"_id": "state"}, {"_id": 0})
    day = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    await db.claude_cache.count_documents({"date_key": day})
    await db.scan_results.find_one({}, {"_id": 0}, sort=[("finished_at", -1)])
    await db.watchlist.count_documents({})
    await db.alerts.count_documents({"triggered": False})


async def check() -> dict:
    reason = None
    try:
        await asyncio.wait_for(_read_database(), timeout=5.0)
    except Exception as exc:
        reason = database_failure_reason(exc)
    available = reason is None
    notification = await _notifier.observe(available)
    return {"db_available": available, "db_reason": reason, "notification": notification,
            "protection_status": "NOT_ASSESSED" if available else "UNVERIFIED"}
