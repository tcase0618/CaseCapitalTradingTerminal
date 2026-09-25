"""Deterministic equity-book rotation for the Public execution account.

This service owns capital release, not protective exits.  Stops remain the
first line of defense in ``public_execution``.  A rebalance exits a weak
holding only when a materially stronger, live-authorized PM candidate exists;
the replacement buy waits for a broker-confirmed sale fill.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from . import execution_safety, portfolio_manager, public_api, public_execution, safety
from .db import get_db, stamped

BROKER_BASE = "public"


def _enabled() -> bool:
    return public_execution.enabled() and os.environ.get("PUBLIC_PM_REBALANCE_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _plan_is_fresh(plan: dict[str, Any]) -> bool:
    raw = plan.get("scan_finished_at") or plan.get("generated_at")
    try:
        generated = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if generated.tzinfo is None:
            generated = generated.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return False
    max_age = max(60, _int_env("PUBLIC_PM_REBALANCE_PLAN_MAX_AGE_SECONDS", 4 * 60 * 60))
    return (datetime.now(timezone.utc) - generated.astimezone(timezone.utc)).total_seconds() <= max_age


def _ticker(row: dict[str, Any]) -> str:
    return public_execution._symbol(row)


def _candidate_action(row: dict[str, Any]) -> str:
    return str(row.get("pre_execution_action") or row.get("action") or "").upper()


def _candidate_score(row: dict[str, Any]) -> float:
    """Comparable PM edge for incumbent-versus-replacement decisions."""
    return round(
        _num(row.get("pm_score"))
        + min(6.0, _num(row.get("case_score")) * 0.04)
        + min(4.0, _num(row.get("strategy_confidence")) * 4.0),
        2,
    )


def _eligible_candidate(row: dict[str, Any], held: set[str]) -> bool:
    ticker = _ticker(row)
    route = str(row.get("route") or row.get("preferred_route") or "").upper()
    return bool(
        ticker
        and ticker not in held
        and _candidate_action(row) in {"ACCUMULATE", "STARTER"}
        and route != "OPTION"
        and not bool(row.get("target_is_proxy"))
        and str(row.get("target_source") or "") != "thesis_lane_proxy_pending_validation"
        and public_execution._stop_price(row) > 0
    )


def build_rebalance_plan(pm_payload: dict[str, Any], positions: list[dict[str, Any]], portfolio_scores: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Create a deterministic, read-only replacement plan from one PM cycle."""
    recommendations = list(pm_payload.get("recommendations") or [])
    portfolio_scores = portfolio_scores or {}
    held = {_ticker(position) for position in positions if _ticker(position)}
    candidates = [row for row in recommendations if _eligible_candidate(row, held)]
    candidates.sort(key=lambda row: (_candidate_score(row), _num(row.get("pm_score"))), reverse=True)
    best = candidates[0] if candidates else None

    exit_loss = _float_env("PUBLIC_PM_REBALANCE_EXIT_LOSS_PCT", -7.0)
    review_loss = _float_env("PUBLIC_PM_REBALANCE_REVIEW_LOSS_PCT", -3.0)
    exit_edge_ceiling = _float_env("PUBLIC_PM_REBALANCE_EXIT_EDGE_MAX", 48.0)
    review_edge_ceiling = _float_env("PUBLIC_PM_REBALANCE_REVIEW_EDGE_MAX", 55.0)
    edge_gap_required = _float_env("PUBLIC_PM_REBALANCE_MIN_EDGE_GAP", 18.0)
    rec_by_ticker = {_ticker(row): row for row in recommendations if _ticker(row)}
    actions: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []

    for position in positions:
        ticker = _ticker(position)
        if not ticker or _num(position.get("quantity")) <= 0:
            continue
        incumbent = rec_by_ticker.get(ticker)
        pnl = portfolio_manager._position_unrealized_pct(position)
        holding_edge = portfolio_manager._holding_edge(position, incumbent)
        protected_winner = bool(pnl is not None and pnl >= 8 and holding_edge >= 50)
        scorecard = portfolio_scores.get(ticker) or {}
        review = {
            "ticker": ticker,
            "action": "HOLD",
            "reason": "no materially superior executable replacement",
            "unrealized_pct": round(pnl, 2) if pnl is not None else None,
            "holding_edge": holding_edge,
            "market_value": _num(position.get("market_value")),
            "quantity": _num(position.get("quantity")),
            "protected_winner": protected_winner,
            "portfolio_score": scorecard.get("portfolio_score"),
            "portfolio_state": scorecard.get("recommended_state"),
        }
        if not best:
            reviews.append(review)
            continue
        replacement_edge = _candidate_score(best)
        gap = round(replacement_edge - holding_edge, 2)
        if protected_winner:
            review.update({"reason": "protected winner retained unless independently invalidated", "replacement": _ticker(best), "edge_gap": gap})
        elif pnl is None:
            review.update({"reason": "unrealized performance unavailable; no discretionary sale", "replacement": _ticker(best), "edge_gap": gap})
        elif scorecard and scorecard.get("recommended_state") not in {"EXIT_REVIEW", "REPLACE_REVIEW"}:
            review.update({"reason": "portfolio-quality review retains holding; no automatic rotation", "replacement": _ticker(best), "edge_gap": gap})
        elif pnl <= exit_loss and holding_edge < exit_edge_ceiling and gap >= edge_gap_required:
            review.update({"action": "EXIT_AND_REPLACE", "reason": "weak loser fails PM edge threshold and stronger replacement clears edge gap", "replacement": _ticker(best), "edge_gap": gap})
            actions.append({**review, "candidate": best})
        elif pnl <= review_loss and holding_edge < review_edge_ceiling and gap >= edge_gap_required:
            review.update({"action": "EXIT_AND_REPLACE", "reason": "deteriorating holding releases capital to materially stronger replacement", "replacement": _ticker(best), "edge_gap": gap})
            actions.append({**review, "candidate": best})
        else:
            review.update({"replacement": _ticker(best), "edge_gap": gap})
        reviews.append(review)

    actions.sort(key=lambda row: (row["edge_gap"], -_num(row.get("unrealized_pct"))), reverse=True)
    return {
        "ok": True,
        "read_only": True,
        "scan_finished_at": pm_payload.get("scan_finished_at"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "policy": {
            "exit_loss_pct": exit_loss,
            "review_loss_pct": review_loss,
            "minimum_edge_gap": edge_gap_required,
            "protected_winners": "retain",
            "replacement_sequence": "sell fill confirmed before replacement buy",
            "portfolio_score_gate": "when present, only EXIT_REVIEW or REPLACE_REVIEW may rotate",
        },
        "best_candidate": _ticker(best) if best else None,
        "candidate_count": len(candidates),
        "position_reviews": reviews,
        "actions": actions[:_int_env("PUBLIC_PM_REBALANCE_MAX_EXITS_PER_CYCLE", 1)],
    }


async def _open_protective_tickers() -> set[str]:
    rows = await get_db().tf_trades.find(
        {"broker_base": BROKER_BASE, "status": "OPEN", "protective_order_id": {"$exists": True, "$ne": None}},
        {"_id": 0, "ticker": 1},
    ).to_list(500)
    return {_ticker(row) for row in rows if _ticker(row)}


async def _persist_action(action: dict[str, Any], cycle_key: str) -> dict[str, Any]:
    db = get_db()
    ticker, candidate = action["ticker"], action["candidate"]
    intent_id = f"public-rebalance:{cycle_key}:{ticker}:{_ticker(candidate)}"
    document = stamped({
        "intent_id": intent_id,
        "broker_base": BROKER_BASE,
        "status": "PLANNED",
        "sell_ticker": ticker,
        "sell_quantity": action["quantity"],
        "sell_market_value": action["market_value"],
        "holding_edge": action["holding_edge"],
        "unrealized_pct": action["unrealized_pct"],
        "edge_gap": action["edge_gap"],
        "reason": action["reason"],
        "candidate": candidate,
        "buy_ticker": _ticker(candidate),
        "cycle_key": cycle_key,
        "planned_at": datetime.now(timezone.utc).isoformat(),
    })
    await db.pm_rebalance_intents.update_one(
        {"intent_id": intent_id},
        {"$setOnInsert": document, "$set": {"last_reviewed_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )
    return await db.pm_rebalance_intents.find_one({"intent_id": intent_id}, {"_id": 0}) or document


async def _reconcile_intents(client: Any) -> dict[str, Any]:
    """Advance existing sell intents; only a confirmed fill can unlock a buy."""
    db = get_db()
    rows = await db.pm_rebalance_intents.find(
        {"broker_base": BROKER_BASE, "status": {"$in": ["SELL_SUBMITTED", "SELL_FILLED", "BUY_WAITING"]}},
        {"_id": 0},
    ).to_list(100)
    result = {"checked": len(rows), "sell_filled": 0, "replacement_submitted": [], "errors": []}
    for intent in rows:
        status = str(intent.get("status") or "")
        if status == "SELL_SUBMITTED":
            try:
                order = await client.get_order(str(intent.get("sell_order_id")))
            except Exception as exc:
                result["errors"].append({"intent_id": intent.get("intent_id"), "reason": f"sell_poll_failed:{exc.__class__.__name__}"})
                continue
            broker_status = str(order.get("status") or order.get("orderStatus") or "").upper()
            update = {"sell_order_status": broker_status, "last_checked_at": datetime.now(timezone.utc).isoformat()}
            if broker_status == "FILLED":
                filled = _num(order.get("filledQuantity") or order.get("filled_quantity"))
                if filled > 0:
                    update.update({"status": "SELL_FILLED", "sell_filled_qty": filled, "sell_fill_price": _num(order.get("averagePrice") or order.get("average_price")), "sell_filled_at": datetime.now(timezone.utc).isoformat()})
                    result["sell_filled"] += 1
            elif broker_status in {"CANCELLED", "CANCELED", "REJECTED", "EXPIRED", "FAILED"}:
                update.update({"status": "SELL_TERMINAL", "terminal_reason": f"sell_{broker_status.lower()}"})
            await db.pm_rebalance_intents.update_one({"intent_id": intent.get("intent_id")}, {"$set": update})
            continue
        if status not in {"SELL_FILLED", "BUY_WAITING"}:
            continue
        candidate = dict(intent.get("candidate") or {})
        if not candidate:
            await db.pm_rebalance_intents.update_one({"intent_id": intent.get("intent_id")}, {"$set": {"status": "BUY_TERMINAL", "terminal_reason": "candidate_missing"}})
            continue
        # Restore the original PM approval. Cash constraints are intentionally
        # re-evaluated by execute_pm_equity using the broker's current book.
        candidate["action"] = _candidate_action(candidate)
        candidate["allocation_usd"] = max(6.0, _num(candidate.get("pre_execution_allocation_usd") or candidate.get("allocation_usd"), 6.0))
        buy = await public_execution.execute_pm_equity([candidate], cycle_id=f"rebalance:{intent.get('intent_id')}")
        submitted = (buy.get("executed") or [])
        if submitted:
            await db.pm_rebalance_intents.update_one({"intent_id": intent.get("intent_id")}, {"$set": {"status": "BUY_SUBMITTED", "buy_order": submitted[0], "buy_submitted_at": datetime.now(timezone.utc).isoformat()}})
            result["replacement_submitted"].append(submitted[0])
        else:
            await db.pm_rebalance_intents.update_one({"intent_id": intent.get("intent_id")}, {"$set": {"status": "BUY_WAITING", "buy_last_rejection": (buy.get("rejected") or [])[:3], "last_checked_at": datetime.now(timezone.utc).isoformat()}})
    return result


async def run_rebalance_cycle() -> dict[str, Any]:
    """Run one capital-management tick from the persisted latest PM plan."""
    if not _enabled():
        return {"skipped": True, "reason": "public_pm_rebalance_disabled"}
    if public_execution._public_session_now() != "CORE":
        return {"skipped": True, "reason": "public_pm_rebalance_core_session_only"}
    db = get_db()
    pm_doc = await db.portfolio_manager_history.find_one({}, {"_id": 0}, sort=[("generated_at", -1)])
    if not pm_doc:
        return {"skipped": True, "reason": "pm_plan_unavailable"}
    if not _plan_is_fresh(pm_doc):
        return {"skipped": True, "reason": "pm_plan_stale_for_rebalance"}
    async with public_api.PublicAPIClient(use_sdk=False) as client:
        reconciled = await _reconcile_intents(client)
        state = await public_execution.portfolio_state()
        if not state.get("ok"):
            return {"skipped": True, "reason": state.get("reason") or "public_portfolio_unavailable", "reconciled": reconciled}
        latest_scores = await db.bot_state.find_one({"_id": "pm_portfolio_latest"}, {"_id": 0}) or {}
        score_map = {str(row.get("ticker") or "").upper(): row for row in latest_scores.get("holdings") or []}
        plan = build_rebalance_plan(pm_doc, state.get("positions") or [], score_map)
        protected = await _open_protective_tickers()
        submitted: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        cycle_key = str(pm_doc.get("scan_finished_at") or pm_doc.get("generated_at") or "unknown")
        for action in plan.get("actions") or []:
            ticker = action["ticker"]
            if ticker in protected:
                skipped.append({"ticker": ticker, "reason": "active_protective_order_preserved"})
                continue
            intent = await _persist_action(action, cycle_key)
            if intent.get("status") != "PLANNED":
                skipped.append({"ticker": ticker, "reason": f"intent_{str(intent.get('status')).lower()}"})
                continue
            quote_rows = public_execution._quotes(await client.quotes([ticker]))
            quote = next((row for row in quote_rows if _ticker(row) == ticker), quote_rows[0] if quote_rows else {})
            price = public_execution._quote_price(quote)
            fresh, age = safety.quote_is_fresh({"ts": public_execution._quote_timestamp(quote)})
            if not fresh or price <= 0:
                skipped.append({"ticker": ticker, "reason": "rebalance_quote_stale_or_unverifiable", "age_seconds": age})
                continue
            client_id = execution_safety.stable_client_order_id("public_rebalance_sell", intent.get("intent_id"), ticker, action["quantity"], round(price, 4), prefix="public")
            claim = await execution_safety.claim_execution_intent(scope="public_equity_rebalance_exit", client_order_id=client_id, symbol=ticker, side="sell", metadata={"intent_id": intent.get("intent_id"), "reason": action["reason"]})
            if not claim.get("ok"):
                skipped.append({"ticker": ticker, "reason": claim.get("reason") or "duplicate_execution_intent"})
                continue
            try:
                result = await client.submit_equity_order(symbol=ticker, side="SELL", quantity=action["quantity"], limit_price=price, time_in_force="DAY", session="CORE", client_order_id=client_id)
                order = result.get("order") or {}
                order_id = order.get("orderId") or order.get("id")
                if not order_id:
                    raise RuntimeError("Public rebalance sell response missing order id")
                await db.pm_rebalance_intents.update_one({"intent_id": intent.get("intent_id")}, {"$set": {"status": "SELL_SUBMITTED", "sell_order_id": order_id, "sell_client_order_id": client_id, "sell_limit_price": price, "sell_preflight": result.get("preflight"), "sell_submitted_at": datetime.now(timezone.utc).isoformat()}})
                await execution_safety.mark_execution_intent(client_id, "submitted", {"order_id": order_id, "broker": BROKER_BASE})
                submitted.append({"ticker": ticker, "order_id": order_id, "replacement": action["replacement"], "reason": action["reason"]})
            except Exception as exc:
                await execution_safety.mark_execution_intent(client_id, "broker_rejected", {"error": str(exc)[:220]})
                skipped.append({"ticker": ticker, "reason": "public_rebalance_sell_failed", "detail": str(exc)[:220]})
    return {"skipped": False, "plan": plan, "reconciled": reconciled, "submitted": submitted, "skipped_actions": skipped}
