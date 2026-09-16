"""Five-minute, evidence-backed Portfolio Manager book review.

This module deliberately separates *portfolio judgement* from broker execution.
It scores the actual broker book and ranks the current PM opportunity set, then
persists a replayable snapshot.  ``pm_rebalance`` may consume the conservative
REPLACE_REVIEW state, but this service never submits an order itself.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import portfolio_manager, public_execution
from .db import get_db, stamped

BROKER_BASE = "public"
SCORE_VERSION = "pm-portfolio-v1"


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _pct(value: Any) -> float | None:
    if value is None:
        return None
    result = _num(value, float("nan"))
    if result != result:
        return None
    # Public's normalized portfolio adapter already returns percentage units.
    # Do not infer fractions here: a small real gain such as 0.28% must remain
    # 0.28 rather than becoming 28%.
    return result


def _ticker(row: dict[str, Any]) -> str:
    return public_execution._symbol(row)


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return round(max(low, min(high, value)), 1)


def _action(row: dict[str, Any]) -> str:
    return str(row.get("pre_execution_action") or row.get("action") or "").upper()


def candidate_edge(row: dict[str, Any]) -> float:
    """Comparable, bounded candidate quality used only for replacement ranking."""
    pm = _clamp(_num(row.get("pm_score")))
    case = _clamp(_num(row.get("case_score")))
    raw_confidence = _num(row.get("strategy_confidence"))
    confidence = _clamp(raw_confidence * 100.0 if 0.0 <= raw_confidence <= 1.0 else raw_confidence)
    rr = _num(row.get("risk_reward"))
    rr_component = _clamp(rr * 20.0) if rr > 0 else 35.0
    return round(pm * 0.55 + case * 0.15 + confidence * 0.15 + rr_component * 0.15, 1)


def _executable_candidate(row: dict[str, Any], held: set[str]) -> tuple[bool, str | None]:
    ticker = _ticker(row)
    if not ticker or ticker in held:
        return False, "already_held_or_missing_symbol"
    if _action(row) not in {"ACCUMULATE", "STARTER"}:
        return False, "pm_not_actionable"
    if str(row.get("route") or row.get("preferred_route") or "").upper() == "OPTION":
        return False, "options_route_not_an_equity_replacement"
    if row.get("equity_execution_blocker"):
        return False, "equity_execution_blocked"
    if bool(row.get("target_is_proxy")):
        return False, "target_is_proxy"
    if public_execution._stop_price(row) <= 0:
        return False, "missing_stop"
    return True, None


def build_opportunity_queue(recommendations: list[dict[str, Any]], held: set[str]) -> list[dict[str, Any]]:
    """Rank replacements and retain exclusions for an auditable funnel."""
    queue: list[dict[str, Any]] = []
    for row in recommendations:
        if not isinstance(row, dict):
            continue
        eligible, reason = _executable_candidate(row, held)
        queue.append({
            "ticker": _ticker(row),
            "candidate_edge": candidate_edge(row),
            "pm_action": _action(row),
            "pm_score": _num(row.get("pm_score")),
            "allocation_usd": _num(row.get("pre_execution_allocation_usd") or row.get("allocation_usd")),
            "strategy_lanes": list(row.get("strategy_lanes") or []),
            "executable": eligible,
            "exclusion_reason": reason,
            "recommendation": row,
        })
    queue.sort(key=lambda item: (item["executable"], item["candidate_edge"], item["pm_score"]), reverse=True)
    for rank, item in enumerate((item for item in queue if item["executable"]), start=1):
        item["rank"] = rank
    return queue


def _holding_stop(position: dict[str, Any], trade: dict[str, Any] | None) -> float:
    return _num((trade or {}).get("pm_active_stop") or (trade or {}).get("current_stop") or position.get("stop_price"))


def _holding_score(position: dict[str, Any], recommendation: dict[str, Any] | None, trade: dict[str, Any] | None, best: dict[str, Any] | None, total_equity: float) -> dict[str, Any]:
    ticker = _ticker(position)
    market_value = _num(position.get("market_value") or position.get("marketValue"))
    pnl = _pct(position.get("unrealized_pct") if position.get("unrealized_pct") is not None else position.get("unrealizedPLPercent"))
    has_current_recommendation = recommendation is not None
    pm_score = _clamp(_num((recommendation or {}).get("pm_score"), 45.0)) if recommendation else 45.0
    path_quality = _clamp(50.0 + (pnl or 0.0) * 3.0) if pnl is not None else 45.0
    stop = _holding_stop(position, trade)
    price = _num(position.get("current_price") or position.get("price") or position.get("last_price") or position.get("lastPrice"))
    broker_protected = bool(
        (trade or {}).get("protective_order_id")
        and str((trade or {}).get("protective_order_status") or "").upper() == "SUBMITTED"
    )
    if stop <= 0 or price <= 0:
        risk_state, risk_quality = "UNVERIFIED", 25.0
    elif not broker_protected:
        risk_state, risk_quality = "UNPROTECTED", 15.0
    else:
        stop_distance = ((price - stop) / price) * 100.0
        risk_state, risk_quality = ("PROTECTED", _clamp(60.0 + stop_distance * 4.0))
    concentration = (market_value / total_equity * 100.0) if total_equity > 0 else None
    fit_quality = _clamp(100.0 - max(0.0, (concentration or 0.0) - 15.0) * 4.0) if concentration is not None else 50.0
    own_edge = candidate_edge(recommendation or {"pm_score": pm_score})
    replacement_edge = _num((best or {}).get("candidate_edge")) if best else None
    edge_gap = round(replacement_edge - own_edge, 1) if replacement_edge is not None else None
    opportunity_quality = _clamp(80.0 - max(0.0, edge_gap or 0.0) * 2.5) if edge_gap is not None else 80.0
    overall = _clamp(pm_score * 0.35 + path_quality * 0.20 + risk_quality * 0.20 + fit_quality * 0.10 + opportunity_quality * 0.15)
    state = "HOLD"
    reasons: list[str] = []
    if not has_current_recommendation:
        reasons.append("holding has no fresh PM recommendation; default score is informational only")
    if risk_state == "UNVERIFIED":
        reasons.append("stop coverage is unavailable in the portfolio record")
    elif risk_state == "UNPROTECTED":
        reasons.append("broker-side protective order is not confirmed; monitored emergency exit remains fallback only")
    if pnl is not None and pnl <= -7.0 and pm_score <= 35.0:
        state, reasons = "EXIT_REVIEW", reasons + ["deep loss and weak current PM thesis"]
    elif pnl is not None and pnl <= -3.0 and (edge_gap or 0.0) >= 18.0 and overall < 55.0:
        state, reasons = "REPLACE_REVIEW", reasons + ["weaker holding trails an executable replacement by the required edge gap"]
    elif pnl is not None and pnl <= -3.0 and overall < 55.0:
        state, reasons = "TRIM_REVIEW", reasons + ["deteriorating path quality requires PM review"]
    elif pnl is not None and pnl >= 8.0 and overall >= 75.0:
        state, reasons = "RATCHET_REVIEW", reasons + ["strong winner with healthy portfolio quality"]
    if not reasons:
        reasons.append("holding remains within the current PM risk and opportunity contract")
    return {
        "ticker": ticker,
        "portfolio_score": overall,
        "recommended_state": state,
        "components": {
            "thesis_health": pm_score,
            "path_quality": path_quality,
            "risk_quality": risk_quality,
            "portfolio_fit": fit_quality,
            "opportunity_cost": opportunity_quality,
        },
        "risk_state": risk_state,
        "unrealized_pct": round(pnl, 2) if pnl is not None else None,
        "market_value": market_value,
        "concentration_pct": round(concentration, 2) if concentration is not None else None,
        "current_stop": stop or None,
        "incumbent_edge": own_edge,
        "replacement_ticker": (best or {}).get("ticker"),
        "replacement_edge_gap": edge_gap,
        "reasons": reasons,
        "advisory_only": True,
    }


async def run_portfolio_monitor() -> dict[str, Any]:
    """Persist one portfolio scorecard; intentionally broker read-only."""
    state = await public_execution.portfolio_state()
    if not state.get("ok"):
        return {"ok": False, "skipped": True, "reason": state.get("reason") or "public_portfolio_unavailable"}
    pm_payload = await portfolio_manager.latest_persisted_portfolio_plan()
    if not pm_payload:
        return {"ok": False, "skipped": True, "reason": "pm_plan_unavailable"}
    positions = list(state.get("positions") or [])
    held = {_ticker(position) for position in positions if _ticker(position)}
    queue = build_opportunity_queue(list(pm_payload.get("recommendations") or []), held)
    best = next((item for item in queue if item.get("executable")), None)
    db = get_db()
    trades = await db.tf_trades.find({"broker_base": BROKER_BASE, "status": "OPEN"}, {"_id": 0}).to_list(500)
    trades_by_ticker = {_ticker(trade): trade for trade in trades if _ticker(trade)}
    recs = {_ticker(row): row for row in pm_payload.get("recommendations") or [] if _ticker(row)}
    total_equity = _num(state.get("equity") or state.get("portfolio_value") or state.get("market_value"))
    holdings = [_holding_score(position, recs.get(_ticker(position)), trades_by_ticker.get(_ticker(position)), best, total_equity) for position in positions if _ticker(position)]
    holdings.sort(key=lambda item: item["portfolio_score"])
    now = datetime.now(timezone.utc)
    bucket = now.replace(second=0, microsecond=0, minute=(now.minute // 5) * 5).isoformat()
    snapshot_id = f"pm-portfolio:{BROKER_BASE}:{bucket}"
    snapshot = stamped({
        "snapshot_id": snapshot_id,
        "generated_at": now.isoformat(),
        "score_version": SCORE_VERSION,
        "broker_base": BROKER_BASE,
        "advisory_only": True,
        "pm_scan_finished_at": pm_payload.get("scan_finished_at"),
        "position_count": len(holdings),
        "equity": total_equity,
        "holdings": holdings,
        "opportunity_queue": [{key: value for key, value in item.items() if key != "recommendation"} for item in queue[:100]],
    })
    await db.pm_portfolio_snapshots.update_one({"snapshot_id": snapshot_id}, {"$set": snapshot}, upsert=True)
    await db.bot_state.update_one({"_id": "pm_portfolio_latest"}, {"$set": snapshot}, upsert=True)
    for holding in holdings:
        await db.pm_company_profiles.update_one({"ticker": holding["ticker"]}, {"$set": {
            "latest_portfolio_score": holding["portfolio_score"],
            "latest_portfolio_state": holding["recommended_state"],
            "latest_portfolio_score_at": now.isoformat(),
        }}, upsert=False)
    return {"ok": True, "advisory_only": True, "snapshot_id": snapshot_id, "position_count": len(holdings), "queue_count": len(queue), "replace_reviews": sum(item["recommended_state"] == "REPLACE_REVIEW" for item in holdings)}


async def latest_portfolio_monitor() -> dict[str, Any] | None:
    return await get_db().bot_state.find_one({"_id": "pm_portfolio_latest"}, {"_id": 0})
