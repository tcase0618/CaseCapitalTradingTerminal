"""Lottery League grading and learning.

The scanner finds candidates. This module grades closed tickets and learns from
that permanent record. Open tickets can be monitored, but they never enter EV.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from statistics import median
from typing import Any

from .db import get_db, log_activity, stamped
from .lottery import EXIT_HAIRCUT_PCT, ENTRY_HAIRCUT_PCT, LL_RUBRIC_VERSION

GRADER_VERSION = "lottery-grader-v1.0-closed-ticket-truth"
LEARNING_VERSION = "lottery-learning-v1.0"
VARIANT_DECISION_N = 60
LEAGUE_DECISION_N = 150


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, str):
            value = value.replace("$", "").replace(",", "").replace("%", "").strip()
            if value in {"", "-", "N/A"}:
                return default
        return float(value)
    except Exception:
        return default


def _score_bucket(score: Any) -> str:
    s = _num(score, 0)
    if s >= 90:
        return "90+"
    if s >= 80:
        return "80-89"
    if s >= 70:
        return "70-79"
    if s >= 60:
        return "60-69"
    return "<60"


def _float_bucket(ticket: dict[str, Any]) -> str:
    snapshot = ticket.get("entry_snapshot") or {}
    explicit = ticket.get("float_bucket") or ticket.get("float_confidence") or snapshot.get("float_confidence")
    if explicit:
        return str(explicit)
    shares = _num(ticket.get("float_proxy") or snapshot.get("float_proxy") or ticket.get("shares_outstanding"), 0)
    if not shares:
        return "UNKNOWN"
    if shares <= 10_000_000:
        return "<=10M"
    if shares <= 30_000_000:
        return "10M-30M"
    if shares <= 60_000_000:
        return "30M-60M"
    return ">60M"


def _catalyst_class(ticket: dict[str, Any]) -> str:
    snapshot = ticket.get("entry_snapshot") or {}
    triggers = {str(x).upper() for x in (ticket.get("triggers") or snapshot.get("triggers") or ticket.get("catalysts") or [])}
    if "PHARMA/FDA" in triggers or "PDUFA" in triggers:
        return "PHARMA_FDA"
    if "CONTRACT" in triggers or "CONTRACT_SURGE" in triggers:
        return "CONTRACT"
    if "EARNINGS" in triggers or "UPCOMING_EARNINGS" in triggers:
        return "EARNINGS"
    if "ATTENTION" in triggers or "X_FACTOR" in triggers:
        return "ATTENTION"
    return "UNCLASSIFIED"


def _entry_fill(ticket: dict[str, Any]) -> float:
    return _num(
        ticket.get("entry_fill_price")
        or ticket.get("filled_avg_price")
        or ticket.get("entry_price"),
        0,
    )


def _exit_fill(ticket: dict[str, Any]) -> float:
    return _num(
        ticket.get("exit_fill_price")
        or ticket.get("exit_avg_price")
        or ticket.get("exit_price"),
        0,
    )


def _holding_sessions(ticket: dict[str, Any]) -> int | None:
    try:
        start_raw = ticket.get("opened_at") or ticket.get("created_at")
        end_raw = ticket.get("closed_at")
        if not start_raw or not end_raw:
            return None
        start = datetime.fromisoformat(str(start_raw).replace("Z", "+00:00")).date()
        end = datetime.fromisoformat(str(end_raw).replace("Z", "+00:00")).date()
        return max(0, (end - start).days)
    except Exception:
        return None


def _grade_ticket(ticket: dict[str, Any]) -> dict[str, Any] | None:
    snapshot = ticket.get("entry_snapshot") or {}
    entry = _entry_fill(ticket)
    exit_price = _exit_fill(ticket)
    if entry <= 0 or exit_price <= 0:
        return None

    peak = max(_num(ticket.get("peak_price"), entry), entry, exit_price)
    trough = min(
        x for x in [
            _num(ticket.get("trough_price"), entry),
            _num(ticket.get("lowest_price_reached"), entry),
            entry,
            exit_price,
        ]
        if x > 0
    )
    realized_multiple = exit_price / entry
    peak_multiple = peak / entry
    max_drawdown_pct = ((trough - entry) / entry) * 100
    raw_return_pct = (realized_multiple - 1) * 100
    haircut_return_pct = ((exit_price * (1 - EXIT_HAIRCUT_PCT / 100)) - (entry * (1 + ENTRY_HAIRCUT_PCT / 100))) / entry * 100
    mfe_vs_realized_gap_pct = max(0.0, ((peak - exit_price) / entry) * 100)

    exit_reason = ticket.get("exit_reason") or ticket.get("risk_state") or "operator_close"
    regime = ticket.get("regime") or {}
    regime_label = str(regime.get("status") or regime.get("weather") or ticket.get("regime_label") or "unknown").upper()

    grade = stamped({
        "ticket_id": ticket.get("ticket_id"),
        "ticker": ticket.get("ticker"),
        "book": "lottery",
        "variant": ticket.get("variant") or "V1_DAY2_CONTINUATION",
        "status": "GRADED",
        "graded_at": _now().isoformat(),
        "grader_version": GRADER_VERSION,
        "rubric_version": ticket.get("rubric_version") or LL_RUBRIC_VERSION,
        "entry_snapshot": {
            "score": ticket.get("score") if ticket.get("score") is not None else snapshot.get("score"),
            "score_bucket": _score_bucket(ticket.get("score") if ticket.get("score") is not None else snapshot.get("score")),
            "trigger_type": ticket.get("trigger_type"),
            "triggers": ticket.get("triggers") or snapshot.get("triggers") or ticket.get("catalysts") or [],
            "float_tier": _float_bucket(ticket),
            "catalyst_class": _catalyst_class(ticket),
            "entry_price": ticket.get("entry_price"),
            "entry_fill_price": entry,
            "quote_age_seconds": ticket.get("quote_age_seconds"),
            "regime": regime_label,
            "opened_at": ticket.get("opened_at") or ticket.get("created_at"),
        },
        "exit": {
            "exit_price": ticket.get("exit_price"),
            "exit_fill_price": exit_price,
            "exit_reason": exit_reason,
            "closed_at": ticket.get("closed_at"),
            "holding_sessions": _holding_sessions(ticket),
        },
        "truth": {
            "realized_multiple": round(realized_multiple, 3),
            "peak_multiple": round(peak_multiple, 3),
            "mfe_pct": round((peak_multiple - 1) * 100, 2),
            "mae_pct": round(max_drawdown_pct, 2),
            "mfe_vs_realized_gap_pct": round(mfe_vs_realized_gap_pct, 2),
            "raw_return_pct": round(raw_return_pct, 2),
            "haircut_return_pct": round(haircut_return_pct, 2),
            "entry_haircut_pct": ENTRY_HAIRCUT_PCT,
            "exit_haircut_pct": EXIT_HAIRCUT_PCT,
            "headline_basis": "haircut_return_pct",
        },
        "learning_tags": {
            "variant": ticket.get("variant") or "V1_DAY2_CONTINUATION",
            "regime": regime_label,
            "float_tier": _float_bucket(ticket),
            "catalyst_class": _catalyst_class(ticket),
            "score_bucket": _score_bucket(ticket.get("score") if ticket.get("score") is not None else snapshot.get("score")),
            "exit_reason": exit_reason,
        },
    })
    return grade


async def grade_closed_tickets() -> dict[str, Any]:
    """Create/update grade records for all fully closed Lottery tickets."""
    db = get_db()
    tickets = await db.ll_tickets.find(
        {"$or": [{"status": "CLOSED"}, {"closed_at": {"$exists": True, "$ne": None}}]},
        {"_id": 0},
    ).to_list(5000)
    written = 0
    skipped = 0
    for ticket in tickets:
        grade = _grade_ticket(ticket)
        if not grade or not grade.get("ticket_id"):
            skipped += 1
            continue
        await db.ll_grades.update_one(
            {"ticket_id": grade["ticket_id"]},
            {"$setOnInsert": grade},
            upsert=True,
        )
        written += 1
    return {"ok": True, "tickets_seen": len(tickets), "grades_ready": written, "skipped": skipped}


def _segment_key(grade: dict[str, Any], dimension: str) -> str:
    tags = grade.get("learning_tags") or {}
    if dimension == "combined":
        return "ALL"
    return str(tags.get(dimension) or "UNKNOWN")


def _aggregate(rows: list[dict[str, Any]], dimension: str = "combined") -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_segment_key(row, dimension)].append(row)

    out = []
    for key, items in grouped.items():
        returns = [_num((r.get("truth") or {}).get("haircut_return_pct"), 0) for r in items]
        raw_returns = [_num((r.get("truth") or {}).get("raw_return_pct"), 0) for r in items]
        mfes = [_num((r.get("truth") or {}).get("mfe_pct"), 0) for r in items]
        gaps = [_num((r.get("truth") or {}).get("mfe_vs_realized_gap_pct"), 0) for r in items]
        maes = [_num((r.get("truth") or {}).get("mae_pct"), 0) for r in items]
        n = len(items)
        ev = sum(returns) / n if n else 0
        exit_reasons: dict[str, int] = defaultdict(int)
        for row in items:
            exit_reasons[str((row.get("learning_tags") or {}).get("exit_reason") or "UNKNOWN")] += 1
        status = "GATHERING"
        threshold = LEAGUE_DECISION_N if dimension == "combined" else VARIANT_DECISION_N
        if n >= threshold and ev <= 0:
            status = "RETIRE"
        elif n >= threshold:
            status = "VALIDATED_POSITIVE"
        out.append({
            "segment": key,
            "dimension": dimension,
            "n": n,
            "ev_per_ticket_pct_haircut": round(ev, 2) if n else None,
            "ev_per_ticket_pct_raw": round(sum(raw_returns) / n, 2) if n else None,
            "median_ticket_pct": round(median(returns), 2) if returns else None,
            "hit_rate_30": round(sum(1 for r in returns if r >= 30) / n, 3) if n else None,
            "hit_rate_100": round(sum(1 for r in returns if r >= 100) / n, 3) if n else None,
            "hit_rate_300": round(sum(1 for r in returns if r >= 300) / n, 3) if n else None,
            "avg_mfe_pct": round(sum(mfes) / n, 2) if n else None,
            "avg_mae_pct": round(sum(maes) / n, 2) if n else None,
            "mfe_vs_realized_gap_pct": round(sum(gaps) / n, 2) if n else None,
            "exit_reasons": dict(sorted(exit_reasons.items())),
            "decision_status": status,
            "n_to_decision": max(0, threshold - n),
        })
    out.sort(key=lambda r: (r["dimension"] != "combined", -(r.get("n") or 0), -(r.get("ev_per_ticket_pct_haircut") or -999)))
    return out


async def truth_board(limit: int = 300) -> dict[str, Any]:
    await grade_closed_tickets()
    db = get_db()
    grades = await db.ll_grades.find({}, {"_id": 0}).sort("graded_at", -1).to_list(limit)
    combined = _aggregate(grades, "combined")
    dimensions = {
        "variant": _aggregate(grades, "variant"),
        "regime": _aggregate(grades, "regime"),
        "float_tier": _aggregate(grades, "float_tier"),
        "catalyst_class": _aggregate(grades, "catalyst_class"),
        "score_bucket": _aggregate(grades, "score_bucket"),
        "exit_reason": _aggregate(grades, "exit_reason"),
    }
    concentration = _concentration(grades)
    latest_learning = await db.ll_learning_runs.find_one({}, {"_id": 0}, sort=[("ran_at", -1)])
    learned_config = await db.ll_learned_config.find_one({"_id": "current"}, {"_id": 0})
    return {
        "ok": True,
        "grader_version": GRADER_VERSION,
        "generated_at": _now().isoformat(),
        "grade_count": len(grades),
        "combined": combined[0] if combined else _empty_segment("ALL", "combined"),
        "segments": dimensions,
        "concentration": concentration,
        "latest_grades": grades[:40],
        "learning": latest_learning or {},
        "learned_config": learned_config or default_learned_config(),
        "rules": {
            "headline_basis": "haircut_return_pct",
            "open_ticket_policy": "Open tickets are marks only; closed tickets enter EV.",
            "kill_criteria": f"Variant EV <= 0 after {VARIANT_DECISION_N}; League EV <= 0 after {LEAGUE_DECISION_N}.",
        },
    }


def _empty_segment(segment: str, dimension: str) -> dict[str, Any]:
    return {
        "segment": segment,
        "dimension": dimension,
        "n": 0,
        "ev_per_ticket_pct_haircut": None,
        "ev_per_ticket_pct_raw": None,
        "median_ticket_pct": None,
        "decision_status": "GATHERING",
        "n_to_decision": LEAGUE_DECISION_N if dimension == "combined" else VARIANT_DECISION_N,
    }


def _concentration(grades: list[dict[str, Any]]) -> dict[str, Any]:
    returns = sorted([_num((g.get("truth") or {}).get("haircut_return_pct"), 0) for g in grades], reverse=True)
    positive = [r for r in returns if r > 0]
    total = sum(positive)
    if not total:
        return {"top1_pct": None, "top5_pct": None, "positive_return_sum": 0}
    return {
        "top1_pct": round((positive[0] / total) * 100, 1) if positive else None,
        "top5_pct": round((sum(positive[:5]) / total) * 100, 1),
        "positive_return_sum": round(total, 2),
    }


def default_learned_config() -> dict[str, Any]:
    return {
        "version": LEARNING_VERSION,
        "min_ticket_score": 60,
        "retired_variants": [],
        "preferred_segments": [],
        "penalized_segments": [],
        "ladder_bias": "baseline",
        "status": "GATHERING",
        "reason": "No closed graded ticket sample yet.",
    }


async def run_learning_cycle(triggered_by: str = "operator") -> dict[str, Any]:
    board = await truth_board(limit=5000)
    grades = board["latest_grades"]
    all_grades = await get_db().ll_grades.find({}, {"_id": 0}).to_list(5000)
    combined = (_aggregate(all_grades, "combined") or [_empty_segment("ALL", "combined")])[0]
    segments = {
        "variant": _aggregate(all_grades, "variant"),
        "regime": _aggregate(all_grades, "regime"),
        "float_tier": _aggregate(all_grades, "float_tier"),
        "catalyst_class": _aggregate(all_grades, "catalyst_class"),
        "score_bucket": _aggregate(all_grades, "score_bucket"),
    }
    changes: list[dict[str, Any]] = []
    config = default_learned_config()
    config["sample_count"] = combined.get("n", 0)

    retired = [
        r["segment"] for r in segments["variant"]
        if r.get("n", 0) >= VARIANT_DECISION_N and _num(r.get("ev_per_ticket_pct_haircut"), 0) <= 0
    ]
    if retired:
        config["retired_variants"] = retired
        changes.append({"type": "retire_variant", "segments": retired})

    preferred = []
    penalized = []
    for dimension, rows in segments.items():
        for row in rows:
            n = row.get("n", 0)
            ev = row.get("ev_per_ticket_pct_haircut")
            if n < 10 or ev is None:
                continue
            record = {"dimension": dimension, "segment": row["segment"], "n": n, "ev": ev}
            if ev >= 20:
                preferred.append(record)
            elif ev <= -15:
                penalized.append(record)
    config["preferred_segments"] = preferred[:12]
    config["penalized_segments"] = penalized[:12]

    score_rows = {r["segment"]: r for r in segments["score_bucket"]}
    low = score_rows.get("60-69")
    high = score_rows.get("70-79")
    if low and low.get("n", 0) >= 10 and _num(low.get("ev_per_ticket_pct_haircut"), 0) <= -10:
        config["min_ticket_score"] = 70
        changes.append({"type": "raise_min_ticket_score", "from": 60, "to": 70, "reason": "60-69 bucket negative EV"})
    elif high and high.get("n", 0) >= 10 and _num(high.get("ev_per_ticket_pct_haircut"), 0) >= 15:
        config["min_ticket_score"] = 60

    gap = _num(combined.get("mfe_vs_realized_gap_pct"), 0)
    if combined.get("n", 0) >= 15 and gap >= 45:
        config["ladder_bias"] = "loosen_second_ladder_or_trail"
        changes.append({"type": "exit_ladder", "action": config["ladder_bias"], "mfe_gap_pct": gap})
    elif combined.get("n", 0) >= 15:
        config["ladder_bias"] = "baseline"

    if combined.get("n", 0) >= LEAGUE_DECISION_N and _num(combined.get("ev_per_ticket_pct_haircut"), 0) <= 0:
        config["status"] = "RETIRE_LEAGUE"
        changes.append({"type": "retire_league", "reason": "combined EV <= 0 after decision sample"})
    elif combined.get("n", 0) >= LEAGUE_DECISION_N:
        config["status"] = "VALIDATED_POSITIVE"
    else:
        config["status"] = "GATHERING"
    config["reason"] = f"{combined.get('n', 0)}/{LEAGUE_DECISION_N} closed tickets; headline EV {combined.get('ev_per_ticket_pct_haircut')}%."

    run = stamped({
        "ran_at": _now().isoformat(),
        "triggered_by": triggered_by,
        "learning_version": LEARNING_VERSION,
        "grader_version": GRADER_VERSION,
        "sample_count": combined.get("n", 0),
        "combined": combined,
        "segments": segments,
        "changes": changes,
        "learned_config": config,
        "notes": _learning_notes(combined, changes),
    })
    db = get_db()
    await db.ll_learning_runs.insert_one(run)
    await db.ll_learned_config.update_one(
        {"_id": "current"},
        {"$set": {**config, "updated_at": _now().isoformat()}},
        upsert=True,
    )
    await log_activity(f"Lottery Learning cycle: {combined.get('n', 0)} closed tickets, {len(changes)} changes", "info", {"changes": changes})
    run.pop("_id", None)
    return {"ok": True, **run}


def _learning_notes(combined: dict[str, Any], changes: list[dict[str, Any]]) -> list[str]:
    n = combined.get("n", 0)
    if not n:
        return ["No closed graded Lottery tickets yet. Learning is armed but waiting for truth data."]
    notes = [
        f"Headline EV is {combined.get('ev_per_ticket_pct_haircut')}% on {n} closed tickets.",
        f"Median ticket is {combined.get('median_ticket_pct')}%; compare this to EV to see tail concentration.",
    ]
    if combined.get("mfe_vs_realized_gap_pct") is not None:
        notes.append(f"Average MFE left behind is {combined.get('mfe_vs_realized_gap_pct')}%.")
    if not changes:
        notes.append("No config changes met sample-size thresholds.")
    return notes
