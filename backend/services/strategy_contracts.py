"""Evidence-based shadow contracts for strategy-specific PM decisions.

This is intentionally a research layer.  It records how a strategy-specific
target, holding window, and option contract *would* be assessed from the
frozen PM packet.  It never changes a PM recommendation, order, stop, or exit.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from .db import get_db, stamped
from . import pnl_tracker, strategy_lifecycle


SHADOW_CONTRACT_VERSION = "strategy-shadow-contract-v1"
MIN_DTE_BUFFER = 7


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _strategy_id(row: dict[str, Any]) -> str:
    scanner = row.get("strategy_scanner") if isinstance(row.get("strategy_scanner"), dict) else {}
    return str(
        row.get("strategy_id")
        or row.get("screener_id")
        or row.get("source_scan")
        or scanner.get("screener_id")
        or "CORE"
    ).strip().lower()


def _target_assessment(row: dict[str, Any]) -> dict[str, Any]:
    entry = _num(row.get("price") or row.get("entry_price"))
    target = _num(row.get("target"))
    source = str(row.get("target_source") or "missing")
    if bool(row.get("target_is_proxy")) or "proxy" in source.lower():
        return {"status": "PROXY_REJECTED", "entry_price": entry or None, "target_price": target or None, "source": source}
    if entry <= 0 or target <= 0:
        return {"status": "INSUFFICIENT", "entry_price": entry or None, "target_price": target or None, "source": source}
    direction = str(row.get("direction") or row.get("options_direction") or "BULLISH").upper()
    valid_direction = target < entry if direction in {"BEARISH", "PUT"} else target > entry
    if not valid_direction:
        return {"status": "INVALID_DIRECTION", "entry_price": entry, "target_price": target, "source": source}
    return {"status": "VERIFIED", "entry_price": entry, "target_price": target, "source": source}


def _expiration_days(instrument: dict[str, Any], observed_at: str | None) -> int | None:
    value = instrument.get("expiration") or instrument.get("expiration_date") or instrument.get("expiry")
    if not value:
        return None
    try:
        expiry = date.fromisoformat(str(value)[:10])
        observed = datetime.fromisoformat(str(observed_at or "").replace("Z", "+00:00")).date()
        return (expiry - observed).days
    except (TypeError, ValueError):
        return None


def _contract_assessment(
    row: dict[str, Any], lifecycle: dict[str, Any], target: dict[str, Any], ticket: dict[str, Any] | None, observed_at: str | None,
) -> dict[str, Any]:
    if not ticket:
        return {"status": "NO_OPTIONS_DESK_TICKET", "execution_effect": "none"}
    instrument = ticket.get("instrument") if isinstance(ticket.get("instrument"), dict) else {}
    if not instrument:
        return {"status": "NO_SELECTED_INSTRUMENT", "desk_blockers": ticket.get("blocked_reasons") or [], "execution_effect": "none"}
    dte = _expiration_days(instrument, observed_at)
    min_dte = (lifecycle.get("max_hold_trading_days") or 0) + MIN_DTE_BUFFER
    result = {
        "status": "INSUFFICIENT",
        "symbol": instrument.get("symbol") or instrument.get("contractSymbol"),
        "kind": instrument.get("kind") or "single_leg",
        "option_type": instrument.get("type") or instrument.get("option_type") or instrument.get("right"),
        "strike": _num(instrument.get("strike") or instrument.get("strike_price")) or None,
        "expiration": instrument.get("expiration") or instrument.get("expiration_date") or instrument.get("expiry"),
        "days_to_expiration": dte,
        "required_min_dte": min_dte,
        "desk_ready": bool(ticket.get("manual_fire_ready")),
        "desk_blockers": ticket.get("blocked_reasons") or [],
        "match_scope": "ticker_only",
        "execution_effect": "none",
    }
    if target.get("status") != "VERIFIED":
        result["status"] = "TARGET_NOT_VERIFIED"
        return result
    if dte is None:
        result["status"] = "EXPIRATION_UNVERIFIABLE"
        return result
    if dte < min_dte:
        result["status"] = "EXPIRATION_TOO_SHORT"
        return result
    strike = _num(result["strike"])
    option_type = str(result["option_type"] or "").upper()
    direction = str(row.get("direction") or row.get("options_direction") or "BULLISH").upper()
    target_price = _num(target.get("target_price"))
    reachable = (
        not strike
        or not option_type
        or (option_type.startswith("C") and direction not in {"BEARISH", "PUT"} and target_price > strike)
        or (option_type.startswith("P") and direction in {"BEARISH", "PUT"} and target_price < strike)
    )
    if not reachable:
        result["status"] = "TARGET_NOT_REACHABLE_BY_SELECTED_CONTRACT"
        return result
    result["status"] = "SHADOW_VALID"
    return result


def _ticket_map(options_payload: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    tickets: dict[str, dict[str, Any]] = {}
    for ticket in (options_payload or {}).get("candidates") or []:
        ticker = str(ticket.get("ticker") or "").upper()
        if ticker and ticker not in tickets:
            tickets[ticker] = ticket
    return tickets


async def build_shadow_contracts(
    pm_recommendations: list[dict[str, Any]],
    *,
    options_payload: dict[str, Any] | None,
    cycle_id: str,
    observed_at: str | None,
    persist: bool = True,
) -> dict[str, Any]:
    """Build immutable, forward-only contracts from the frozen PM output."""
    tickets = _ticket_map(options_payload)
    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for row in pm_recommendations:
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            continue
        strategy_id = _strategy_id(row)
        action = str(row.get("action") or "WATCH").upper()
        lifecycle = row.get("lifecycle_plan") if isinstance(row.get("lifecycle_plan"), dict) else strategy_lifecycle.plan_for(row, action=action)
        target = _target_assessment(row)
        ticket = tickets.get(ticker)
        contract = _contract_assessment(row, lifecycle, target, ticket, observed_at)
        observation_id = pnl_tracker._observation_id(cycle_id=cycle_id, ticker=ticker, screener_id=strategy_id)
        status = "SHADOW_ONLY" if action in {"ACCUMULATE", "STARTER"} else "NOT_ACTIONABLE"
        shadow = stamped({
            "contract_id": f"{observation_id}:shadow",
            "observation_id": observation_id,
            "cycle_id": cycle_id,
            "observed_at": observed_at,
            "version": SHADOW_CONTRACT_VERSION,
            "status": status,
            "execution_effect": "none",
            "ticker": ticker,
            "strategy_id": strategy_id,
            "scanner_family": row.get("scanner_family"),
            "action": action,
            "pm_score": row.get("pm_score"),
            "lifecycle": lifecycle,
            "target_assessment": target,
            "option_contract_assessment": contract,
        })
        counts[contract["status"]] = counts.get(contract["status"], 0) + 1
        rows.append(shadow)

    if persist:
        db = get_db()
        await db.strategy_shadow_contracts.delete_many({})
        if rows:
            await db.strategy_shadow_contracts.insert_many(rows)
            await db.strategy_shadow_contract_history.insert_many([
                stamped({**row, "history_recorded_at": observed_at, "history_source": "full_terminal_cycle"}) for row in rows
            ])
    return {
        "version": SHADOW_CONTRACT_VERSION,
        "status": "SHADOW_ONLY",
        "execution_effect": "none",
        "generated_at": observed_at,
        "summary": {"total": len(rows), "contract_statuses": counts},
        "contracts": rows,
    }


async def latest(limit: int = 200) -> dict[str, Any]:
    rows = await get_db().strategy_shadow_contracts.find({}, {"_id": 0}).sort("pm_score", -1).to_list(limit)
    return {
        "version": SHADOW_CONTRACT_VERSION,
        "status": "SHADOW_ONLY",
        "execution_effect": "none",
        "summary": {"total": len(rows), "contract_statuses": {status: sum(1 for row in rows if (row.get("option_contract_assessment") or {}).get("status") == status) for status in sorted({(row.get("option_contract_assessment") or {}).get("status", "UNKNOWN") for row in rows})}},
        "contracts": rows,
    }
