"""Portfolio Manager Learning Engine.

Learns from PM decisions, not from Claude and not from Trade Floor overrides.
The first version is read-only: it reconstructs historical PM decisions from
stored scans and joins them to signal performance rows when returns mature.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from . import portfolio_manager
from .db import get_db

MIN_ACTION_SAMPLES = 5
MIN_FULL_SAMPLES = 30


def _date_key(scan: dict[str, Any]) -> str | None:
    raw = scan.get("finished_at") or scan.get("created_at") or scan.get("started_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date().isoformat()
    except Exception:
        return str(raw)[:10] if len(str(raw)) >= 10 else None


def _ret_basis(perf: dict[str, Any] | None) -> tuple[float | None, str | None]:
    if not perf:
        return None, None
    for key in ("return_30d", "return_7d", "return_90d"):
        val = perf.get(key)
        if val is not None:
            try:
                return float(val), key.replace("return_", "")
            except (TypeError, ValueError):
                return None, None
    return None, None


def _empty_bucket() -> dict[str, Any]:
    return {
        "samples": 0,
        "wins": 0,
        "avg_return": 0.0,
        "best_return": None,
        "worst_return": None,
    }


def _add(bucket: dict[str, Any], ret: float) -> None:
    n = bucket["samples"]
    bucket["samples"] = n + 1
    bucket["wins"] += 1 if ret > 0 else 0
    bucket["avg_return"] = ((bucket["avg_return"] * n) + ret) / (n + 1)
    bucket["best_return"] = ret if bucket["best_return"] is None else max(bucket["best_return"], ret)
    bucket["worst_return"] = ret if bucket["worst_return"] is None else min(bucket["worst_return"], ret)


def _finalize(bucket: dict[str, Any]) -> dict[str, Any]:
    samples = bucket["samples"]
    return {
        **bucket,
        "win_rate": round(bucket["wins"] / samples, 3) if samples else None,
        "avg_return": round(bucket["avg_return"], 2),
        "best_return": round(bucket["best_return"], 2) if bucket["best_return"] is not None else None,
        "worst_return": round(bucket["worst_return"], 2) if bucket["worst_return"] is not None else None,
    }


def _ranked(stats: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for key, bucket in stats.items():
        rows.append({"key": key, **_finalize(bucket)})
    rows.sort(key=lambda r: (r["samples"], r["avg_return"]), reverse=True)
    return rows


async def status(limit_scans: int = 120) -> dict[str, Any]:
    db = get_db()
    scans = await db.scan_results.find({}, {"_id": 0}).sort("finished_at", -1).to_list(limit_scans)
    action_stats: dict[str, dict[str, Any]] = defaultdict(_empty_bucket)
    sector_stats: dict[str, dict[str, Any]] = defaultdict(_empty_bucket)
    signal_stats: dict[str, dict[str, Any]] = defaultdict(_empty_bucket)
    option_stats: dict[str, dict[str, Any]] = defaultdict(_empty_bucket)
    samples = 0
    pending = 0
    reconstructed = 0
    latest_decisions: list[dict[str, Any]] = []
    basis_counts: dict[str, int] = defaultdict(int)

    for scan_i, scan in enumerate(scans):
        date = _date_key(scan)
        rows = scan.get("results") or []
        if not date or not rows:
            continue
        pm_rows = portfolio_manager.evaluate_rows(rows, equity=portfolio_manager.DEFAULT_EQUITY, mode="BALANCED")
        reconstructed += len(pm_rows)
        for pm_row in pm_rows:
            ticker = pm_row["ticker"]
            perf = await db.signal_performance.find_one(
                {"ticker": ticker, "date": date},
                {"_id": 0, "return_7d": 1, "return_30d": 1, "return_90d": 1},
            )
            ret, basis = _ret_basis(perf)
            if scan_i == 0 and len(latest_decisions) < 12:
                latest_decisions.append({
                    "ticker": ticker,
                    "action": pm_row["action"],
                    "pm_score": pm_row["pm_score"],
                    "risk_reward": pm_row.get("risk_reward"),
                    "allocated_risk_reward": pm_row.get("risk_reward") if pm_row.get("allocation_usd") else None,
                    "allocation_usd": pm_row["allocation_usd"],
                    "outcome_return": ret,
                    "outcome_basis": basis,
                })
            if ret is None:
                pending += 1
                continue
            samples += 1
            basis_counts[basis or "unknown"] += 1
            _add(action_stats[pm_row["action"]], ret)
            _add(sector_stats[pm_row.get("sector") or "Unknown"], ret)
            _add(option_stats[pm_row.get("option_view") or "Unknown"], ret)
            for sig in pm_row.get("signals") or []:
                _add(signal_stats[sig], ret)

    phase = "pre_learning"
    if samples >= MIN_FULL_SAMPLES:
        phase = "full_learning_ready"
    elif samples >= MIN_ACTION_SAMPLES:
        phase = "action_learning_ready"

    action_rows = _ranked(action_stats)
    recommendations = []
    for row in action_rows:
        if row["samples"] < MIN_ACTION_SAMPLES:
            continue
        if (row.get("win_rate") or 0) >= 0.58 and row["avg_return"] > 0:
            recommendations.append(f"Consider increasing {row['key']} capacity; {row['samples']} samples, {row['avg_return']}% avg return.")
        if (row.get("win_rate") or 0) < 0.45 or row["avg_return"] < -2:
            recommendations.append(f"Consider tightening {row['key']} thresholds; {row['samples']} samples, {row['avg_return']}% avg return.")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "phase": phase,
        "samples": samples,
        "pending_outcomes": pending,
        "reconstructed_decisions": reconstructed,
        "min_action_samples": MIN_ACTION_SAMPLES,
        "min_full_samples": MIN_FULL_SAMPLES,
        "basis_counts": dict(basis_counts),
        "action_stats": action_rows,
        "sector_stats": _ranked(sector_stats)[:20],
        "signal_stats": _ranked(signal_stats)[:30],
        "option_stats": _ranked(option_stats),
        "latest_decisions": latest_decisions,
        "recommendations": recommendations,
        "profile_rules": portfolio_manager.MODE_PROFILES,
    }
