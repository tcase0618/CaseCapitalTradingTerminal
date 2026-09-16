"""PM decision grading and shadow-policy promotion controls.

The learner writes scorecards and challenger status only.  It cannot change a
PM threshold, order size, or broker route.  A policy needs observed outcomes
and an explicit operator promotion before it can ever affect production.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from . import pricer
from .db import get_db, stamped

MIN_PROMOTION_SAMPLES = 50


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bucket() -> dict[str, Any]:
    return {"samples": 0, "wins": 0, "sum_return": 0.0}


def _add(bucket: dict[str, Any], result: float) -> None:
    bucket["samples"] += 1
    bucket["wins"] += int(result > 0)
    bucket["sum_return"] += result


def _summary(key: str, bucket: dict[str, Any]) -> dict[str, Any]:
    samples = bucket["samples"]
    return {
        "key": key,
        "samples": samples,
        "win_rate": round(bucket["wins"] / samples, 3) if samples else None,
        "avg_return_pct": round(bucket["sum_return"] / samples, 3) if samples else None,
        "promotion_eligible": samples >= MIN_PROMOTION_SAMPLES,
    }


async def _resolution_close(ticker: str, decision_at: datetime, horizon_days: int) -> tuple[float, str | None]:
    """Return the first daily close at/after a fixed decision horizon.

    This prevents the shadow learner from repeatedly grading an old decision
    against today's moving price, which would overwrite historical outcomes.
    """
    target = (decision_at.astimezone(timezone.utc) + timedelta(days=horizon_days)).date()
    closes = await pricer.get_history_range(
        ticker,
        target.isoformat(),
        (target + timedelta(days=7)).isoformat(),
    )
    for day in sorted(closes):
        price = _num(closes[day])
        if price > 0:
            return price, day
    return 0.0, None


async def run_shadow_learning(*, horizon_days: int = 1, limit: int = 1000) -> dict[str, Any]:
    """Grade mature ledger decisions at a fixed historical close, shadow-only."""
    db = get_db()
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, horizon_days))
    decisions = await db.pm_decision_ledger.find({}, {"_id": 0}).sort("decision_at", -1).to_list(limit)
    matured = []
    for decision in decisions:
        try:
            decision_at = datetime.fromisoformat(str(decision.get("decision_at")).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if decision_at > cutoff or _num(decision.get("price")) <= 0:
            continue
        matured.append(decision)
    action_buckets: dict[str, dict[str, Any]] = defaultdict(_bucket)
    lane_buckets: dict[str, dict[str, Any]] = defaultdict(_bucket)
    written = unavailable = 0
    for decision in matured:
        ticker = str(decision.get("ticker") or "").upper()
        entry = _num(decision.get("price"))
        outcome_id = f"pm-outcome:{decision.get('decision_id')}:{horizon_days}d"
        existing = await db.pm_decision_outcomes.find_one({"outcome_id": outcome_id}, {"_id": 0})
        if existing:
            result = _num(existing.get("return_pct"))
            _add(action_buckets[str(decision.get("action") or "UNKNOWN")], result)
            evidence = await db.pm_company_observations.find_one({"observation_id": decision.get("evidence_ref")}, {"_id": 0, "strategy_lanes": 1}) or {}
            for lane in evidence.get("strategy_lanes") or []:
                _add(lane_buckets[str(lane)], result)
            continue
        close, close_date = await _resolution_close(ticker, decision_at, horizon_days)
        if close <= 0 or entry <= 0 or not close_date:
            unavailable += 1
            continue
        result = round((close / entry - 1.0) * 100.0, 4)
        doc = stamped({
            "outcome_id": outcome_id,
            "decision_id": decision.get("decision_id"),
            "ticker": ticker,
            "horizon_days": horizon_days,
            "entry_price": entry,
            "observed_close": close,
            "observed_close_date": close_date,
            "return_pct": result,
            "action": decision.get("action"),
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "source": "pm_decision_ledger+point_in_time_daily_close",
        })
        await db.pm_decision_outcomes.update_one({"outcome_id": outcome_id}, {"$set": doc}, upsert=True)
        _add(action_buckets[str(decision.get("action") or "UNKNOWN")], result)
        evidence = await db.pm_company_observations.find_one({"observation_id": decision.get("evidence_ref")}, {"_id": 0, "strategy_lanes": 1}) or {}
        for lane in evidence.get("strategy_lanes") or []:
            _add(lane_buckets[str(lane)], result)
        written += 1
    actions = sorted((_summary(key, bucket) for key, bucket in action_buckets.items()), key=lambda item: (item["samples"], item["avg_return_pct"] or -999), reverse=True)
    lanes = sorted((_summary(key, bucket) for key, bucket in lane_buckets.items()), key=lambda item: (item["samples"], item["avg_return_pct"] or -999), reverse=True)
    generated_at = datetime.now(timezone.utc).isoformat()
    report = stamped({
        "report_id": f"pm-policy-shadow:{horizon_days}d:{generated_at[:13]}",
        "generated_at": generated_at,
        "mode": "SHADOW_ONLY",
        "horizon_days": horizon_days,
        "resolved_outcomes": written,
        "unavailable_prices": unavailable,
        "action_scorecard": actions,
        "lane_scorecard": lanes,
        "promotion_rule": {"minimum_samples": MIN_PROMOTION_SAMPLES, "operator_promotion_required": True, "automatic_execution_change": False},
    })
    await db.pm_policy_reports.update_one({"report_id": report["report_id"]}, {"$set": report}, upsert=True)
    await db.bot_state.update_one({"_id": "pm_policy_latest"}, {"$set": report}, upsert=True)
    return {"ok": True, "mode": "SHADOW_ONLY", "resolved_outcomes": written, "unavailable_prices": unavailable, "actions": actions, "lanes": lanes}


async def latest_status() -> dict[str, Any] | None:
    return await get_db().bot_state.find_one({"_id": "pm_policy_latest"}, {"_id": 0})
