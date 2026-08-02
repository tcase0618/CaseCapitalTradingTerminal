"""Terminal Truth Review.

This is the institutional review layer. It does not trade, score live orders,
or override the PM. It consolidates evidence from scanner, PM, options, Kronos,
Case Court, QC, and realized outcomes into append-only records and weekly
investor-style packets.
"""
from __future__ import annotations

import asyncio
import hashlib
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

from .db import get_db, log_activity, stamped

REVIEW_VERSION = "truth-review-v1.0"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, str):
            value = value.replace("$", "").replace(",", "").replace("%", "").strip()
            if value in {"", "-", "N/A", "None"}:
                return default
        return float(value)
    except Exception:
        return default


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _hit_rate(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(1 for v in values if v > 0) / len(values) * 100, 2)


def _hash_id(*parts: Any) -> str:
    material = "|".join(str(p or "") for p in parts)
    return hashlib.sha1(material.encode("utf-8")).hexdigest()[:16]


def _grade(sample: int, expectancy: float | None) -> str:
    if sample < 30:
        return "BUILDING_SAMPLE"
    if expectancy is None:
        return "NO_TRUTH_DATA"
    if expectancy > 2:
        return "POSITIVE_EDGE"
    if expectancy > 0:
        return "THIN_EDGE"
    return "NEGATIVE_EDGE"


def _freshness(iso: Any) -> dict[str, Any]:
    if not iso:
        return {"age_hours": None, "label": "missing"}
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = max(0.0, (_now() - dt.astimezone(timezone.utc)).total_seconds() / 3600)
        label = "fresh" if age <= 4 else "watch" if age <= 26 else "stale"
        return {"age_hours": round(age, 2), "label": label}
    except Exception:
        return {"age_hours": None, "label": "unknown"}


async def refresh_ledger(limit: int = 500) -> dict[str, Any]:
    """Append unique evidence events into truth_ledger_events.

    The ledger is append-only by event_id. Refreshing only inserts missing
    records and never rewrites a prior truth event.
    """
    from . import case_court, kronos, options_desk, portfolio_manager, scanner

    db = get_db()
    inserted = Counter()
    scan = await scanner.latest_scan() or {}
    pm = await portfolio_manager.latest_portfolio_plan()
    options = await options_desk.candidates()
    court = await case_court.latest(limit=75)
    kronos_payload = await kronos.forecast(persist=False)

    docs: list[dict[str, Any]] = []
    scan_finished = scan.get("finished_at")
    for row in (scan.get("results") or [])[:limit]:
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            continue
        docs.append({
            "event_id": f"scan-{_hash_id(scan_finished, ticker, row.get('signal_score'))}",
            "type": "scan_pick",
            "ticker": ticker,
            "source": "scanner",
            "event_at": scan_finished,
            "review_version": REVIEW_VERSION,
            "payload": {
                "signals": row.get("signals") or [],
                "signal_score": row.get("signal_score"),
                "price": row.get("price"),
                "sector": row.get("sector"),
                "risk": row.get("risk"),
                "options": row.get("options"),
            },
        })

    for row in (pm.get("recommendations") or pm.get("decisions") or [])[:limit]:
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            continue
        docs.append({
            "event_id": f"pm-{_hash_id(row.get('generated_at') or scan_finished, ticker, row.get('action'), row.get('pm_score'))}",
            "type": "pm_decision",
            "ticker": ticker,
            "source": "portfolio_manager",
            "event_at": row.get("generated_at") or pm.get("generated_at") or scan_finished,
            "review_version": REVIEW_VERSION,
            "payload": {
                "action": row.get("action"),
                "pm_score": row.get("pm_score"),
                "risk_reward": row.get("risk_reward"),
                "entry_low": row.get("entry_low"),
                "entry_high": row.get("entry_high"),
            },
        })

    for row in (options.get("candidates") or [])[:limit]:
        ticker = str(row.get("ticker") or "").upper()
        docs.append({
            "event_id": f"option-candidate-{_hash_id(row.get('candidate_id'), row.get('generated_at'))}",
            "type": "option_candidate",
            "ticker": ticker,
            "source": "options_desk",
            "event_at": row.get("generated_at"),
            "review_version": REVIEW_VERSION,
            "payload": {
                "route": row.get("route"),
                "ready": row.get("manual_fire_ready"),
                "risk_budget": row.get("risk_budget"),
                "contracts": row.get("contracts"),
                "blocked_reasons": row.get("blocked_reasons") or [],
                "instrument": row.get("instrument"),
                "data_quality": row.get("data_quality"),
            },
        })

    for row in (court.get("trials") or [])[:limit]:
        ticker = str(row.get("ticker") or "").upper()
        judge = row.get("judge") or {}
        docs.append({
            "event_id": f"court-{_hash_id(row.get('case_id'), row.get('session_id'))}",
            "type": "case_court_trial",
            "ticker": ticker,
            "source": "case_court",
            "event_at": row.get("generated_at"),
            "review_version": REVIEW_VERSION,
            "payload": {
                "posture": judge.get("advisory_posture"),
                "expression_hint": judge.get("expression_hint"),
                "advisory_alignment_ok": judge.get("advisory_alignment_ok"),
                "defense_score": (row.get("defense") or {}).get("score"),
                "prosecution_score": (row.get("prosecution") or {}).get("score"),
                "coverage": row.get("evidence_coverage"),
            },
        })

    for row in (kronos_payload.get("forecasts") or [])[:limit]:
        ticker = str(row.get("ticker") or "").upper()
        docs.append({
            "event_id": f"kronos-{_hash_id(kronos_payload.get('generated_at'), ticker, row.get('kronos_score'))}",
            "type": "kronos_forecast",
            "ticker": ticker,
            "source": "kronos",
            "event_at": kronos_payload.get("generated_at"),
            "review_version": REVIEW_VERSION,
            "payload": {
                "bias": row.get("bias") or row.get("forecast_bias"),
                "confidence": row.get("confidence"),
                "kronos_score": row.get("kronos_score"),
                "day_cone": row.get("day_cone"),
                "hold_cone": row.get("hold_cone"),
            },
        })

    for doc in docs:
        doc = stamped(doc)
        result = await db.truth_ledger_events.update_one(
            {"event_id": doc["event_id"]},
            {"$setOnInsert": doc},
            upsert=True,
        )
        if result.upserted_id:
            inserted[doc["type"]] += 1

    total = await db.truth_ledger_events.count_documents({})
    payload = {
        "ok": True,
        "review_version": REVIEW_VERSION,
        "generated_at": _now_iso(),
        "inserted": dict(inserted),
        "inserted_total": sum(inserted.values()),
        "ledger_total": total,
    }
    await db.bot_state.update_one(
        {"_id": "truth_review_ledger_latest"},
        {"$set": payload},
        upsert=True,
    )
    return payload


async def _scanner_truth() -> dict[str, Any]:
    db = get_db()
    rows = await db.signal_performance.find({}, {"_id": 0}).sort("date", -1).to_list(5000)
    matured_7 = [_num(r.get("return_7d")) for r in rows if r.get("return_7d") is not None]
    matured_30 = [_num(r.get("return_30d")) for r in rows if r.get("return_30d") is not None]
    matured_90 = [_num(r.get("return_90d")) for r in rows if r.get("return_90d") is not None]
    latest_scan = await db.scan_results.find_one({}, {"_id": 0}, sort=[("finished_at", -1)])
    latest_age = _freshness((latest_scan or {}).get("finished_at"))
    return {
        "latest_scan_at": (latest_scan or {}).get("finished_at"),
        "latest_scan_freshness": latest_age,
        "latest_count": len((latest_scan or {}).get("results") or []),
        "samples": {"7d": len(matured_7), "30d": len(matured_30), "90d": len(matured_90)},
        "returns": {
            "avg_7d": _avg(matured_7),
            "win_7d": _hit_rate(matured_7),
            "avg_30d": _avg(matured_30),
            "win_30d": _hit_rate(matured_30),
            "avg_90d": _avg(matured_90),
            "win_90d": _hit_rate(matured_90),
        },
        "grade": _grade(len(matured_30), _avg(matured_30)),
    }


async def _options_truth() -> dict[str, Any]:
    from . import options_desk

    db = get_db()
    trades = await db.options_desk_trades.find({}, {"_id": 0}).sort("last_synced_at", -1).to_list(1000)
    closed = [t for t in trades if str(t.get("status") or "").lower() in {"closed", "filled_exit", "flat_no_position"} or t.get("exit_order")]
    active = [t for t in trades if str(t.get("status") or "").lower() in {"active", "pending_protective_close_market_closed", "hard_stop_close_submitted", "ratchet_close_submitted"}]
    realized_pcts = []
    for row in closed:
        entry = _num(row.get("entry_premium"))
        exit_price = _num(row.get("exit_premium") or row.get("exit_fill_price") or row.get("close_price"))
        if entry > 0 and exit_price > 0:
            realized_pcts.append((exit_price - entry) / entry * 100)
        elif row.get("realized_pct") is not None:
            realized_pcts.append(_num(row.get("realized_pct")))
    unrealized = [_num(t.get("unrealized_pct")) for t in active if t.get("unrealized_pct") is not None]
    mark_audit = await options_desk.latest_mark_audit()
    candidates = await options_desk.candidates()
    return {
        "trades": len(trades),
        "closed": len(closed),
        "active": len(active),
        "realized": {
            "sample": len(realized_pcts),
            "avg_pct": _avg(realized_pcts),
            "win_rate": _hit_rate(realized_pcts),
            "median_pct": round(median(realized_pcts), 2) if realized_pcts else None,
            "grade": _grade(len(realized_pcts), _avg(realized_pcts)),
        },
        "unrealized": {
            "sample": len(unrealized),
            "avg_pct": _avg(unrealized),
            "win_rate": _hit_rate(unrealized),
        },
        "candidates": {
            "total": len(candidates.get("candidates") or []),
            "ready": sum(1 for c in candidates.get("candidates") or [] if c.get("manual_fire_ready")),
            "research_only": sum(1 for c in candidates.get("candidates") or [] if c.get("route") in {"OPTION", "BOTH"} and not c.get("manual_fire_ready")),
        },
        "mark_audit": {
            "ok": mark_audit.get("ok"),
            "checked_at": mark_audit.get("checked_at"),
            "critical": mark_audit.get("critical", 0),
            "warnings": mark_audit.get("warnings", 0),
        },
    }


async def _court_truth() -> dict[str, Any]:
    from . import case_court

    latest = await case_court.latest(limit=75)
    record = await case_court.record(days=90)
    trials = latest.get("trials") or []
    posture_counts = Counter((t.get("judge") or {}).get("advisory_posture") or "UNKNOWN" for t in trials)
    return {
        "latest_trials": len(trials),
        "postures": dict(posture_counts),
        "decision_grade": sum(1 for t in trials if (t.get("evidence_coverage") or {}).get("decision_grade")),
        "advisory_alignment": sum(1 for t in trials if (t.get("judge") or {}).get("advisory_alignment_ok")),
        "record": {
            "graded": record.get("graded", 0),
            "open_trials": record.get("open_trials", 0),
            "by_posture": record.get("by_posture") or {},
            "sample_note": record.get("sample_note"),
        },
        "grade": "PROVING" if (record.get("graded") or 0) >= 30 else "BUILDING_SAMPLE",
    }


async def _kronos_truth() -> dict[str, Any]:
    from . import kronos

    db = get_db()
    snapshots = await db.kronos_forecast_snapshots.find({}, {"_id": 0}).sort("generated_at", -1).to_list(500)
    disagreements = await kronos.disagreement_performance(limit=500)
    today = _now()
    calendar = await kronos.calendar_month(year=today.year, month=today.month)
    days = calendar.get("days") or []
    scored = [d for d in days if d.get("spy_actual_pct") is not None or d.get("actual_spy_pct") is not None]
    direction_wins = 0
    for d in scored:
        pred = str(d.get("spy_direction") or d.get("prediction") or "").upper()
        actual = _num(d.get("spy_actual_pct") or d.get("actual_spy_pct"))
        if (pred in {"UP", "BULLISH"} and actual > 0) or (pred in {"DOWN", "BEARISH"} and actual < 0):
            direction_wins += 1
    return {
        "snapshots": len(snapshots),
        "latest_snapshot_at": snapshots[0].get("generated_at") if snapshots else None,
        "latest_freshness": _freshness(snapshots[0].get("generated_at") if snapshots else None),
        "disagreements": len(disagreements.get("rows") or []),
        "calendar_days_scored": len(scored),
        "direction_win_rate": round(direction_wins / max(1, len(scored)) * 100, 2) if scored else None,
        "grade": "PROVING" if len(scored) >= 20 else "BUILDING_SAMPLE",
    }


async def _lottery_truth() -> dict[str, Any]:
    from . import lottery_grader

    board = await lottery_grader.truth_board(limit=500)
    combined = board.get("combined") or {}
    return {
        "grade_count": board.get("grade_count", 0),
        "combined": combined,
        "concentration": board.get("concentration") or {},
        "learning_status": (board.get("learned_config") or {}).get("status"),
        "grade": combined.get("decision_status") or "GATHERING",
    }


async def _qc_truth(force_refresh: bool = False) -> dict[str, Any]:
    from . import data_quality, data_truth, execution_gate

    qc = await data_quality.overview(force_refresh=force_refresh, record_event=False)
    truth = await data_truth.overview(force_refresh=False, persist=False)
    gate = await execution_gate.overview(force_refresh=False)
    return {
        "qc_score": qc.get("score"),
        "critical_score": qc.get("critical_score"),
        "truth_grade": truth.get("truth_grade"),
        "truth_decision": truth.get("decision"),
        "gate_decision": gate.get("decision"),
        "blockers": gate.get("blockers") or [],
        "warnings": gate.get("warnings") or [],
        "scoped_blockers": ((truth.get("qc") or {}).get("scoped_blocker_counts") or {}),
        "execution": truth.get("execution") or {},
    }


async def _scheduler_truth() -> dict[str, Any]:
    from . import integration_status

    db = get_db()
    jobs = integration_status.scheduled_jobs()
    rows = []
    for job in jobs:
        name = job.get("name") or job.get("id")
        terms = [job.get("id", ""), name]
        query = {"$or": [{"message": {"$regex": str(t), "$options": "i"}} for t in terms if t]}
        last = await db.activity_log.find_one(query, {"_id": 0}, sort=[("ts", -1)])
        rows.append({
            **job,
            "last_seen_at": (last or {}).get("ts"),
            "last_seen_freshness": _freshness((last or {}).get("ts")),
            "last_message": (last or {}).get("message"),
            "last_level": (last or {}).get("level"),
        })
    errors = await db.activity_log.find(
        {"level": {"$in": ["error", "critical"]}},
        {"_id": 0},
    ).sort("ts", -1).to_list(10)
    return {"jobs": rows, "recent_errors": errors}


def _overall_score(parts: dict[str, Any]) -> dict[str, Any]:
    score = 100.0
    holes: list[str] = []
    qc = parts.get("qc") or {}
    if qc.get("truth_decision") == "BLOCK" or qc.get("gate_decision") == "BLOCK":
        score -= 22
        holes.append("Execution/data truth gate is blocking.")
    if (parts.get("scanner") or {}).get("grade") == "BUILDING_SAMPLE":
        score -= 8
        holes.append("Scanner edge sample is still building.")
    options = parts.get("options") or {}
    if (options.get("realized") or {}).get("sample", 0) < 30:
        score -= 12
        holes.append("Options strategy has fewer than 30 closed truth samples.")
    elif (options.get("realized") or {}).get("grade") == "NEGATIVE_EDGE":
        score -= 25
        holes.append("Options closed-trade expectancy is negative.")
    court = parts.get("case_court") or {}
    if (court.get("record") or {}).get("graded", 0) < 30:
        score -= 7
        holes.append("Case Court has not been forward-validated yet.")
    kronos_part = parts.get("kronos") or {}
    if (kronos_part.get("calendar_days_scored") or 0) < 20:
        score -= 6
        holes.append("Kronos forecast truth sample is still small.")
    lottery = parts.get("lottery") or {}
    if (lottery.get("grade_count") or 0) < 30:
        score -= 5
        holes.append("Lottery League has not produced enough closed grades.")
    if not holes:
        holes.append("No major institutional readiness blockers found in current review.")
    return {
        "score": round(max(0, min(100, score)), 1),
        "rating": "INVESTOR_READY_PAPER" if score >= 90 else "OPERATIONAL" if score >= 75 else "BUILDING" if score >= 60 else "NEEDS_REPAIR",
        "holes": holes,
    }


async def overview(force_refresh: bool = False, persist: bool = False) -> dict[str, Any]:
    if force_refresh:
        await refresh_ledger()
    scanner, options, court, kronos_part, lottery, qc, scheduler = await asyncio.gather(
        _scanner_truth(),
        _options_truth(),
        _court_truth(),
        _kronos_truth(),
        _lottery_truth(),
        _qc_truth(force_refresh=force_refresh),
        _scheduler_truth(),
    )
    parts = {
        "scanner": scanner,
        "options": options,
        "case_court": court,
        "kronos": kronos_part,
        "lottery": lottery,
        "qc": qc,
        "scheduler": scheduler,
    }
    overall = _overall_score(parts)
    packet = _build_packet(parts, overall)
    payload = {
        "ok": True,
        "review_version": REVIEW_VERSION,
        "generated_at": _now_iso(),
        "overall": overall,
        "systems": parts,
        "investor_packet": packet,
    }
    if persist:
        db = get_db()
        await db.truth_review_snapshots.insert_one(stamped(payload))
        await db.bot_state.update_one({"_id": "truth_review_latest"}, {"$set": payload}, upsert=True)
    return payload


def _build_packet(parts: dict[str, Any], overall: dict[str, Any]) -> dict[str, Any]:
    scanner = parts.get("scanner") or {}
    options = parts.get("options") or {}
    court = parts.get("case_court") or {}
    kronos_part = parts.get("kronos") or {}
    qc = parts.get("qc") or {}
    return {
        "title": "Case Capital Terminal Truth Review",
        "readiness_score": overall.get("score"),
        "rating": overall.get("rating"),
        "headline": (
            f"Terminal rating {overall.get('rating')} at {overall.get('score')}/100. "
            f"Scanner 30D avg {((scanner.get('returns') or {}).get('avg_30d'))}; "
            f"options closed sample {(options.get('realized') or {}).get('sample', 0)}; "
            f"QC gate {qc.get('gate_decision')}."
        ),
        "proof_points": [
            f"Scanner latest count: {scanner.get('latest_count')} with freshness {((scanner.get('latest_scan_freshness') or {}).get('label'))}.",
            f"Options ready/research-only: {(options.get('candidates') or {}).get('ready', 0)} / {(options.get('candidates') or {}).get('research_only', 0)}.",
            f"Case Court forward-graded trials: {((court.get('record') or {}).get('graded', 0))}.",
            f"Kronos scored calendar days: {kronos_part.get('calendar_days_scored', 0)}.",
        ],
        "diligence_gaps": overall.get("holes") or [],
        "recommended_next_actions": [
            "Keep all truth records append-only; do not reset learning history before investor review.",
            "Let options collect at least 30 closed paper trades before treating the strategy as validated.",
            "Use QC blockers as execution blockers only when scoped to system/equity/options, not display-only feeds.",
            "Review this packet weekly before changing strategy thresholds.",
        ],
    }


async def weekly_packet(force_refresh: bool = True) -> dict[str, Any]:
    payload = await overview(force_refresh=force_refresh, persist=True)
    packet = stamped({
        "packet_type": "weekly_truth_review",
        "week_of": (_now() - timedelta(days=_now().weekday())).date().isoformat(),
        "review_version": REVIEW_VERSION,
        "generated_at": payload["generated_at"],
        "overall": payload["overall"],
        "investor_packet": payload["investor_packet"],
        "systems": payload["systems"],
    })
    db = get_db()
    await db.truth_review_packets.insert_one(packet)
    await db.bot_state.update_one(
        {"_id": "truth_review_weekly_latest"},
        {"$set": {k: v for k, v in packet.items() if k != "_id"}},
        upsert=True,
    )
    await log_activity(
        f"Truth Review packet generated: {packet['overall']['rating']} {packet['overall']['score']}/100",
        "info",
        {"rating": packet["overall"]["rating"], "score": packet["overall"]["score"]},
    )
    packet.pop("_id", None)
    return {"ok": True, **packet}


async def ledger(limit: int = 150, event_type: str | None = None, ticker: str | None = None) -> dict[str, Any]:
    db = get_db()
    q: dict[str, Any] = {}
    if event_type and event_type != "all":
        q["type"] = event_type
    if ticker:
        q["ticker"] = str(ticker).upper().replace("$", "")
    rows = await db.truth_ledger_events.find(q, {"_id": 0}).sort("created_at", -1).to_list(max(1, min(limit, 500)))
    return {"ok": True, "events": rows, "count": len(rows)}


async def packets(limit: int = 20) -> dict[str, Any]:
    db = get_db()
    rows = await db.truth_review_packets.find({}, {"_id": 0}).sort("generated_at", -1).to_list(max(1, min(limit, 100)))
    return {"ok": True, "packets": rows}
