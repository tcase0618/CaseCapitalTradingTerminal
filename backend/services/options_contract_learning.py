"""Shadow learning loop for options contract selection."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from .db import get_db, stamped

SHADOW_MIN_SAMPLES = 100
LIVE_MIN_SAMPLES = 150
MIN_CLEAR_ALPHA_ADVANTAGE_PCT = 3.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _num(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _mid(contract: dict[str, Any]) -> float | None:
    bid = _num(contract.get("bid"))
    ask = _num(contract.get("ask"))
    premium = _num(contract.get("premium"))
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return premium if premium and premium > 0 else ask


def selection_id(candidate: dict[str, Any]) -> str:
    instrument = candidate.get("instrument") or {}
    symbol = str(instrument.get("symbol") or instrument.get("contractSymbol") or "unknown")
    return f"{candidate.get('candidate_id') or 'unknown'}:{symbol}"


def build_selection_record(candidate: dict[str, Any]) -> dict[str, Any] | None:
    instrument = candidate.get("instrument") or {}
    symbol = str(instrument.get("symbol") or instrument.get("contractSymbol") or "")
    if not symbol or candidate.get("route") not in {"OPTION", "BOTH"}:
        return None
    alternatives = []
    for item in instrument.get("selection_alternatives") or []:
        if isinstance(item, dict):
            alternatives.append({
                "symbol": str(item.get("symbol") or item.get("contractSymbol") or ""),
                "strike": _num(item.get("strike")),
                "expiration": item.get("expiration"),
                "bid": _num(item.get("bid")),
                "ask": _num(item.get("ask")),
                "premium": _num(item.get("premium")),
                "delta": _num(item.get("delta")),
                "spread_pct": _num(item.get("spread_pct")),
                "volume": int(item.get("volume") or 0),
                "open_interest": int(item.get("open_interest") or 0),
                "selection_score": _num(item.get("selection_score")),
            })
    selected = {
        "symbol": symbol,
        "strike": _num(instrument.get("strike")),
        "expiration": instrument.get("expiration"),
        "bid": _num(instrument.get("bid")),
        "ask": _num(instrument.get("ask")),
        "premium": _num(instrument.get("premium")),
        "delta": _num(instrument.get("delta")),
        "delta_estimated": bool(instrument.get("delta_estimated")),
        "spread_pct": ((_num(instrument.get("spread")) or 0) / (_num(instrument.get("ask")) or 1)) * 100,
        "volume": int(instrument.get("volume") or 0),
        "open_interest": int(instrument.get("open_interest") or 0),
        "entry_mid": _mid(instrument),
    }
    return {
        "selection_id": selection_id(candidate),
        "candidate_id": candidate.get("candidate_id"),
        "ticker": candidate.get("ticker"),
        "strategy": candidate.get("strategy"),
        "strategy_lane": (candidate.get("strategy_lane") or {}).get("lane"),
        "pm_score": _num(candidate.get("pm_score")),
        "risk_budget": _num(candidate.get("risk_budget")),
        "data_quality": candidate.get("data_quality"),
        "data_feed": candidate.get("data_feed"),
        "selected": selected,
        "alternatives": alternatives,
        "selection_version": "heuristic_v1",
        "learning_mode": "shadow_only",
        "status": "PENDING",
        "created_at": _now(),
    }


def resolve_record(record: dict[str, Any], selected_exit: float, alternative_exits: dict[str, float] | None = None) -> dict[str, Any]:
    entry = _num((record.get("selected") or {}).get("entry_mid")) or 0
    selected_return = ((selected_exit - entry) / entry * 100.0) if entry > 0 else None
    counterfactuals = []
    for alternative in record.get("alternatives") or []:
        symbol = str(alternative.get("symbol") or "")
        exit_mark = (alternative_exits or {}).get(symbol)
        alt_entry = _mid(alternative)
        if exit_mark is None or not alt_entry or alt_entry <= 0:
            continue
        counterfactuals.append({"symbol": symbol, "return_pct": round((float(exit_mark) - alt_entry) / alt_entry * 100.0, 4)})
    return {
        **record,
        "status": "RESOLVED",
        "resolved_at": _now(),
        "selected_exit": float(selected_exit),
        "selected_return_pct": round(selected_return, 4) if selected_return is not None else None,
        "counterfactuals": counterfactuals,
        "best_counterfactual_return_pct": max((x["return_pct"] for x in counterfactuals), default=None),
    }


def promotion_state(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the bounded promotion state; this never changes execution flags."""
    resolved = [r for r in records if r.get("status") == "RESOLVED" and r.get("selected_return_pct") is not None]
    selected_returns = [_num(r.get("selected_return_pct")) for r in resolved]
    selected_returns = [x for x in selected_returns if x is not None]
    counterfactuals = [_num(r.get("best_counterfactual_return_pct")) for r in resolved]
    counterfactuals = [x for x in counterfactuals if x is not None]
    selected_avg = sum(selected_returns) / len(selected_returns) if selected_returns else None
    counterfactual_avg = sum(counterfactuals) / len(counterfactuals) if counterfactuals else None
    alpha_advantage = selected_avg - counterfactual_avg if selected_avg is not None and counterfactual_avg is not None else None
    advisory_ready = len(resolved) >= SHADOW_MIN_SAMPLES
    clear_alpha = bool(
        advisory_ready
        and len(resolved) >= LIVE_MIN_SAMPLES
        and selected_avg is not None
        and selected_avg > 0
        and alpha_advantage is not None
        and alpha_advantage >= MIN_CLEAR_ALPHA_ADVANTAGE_PCT
    )
    return {
        "mode": "advisory" if advisory_ready else "shadow_only",
        "advisory_ready": advisory_ready,
        "live_eligible": clear_alpha,
        "resolved_samples": len(resolved),
        "selected_avg_return_pct": round(selected_avg, 4) if selected_avg is not None else None,
        "best_alternative_avg_return_pct": round(counterfactual_avg, 4) if counterfactual_avg is not None else None,
        "alpha_advantage_pct": round(alpha_advantage, 4) if alpha_advantage is not None else None,
        "minimums": {
            "advisory_samples": SHADOW_MIN_SAMPLES,
            "live_samples": LIVE_MIN_SAMPLES,
            "clear_alpha_advantage_pct": MIN_CLEAR_ALPHA_ADVANTAGE_PCT,
        },
        "reason": (
            "Live eligibility achieved: selected contracts have positive average return and at least "
            f"{MIN_CLEAR_ALPHA_ADVANTAGE_PCT:.1f} percentage points of advantage over the best alternatives."
            if clear_alpha else
            "Live eligibility not achieved; keep selector unchanged and continue collecting outcomes."
        ),
    }


async def record_selection(candidate: dict[str, Any]) -> dict[str, Any] | None:
    record = build_selection_record(candidate)
    if not record:
        return None
    db = get_db()
    await db.options_contract_selection.update_one(
        {"selection_id": record["selection_id"]},
        {"$setOnInsert": stamped(record)},
        upsert=True,
    )
    return record


async def resolve_selection(selection_id_value: str, selected_exit: float, alternative_exits: dict[str, float] | None = None) -> dict[str, Any]:
    db = get_db()
    record = await db.options_contract_selection.find_one({"selection_id": selection_id_value}, {"_id": 0})
    if not record:
        return {"ok": False, "reason": "selection_not_found"}
    resolved = resolve_record(record, selected_exit, alternative_exits)
    await db.options_contract_selection.replace_one({"selection_id": selection_id_value}, stamped(resolved), upsert=True)
    return {"ok": True, "record": resolved}


async def status(limit: int = 200) -> dict[str, Any]:
    db = get_db()
    rows = await db.options_contract_selection.find({}, {"_id": 0}).sort("created_at", -1).to_list(limit)
    resolved = [r for r in rows if r.get("status") == "RESOLVED" and r.get("selected_return_pct") is not None]
    stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"samples": 0, "selected_sum": 0.0, "alternative_sum": 0.0, "alternative_samples": 0})
    for row in resolved:
        item = stats[str(row.get("strategy") or "UNKNOWN")]
        item["samples"] += 1
        item["selected_sum"] += float(row["selected_return_pct"])
        for cf in row.get("counterfactuals") or []:
            item["alternative_sum"] += float(cf.get("return_pct") or 0)
            item["alternative_samples"] += 1
    promotion = promotion_state(resolved)
    strategies = {}
    for key, item in stats.items():
        strategies[key] = {
            "samples": item["samples"],
            "selected_avg_return_pct": round(item["selected_sum"] / item["samples"], 4),
            "alternative_avg_return_pct": round(item["alternative_sum"] / item["alternative_samples"], 4) if item["alternative_samples"] else None,
            "promotion_ready": item["samples"] >= 30 and item["alternative_samples"] >= 30,
        }
    recommendations = ["Contract selection remains unchanged until explicit promotion approval is recorded."]
    if not resolved:
        recommendations.append("No resolved contract outcomes yet; do not tune from candidate count alone.")
    elif len(resolved) < 30:
        recommendations.append(f"Need at least {30 - len(resolved)} more resolved selections before promotion review.")
    return {
        "generated_at": _now(), "mode": promotion["mode"], "selection_version": "heuristic_v1",
        "promotion": promotion,
        "total_records": len(rows), "pending": sum(1 for r in rows if r.get("status") == "PENDING"),
        "resolved": len(resolved), "strategies": strategies, "recommendations": recommendations,
        "latest": rows[: min(20, limit)],
    }
