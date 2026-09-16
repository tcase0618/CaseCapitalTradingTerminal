"""Persistent, evidence-backed Portfolio Manager memory.

This is deliberately not an LLM narrative service.  It turns a frozen PM
packet into immutable ticker observations and a current company dossier whose
summary is rendered exclusively from fields present in that packet.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .db import get_db, stamped


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _ticker(row: dict[str, Any]) -> str:
    return str(row.get("ticker") or row.get("symbol") or "").upper().strip()


def _unique_strings(values: Any, limit: int = 8) -> list[str]:
    if not isinstance(values, (list, tuple)):
        values = [values] if values else []
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _strategy_context(row: dict[str, Any]) -> list[dict[str, Any]]:
    views = [view for view in row.get("strategy_views") or [] if isinstance(view, dict)]
    if not views and isinstance(row.get("strategy_scanner"), dict):
        scanner = row["strategy_scanner"]
        views = [{
            "screener_id": scanner.get("screener_id") or row.get("source_scan"),
            "family": scanner.get("family") or row.get("scanner_family"),
            "lane": scanner.get("lane"),
        }]
    return [
        {
            "screener_id": view.get("screener_id"),
            "family": view.get("family"),
            "lane": view.get("lane"),
            "native_score": view.get("native_score"),
            "case_score": view.get("case_score"),
            "confidence": view.get("confidence"),
        }
        for view in views[:8]
    ]


def build_observation(row: dict[str, Any], *, cycle_id: str, observed_at: str, prior: dict[str, Any] | None = None) -> dict[str, Any]:
    """Normalize one PM recommendation into an immutable company observation."""
    ticker = _ticker(row)
    action = str(row.get("action") or "WATCH").upper()
    prior_action = str((prior or {}).get("latest_action") or "").upper() or None
    prior_score = _num((prior or {}).get("latest_pm_score"), -1.0)
    score = _num(row.get("pm_score"))
    signals = _unique_strings(row.get("signals"))
    strategies = _strategy_context(row)
    lanes = _unique_strings([view.get("lane") for view in strategies if view.get("lane")])
    cautions = _unique_strings(row.get("cautions"), 5)
    reasons: list[str] = []
    if prior_action and prior_action != action:
        reasons.append(f"PM action changed from {prior_action} to {action}")
    if prior_score >= 0 and round(score - prior_score, 1) != 0:
        direction = "rose" if score > prior_score else "fell"
        reasons.append(f"PM score {direction} {abs(score - prior_score):.1f} points")
    blocker = row.get("equity_execution_blocker")
    if blocker:
        reasons.append(f"Execution constraint: {blocker}")
    if not reasons:
        reasons.append("PM state refreshed from the current frozen scan packet")
    return {
        "ticker": ticker,
        "cycle_id": cycle_id,
        "observed_at": observed_at,
        "action": action,
        "pre_execution_action": row.get("pre_execution_action"),
        "pm_score": score,
        "case_score": _num(row.get("case_score")),
        "strategy_confidence": _num(row.get("strategy_confidence")),
        "route": row.get("route") or row.get("preferred_route"),
        "allocation_usd": _num(row.get("allocation_usd")),
        "price": _num(row.get("price")),
        "entry": row.get("entry") or row.get("entry_price"),
        "target": row.get("target") or row.get("target_blended"),
        "stop": row.get("stop") or row.get("stop_loss"),
        "horizon_days": row.get("horizon_days") or row.get("hold_window_days"),
        "thesis": str(row.get("thesis") or "").strip()[:1600],
        "signals": signals,
        "strategy_views": strategies,
        "strategy_lanes": lanes,
        "source_scans": _unique_strings(row.get("scanner_sources") or row.get("source_scan")),
        "cautions": cautions,
        "execution_blocker": blocker,
        "target_is_proxy": bool(row.get("target_is_proxy")),
        "change_reasons": reasons,
        "evidence_integrity": {
            "has_price": _num(row.get("price")) > 0,
            "has_stop": _num(row.get("stop") or row.get("stop_loss")) > 0,
            "has_target": _num(row.get("target") or row.get("target_blended")) > 0,
            "signals_count": len(signals),
            "strategy_views_count": len(strategies),
        },
    }


def render_synopsis(observation: dict[str, Any]) -> str:
    """Render a concise factual PM summary from a normalized observation."""
    ticker = observation["ticker"]
    action = observation["action"]
    score = observation["pm_score"]
    parts = [f"PM currently classifies ${ticker} as {action} with a {score:.1f} score."]
    lanes = observation.get("strategy_lanes") or []
    signals = observation.get("signals") or []
    if lanes:
        parts.append(f"Active strategy lanes: {', '.join(lanes[:3])}.")
    elif signals:
        parts.append(f"Observed signals: {', '.join(signals[:4])}.")
    if observation.get("execution_blocker"):
        parts.append(f"No new equity order is authorized because {observation['execution_blocker']}.")
    elif observation.get("target_is_proxy"):
        parts.append("The target remains a proxy and is not authorized for live equity execution.")
    elif observation.get("stop") and observation.get("target"):
        parts.append(f"Current risk contract: stop {observation['stop']}, target {observation['target']}.")
    if observation.get("cautions"):
        parts.append(f"Cautions: {'; '.join(observation['cautions'][:2])}.")
    return " ".join(parts)


async def record_pm_cycle(pm_payload: dict[str, Any], *, cycle_id: str, observed_at: str | None = None) -> dict[str, Any]:
    """Persist one decision/evidence observation per ticker in a PM cycle."""
    db = get_db()
    observed_at = observed_at or _now()
    written = 0
    action_counts: dict[str, int] = {}
    for row in pm_payload.get("recommendations") or []:
        if not isinstance(row, dict) or not _ticker(row):
            continue
        ticker = _ticker(row)
        prior = await db.pm_company_profiles.find_one({"ticker": ticker}, {"_id": 0})
        observation = build_observation(row, cycle_id=cycle_id, observed_at=observed_at, prior=prior)
        observation_id = f"pm-observation:{cycle_id}:{ticker}"
        decision_id = f"pm-decision:{cycle_id}:{ticker}"
        await db.pm_company_observations.update_one(
            {"observation_id": observation_id},
            {"$setOnInsert": stamped({"observation_id": observation_id, **observation})},
            upsert=True,
        )
        await db.pm_decision_ledger.update_one(
            {"decision_id": decision_id},
            {"$setOnInsert": stamped({
                "decision_id": decision_id,
                "ticker": ticker,
                "cycle_id": cycle_id,
                "decision_at": observed_at,
                "action": observation["action"],
                "pm_score": observation["pm_score"],
                "price": observation["price"],
                "allocation_usd": observation["allocation_usd"],
                "reason_codes": observation["change_reasons"],
                "evidence_ref": observation_id,
                "execution_blocker": observation["execution_blocker"],
            })},
            upsert=True,
        )
        synopsis = render_synopsis(observation)
        await db.pm_company_profiles.update_one(
            {"ticker": ticker},
            {
                "$setOnInsert": stamped({"ticker": ticker, "first_pm_observed_at": observed_at}),
                "$set": {
                    "ticker": ticker,
                    "updated_at": observed_at,
                    "latest_cycle_id": cycle_id,
                    "latest_action": observation["action"],
                    "latest_pm_score": observation["pm_score"],
                    "latest_price": observation["price"],
                    "latest_observation_id": observation_id,
                    "latest_decision_id": decision_id,
                    "latest_synopsis": synopsis,
                    "latest_change_reasons": observation["change_reasons"],
                    "latest_evidence_integrity": observation["evidence_integrity"],
                    "latest_strategy_lanes": observation["strategy_lanes"],
                    "latest_signals": observation["signals"],
                    "latest_thesis": observation["thesis"],
                    "latest_cautions": observation["cautions"],
                    "latest_execution_blocker": observation["execution_blocker"],
                },
            },
            upsert=True,
        )
        action_counts[observation["action"]] = action_counts.get(observation["action"], 0) + 1
        written += 1
    return {"ok": True, "cycle_id": cycle_id, "observed_at": observed_at, "written": written, "actions": action_counts}


async def company_dossier(ticker: str, *, limit: int = 20) -> dict[str, Any]:
    """Return the latest PM state plus an immutable, reverse-chronological timeline."""
    db = get_db()
    symbol = str(ticker or "").upper().strip()
    profile = await db.pm_company_profiles.find_one({"ticker": symbol}, {"_id": 0}) or {}
    decisions = await db.pm_decision_ledger.find({"ticker": symbol}, {"_id": 0}).sort("decision_at", -1).to_list(max(1, min(limit, 100)))
    observations = await db.pm_company_observations.find({"ticker": symbol}, {"_id": 0}).sort("observed_at", -1).to_list(max(1, min(limit, 100)))
    # External market evidence remains a separately labelled research input.
    # A missing adapter or DB row must never make a company dossier unavailable.
    try:
        from . import prediction_markets
        prediction_evidence = await prediction_markets.ticker_evidence(symbol, limit=limit)
    except Exception:
        prediction_evidence = []
    return {
        "ok": bool(profile or decisions),
        "ticker": symbol,
        "profile": profile or None,
        "decisions": decisions,
        "observations": observations,
        "prediction_markets": prediction_evidence,
        "data_role": "pm_memory_and_decision_audit",
    }
