"""Immutable, cross-desk execution funnel records.

This module is observability only. It translates outputs that already exist in
the PM, Public equity, and Alpaca options paths into a single terminal state
per PM recommendation. It deliberately does not alter routing or execution.
"""
from __future__ import annotations

import hashlib
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from .db import get_db, stamped


FUNNEL_VERSION = "execution-funnel-v1"
_APPROVED = {"ACCUMULATE", "STARTER"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ticker(value: Any) -> str:
    return str((value or {}).get("ticker") or "").upper() if isinstance(value, dict) else ""


def _strategy_id(row: dict[str, Any]) -> str:
    return str(row.get("source_scan") or row.get("strategy_id") or "CORE").strip().lower()


def _first_by_ticker(rows: Any) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(rows, list):
        return result
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        ticker = _ticker(row)
        if ticker and ticker not in result:
            result[ticker] = row
    return result


def _options_ticket_by_ticker(options_payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return _first_by_ticker((options_payload or {}).get("candidates"))


def _route(row: dict[str, Any], ticket: dict[str, Any] | None) -> str:
    explicit = str(row.get("route") or row.get("preferred_route") or "").upper()
    if explicit in {"OPTION", "BOTH", "EQUITY"}:
        return explicit
    if ticket and str(ticket.get("route") or "").upper() in {"OPTION", "BOTH"}:
        return str(ticket.get("route")).upper()
    return "EQUITY"


def _event_id(cycle_id: str, ticker: str, strategy_id: str) -> str:
    raw = f"{cycle_id}|{ticker}|{strategy_id}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:32]


def build_cycle(
    *,
    cycle_id: str,
    pm_recommendations: list[dict[str, Any]],
    equity_execution: dict[str, Any] | None,
    options_payload: dict[str, Any] | None,
    options_execution: dict[str, Any] | None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Create a terminal state for every PM decision in a frozen cycle."""
    equity_execution = equity_execution or {}
    options_execution = options_execution or {}
    equity_submitted = _first_by_ticker(equity_execution.get("executed"))
    equity_rejected = _first_by_ticker(equity_execution.get("rejected"))
    option_submitted = _first_by_ticker(options_execution.get("submitted"))
    option_skipped = _first_by_ticker(options_execution.get("skipped"))
    tickets = _options_ticket_by_ticker(options_payload)
    events: list[dict[str, Any]] = []

    for row in pm_recommendations or []:
        ticker = _ticker(row)
        if not ticker:
            continue
        action = str(row.get("action") or "WATCH").upper()
        strategy_id = _strategy_id(row)
        ticket = tickets.get(ticker)
        route = _route(row, ticket)
        state = "PM_NOT_APPROVED"
        reason = f"pm_action_{action.lower()}"
        broker_state = "NOT_ATTEMPTED"

        if action in _APPROVED:
            state = "PM_APPROVED"
            reason = None
            if route in {"OPTION", "BOTH"}:
                if ticker in option_submitted:
                    state, broker_state = "ORDER_SUBMITTED", "SUBMITTED"
                elif ticker in option_skipped:
                    state, broker_state = "OPTIONS_BLOCKED", "NOT_SUBMITTED"
                    reason = option_skipped[ticker].get("reason") or "options_execution_skipped"
                elif ticket and ticket.get("blocked_reasons"):
                    state, broker_state = "OPTIONS_NOT_READY", "NOT_SUBMITTED"
                    reason = "; ".join(str(x) for x in ticket.get("blocked_reasons") or [])
                elif options_execution.get("skipped"):
                    state, broker_state = "OPTIONS_EXECUTION_SKIPPED", "NOT_ATTEMPTED"
                    reason = options_execution.get("reason") or "options_execution_skipped"
                elif options_execution.get("reason") and not options_execution.get("submitted"):
                    state, broker_state = "OPTIONS_EXECUTION_BLOCKED", "NOT_ATTEMPTED"
                    reason = options_execution.get("reason")
                elif not ticket:
                    state, broker_state = "OPTIONS_TICKET_MISSING", "NOT_ATTEMPTED"
                    reason = "options_desk_ticket_missing"
            else:
                if ticker in equity_submitted:
                    state, broker_state = "ORDER_SUBMITTED", "SUBMITTED"
                elif ticker in equity_rejected:
                    state, broker_state = "EQUITY_BLOCKED", "NOT_SUBMITTED"
                    reason = equity_rejected[ticker].get("reason") or "equity_execution_rejected"
                elif equity_execution.get("skipped"):
                    state, broker_state = "EQUITY_EXECUTION_SKIPPED", "NOT_ATTEMPTED"
                    reason = equity_execution.get("reason") or "equity_execution_skipped"
                elif equity_execution.get("reason") and not equity_execution.get("executed"):
                    state, broker_state = "EQUITY_EXECUTION_BLOCKED", "NOT_ATTEMPTED"
                    reason = equity_execution.get("reason")
                else:
                    state, broker_state = "EQUITY_NOT_ATTEMPTED", "NOT_ATTEMPTED"
                    reason = "missing_equity_execution_outcome"

        event = stamped({
            "event_id": _event_id(cycle_id, ticker, strategy_id),
            "cycle_id": cycle_id,
            "observed_at": observed_at or _now(),
            "version": FUNNEL_VERSION,
            "ticker": ticker,
            "strategy_id": strategy_id,
            "scanner_family": row.get("scanner_family"),
            "pm_action": action,
            "pm_score": row.get("pm_score"),
            "allocation_usd": row.get("allocation_usd"),
            "intended_route": route,
            "terminal_state": state,
            "terminal_reason": reason,
            "broker_state": broker_state,
            "equity_result": equity_submitted.get(ticker) or equity_rejected.get(ticker),
            "options_ticket": ticket,
            "options_result": option_submitted.get(ticker) or option_skipped.get(ticker),
            "execution_effect": "none",
        })
        events.append(event)

    state_counts = Counter(str(event["terminal_state"]) for event in events)
    reason_counts = Counter(str(event["terminal_reason"]) for event in events if event.get("terminal_reason"))
    return {
        "version": FUNNEL_VERSION,
        "cycle_id": cycle_id,
        "observed_at": observed_at or _now(),
        "summary": {
            "pm_docket": len(events),
            "pm_approved": sum(1 for event in events if event["pm_action"] in _APPROVED),
            "state_counts": dict(state_counts),
            "reason_counts": dict(reason_counts),
        },
        "events": events,
    }


async def persist(payload: dict[str, Any]) -> None:
    db = get_db()
    events = payload.get("events") or []
    for event in events:
        await db.execution_funnel_events.update_one(
            {"event_id": event["event_id"]}, {"$setOnInsert": event}, upsert=True,
        )
    cycle_doc = stamped({
        "cycle_id": payload.get("cycle_id"),
        "observed_at": payload.get("observed_at"),
        "version": payload.get("version"),
        "summary": payload.get("summary") or {},
    })
    await db.execution_funnel_cycles.update_one(
        {"cycle_id": cycle_doc["cycle_id"]}, {"$set": cycle_doc}, upsert=True,
    )


async def latest(limit: int = 200) -> dict[str, Any]:
    db = get_db()
    events = await db.execution_funnel_events.find({}, {"_id": 0}).sort("observed_at", -1).to_list(max(1, min(limit, 1000)))
    return {
        "version": FUNNEL_VERSION,
        "summary": {
            "events": len(events),
            "state_counts": dict(Counter(str(event.get("terminal_state") or "UNKNOWN") for event in events)),
            "reason_counts": dict(Counter(str(event.get("terminal_reason")) for event in events if event.get("terminal_reason"))),
        },
        "events": events,
    }
