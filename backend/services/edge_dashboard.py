"""Edge proof dashboard.

This service is the honest scoreboard: enough sample size or not, expectancy
positive or not, and which parts of the terminal are helping versus only
generating interesting research.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _avg(values: list[float]) -> float:
    return round(sum(values) / max(1, len(values)), 3)


def _field(row: dict[str, Any], key: str) -> Any:
    cur: Any = row
    for part in key.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _bucket_stats(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        label = _field(row, key) or row.get(key) or "UNKNOWN"
        value = row.get("gain_pct")
        if value is None:
            continue
        buckets[str(label)].append(_num(value))
    out = []
    for label, values in buckets.items():
        wins = sum(1 for v in values if v > 0)
        losses = sum(1 for v in values if v < 0)
        out.append({
            "bucket": label,
            "sample": len(values),
            "win_rate": round(wins / max(1, len(values)) * 100, 2),
            "avg_return_pct": _avg(values),
            "wins": wins,
            "losses": losses,
            "grade": "PROVING" if len(values) >= 30 and _avg(values) > 0 else "EARLY" if len(values) < 30 else "WEAK",
        })
    return sorted(out, key=lambda r: (r["sample"], r["avg_return_pct"]), reverse=True)


def _current_counts(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    counts: dict[str, int] = defaultdict(int)
    ready: dict[str, int] = defaultdict(int)
    for row in rows:
        label = str(_field(row, key) or row.get(key) or "UNKNOWN")
        counts[label] += 1
        if row.get("manual_fire_ready") or (row.get("judge") or {}).get("live_run_ready"):
            ready[label] += 1
    return [
        {
            "bucket": label,
            "count": count,
            "ready": ready.get(label, 0),
            "ready_rate": round(ready.get(label, 0) / max(1, count) * 100, 2),
        }
        for label, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)
    ]


async def overview() -> dict[str, Any]:
    from . import case_court, data_truth, options_desk, pnl_tracker

    truth = await data_truth.overview(force_refresh=False, persist=False)
    rows = await pnl_tracker.signals_tracker_summary(limit=500)
    options = await options_desk.candidates()
    court = await case_court.latest()
    matured = [r for r in rows if r.get("gain_pct") is not None]
    gains = [_num(r.get("gain_pct")) for r in matured]
    wins = sum(1 for v in gains if v > 0)
    losses = sum(1 for v in gains if v < 0)
    avg_win = _avg([v for v in gains if v > 0])
    avg_loss = _avg([v for v in gains if v < 0])
    expectancy = round((wins / max(1, len(gains)) * avg_win) + (losses / max(1, len(gains)) * avg_loss), 3)
    sample_grade = "ENOUGH_TO_TRUST" if len(gains) >= 100 else "BUILDING_SAMPLE" if len(gains) >= 30 else "TOO_EARLY"
    alpha_grade = "POSITIVE" if expectancy > 0 and len(gains) >= 30 else "UNPROVEN"

    opt_rows = options.get("candidates") or []
    court_rows = court.get("trials") or []
    court_postures = [(r.get("judge") or {}).get("advisory_posture") or "UNKNOWN" for r in court_rows]
    decision_grade = sum(1 for r in court_rows if (r.get("evidence_coverage") or {}).get("decision_grade"))
    neutralized = sum(
        1
        for r in court_rows
        for e in (r.get("exhibits") or [])
        if e.get("status") in {"NOT_APPLICABLE", "MISSING_OPTIONAL", "STALE_OPTIONAL"}
    )
    live_ready = sum(1 for r in court_rows if (r.get("judge") or {}).get("live_run_ready"))
    gaps = []
    if truth.get("truth_grade") in {"D", "F"}:
        gaps.append("QC truth grade is blocking or weak.")
    if len(gains) < 30:
        gaps.append("Not enough matured trade samples for a credible edge read.")
    if sum(1 for r in opt_rows if r.get("manual_fire_ready")) == 0 and truth.get("execution", {}).get("options_execution_enabled"):
        gaps.append("Options execution is enabled but no current option tickets are execution-ready.")
    if len(court_rows) and sum(1 for r in court_rows if r.get("live_run_ready")) / max(1, len(court_rows)) > 0.5:
        gaps.append("Case Court may still be too permissive; too many trials are passing live-ready.")
    if (truth.get("scan") or {}).get("single_letter_tickers"):
        gaps.append(
            "Ticker hygiene warning: latest scan includes single-letter symbols "
            f"{', '.join((truth.get('scan') or {}).get('single_letter_tickers')[:8])}."
        )
    if (truth.get("scan") or {}).get("ticker_hygiene_rejected_count"):
        gaps.append(
            f"Ticker hygiene rejected {(truth.get('scan') or {}).get('ticker_hygiene_rejected_count')} bad rows in the latest scan."
        )
    if court_rows and live_ready / max(1, len(court_rows)) > 0.35:
        gaps.append("Case Court live-ready rate is still high; keep it advisory until forward outcomes prove it.")

    return {
        "ok": True,
        "truth": truth,
        "edge": {
            "sample_grade": sample_grade,
            "alpha_grade": alpha_grade,
            "sample": len(gains),
            "win_rate": round(wins / max(1, len(gains)) * 100, 2),
            "avg_return_pct": _avg(gains),
            "avg_win_pct": avg_win,
            "avg_loss_pct": avg_loss,
            "expectancy_pct": expectancy,
            "wins": wins,
            "losses": losses,
        },
        "buckets": {
            "sector": _bucket_stats(matured, "sector")[:12],
            "strategy": _bucket_stats(matured, "options_strategy")[:12],
            "conviction": _bucket_stats(matured, "conviction")[:12],
            "time_horizon": _bucket_stats(matured, "time_horizon")[:12],
        },
        "attribution": {
            "options_strategy_lanes": _current_counts(opt_rows, "strategy_lane.lane")[:12],
            "options_routes": _current_counts(opt_rows, "route")[:8],
            "case_postures": [
                {"bucket": posture, "count": court_postures.count(posture)}
                for posture in sorted(set(court_postures))
            ],
            "data_truth": {
                "grade": truth.get("truth_grade"),
                "decision": truth.get("decision"),
                "ticker_rejects": (truth.get("scan") or {}).get("ticker_hygiene_rejected_count") or 0,
                "single_letter_tickers": (truth.get("scan") or {}).get("single_letter_tickers") or [],
            },
        },
        "options": {
            "total": len(opt_rows),
            "ready": sum(1 for r in opt_rows if r.get("manual_fire_ready")),
            "research_only": sum(1 for r in opt_rows if r.get("route") in {"OPTION", "BOTH"} and not r.get("manual_fire_ready")),
            "by_lane": _current_counts(opt_rows, "strategy_lane.lane")[:8],
        },
        "case_court": {
            "trials": len(court_rows),
            "live_ready": live_ready,
            "needs_data": sum(1 for r in court_rows if str((r.get("judge") or {}).get("advisory_posture") or "").upper() == "REQUIRES_CLEANER_DATA"),
            "decision_grade": decision_grade,
            "neutralized_exhibits": neutralized,
            "authority": "READ_ONLY_UNTIL_FORWARD_VALIDATED",
        },
        "holes": gaps,
    }
