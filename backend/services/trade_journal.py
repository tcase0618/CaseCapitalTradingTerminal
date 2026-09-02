"""Algorithmic trade journal.

This journal is a deterministic event ledger. It summarizes PM decisions,
Trade Floor execution records, ratchet events, phase outcomes, and signal
performance without calling an LLM or inventing missing results.
"""
from __future__ import annotations

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


def _pct(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def _lesson_for_decision(row: dict[str, Any], ret: float | None) -> str:
    action = row.get("action")
    allocation = _num(row.get("allocation_usd"))
    if ret is None:
        if allocation > 0:
            return "Awaiting return data; keep this in active evidence until the outcome matures."
        return "No matured return yet; preserve the reject/watch decision for future false-negative review."
    if allocation > 0 and ret > 0:
        return "PM approved and the signal worked; candidate for rule reinforcement."
    if allocation > 0 and ret <= 0:
        return "PM approved but outcome was weak; review sizing, stop distance, and setup quality."
    if action in {"WATCH", "REJECT"} and ret > 5:
        return "False-negative candidate; PM was too strict or missed a catalyst."
    if action in {"WATCH", "REJECT"} and ret <= 0:
        return "Good pass; PM avoided a weak setup."
    return "Needs more evidence."


def _trade_lesson(trade: dict[str, Any]) -> str:
    status = trade.get("status") or "UNKNOWN"
    realized = _pct(trade.get("realized_pct"))
    if status == "OPEN":
        return "Open evidence item; track ratchets, current stop, and rule obedience."
    if realized is None:
        return "Closed trade lacks realized return; verify exit price and entry reference."
    if realized > 0:
        return "Winner; compare final exit to PM target and peak gain to find missed upside."
    return "Loser; compare drawdown, stop behavior, and market regime at entry."


async def _perf_for(db, ticker: str, date: str | None) -> tuple[float | None, str | None]:
    if not ticker or not date:
        return None, None
    perf = await db.signal_performance.find_one(
        {"ticker": ticker, "date": date},
        {"_id": 0, "return_7d": 1, "return_30d": 1, "return_90d": 1},
    )
    return _ret_basis(perf)


def _bucket() -> dict[str, Any]:
    return {"n": 0, "wins": 0, "avg_return": 0.0, "pnl": 0.0}


def _add_bucket(bucket: dict[str, Any], ret: float | None, pnl: float | None = None) -> None:
    if ret is None:
        return
    n = bucket["n"]
    bucket["n"] = n + 1
    bucket["wins"] += 1 if ret > 0 else 0
    bucket["avg_return"] = ((bucket["avg_return"] * n) + ret) / (n + 1)
    bucket["pnl"] += _num(pnl)


def _final_bucket(key: str, bucket: dict[str, Any]) -> dict[str, Any]:
    n = bucket["n"]
    return {
        "key": key,
        "samples": n,
        "win_rate": round(bucket["wins"] / n, 3) if n else None,
        "avg_return": round(bucket["avg_return"], 2),
        "pnl": round(bucket["pnl"], 2),
    }


async def overview(limit_scans: int = 120, limit_trades: int = 200) -> dict[str, Any]:
    db = get_db()
    scans = await db.scan_results.find({}, {"_id": 0}).sort("finished_at", -1).allow_disk_use(True).to_list(limit_scans)
    trades = await db.tf_trades.find({}, {"_id": 0}).sort("submitted_at", -1).to_list(limit_trades)
    phase_outcomes = await db.tf_phase_outcomes.find({}, {"_id": 0}).sort("closed_at", -1).to_list(limit_trades)
    ratchets = await db.pm_ratchet_events.find({}, {"_id": 0}).sort("created_at", -1).to_list(limit_trades)
    signal_perf_count = await db.signal_performance.count_documents({})

    latest_scan = scans[0] if scans else {}
    latest_date = _date_key(latest_scan)
    try:
        from . import pm_rules
        ruleset = await pm_rules.get_ruleset()
        profile_override = await pm_rules.profile_override_for("BALANCED")
    except Exception:
        ruleset = {"ruleset_id": "pm-default-v1", "name": "PM Default v1"}
        profile_override = {}
    latest_pm = portfolio_manager.evaluate_rows(
        latest_scan.get("results") or [],
        equity=portfolio_manager.DEFAULT_EQUITY,
        mode="BALANCED",
        profile_override=profile_override,
    )

    capsules: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in latest_pm[:60]:
        ret, basis = await _perf_for(db, row["ticker"], latest_date)
        item = {
            "date": latest_date,
            "ticker": row["ticker"],
            "action": row["action"],
            "pm_score": row.get("pm_score"),
            "risk_reward": row.get("risk_reward"),
            "allocation_usd": row.get("allocation_usd"),
            "risk_usd": row.get("risk_usd"),
            "sector": row.get("sector") or "Unknown",
            "ratchet_profile": (row.get("ratchet_plan") or {}).get("profile") or "OFF",
            "ruleset_id": ruleset.get("ruleset_id"),
            "ruleset_name": ruleset.get("name"),
            "signals": row.get("signals") or [],
            "reasons": row.get("reasons") or [],
            "cautions": row.get("cautions") or [],
            "outcome_return": ret,
            "outcome_basis": basis,
            "lesson": _lesson_for_decision(row, ret),
        }
        capsules.append(item)
        if row["action"] in {"WATCH", "REJECT"}:
            rejected.append(item)

    universe_rows: list[dict[str, Any]] = []
    for row in (latest_scan.get("results") or [])[:18]:
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            continue
        modes = {}
        for mode in ("RISK_OFF", "CONSERVATIVE", "BALANCED", "AGGRESSIVE"):
            replay = portfolio_manager.evaluate_rows([row], equity=portfolio_manager.DEFAULT_EQUITY, mode=mode)
            pm_row = replay[0] if replay else {}
            modes[mode] = {
                "action": pm_row.get("action"),
                "allocation_usd": pm_row.get("allocation_usd"),
                "risk_usd": pm_row.get("risk_usd"),
                "ratchet": (pm_row.get("ratchet_plan") or {}).get("profile") or "OFF",
            }
        universe_rows.append({"ticker": ticker, "modes": modes})

    evidence: list[dict[str, Any]] = []
    trade_by_ticker = {str(t.get("ticker") or "").upper(): t for t in trades}
    for trade in trades[:80]:
        evidence.append({
            "type": "TRADE",
            "ticker": trade.get("ticker"),
            "date": trade.get("submitted_at"),
            "status": trade.get("status"),
            "pm_action": trade.get("pm_action"),
            "pm_score": trade.get("pm_score"),
            "entry": trade.get("entry_price_ref") or trade.get("limit_price"),
            "exit": trade.get("exit_price"),
            "realized_pct": _pct(trade.get("realized_pct")),
            "ratchet_profile": ((trade.get("pm_ratchet_plan") or {}).get("profile") or "OFF"),
            "lesson": _trade_lesson(trade),
        })
    if not evidence:
        for row in capsules[:20]:
            evidence.append({
                "type": "PM_DECISION",
                "ticker": row["ticker"],
                "date": row["date"],
                "status": "PENDING_TRADE",
                "pm_action": row["action"],
                "pm_score": row["pm_score"],
                "entry": None,
                "exit": None,
                "realized_pct": row["outcome_return"],
                "ratchet_profile": row["ratchet_profile"],
                "lesson": row["lesson"],
            })

    dna_stats: dict[str, dict[str, Any]] = defaultdict(_bucket)
    pain_stats: dict[str, dict[str, Any]] = defaultdict(_bucket)
    decision_count = 0
    accepted_count = 0
    pending_outcomes = 0
    matured_outcomes = 0
    missed_winners = 0
    avoided_losers = 0

    for scan in scans:
        date = _date_key(scan)
        rows = scan.get("results") or []
        if not date or not rows:
            continue
        pm_rows = portfolio_manager.evaluate_rows(rows, equity=portfolio_manager.DEFAULT_EQUITY, mode="BALANCED")
        decision_count += len(pm_rows)
        for row in pm_rows:
            accepted = row["action"] in {"ACCUMULATE", "STARTER"} and _num(row.get("allocation_usd")) > 0
            if accepted:
                accepted_count += 1
            ret, _basis = await _perf_for(db, row["ticker"], date)
            if ret is None:
                pending_outcomes += 1
            else:
                matured_outcomes += 1
            if not accepted and ret is not None and ret > 5:
                missed_winners += 1
            if not accepted and ret is not None and ret <= 0:
                avoided_losers += 1
            dna = "|".join([
                (row.get("sector") or "Unknown").title(),
                row["action"],
                (row.get("ratchet_plan") or {}).get("profile") or "OFF",
            ])
            _add_bucket(dna_stats[dna], ret)
            for sig in row.get("signals") or ["NO_SIGNAL"]:
                _add_bucket(pain_stats[str(sig)], ret)

    for outcome in phase_outcomes:
        ret = _pct(outcome.get("final_realized_pct"))
        _add_bucket(pain_stats[f"close:{outcome.get('close_reason') or 'UNKNOWN'}"], ret)

    dna_rows = [_final_bucket(k, v) for k, v in dna_stats.items()]
    dna_rows.sort(key=lambda r: (r["samples"], r["avg_return"]), reverse=True)
    pain_rows = [_final_bucket(k, v) for k, v in pain_stats.items()]
    pain_rows.sort(key=lambda r: (r["samples"], -abs(r["avg_return"])), reverse=True)

    trade_count = len(trades)
    closed_trades = [t for t in trades if t.get("status") == "CLOSED"]
    open_trades = [t for t in trades if t.get("status") == "OPEN"]
    obedience_checks = 0
    obedience_pass = 0
    for trade in trades:
        pm_plan = trade.get("pm_plan") or {}
        if pm_plan:
            obedience_checks += 1
            if trade.get("pm_action") in {"ACCUMULATE", "STARTER"} and _num(trade.get("notional")) <= _num(pm_plan.get("allocation_usd"), 10**9):
                obedience_pass += 1

    recommendations = []
    if matured_outcomes < 30:
        recommendations.append("Needs more matured outcomes before promoting or demoting PM rules.")
    if missed_winners:
        recommendations.append(f"Review {missed_winners} rejected/watch names that later moved more than 5%.")
    if avoided_losers:
        recommendations.append(f"PM avoided {avoided_losers} weak rejected/watch outcomes.")
    if not trades:
        recommendations.append("No Trade Floor records yet; journal is operating from PM decisions and scan evidence only.")
    if ratchets:
        recommendations.append("Ratchet events are available; compare stop raises against final exits.")

    credible_data_sources = [
        {"name": "London Strategic Edge", "use": "Primary historical candles, macro context, option chains, flow, financial reports, and ratio enrichment.", "cost": "configured API key"},
        {"name": "SEC EDGAR", "use": "Official filings, company facts, insider forms, share count, debt, revenue history.", "cost": "free"},
        {"name": "FRED", "use": "Macro regime: rates, CPI, labor, credit spreads, yield curve.", "cost": "free API key"},
        {"name": "Alpha Vantage", "use": "Daily indicators and backup quote/fundamental fields with strict caching.", "cost": "free tier"},
        {"name": "Alpaca", "use": "Account, fills, orders, positions, execution truth.", "cost": "broker API"},
        {"name": "Internal Case Cap DB", "use": "PM decisions, rejected trades, ratchets, outcomes, learning feedback.", "cost": "owned data"},
    ]

    return {
        "generated_at": _now(),
        "source_counts": {
            "scan_results": len(scans),
            "signal_performance": signal_perf_count,
            "tf_trades": trade_count,
            "tf_journal": await db.tf_journal.count_documents({}),
            "tf_phase_outcomes": len(phase_outcomes),
            "pm_ratchet_events": len(ratchets),
        },
        "summary": {
            "decision_count": decision_count,
            "accepted_count": accepted_count,
            "rejected_or_watch": max(0, decision_count - accepted_count),
            "matured_outcomes": matured_outcomes,
            "pending_outcomes": pending_outcomes,
            "actual_trades": trade_count,
            "open_trades": len(open_trades),
            "closed_trades": len(closed_trades),
            "missed_winners": missed_winners,
            "avoided_losers": avoided_losers,
            "system_obedience": round(obedience_pass / obedience_checks, 3) if obedience_checks else None,
            "active_ruleset_id": ruleset.get("ruleset_id"),
            "active_ruleset_name": ruleset.get("name"),
        },
        "decision_time_capsules": capsules[:24],
        "rejected_graveyard": rejected[:24],
        "alternate_universe": universe_rows,
        "evidence_locker": evidence[:80],
        "trade_dna": dna_rows[:24],
        "pain_map": pain_rows[:24],
        "rule_feedback": recommendations,
        "credible_data_sources": credible_data_sources,
        "ratchet_events": ratchets[:30],
        "phase_outcomes": phase_outcomes[:30],
    }
