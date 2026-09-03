"""Public equity execution path, kept behind an explicit live feature flag."""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from . import execution_safety, public_api
from .db import get_db, log_activity, stamped

logger = logging.getLogger(__name__)
BROKER_BASE = "public"
ALLOCATIONS = (2.0, 4.0, 6.0)


def enabled() -> bool:
    cfg = public_api.config()
    return bool(cfg.enabled and cfg.account_id and cfg.access_token and cfg.live_equity_enabled and not cfg.research_only)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _allocation(row: dict[str, Any]) -> float:
    value = _num(row.get("allocation_usd"))
    return min(ALLOCATIONS, key=lambda item: abs(item - value)) if value > 0 else 0.0


def _symbol(row: dict[str, Any]) -> str:
    instrument = row.get("instrument") or {}
    return str(row.get("symbol") or row.get("ticker") or instrument.get("symbol") or "").upper().strip()


def _positions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("positions") or payload.get("holdings") or []
    return rows if isinstance(rows, list) else []


def _qty(row: dict[str, Any]) -> float:
    return _num(row.get("quantity") or row.get("qty") or row.get("shares"))


def _quote_price(row: dict[str, Any]) -> float:
    return _num(row.get("ask") or row.get("askPrice") or row.get("last") or row.get("lastPrice"))


def _quotes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("quotes") or payload.get("results") or payload.get("data") or []
    if isinstance(rows, dict):
        return list(rows.values())
    return rows if isinstance(rows, list) else []


async def execute_pm_equity(pm_rows: list[dict[str, Any]], *, cycle_id: str | None = None) -> dict[str, Any]:
    """Submit only PM-approved equity rows, with Public preflight first."""
    if not enabled():
        return {"skipped": True, "reason": "public_live_equity_disabled", "executed": [], "rejected": []}
    approved = [row for row in pm_rows if str(row.get("action") or "").upper() in {"ACCUMULATE", "STARTER"} and _allocation(row) > 0]
    executed: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    if not approved:
        return {"skipped": False, "reason": "no_pm_equity_approvals", "executed": [], "rejected": []}
    async with public_api.PublicAPIClient() as client:
        portfolio = await client.portfolio()
        held = {_symbol(row) for row in _positions(portfolio)}
        quote_rows = _quotes(await client.quotes([_symbol(row) for row in approved]))
        quote_by_symbol = {_symbol(row): row for row in quote_rows}
        for row in approved:
            ticker = _symbol(row)
            amount = _allocation(row)
            price = _quote_price(quote_by_symbol.get(ticker) or {})
            if not ticker or price <= 0:
                rejected.append({"ticker": ticker, "reason": "public_quote_unavailable"})
                continue
            if ticker in held:
                rejected.append({"ticker": ticker, "reason": "public_position_exists"})
                continue
            client_id = execution_safety.stable_client_order_id("public_pm", cycle_id or "", ticker, amount, round(price, 4), prefix="public")
            claim = await execution_safety.claim_execution_intent(scope="public_equity", client_order_id=client_id, symbol=ticker, side="buy", metadata={"amount": amount, "price": price, "cycle_id": cycle_id})
            if not claim.get("ok"):
                rejected.append({"ticker": ticker, "reason": claim.get("reason") or "duplicate_execution_intent"})
                continue
            try:
                result = await client.submit_equity_order(symbol=ticker, side="BUY", amount=amount, limit_price=price, session="TWENTY_FOUR_HOURS", client_order_id=client_id)
            except Exception as exc:
                await execution_safety.mark_execution_intent(client_id, "broker_rejected", {"error": str(exc)[:220]})
                rejected.append({"ticker": ticker, "reason": "public_preflight_or_submission_failed", "detail": str(exc)[:220]})
                continue
            order = result.get("order") or {}
            order_id = order.get("orderId") or order.get("id")
            await get_db().tf_trades.insert_one(stamped({
                "client_order_id": client_id, "public_order_id": order_id, "broker_base": BROKER_BASE,
                "ticker": ticker, "instrument": "EQUITY", "notional": amount, "allocation_usd": amount,
                "limit_price": price, "pm_action": str(row.get("action") or "").upper(), "pm_score": row.get("pm_score"),
                "cycle_id": cycle_id, "status": "OPEN", "fill_status": "PENDING", "qty_remaining": 0.0,
                "current_stop": _num(row.get("stop_price")), "pm_active_stop": _num(row.get("stop_price")),
                "pm_ratchet_plan": row.get("ratchet_plan") or {"enabled": False}, "submitted_at": datetime.now(timezone.utc).isoformat(),
                "public_preflight": result.get("preflight"),
            }))
            await execution_safety.mark_execution_intent(client_id, "submitted", {"order_id": order_id, "broker": BROKER_BASE})
            executed.append({"ticker": ticker, "allocation_usd": amount, "limit_price": price, "order_id": order_id})
            held.add(ticker)
    await log_activity("Public equity execution completed", "info", {"executed": len(executed), "rejected": len(rejected)})
    return {"skipped": False, "executed": executed, "rejected": rejected, "rejection_reason_counts": dict(Counter(item.get("reason", "unknown") for item in rejected))}


async def reconcile() -> dict[str, Any]:
    if not enabled():
        return {"skipped": True, "reason": "public_live_equity_disabled"}
    db = get_db()
    async with public_api.PublicAPIClient() as client:
        positions = {_symbol(row): row for row in _positions(await client.portfolio())}
        rows = await db.tf_trades.find({"broker_base": BROKER_BASE, "status": "OPEN"}, {"_id": 0}).to_list(500)
        updated = closed = 0
        for trade in rows:
            ticker = _symbol(trade)
            position = positions.get(ticker)
            if position:
                qty = _qty(position)
                await db.tf_trades.update_one({"client_order_id": trade.get("client_order_id"), "broker_base": BROKER_BASE}, {"$set": {"fill_status": "FILLED" if qty > 0 else trade.get("fill_status", "PENDING"), "qty_remaining": qty, "qty_total": max(qty, _num(trade.get("qty_total"))), "last_synced_at": datetime.now(timezone.utc).isoformat()}})
                updated += 1
            elif trade.get("fill_status") == "FILLED":
                await db.tf_trades.update_one({"client_order_id": trade.get("client_order_id"), "broker_base": BROKER_BASE}, {"$set": {"status": "CLOSED", "qty_remaining": 0.0, "closed_at": datetime.now(timezone.utc).isoformat(), "close_reason": "public_position_absent"}})
                closed += 1
    return {"skipped": False, "updated": updated, "closed": closed, "broker": BROKER_BASE}
