"""R&D strategy lab.

This is a read-only research layer for Qlib-style experiments. It scores
candidate strategies and promotion readiness from stored terminal data, but it
never routes orders or changes PM rules.
"""
from __future__ import annotations

import importlib.util
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from . import portfolio_manager
from .db import get_db
from .pm_learning import _date_key, _ret_basis


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _qlib_status() -> dict[str, Any]:
    spec = importlib.util.find_spec("qlib")
    installed = spec is not None
    version = None
    if installed:
        try:
            import qlib
            version = getattr(qlib, "__version__", None)
        except Exception:
            version = None
    return {
        "installed": installed,
        "version": version,
        "mode": "adapter_ready" if installed else "adapter_ready_runtime_optional",
        "role": "offline research/backtest only",
        "guardrail": "R&D cannot execute, resize, or override Portfolio Manager.",
    }


def _bucket() -> dict[str, Any]:
    return {
        "samples": 0,
        "wins": 0,
        "avg_return": 0.0,
        "best": None,
        "worst": None,
        "avg_score": 0.0,
        "avg_rr": 0.0,
        "allocated": 0,
    }


def _add(bucket: dict[str, Any], ret: float | None, score: float, rr: float, allocated: bool) -> None:
    n = bucket["samples"]
    bucket["samples"] = n + 1
    bucket["avg_score"] = ((bucket["avg_score"] * n) + score) / (n + 1)
    bucket["avg_rr"] = ((bucket["avg_rr"] * n) + rr) / (n + 1)
    bucket["allocated"] += 1 if allocated else 0
    if ret is None:
        return
    ret_n = max(0, bucket.get("matured", 0))
    bucket["matured"] = ret_n + 1
    bucket["wins"] += 1 if ret > 0 else 0
    bucket["avg_return"] = ((bucket["avg_return"] * ret_n) + ret) / (ret_n + 1)
    bucket["best"] = ret if bucket["best"] is None else max(bucket["best"], ret)
    bucket["worst"] = ret if bucket["worst"] is None else min(bucket["worst"], ret)


def _finalize(key: str, bucket: dict[str, Any]) -> dict[str, Any]:
    matured = int(bucket.get("matured", 0))
    samples = int(bucket["samples"])
    return {
        "key": key,
        "samples": samples,
        "matured": matured,
        "allocated": bucket["allocated"],
        "coverage_pct": round((matured / samples) * 100.0, 1) if samples else 0,
        "win_rate": round((bucket["wins"] / matured) * 100.0, 1) if matured else None,
        "avg_return": round(bucket["avg_return"], 2),
        "best": round(bucket["best"], 2) if bucket["best"] is not None else None,
        "worst": round(bucket["worst"], 2) if bucket["worst"] is not None else None,
        "avg_score": round(bucket["avg_score"], 1),
        "avg_rr": round(bucket["avg_rr"], 2),
    }


def _rank(stats: dict[str, dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    rows = [_finalize(k, v) for k, v in stats.items()]
    rows.sort(key=lambda r: (r["matured"], r["avg_return"], r["win_rate"] or 0), reverse=True)
    return rows[:limit]


def _strategy_blueprints() -> list[dict[str, Any]]:
    return [
        {
            "id": "qlib-alpha360-pm",
            "name": "Qlib Alpha360 PM Challenger",
            "sleeve": "Core ML",
            "hypothesis": "Learn nonlinear combinations of scanner score, PM score, RR, volatility, and sector context.",
            "inputs": ["scan rows", "PM decisions", "signal performance", "LSE candles", "macro regime"],
            "output": "ranked candidate score compared against PM",
            "risk": "advisory only",
        },
        {
            "id": "signal-ensemble",
            "name": "Signal Ensemble Lab",
            "sleeve": "Screeners",
            "hypothesis": "Test which signal stacks actually mature into positive 7D/30D returns.",
            "inputs": ["dark horse", "x factor", "narrative lock", "lottery", "SEC", "contracts"],
            "output": "signal weight candidates",
            "risk": "requires minimum sample size before promotion",
        },
        {
            "id": "kronos-disagreement",
            "name": "Kronos Disagreement Desk",
            "sleeve": "Forecast",
            "hypothesis": "When Kronos disagrees with PM, track whether PM or forecast pressure wins.",
            "inputs": ["Kronos forecasts", "PM route", "open positions", "actual returns"],
            "output": "PM challenge score",
            "risk": "no routing authority",
        },
        {
            "id": "options-liquidity-ratchet",
            "name": "Options Liquidity + Ratchet Study",
            "sleeve": "Options",
            "hypothesis": "Separate option entry quality from underlying stock signal quality.",
            "inputs": ["Alpaca snapshots", "LSE options flow", "option fills", "theta checks"],
            "output": "entry filter and ratchet calibration",
            "risk": "paper-account evidence first",
        },
        {
            "id": "sec-event-drift",
            "name": "SEC Event Drift",
            "sleeve": "Event",
            "hypothesis": "Certain forms and language clusters have repeatable 30D drift.",
            "inputs": ["SEC filings", "EdgarTools company file", "1M reaction table"],
            "output": "filing-type alpha tags",
            "risk": "needs deduped filing accession history",
        },
        {
            "id": "macro-regime-gate",
            "name": "Macro Regime Gate",
            "sleeve": "Macro",
            "hypothesis": "PM aggressiveness should change when rates, CPI, PMI, labor, and yields deteriorate.",
            "inputs": ["LSE macro", "bond yields", "SPY trend", "scanner breadth"],
            "output": "risk-on/risk-off gate",
            "risk": "research only until stable out-of-sample",
        },
        {
            "id": "pharma-binary-map",
            "name": "Pharma Binary Map",
            "sleeve": "Catalyst",
            "hypothesis": "FDA/clinical catalysts need a different return distribution and risk model.",
            "inputs": ["pharma events", "SEC filings", "price gaps", "options IV"],
            "output": "binary-event pass/defined-risk score",
            "risk": "high variance; options defined-risk only",
        },
    ]


def _promotion_gates(samples: int, matured: int, qlib_installed: bool, lse_ok: bool) -> list[dict[str, Any]]:
    gates = [
        {"name": "Data coverage", "ok": samples >= 150, "detail": f"{samples} reconstructed decisions"},
        {"name": "Matured outcomes", "ok": matured >= 50, "detail": f"{matured} return outcomes joined"},
        {"name": "Qlib adapter", "ok": True, "detail": "runtime optional; adapters are isolated from execution"},
        {"name": "LSE market data", "ok": bool(lse_ok), "detail": "primary candles/options/macro source" if lse_ok else "fallback data only"},
        {"name": "No execution bridge", "ok": True, "detail": "R&D is read-only by contract"},
        {"name": "Promotion review", "ok": False, "detail": "human review required before PM rule changes"},
    ]
    if qlib_installed:
        gates[2]["detail"] = "Qlib runtime import detected"
    return gates


def _experiment_score(samples: int, matured: int, win_rate: float | None, avg_return: float, coverage: float) -> int:
    score = min(35, samples / 6)
    score += min(25, matured / 2)
    score += max(0, min(20, ((win_rate or 50) - 40) * 1.25))
    score += max(0, min(15, avg_return * 2.5))
    score += min(5, coverage / 20)
    return int(round(max(0, min(100, score))))


async def dashboard(limit_scans: int = 160) -> dict[str, Any]:
    db = get_db()
    qlib = _qlib_status()
    scans = await db.scan_results.find({}, {"_id": 0}).sort("finished_at", -1).to_list(max(20, min(limit_scans, 400)))
    latest_scan = scans[0] if scans else {}

    signal_stats: dict[str, dict[str, Any]] = defaultdict(_bucket)
    action_stats: dict[str, dict[str, Any]] = defaultdict(_bucket)
    sector_stats: dict[str, dict[str, Any]] = defaultdict(_bucket)
    option_view_stats: dict[str, dict[str, Any]] = defaultdict(_bucket)
    samples = 0
    matured = 0
    pending = 0
    latest_candidates: list[dict[str, Any]] = []

    for scan_i, scan in enumerate(scans):
        date = _date_key(scan)
        rows = scan.get("results") or []
        if not date or not rows:
            continue
        pm_rows = portfolio_manager.evaluate_rows(rows, equity=portfolio_manager.DEFAULT_EQUITY, mode="BALANCED")
        for row in pm_rows:
            ticker = row.get("ticker")
            perf = await db.signal_performance.find_one(
                {"ticker": ticker, "date": date},
                {"_id": 0, "return_7d": 1, "return_30d": 1, "return_90d": 1},
            )
            ret, basis = _ret_basis(perf)
            score = _num(row.get("pm_score"))
            rr = _num(row.get("risk_reward"))
            allocated = _num(row.get("allocation_usd")) > 0
            samples += 1
            if ret is None:
                pending += 1
            else:
                matured += 1
            _add(action_stats[row.get("action") or "UNKNOWN"], ret, score, rr, allocated)
            _add(sector_stats[row.get("sector") or "Unknown"], ret, score, rr, allocated)
            _add(option_view_stats[row.get("option_view") or "Unknown"], ret, score, rr, allocated)
            for sig in row.get("signals") or []:
                _add(signal_stats[str(sig)], ret, score, rr, allocated)
            if scan_i == 0 and len(latest_candidates) < 20:
                latest_candidates.append({
                    "ticker": ticker,
                    "action": row.get("action"),
                    "pm_score": score,
                    "risk_reward": rr,
                    "allocation_usd": row.get("allocation_usd"),
                    "option_view": row.get("option_view"),
                    "sector": row.get("sector") or "Unknown",
                    "signals": row.get("signals") or [],
                    "research_tag": "train" if allocated else "observe",
                })

    signal_rows = _rank(signal_stats, 18)
    action_rows = _rank(action_stats, 8)
    sector_rows = _rank(sector_stats, 12)
    option_rows = _rank(option_view_stats, 8)

    try:
        from . import london_strategic_edge as lse
        lse_health = await lse.health_probe()
    except Exception as exc:
        lse_health = {"ok": False, "reason": str(exc)[:180]}
    try:
        from . import edgartools_bridge
        edgar_ok = (await edgartools_bridge.company_snapshot("SPY")).get("ok")
    except Exception:
        edgar_ok = False
    try:
        from . import kronos
        disagreements = await kronos.disagreement_performance(limit=200)
    except Exception as exc:
        disagreements = {"rows": [], "error": str(exc)[:180]}

    best_signal = signal_rows[0] if signal_rows else {}
    coverage = round((matured / samples) * 100.0, 1) if samples else 0
    win_rate = round((sum(r.get("win_rate") or 0 for r in action_rows if r.get("win_rate") is not None) / max(1, len([r for r in action_rows if r.get("win_rate") is not None]))), 1) if action_rows else None
    avg_return = round(sum(r.get("avg_return") or 0 for r in action_rows) / max(1, len(action_rows)), 2) if action_rows else 0

    experiments = []
    for blueprint in _strategy_blueprints():
        focus = best_signal if blueprint["id"] in {"qlib-alpha360-pm", "signal-ensemble"} else (action_rows[0] if action_rows else {})
        readiness = _experiment_score(samples, matured, focus.get("win_rate"), focus.get("avg_return") or 0, coverage)
        experiments.append({
            **blueprint,
            "readiness": readiness,
            "status": "ready_for_research" if readiness >= 70 else "collecting_evidence" if readiness >= 45 else "early_stage",
            "sample_anchor": focus.get("key") or "terminal-wide",
        })

    return {
        "ok": True,
        "generated_at": _now(),
        "mode": "read_only_research",
        "qlib": qlib,
        "source_map": {
            "scan_results": len(scans),
            "latest_scan_at": latest_scan.get("finished_at") or latest_scan.get("created_at"),
            "lse": lse_health,
            "edgartools": {"ok": bool(edgar_ok)},
            "kronos_disagreements": len(disagreements.get("rows") or []),
        },
        "stats": {
            "reconstructed_decisions": samples,
            "matured_outcomes": matured,
            "pending_outcomes": pending,
            "coverage_pct": coverage,
            "lab_win_rate": win_rate,
            "avg_action_return": avg_return,
            "active_experiments": len(experiments),
        },
        "strategy_blueprints": experiments,
        "signal_lab": signal_rows,
        "action_lab": action_rows,
        "sector_lab": sector_rows,
        "option_lab": option_rows,
        "latest_candidates": latest_candidates,
        "promotion_gates": _promotion_gates(samples, matured, qlib["installed"], bool(lse_health.get("ok"))),
        "qlib_pipeline": [
            {"stage": "Universe", "detail": "Latest scanner/PM candidate universe, plus SPY benchmark"},
            {"stage": "Feature Store", "detail": "LSE candles, macro, options flow, SEC/EdgarTools, PM scores, signal tags"},
            {"stage": "Model Lab", "detail": "Qlib Alpha360/Alpha158 style feature sets and terminal-specific factors"},
            {"stage": "Backtest", "detail": "Walk-forward tests against PM and SPY, no look-ahead promotion"},
            {"stage": "Challenge", "detail": "Paper recommendation challenger; no execution bridge"},
            {"stage": "Promote", "detail": "Only after sample gates, drift checks, and human review"},
        ],
        "disagreements": disagreements.get("rows", [])[:20],
    }


async def refresh_snapshot(limit_scans: int = 180, triggered_by: str = "scheduler") -> dict[str, Any]:
    db = get_db()
    payload = await dashboard(limit_scans=limit_scans)
    doc = {
        **payload,
        "triggered_by": triggered_by,
        "snapshot_at": _now(),
    }
    try:
        await db.research_lab_snapshots.insert_one(doc)
        await db.bot_state.update_one(
            {"_id": "research_lab_latest"},
            {"$set": {
                "snapshot_at": doc["snapshot_at"],
                "triggered_by": triggered_by,
                "reconstructed_decisions": payload.get("stats", {}).get("reconstructed_decisions", 0),
                "matured_outcomes": payload.get("stats", {}).get("matured_outcomes", 0),
                "active_experiments": payload.get("stats", {}).get("active_experiments", 0),
                "qlib_installed": payload.get("qlib", {}).get("installed", False),
            }},
            upsert=True,
        )
    except Exception:
        pass
    return payload
