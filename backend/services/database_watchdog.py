"""DB-independent, process-local notification for an unavailable ledger.

At most one delivery attempt per outage per process. A timed-out send can have
been accepted, so it is not retried until two healthy probes span 60 seconds.
This cannot promise cross-process or restart-persistent exactly-once delivery.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
import logging
import os
import time

import httpx

from .db import database_failure_reason, get_db

logger = logging.getLogger(__name__)
_probe_details: ContextVar[dict | None] = ContextVar("database_probe_details", default=None)


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
    text = ("CASE CAPITAL | LEDGER READ FAILURE - CRITICAL\n"
            "A critical ledger read failed or timed out; PostgreSQL may still be online.\n"
            "Terminal exit protection is UNVERIFIED. Check backend query diagnostics "
            "and broker-side protection; no database crash or recovery is implied.")
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
    # Do not fetch full scan payloads or count display/research collections to
    # decide whether the position ledger is reachable during protective work.
    for stage, operation in [
        ("bot_state", lambda: db.bot_state.find_one({"_id": "state"}, {"_id": 0})),
        ("tf_trades", lambda: db.tf_trades.find_one({}, {"_id": 0, "client_order_id": 1})),
    ]:
        details = _probe_details.get()
        started = _now()
        if details is not None:
            details["stage"] = stage
        try:
            await operation()
        finally:
            if details is not None:
                details["stage_timings_ms"][stage] = round((_now() - started) * 1000, 2)


async def check() -> dict:
    reason = None
    details = {"stage": "initialization", "stage_timings_ms": {}}
    context = _probe_details.set(details)
    try:
        await asyncio.wait_for(_read_database(), timeout=5.0)
    except Exception as exc:
        reason = database_failure_reason(exc)
        logger.warning("Critical ledger probe failed: stage=%s reason=%s timings_ms=%s",
                       details["stage"], reason, details["stage_timings_ms"])
    finally:
        _probe_details.reset(context)
    available = reason is None
    notification = await _notifier.observe(available)
    return {"db_available": available, "db_reason": reason, "notification": notification,
            "failure_scope": "critical_ledger_read" if reason else None,
            "failed_stage": details["stage"] if reason else None,
            "stage_timings_ms": details["stage_timings_ms"],
            "protection_status": "NOT_ASSESSED" if available else "UNVERIFIED"}
