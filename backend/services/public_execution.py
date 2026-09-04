"""Public equity execution path, kept behind an explicit live feature flag."""
from __future__ import annotations

import logging
import os
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from . import execution_safety, public_api, safety
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


def _strategy_attribution(row: dict[str, Any]) -> dict[str, Any]:
    scanner = row.get("strategy_scanner") or {}
    source_scan = row.get("source_scan")
    views = [view for view in (row.get("strategy_views") or []) if isinstance(view, dict)]
    view_lanes = [str(view.get("lane")) for view in views if view.get("lane")]
    view_screeners = [str(view.get("screener_id")) for view in views if view.get("screener_id")]
    lanes = row.get("strategy_lanes") or scanner.get("lanes") or view_lanes
    return {
        "strategy_id": row.get("strategy_id") or scanner.get("id") or scanner.get("name") or source_scan,
        "screener_id": row.get("screener_id") or scanner.get("screener_id") or scanner.get("id") or source_scan or (view_screeners[0] if view_screeners else None),
        "scanner_family": row.get("scanner_family") or scanner.get("family"),
        "strategy_lanes": list(dict.fromkeys(str(lane) for lane in lanes)) if isinstance(lanes, (list, tuple)) else ([str(lanes)] if lanes else []),
    }


def _positions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("positions") or payload.get("holdings") or []
    return rows if isinstance(rows, list) else []


def _qty(row: dict[str, Any]) -> float:
    # Trade-floor rows store the broker-confirmed holding in qty_remaining;
    # Public portfolio rows normally expose quantity/shares instead.
    return _num(row.get("qty_remaining") or row.get("quantity") or row.get("qty") or row.get("shares"))


def _quote_price(row: dict[str, Any]) -> float:
    return _num(row.get("ask") or row.get("askPrice") or row.get("last") or row.get("lastPrice"))


def _quote_timestamp(row: dict[str, Any]) -> Any:
    return row.get("quoteTime") or row.get("quote_time") or row.get("timestamp") or row.get("updatedAt")


def _stop_price(row: dict[str, Any]) -> float:
    """Accept the PM contract's ``stop`` field and legacy ``stop_price``."""
    return _num(row.get("stop") or row.get("stop_price"))


def _numeric_field(payload: Any, names: set[str]) -> float | None:
    """Find a positive account value across Public's changing response shapes."""
    normalized = {name.replace("_", "").lower() for name in names}
    if isinstance(payload, dict):
        priority = ("buyingpower", "availablecash", "cash")
        for wanted in priority:
            for key, value in payload.items():
                if wanted in normalized and str(key).replace("_", "").lower() == wanted:
                    number = _num(value, -1.0)
                    if number >= 0:
                        return number
        for key, value in payload.items():
            if str(key).replace("_", "").lower() in normalized:
                number = _num(value, -1.0)
                if number >= 0:
                    return number
        for value in payload.values():
            found = _numeric_field(value, names)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _numeric_field(value, names)
            if found is not None:
                return found
    return None


def _quotes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("quotes") or payload.get("results") or payload.get("data") or []
    if isinstance(rows, dict):
        return list(rows.values())
    return rows if isinstance(rows, list) else []


async def reconciliation_health(max_age_seconds: int = 900) -> dict[str, Any]:
    state = await get_db().bot_state.find_one({"_id": "public_reconciliation"}, {"_id": 0}) or {}
    checked_at = state.get("last_success_at")
    if not checked_at:
        return {"ok": False, "reason": "public_reconciliation_not_initialized"}
    try:
        parsed = datetime.fromisoformat(str(checked_at).replace("Z", "+00:00"))
        age = max(0, int((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()))
    except (TypeError, ValueError):
        return {"ok": False, "reason": "public_reconciliation_timestamp_invalid"}
    if age > max_age_seconds:
        return {"ok": False, "reason": "public_reconciliation_stale", "age_seconds": age}
    return {"ok": True, "age_seconds": age, "last_success_at": checked_at}


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
        health = await reconciliation_health()
        if not health.get("ok"):
            # Bootstrap the health record once after deployment. This performs
            # reconciliation only; it does not submit a buy order.
            try:
                await reconcile()
            except Exception as exc:
                logger.exception("Public reconciliation bootstrap failed")
                return {"skipped": False, "reason": "public_reconciliation_failed", "executed": [], "rejected": [{"ticker": "", "reason": "public_reconciliation_failed", "detail": str(exc)[:220]}]}
            health = await reconciliation_health()
            if not health.get("ok"):
                return {"skipped": False, "reason": health.get("reason") or "public_reconciliation_unhealthy", "executed": [], "rejected": [{"ticker": "", "reason": health.get("reason") or "public_reconciliation_unhealthy"}]}
        risk_allowed, safety_status = await execution_safety.add_risk_allowed("public_equity")
        if not risk_allowed:
            return {"skipped": False, "reason": safety_status.get("reason") or "safety_halt", "executed": [], "rejected": []}
        portfolio = await client.portfolio()
        account = await client.accounts()
        buying_power = _numeric_field(account, {"buying_power", "buyingPower", "cash", "available_cash", "availableCash"})
        if buying_power is None:
            buying_power = _numeric_field(portfolio, {"buying_power", "buyingPower", "cash", "available_cash", "availableCash"})
        if buying_power is None:
            return {"skipped": False, "reason": "public_buying_power_unavailable", "executed": [], "rejected": [{"ticker": "", "reason": "public_buying_power_unavailable"}], "rejection_reason_counts": {"public_buying_power_unavailable": 1}}
        from . import trading_halts
        try:
            halt_payload = await trading_halts.fetch_halts()
        except Exception as exc:
            logger.exception("Public halt feed check failed")
            return {"skipped": False, "reason": "public_halt_feed_unavailable", "executed": [], "rejected": [{"ticker": "", "reason": "public_halt_feed_unavailable", "detail": str(exc)[:220]}], "rejection_reason_counts": {"public_halt_feed_unavailable": 1}}
        if not halt_payload.get("ok"):
            return {"skipped": False, "reason": "public_halt_feed_unavailable", "executed": [], "rejected": [{"ticker": "", "reason": "public_halt_feed_unavailable"}], "rejection_reason_counts": {"public_halt_feed_unavailable": 1}}
        active_halts = {str(item.get("symbol") or "").upper() for item in halt_payload.get("halts") or [] if item.get("active")}
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
            if ticker in active_halts:
                rejected.append({"ticker": ticker, "reason": "public_symbol_actively_halted"})
                continue
            quote_row = quote_by_symbol.get(ticker) or {}
            fresh, age = safety.quote_is_fresh({"ts": _quote_timestamp(quote_row)})
            if not fresh:
                rejected.append({"ticker": ticker, "reason": "public_quote_stale_or_unverifiable", "age_seconds": age})
                continue
            if ticker in held:
                rejected.append({"ticker": ticker, "reason": "public_position_exists"})
                continue
            if amount > buying_power:
                rejected.append({"ticker": ticker, "reason": "public_buying_power_insufficient", "required_usd": amount, "available_usd": buying_power})
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
            attribution = _strategy_attribution(row)
            await get_db().tf_trades.insert_one(stamped({
                "client_order_id": client_id, "public_order_id": order_id, "broker_base": BROKER_BASE,
                "ticker": ticker, "instrument": "EQUITY", "notional": amount, "allocation_usd": amount,
                "limit_price": price, "pm_action": str(row.get("action") or "").upper(), "pm_score": row.get("pm_score"),
                "cycle_id": cycle_id, "status": "OPEN", "fill_status": "PENDING", "qty_remaining": 0.0,
                "current_stop": _stop_price(row), "pm_active_stop": _stop_price(row),
                "pm_ratchet_plan": row.get("ratchet_plan") or {"enabled": False}, "submitted_at": datetime.now(timezone.utc).isoformat(),
                "public_preflight": result.get("preflight"),
                **attribution,
                "strategy_attribution": attribution,
            }))
            await execution_safety.mark_execution_intent(client_id, "submitted", {"order_id": order_id, "broker": BROKER_BASE})
            executed.append({"ticker": ticker, "allocation_usd": amount, "limit_price": price, "order_id": order_id})
            held.add(ticker)
            buying_power -= amount
    await log_activity("Public equity execution completed", "info", {"executed": len(executed), "rejected": len(rejected)})
    return {"skipped": False, "executed": executed, "rejected": rejected, "rejection_reason_counts": dict(Counter(item.get("reason", "unknown") for item in rejected))}


async def reconcile() -> dict[str, Any]:
    if not enabled():
        return {"skipped": True, "reason": "public_live_equity_disabled"}
    db = get_db()
    async with public_api.PublicAPIClient() as client:
        pending = await db.tf_trades.find({"broker_base": BROKER_BASE, "status": "OPEN", "fill_status": "PENDING", "public_order_id": {"$exists": True}}, {"_id": 0}).to_list(500)
        order_updates = 0
        poll_errors = 0
        for trade in pending:
            ticker = _symbol(trade)
            submitted_at = trade.get("submitted_at")
            try:
                submitted_dt = datetime.fromisoformat(str(submitted_at).replace("Z", "+00:00")) if submitted_at else None
            except ValueError:
                submitted_dt = None
            ttl_seconds = max(60, int(_num(os.environ.get("PUBLIC_PENDING_ORDER_TTL_SECONDS"), 900)))
            if submitted_dt and (datetime.now(timezone.utc) - submitted_dt.astimezone(timezone.utc)).total_seconds() > ttl_seconds:
                try:
                    await client.cancel_order(str(trade.get("public_order_id")))
                    await db.tf_trades.update_one(
                        {"client_order_id": trade.get("client_order_id"), "broker_base": BROKER_BASE},
                        {"$set": {"status": "CLOSED", "fill_status": "EXPIRED_BY_TERMINAL", "qty_remaining": 0.0, "closed_at": datetime.now(timezone.utc).isoformat(), "close_reason": "public_pending_order_ttl"}},
                    )
                    await execution_safety.mark_execution_intent(trade.get("client_order_id"), "expired", {"reason": "public_pending_order_ttl"})
                    order_updates += 1
                except Exception:
                    logger.exception("Public stale-order cancellation failed for %s", ticker)
                    poll_errors += 1
                continue
            try:
                order = await client.get_order(str(trade.get("public_order_id")))
            except Exception:
                poll_errors += 1
                continue
            status = str(order.get("status") or "").upper()
            if status in {"FILLED", "PARTIALLY_FILLED"}:
                filled_qty = _num(order.get("filledQuantity") or order.get("filled_quantity"))
                update = {"fill_status": status, "qty_total": filled_qty, "qty_remaining": filled_qty, "filled_avg_price": _num(order.get("averagePrice") or order.get("average_price")), "filled_at": order.get("updatedAt") or order.get("updated_at") or datetime.now(timezone.utc).isoformat(), "last_order_status": status}
                stop = _num(trade.get("pm_active_stop") or trade.get("current_stop"))
                if filled_qty > 0 and stop > 0 and not trade.get("protective_order_id"):
                    stop_client_id = execution_safety.stable_client_order_id("public_protective", trade.get("client_order_id"), ticker, stop, prefix="public")
                    stop_claim = await execution_safety.claim_execution_intent(
                        scope="public_equity_exit",
                        client_order_id=stop_client_id,
                        symbol=ticker,
                        side="sell",
                        metadata={"stop": stop, "source_order_id": trade.get("public_order_id")},
                    )
                    if stop_claim.get("ok"):
                        try:
                            protective = await client.submit_equity_order(
                                symbol=ticker,
                                side="SELL",
                                quantity=filled_qty,
                                stop_price=stop,
                                limit_price=round(stop * 0.99, 2),
                                session="TWENTY_FOUR_HOURS",
                                client_order_id=stop_client_id,
                            )
                            protective_order = protective.get("order") or {}
                            protective_id = protective_order.get("orderId") or protective_order.get("id")
                            update.update({"protective_order_id": protective_id, "protective_order_status": "SUBMITTED", "protective_order_preflight": protective.get("preflight")})
                            await execution_safety.mark_execution_intent(stop_client_id, "submitted", {"order_id": protective_id, "broker": BROKER_BASE})
                        except Exception as exc:
                            update.update({"protective_order_status": "FAILED", "protective_order_error": str(exc)[:220]})
                            await execution_safety.mark_execution_intent(stop_client_id, "broker_rejected", {"error": str(exc)[:220]})
                await db.tf_trades.update_one({"client_order_id": trade.get("client_order_id"), "broker_base": BROKER_BASE}, {"$set": update})
                order_updates += 1
            elif status in {"CANCELLED", "REJECTED", "EXPIRED", "FAILED"}:
                await db.tf_trades.update_one({"client_order_id": trade.get("client_order_id"), "broker_base": BROKER_BASE}, {"$set": {"status": "CLOSED", "fill_status": status, "qty_remaining": 0.0, "closed_at": datetime.now(timezone.utc).isoformat(), "close_reason": f"public_order_{status.lower()}", "last_order_status": status}})
                order_updates += 1
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
    result = {"skipped": False, "ok": poll_errors == 0, "order_updates": order_updates, "updated": updated, "closed": closed, "poll_errors": poll_errors, "broker": BROKER_BASE}
    state_update = {"last_attempt_at": datetime.now(timezone.utc).isoformat(), "last_result": result}
    if poll_errors == 0:
        state_update["last_success_at"] = datetime.now(timezone.utc).isoformat()
    await db.bot_state.update_one({"_id": "public_reconciliation"}, {"$set": state_update}, upsert=True)
    return result


async def process_protective_exits() -> dict[str, Any]:
    """Submit a Public stop-limit when a reconciled position breaches its stop."""
    if not enabled():
        return {"skipped": True, "reason": "public_live_equity_disabled"}
    db = get_db()
    rows = await db.tf_trades.find({"broker_base": BROKER_BASE, "status": "OPEN", "fill_status": "FILLED", "qty_remaining": {"$gt": 0}}, {"_id": 0}).to_list(500)
    if not rows:
        return {"skipped": False, "checked": 0, "submitted": []}
    submitted: list[dict[str, Any]] = []
    async with public_api.PublicAPIClient() as client:
        quote_rows = _quotes(await client.quotes([_symbol(row) for row in rows]))
        by_symbol = {_symbol(row): row for row in quote_rows}
        for trade in rows:
            ticker = _symbol(trade)
            stop = _num(trade.get("pm_active_stop") or trade.get("current_stop"))
            current = _quote_price(by_symbol.get(ticker) or {})
            quantity = _qty(trade)
            if stop <= 0 or current <= 0 or quantity <= 0 or current > stop:
                continue
            client_id = execution_safety.stable_client_order_id("public_stop", trade.get("client_order_id"), ticker, stop, prefix="public")
            claim = await execution_safety.claim_execution_intent(scope="public_equity_exit", client_order_id=client_id, symbol=ticker, side="sell", metadata={"stop": stop})
            if not claim.get("ok"):
                continue
            try:
                result = await client.submit_equity_order(symbol=ticker, side="SELL", quantity=quantity, stop_price=stop, limit_price=round(stop * 0.99, 4), session="TWENTY_FOUR_HOURS", client_order_id=client_id)
                order = result.get("order") or {}
                submitted.append({"ticker": ticker, "order_id": order.get("orderId"), "stop": stop})
                await execution_safety.mark_execution_intent(client_id, "submitted", {"order_id": order.get("orderId")})
            except Exception as exc:
                await execution_safety.mark_execution_intent(client_id, "broker_rejected", {"error": str(exc)[:220]})
    return {"skipped": False, "checked": len(rows), "submitted": submitted}
