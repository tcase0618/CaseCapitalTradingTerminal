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


def _bucket_stats(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        label = row.get(key) or "UNKNOWN"
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
        },
        "options": {
            "total": len(opt_rows),
            "ready": sum(1 for r in opt_rows if r.get("manual_fire_ready")),
            "research_only": sum(1 for r in opt_rows if r.get("route") in {"OPTION", "BOTH"} and not r.get("manual_fire_ready")),
        },
        "case_court": {
            "trials": len(court_rows),
            "live_ready": sum(1 for r in court_rows if r.get("live_run_ready")),
            "needs_data": sum(1 for r in court_rows if str(r.get("posture") or "").upper() == "REQUIRES_CLEANER_DATA"),
        },
        "holes": gaps,
    }
