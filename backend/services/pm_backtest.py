"""Portfolio Manager backtest and sandbox replay.

Replays stored scan rows through the same deterministic PM evaluator used by
the live blotter. It does not call Claude and does not place orders.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from . import portfolio_manager
from .db import get_db
from .pm_learning import _date_key, _ret_basis


ALLOWED_OVERRIDES = {
    "max_position_pct",
    "max_single_name_risk_pct",
    "max_gross_deployment_pct",
    "accumulate_score",
    "accumulate_rr",
    "starter_score",
    "starter_rr",
    "watch_score",
}


def _bucket() -> dict[str, Any]:
    return {
        "decisions": 0,
        "allocated": 0,
        "matured": 0,
        "pending": 0,
        "wins": 0,
        "avg_return": 0.0,
        "deployed_usd": 0.0,
        "pnl_usd": 0.0,
    }


def _sim_bucket() -> dict[str, Any]:
    return {
        "simulated": 0,
        "target_hits": 0,
        "stop_hits": 0,
        "hold_exits": 0,
        "avg_sim_return": 0.0,
        "sim_pnl_usd": 0.0,
    }


def _add_sim(bucket: dict[str, Any], row: dict[str, Any], sim: dict[str, Any] | None) -> None:
    if not sim:
        return
    ret = float(sim.get("sim_return_pct") or 0)
    allocation = float(row.get("allocation_usd") or 0)
    n = bucket["simulated"]
    bucket["simulated"] = n + 1
    bucket["target_hits"] += 1 if sim.get("exit_reason") in {"TARGET", "RATCHET_TARGET"} else 0
    bucket["stop_hits"] += 1 if sim.get("exit_reason") == "STOP" else 0
    bucket["hold_exits"] += 1 if sim.get("exit_reason") == "HOLD_TO_BASIS" else 0
    bucket["avg_sim_return"] = ((bucket["avg_sim_return"] * n) + ret) / (n + 1)
    bucket["sim_pnl_usd"] += allocation * (ret / 100.0)


def _finalize_sim(key: str, bucket: dict[str, Any]) -> dict[str, Any]:
    n = bucket["simulated"]
    return {
        "key": key,
        "simulated": n,
        "target_hits": bucket["target_hits"],
        "stop_hits": bucket["stop_hits"],
        "hold_exits": bucket["hold_exits"],
        "target_rate": round(bucket["target_hits"] / n, 3) if n else None,
        "stop_rate": round(bucket["stop_hits"] / n, 3) if n else None,
        "avg_sim_return": round(bucket["avg_sim_return"], 2),
        "sim_pnl_usd": round(bucket["sim_pnl_usd"], 2),
    }


def _simulate_exit(row: dict[str, Any], ret: float | None, basis: str | None) -> dict[str, Any] | None:
    if ret is None or float(row.get("allocation_usd") or 0) <= 0:
        return None
    plan = row.get("ratchet_plan") or {}
    stop_pct = float(plan.get("initial_stop_pct") or row.get("downside_pct") or 0)
    target_pct = float(plan.get("initial_target_pct") or row.get("upside_pct") or 0)
    exit_reason = "HOLD_TO_BASIS"
    sim_return = float(ret)
    ratchet_level = 0
    active_stop_pct = -stop_pct
    active_target_pct = target_pct
    if ret <= -abs(stop_pct):
        exit_reason = "STOP"
        sim_return = -abs(stop_pct)
    elif target_pct > 0 and ret >= target_pct:
        exit_reason = "TARGET"
        sim_return = target_pct
    if plan.get("enabled") and ret > 0:
        trigger = float(plan.get("trigger_step_pct") or 0)
        max_ratchets = int(float(plan.get("max_ratchets") or 0))
        if trigger > 0:
            ratchet_level = min(max_ratchets, max(0, int(ret // trigger)))
        if ratchet_level > 0:
            active_stop_pct = -abs(stop_pct) + ratchet_level * float(plan.get("stop_raise_pct") or 0)
            active_target_pct = target_pct + ratchet_level * float(plan.get("target_raise_pct") or 0)
            if ret >= active_target_pct:
                exit_reason = "RATCHET_TARGET"
                sim_return = active_target_pct
            elif ret <= active_stop_pct:
                exit_reason = "STOP"
                sim_return = active_stop_pct
    return {
        "exit_reason": exit_reason,
        "sim_return_pct": round(sim_return, 2),
        "terminal_return_pct": round(float(ret), 2),
        "basis": basis,
        "ratchet_level": ratchet_level,
        "active_stop_pct": round(active_stop_pct, 2),
        "active_target_pct": round(active_target_pct, 2),
        "method": "close_only_terminal_return",
    }


def _add(bucket: dict[str, Any], row: dict[str, Any], ret: float | None) -> None:
    allocation = float(row.get("allocation_usd") or 0)
    bucket["decisions"] += 1
    if allocation > 0:
        bucket["allocated"] += 1
        bucket["deployed_usd"] += allocation
    if ret is None:
        bucket["pending"] += 1
        return
    bucket["matured"] += 1
    bucket["wins"] += 1 if ret > 0 else 0
    n = bucket["matured"]
    bucket["avg_return"] = ((bucket["avg_return"] * (n - 1)) + ret) / n
    bucket["pnl_usd"] += allocation * (ret / 100.0)


def _finalize_bucket(key: str, bucket: dict[str, Any]) -> dict[str, Any]:
    matured = bucket["matured"]
    deployed = bucket["deployed_usd"]
    return {
        "key": key,
        "decisions": bucket["decisions"],
        "allocated": bucket["allocated"],
        "matured": matured,
        "pending": bucket["pending"],
        "win_rate": round(bucket["wins"] / matured, 3) if matured else None,
        "avg_return": round(bucket["avg_return"], 2),
        "deployed_usd": round(deployed, 2),
        "pnl_usd": round(bucket["pnl_usd"], 2),
        "return_on_deployed_pct": round((bucket["pnl_usd"] / deployed) * 100.0, 2) if deployed > 0 else None,
    }


def _ranked(stats: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [_finalize_bucket(k, v) for k, v in stats.items()]
    rows.sort(key=lambda r: (r["matured"], r["pnl_usd"], r["allocated"]), reverse=True)
    return rows


def _clean_overrides(overrides: dict[str, Any] | None) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in (overrides or {}).items():
        if key not in ALLOWED_OVERRIDES or value is None:
            continue
        try:
            cleaned[key] = float(value)
        except (TypeError, ValueError):
            continue
    return cleaned


async def run(
    limit_scans: int = 120,
    equity: float = portfolio_manager.DEFAULT_EQUITY,
    mode: str = "BALANCED",
    profile_override: dict[str, Any] | None = None,
    ruleset_id: str | None = None,
) -> dict[str, Any]:
    db = get_db()
    active_mode = (mode or "BALANCED").upper()
    if active_mode not in portfolio_manager.MODE_PROFILES:
        active_mode = "BALANCED"
    clean_override = _clean_overrides(profile_override)
    ruleset = None
    try:
        from . import pm_rules
        ruleset = await pm_rules.get_ruleset(ruleset_id)
        clean_override = {
            **(await pm_rules.profile_override_for(active_mode, ruleset_id)),
            **clean_override,
        }
    except Exception:
        ruleset = {"ruleset_id": "pm-default-v1", "name": "PM Default v1"}
    profile = portfolio_manager._profile_for(active_mode, clean_override)

    scans = await db.scan_results.find({}, {"_id": 0}).sort("finished_at", -1).to_list(limit_scans)
    action_stats: dict[str, dict[str, Any]] = defaultdict(_bucket)
    sector_stats: dict[str, dict[str, Any]] = defaultdict(_bucket)
    ratchet_stats: dict[str, dict[str, Any]] = defaultdict(_bucket)
    sim_stats: dict[str, dict[str, Any]] = defaultdict(_sim_bucket)
    total = _bucket()
    sim_total = _sim_bucket()
    sample_rows: list[dict[str, Any]] = []
    scan_count = 0
    basis_counts: dict[str, int] = defaultdict(int)

    for scan in scans:
        date = _date_key(scan)
        rows = scan.get("results") or []
        if not date or not rows:
            continue
        scan_count += 1
        pm_rows = portfolio_manager.evaluate_rows(
            rows,
            equity=equity,
            mode=active_mode,
            profile_override=clean_override,
        )
        for pm_row in pm_rows:
            ticker = pm_row["ticker"]
            perf = await db.signal_performance.find_one(
                {"ticker": ticker, "date": date},
                {"_id": 0, "return_7d": 1, "return_30d": 1, "return_90d": 1},
            )
            ret, basis = _ret_basis(perf)
            sim = _simulate_exit(pm_row, ret, basis)
            if basis:
                basis_counts[basis] += 1
            _add(total, pm_row, ret)
            _add(action_stats[pm_row["action"]], pm_row, ret)
            _add(sector_stats[pm_row.get("sector") or "Unknown"], pm_row, ret)
            ratchet = pm_row.get("ratchet_plan") or {}
            _add(ratchet_stats[ratchet.get("profile") or "OFF"], pm_row, ret)
            _add_sim(sim_total, pm_row, sim)
            _add_sim(sim_stats[ratchet.get("profile") or "OFF"], pm_row, sim)
            if len(sample_rows) < 60:
                sample_rows.append({
                    "scan_date": date,
                    "ticker": ticker,
                    "action": pm_row["action"],
                    "pm_score": pm_row["pm_score"],
                    "risk_reward": pm_row.get("risk_reward"),
                    "allocation_usd": pm_row["allocation_usd"],
                    "risk_usd": pm_row["risk_usd"],
                    "ratchet_profile": ratchet.get("profile") or "OFF",
                    "sector": pm_row.get("sector") or "Unknown",
                    "outcome_return": ret,
                    "outcome_basis": basis,
                    "sim_exit": sim,
                })

    summary = _finalize_bucket("TOTAL", total)
    summary.update({
        "scans": scan_count,
        "sandbox": bool(clean_override),
        "equity": round(equity, 2),
        "mode": active_mode,
        "exit_simulation": _finalize_sim("TOTAL", sim_total),
        "exit_simulation_note": "Close-only simulation uses matured 7/30/90d terminal returns. It does not yet know intraperiod high/low path.",
    })
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "profile_used": profile,
        "ruleset": {
            "ruleset_id": ruleset.get("ruleset_id"),
            "name": ruleset.get("name"),
            "active": ruleset.get("active"),
        },
        "profile_overrides": clean_override,
        "basis_counts": dict(basis_counts),
        "action_stats": _ranked(action_stats),
        "sector_stats": _ranked(sector_stats)[:20],
        "ratchet_stats": _ranked(ratchet_stats),
        "exit_simulation_by_ratchet": [_finalize_sim(k, v) for k, v in sorted(sim_stats.items())],
        "sample_decisions": sample_rows,
    }
