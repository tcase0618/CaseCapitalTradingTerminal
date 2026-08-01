"""Terminal truth layer.

This is the normalized, cross-service answer to: what data can the terminal
trust right now, what is tradable, and what should remain research-only.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from .db import get_db, stamped


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _grade(score: float, blockers: int, warnings: int) -> str:
    if blockers:
        return "F"
    if score >= 90 and warnings == 0:
        return "A"
    if score >= 75:
        return "B"
    if score >= 55:
        return "C"
    if score >= 35:
        return "D"
    return "F"


def _scoped_qc_blockers(checks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    scoped: dict[str, list[dict[str, Any]]] = {
        "system": [],
        "equity": [],
        "options": [],
    }
    for row in checks:
        if not row.get("blocks_trading"):
            continue
        scopes = row.get("execution_scopes") or ["system"]
        if "all" in scopes:
            scopes = ["system", "equity", "options"]
        for scope in scopes:
            if scope in scoped:
                scoped[scope].append(row)
    return scoped


def _execution_flags() -> dict[str, Any]:
    equity_enabled = os.environ.get("ENABLE_TRADE_EXECUTION", "false").strip().lower() in {"1", "true", "yes", "on"}
    options_enabled = os.environ.get("ENABLE_OPTIONS_EXECUTION", "false").strip().lower() in {"1", "true", "yes", "on"}
    opt_base = os.environ.get("OPTIONS_APCA_API_BASE_URL", "")
    eq_base = os.environ.get("APCA_API_BASE_URL", "")
    return {
        "equity_execution_enabled": equity_enabled,
        "options_execution_enabled": options_enabled,
        "equity_paper": "paper-api.alpaca.markets" in eq_base,
        "options_paper": "paper-api.alpaca.markets" in opt_base,
        "options_indicative_allowed": os.environ.get("OPTIONS_ALLOW_INDICATIVE_EXECUTION", "false").strip().lower() in {"1", "true", "yes", "on"},
    }


async def overview(force_refresh: bool = False, persist: bool = True) -> dict[str, Any]:
    from . import case_court, data_quality, options_desk, portfolio_manager

    qc = await data_quality.overview(force_refresh=force_refresh, record_event=False)
    pm = await portfolio_manager.latest_portfolio_plan()
    options = await options_desk.candidates()
    court = await case_court.latest()
    db = get_db()
    latest_scan = await db.scan_results.find_one(
        {},
        {"_id": 0, "finished_at": 1, "results": 1, "duration_sec": 1, "ticker_hygiene": 1},
        sort=[("finished_at", -1)],
    )
    scan_results = (latest_scan or {}).get("results") or []
    hygiene = (latest_scan or {}).get("ticker_hygiene") or {}
    single_letter = sorted({
        str(r.get("ticker") or "").upper()
        for r in scan_results
        if len(str(r.get("ticker") or "").strip()) == 1
    })
    flags = _execution_flags()

    qc_summary = qc.get("summary") or {}
    qc_checks = qc.get("checks", []) or []
    scoped_blockers = _scoped_qc_blockers(qc_checks)
    qc_score = _num(qc.get("critical_score") or qc.get("score"))
    blockers = int(qc_summary.get("blockers") or 0)
    system_blockers = len(scoped_blockers.get("system") or [])
    warnings = int(qc_summary.get("warnings") or 0) + int(qc_summary.get("fallbacks") or 0) + (1 if single_letter else 0)
    truth_grade = _grade(qc_score, system_blockers, warnings)
    pm_rows = pm.get("decisions") or pm.get("rows") or []
    opt_rows = options.get("candidates") or []
    court_rows = court.get("trials") or []
    option_ready = [r for r in opt_rows if r.get("manual_fire_ready")]
    option_research = [r for r in opt_rows if r.get("route") in {"OPTION", "BOTH"} and not r.get("manual_fire_ready")]
    court_ready = [r for r in court_rows if (r.get("judge") or {}).get("live_run_ready")]
    qc_decision = (qc.get("trading_gate") or {}).get("decision")
    data_blocked = qc_decision == "BLOCK" and bool(scoped_blockers.get("system"))
    truth_decision = "BLOCK" if data_blocked else "WATCH" if truth_grade in {"C", "D", "F"} else "PASS"

    payload = {
        "ok": True,
        "generated_at": _now_iso(),
        "truth_grade": truth_grade,
        "tradable": not data_blocked,
        "decision": truth_decision,
        "execution": flags,
        "qc": {
            "score": qc.get("score"),
            "critical_score": qc.get("critical_score"),
            "gate": qc.get("trading_gate") or {},
            "summary": qc_summary,
            "blockers": [r for r in qc_checks if r.get("blocks_trading")][:12],
            "scoped_blockers": {k: v[:12] for k, v in scoped_blockers.items()},
            "scoped_blocker_counts": {k: len(v) for k, v in scoped_blockers.items()},
            "warnings": [r for r in qc_checks if r.get("warnings") or r.get("status") in {"WARN", "FALLBACK", "STALE"}][:12],
        },
        "scan": {
            "finished_at": (latest_scan or {}).get("finished_at"),
            "results": len(scan_results),
            "duration_sec": (latest_scan or {}).get("duration_sec"),
            "single_letter_tickers": single_letter,
            "ticker_hygiene": "WATCH" if single_letter or hygiene.get("rejected_count") else "PASS",
            "ticker_hygiene_rejected_count": hygiene.get("rejected_count") or 0,
            "ticker_hygiene_rejected": (hygiene.get("rejected") or [])[:20],
        },
        "pm": {
            "decisions": len(pm_rows),
            "approved": sum(1 for r in pm_rows if r.get("action") in {"ACCUMULATE", "STARTER"}),
            "watch": sum(1 for r in pm_rows if r.get("action") == "WATCH"),
            "rejected": sum(1 for r in pm_rows if r.get("action") in {"REJECT", "PASS"}),
        },
        "options": {
            "total": len(opt_rows),
            "ready": len(option_ready),
            "research_only": len(option_research),
            "execution_enabled": flags["options_execution_enabled"],
            "paper": flags["options_paper"],
        },
        "case_court": {
            "trials": len(court_rows),
            "live_ready": len(court_ready),
            "needs_data": sum(1 for r in court_rows if str((r.get("judge") or {}).get("advisory_posture") or "").upper() == "REQUIRES_CLEANER_DATA"),
        },
    }
    if persist:
        await db.data_truth_snapshots.insert_one(stamped(payload))
        await db.bot_state.update_one({"_id": "data_truth_latest"}, {"$set": payload}, upsert=True)
    return payload
