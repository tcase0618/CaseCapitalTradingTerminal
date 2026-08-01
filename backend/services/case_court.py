"""Read-only adversarial review layer for scanner candidates.

Case Court does not route, size, execute, or override Portfolio Manager.
It turns existing terminal evidence into a defense case, prosecution case,
expert witness ledger, and advisory court posture for later PM/R&D review.
"""
from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
from typing import Any

from .db import get_db, stamped


MAX_TRIALS = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        n = float(v)
        return n if math.isfinite(n) else default
    except Exception:
        return default


def _ticker(v: Any) -> str:
    return str(v or "").replace("$", "").strip().upper()


def _signals(row: dict[str, Any]) -> list[str]:
    sigs = row.get("signals") or []
    if isinstance(sigs, dict):
        return sorted(str(k) for k, v in sigs.items() if v)
    return sorted(str(s) for s in sigs)


def _age_hours(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (_now() - dt).total_seconds() / 3600.0)
    except Exception:
        return None


async def _bounded(label: str, awaitable, timeout: float = 6.0) -> Any:
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout)
    except asyncio.TimeoutError:
        return {"ok": False, "error": f"{label}_timeout"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _rows(payload: Any, key: str = "results") -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if not isinstance(payload, dict):
        return []
    val = payload.get(key)
    if isinstance(val, list):
        return [r for r in val if isinstance(r, dict)]
    for fallback in ("rows", "data", "candidates", "recommendations"):
        val = payload.get(fallback)
        if isinstance(val, list):
            return [r for r in val if isinstance(r, dict)]
    return []


def _scan_by_ticker(scan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {_ticker(r.get("ticker")): r for r in _rows(scan, "results") if _ticker(r.get("ticker"))}


def _candidate_by_ticker(options_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {_ticker(r.get("ticker")): r for r in _rows(options_payload, "candidates") if _ticker(r.get("ticker"))}


def _kronos_by_ticker(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = _rows(payload, "forecasts")
    return {_ticker(r.get("ticker")): r for r in rows if _ticker(r.get("ticker"))}


def _add(points: list[dict[str, Any]], label: str, weight: float, detail: str, source: str) -> None:
    if weight <= 0:
        return
    points.append({
        "label": label,
        "weight": round(weight, 1),
        "detail": detail,
        "source": source,
    })


def _entry_position(scan_row: dict[str, Any], pm_row: dict[str, Any]) -> str:
    price = _num(pm_row.get("price") or scan_row.get("price"))
    low = _num(pm_row.get("entry_low") or scan_row.get("entry_low"))
    high = _num(pm_row.get("entry_high") or scan_row.get("entry_high"))
    if price <= 0 or low <= 0 or high <= 0:
        return "UNKNOWN"
    if price < low:
        return "BELOW_BAND"
    if price > high:
        return "ABOVE_BAND"
    return "IN_BAND"


def _witnesses(
    scan_row: dict[str, Any],
    pm_row: dict[str, Any],
    options_row: dict[str, Any] | None,
    kronos_row: dict[str, Any] | None,
    qc: dict[str, Any],
    scan_age: float | None,
) -> list[dict[str, Any]]:
    pm_action = str(pm_row.get("action") or "UNKNOWN").upper()
    pm_score = _num(pm_row.get("pm_score"))
    qc_gate = ((qc or {}).get("trading_gate") or {}).get("decision") or "UNKNOWN"
    qc_score = _num((qc or {}).get("score"), default=-1)
    option_route = str((options_row or {}).get("route") or pm_row.get("option_view") or "UNKNOWN").upper()
    kronos_bias = str((kronos_row or {}).get("bias") or (kronos_row or {}).get("forecast_bias") or "UNKNOWN").upper()
    entry = _entry_position(scan_row, pm_row)
    risk_score = _num((scan_row.get("risk") or {}).get("score"))
    return [
        {
            "name": "Portfolio Manager",
            "stance": "BULL" if pm_action in {"ACCUMULATE", "STARTER"} else "WATCH" if pm_action == "WATCH" else "BEAR",
            "score": round(pm_score, 1),
            "testimony": f"PM action {pm_action} with score {pm_score:.1f} and RR {_num(pm_row.get('risk_reward')):.2f}.",
        },
        {
            "name": "QC",
            "stance": "BULL" if qc_gate != "BLOCK" and qc_score >= 80 else "BEAR" if qc_gate == "BLOCK" else "WATCH",
            "score": round(qc_score, 1) if qc_score >= 0 else None,
            "testimony": f"Trading gate {qc_gate}; scan age {scan_age:.1f}h." if scan_age is not None else f"Trading gate {qc_gate}; scan age unavailable.",
        },
        {
            "name": "Kronos",
            "stance": "BULL" if kronos_bias == "BULLISH" else "BEAR" if kronos_bias == "BEARISH" else "WATCH",
            "score": _num((kronos_row or {}).get("kronos_score"), default=None),
            "testimony": f"Forecast bias {kronos_bias}; confidence {(kronos_row or {}).get('confidence', '-')}.",
        },
        {
            "name": "Options Desk",
            "stance": "BULL" if (options_row or {}).get("manual_fire_ready") else "WATCH" if option_route in {"OPTION", "BOTH"} else "BEAR",
            "score": _num((options_row or {}).get("pm_score"), default=None),
            "testimony": f"Route {option_route}; ready {bool((options_row or {}).get('manual_fire_ready'))}; blocks {len((options_row or {}).get('blocked_reasons') or [])}.",
        },
        {
            "name": "Entry Band",
            "stance": "BULL" if entry == "IN_BAND" else "BEAR" if entry == "ABOVE_BAND" else "WATCH",
            "score": None,
            "testimony": f"Current price is {entry.replace('_', ' ').lower()}.",
        },
        {
            "name": "Risk Engine",
            "stance": "BULL" if risk_score < 55 else "WATCH" if risk_score < 75 else "BEAR",
            "score": round(risk_score, 1),
            "testimony": f"Risk score {risk_score:.1f}; terminal risk flags are inherited from scanner evidence.",
        },
    ]


def _defense(scan_row: dict[str, Any], pm_row: dict[str, Any], options_row: dict[str, Any] | None, kronos_row: dict[str, Any] | None) -> dict[str, Any]:
    points: list[dict[str, Any]] = []
    signals = _signals(scan_row)
    pm_score = _num(pm_row.get("pm_score"))
    rr = _num(pm_row.get("risk_reward"))
    upside = _num(pm_row.get("upside_pct"))
    trade_score = _num(pm_row.get("trade_score") or scan_row.get("trade_score"))
    learning_score = _num(pm_row.get("learning_score") or scan_row.get("learning_score"))
    action = str(pm_row.get("action") or "").upper()
    kronos_bias = str((kronos_row or {}).get("bias") or (kronos_row or {}).get("forecast_bias") or "").upper()

    _add(points, "Signal stack", min(24, len(set(signals)) * 6), f"{len(set(signals))} scanner signals confirmed.", "scanner")
    _add(points, "PM conviction", min(24, pm_score * 0.28), f"PM score is {pm_score:.1f}.", "portfolio_manager")
    _add(points, "Risk/reward", min(18, rr * 5), f"Risk/reward is {rr:.2f}.", "portfolio_manager")
    _add(points, "Upside runway", min(12, max(0, upside) * 0.18), f"Blended upside is {upside:.1f}%.", "scanner")
    _add(points, "Trade quality", min(10, trade_score * 0.18), f"Trade score is {trade_score:.1f}.", "scanner")
    _add(points, "Learning support", min(6, learning_score * 0.6), f"Learning score is {learning_score:.1f}.", "learning")
    if action in {"ACCUMULATE", "STARTER"}:
        _add(points, "PM approved capital path", 8, f"PM current action is {action}.", "portfolio_manager")
    if (options_row or {}).get("manual_fire_ready"):
        _add(points, "Options expression ready", 6, "Options ticket is execution-grade and inside risk budget.", "options_desk")
    if kronos_bias == "BULLISH":
        _add(points, "Kronos alignment", 6, "Kronos forecast supports the long thesis.", "kronos")

    score = min(100.0, sum(p["weight"] for p in points))
    return {
        "name": "Defense",
        "stance": "BULLISH",
        "score": round(score, 1),
        "opening_argument": "Capital deserves consideration because confirmed evidence supports drift, acceptable risk/reward, or a clean options expression.",
        "points": sorted(points, key=lambda x: x["weight"], reverse=True)[:8],
    }


def _prosecution(
    scan_row: dict[str, Any],
    pm_row: dict[str, Any],
    options_row: dict[str, Any] | None,
    kronos_row: dict[str, Any] | None,
    qc: dict[str, Any],
    scan_age: float | None,
) -> dict[str, Any]:
    points: list[dict[str, Any]] = []
    risk_score = _num((scan_row.get("risk") or {}).get("score"))
    rr = _num(pm_row.get("risk_reward"))
    downside = _num(pm_row.get("downside_pct"))
    action = str(pm_row.get("action") or "").upper()
    entry = _entry_position(scan_row, pm_row)
    qc_gate = ((qc or {}).get("trading_gate") or {}).get("decision") or "UNKNOWN"
    blockers = ((qc or {}).get("trading_gate") or {}).get("blockers") or []
    options_blocks = (options_row or {}).get("blocked_reasons") or []
    kronos_bias = str((kronos_row or {}).get("bias") or (kronos_row or {}).get("forecast_bias") or "").upper()

    _add(points, "Risk engine objection", max(0, min(24, (risk_score - 45) * 0.55)), f"Risk score is {risk_score:.1f}.", "scanner")
    if rr and rr < 1.3:
        _add(points, "Weak risk/reward", 16, f"Risk/reward is only {rr:.2f}.", "portfolio_manager")
    _add(points, "Downside exposure", min(12, downside * 0.8), f"Downside to stop is {downside:.1f}%.", "portfolio_manager")
    if action in {"WATCH", "REJECT"}:
        _add(points, "PM has not approved sizing", 18 if action == "REJECT" else 10, f"Current PM action is {action}.", "portfolio_manager")
    if entry == "ABOVE_BAND":
        _add(points, "Chase risk", 16, "Price is above the PM entry band.", "portfolio_manager")
    if scan_age is None or scan_age > 26:
        _add(points, "Scan freshness objection", 14, f"Latest scan age is {scan_age:.1f}h." if scan_age else "Scan timestamp unavailable.", "scanner")
    if qc_gate == "BLOCK":
        _add(points, "QC trading gate blocks", 22, "; ".join(str(b) for b in blockers[:3]) or "QC gate is blocked.", "quality")
    if options_blocks:
        _add(points, "Options contract objection", min(16, len(options_blocks) * 4), "; ".join(str(b) for b in options_blocks[:3]), "options_desk")
    if kronos_bias == "BEARISH":
        _add(points, "Kronos disagreement", 10, "Kronos forecast argues against the long setup.", "kronos")

    score = min(100.0, sum(p["weight"] for p in points))
    return {
        "name": "Prosecutor",
        "stance": "BEARISH",
        "score": round(score, 1),
        "opening_objection": "Capital should be withheld or reduced if freshness, entry discipline, liquidity, or downside evidence weakens the case.",
        "points": sorted(points, key=lambda x: x["weight"], reverse=True)[:8],
    }


def _posture(defense: dict[str, Any], prosecution: dict[str, Any], witnesses: list[dict[str, Any]], pm_row: dict[str, Any]) -> dict[str, Any]:
    d = _num(defense.get("score"))
    p = _num(prosecution.get("score"))
    spread = round(d - p, 1)
    pm_action = str(pm_row.get("action") or "UNKNOWN").upper()
    qc = next((w for w in witnesses if w["name"] == "QC"), {})
    options = next((w for w in witnesses if w["name"] == "Options Desk"), {})

    if qc.get("stance") == "BEAR":
        posture = "REQUIRES_CLEANER_DATA"
        detail = "QC witness blocks full confidence; court will not recommend giving this authority yet."
    elif spread >= 25 and pm_action in {"ACCUMULATE", "STARTER"}:
        posture = "COURT_SUPPORTS_PM"
        detail = "Defense materially outweighs prosecution and PM is already constructive."
    elif spread >= 10:
        posture = "BULLISH_WATCH"
        detail = "Defense leads, but the court wants PM confirmation or better execution conditions."
    elif spread <= -15:
        posture = "COURT_OBJECTS"
        detail = "Prosecution has enough evidence to challenge capital allocation."
    else:
        posture = "EVIDENCE_CONFLICT"
        detail = "The case is mixed; preserve it for watchlist/appeal rather than giving it execution authority."

    if options.get("stance") == "BULL" and posture == "COURT_SUPPORTS_PM":
        expression = "OPTION_OR_BOTH_ADVISORY"
    elif pm_action in {"ACCUMULATE", "STARTER"}:
        expression = "EQUITY_ADVISORY"
    else:
        expression = "NO_AUTHORITY"

    return {
        "advisory_posture": posture,
        "expression_hint": expression,
        "defense_minus_prosecutor": spread,
        "detail": detail,
        "authority": "READ_ONLY_NO_EXECUTION_NO_PM_OVERRIDE",
    }


def _trial(
    scan_row: dict[str, Any],
    pm_row: dict[str, Any],
    options_row: dict[str, Any] | None,
    kronos_row: dict[str, Any] | None,
    qc: dict[str, Any],
    scan_finished_at: str | None,
) -> dict[str, Any]:
    scan_age = _age_hours(scan_finished_at)
    defense = _defense(scan_row, pm_row, options_row, kronos_row)
    prosecution = _prosecution(scan_row, pm_row, options_row, kronos_row, qc, scan_age)
    witnesses = _witnesses(scan_row, pm_row, options_row, kronos_row, qc, scan_age)
    posture = _posture(defense, prosecution, witnesses, pm_row)
    ticker = _ticker(scan_row.get("ticker") or pm_row.get("ticker"))
    return {
        "case_id": f"court-{ticker}-{str(scan_finished_at or _now().isoformat())[:19]}",
        "ticker": ticker,
        "court_status": "ADVISORY_TRIAL",
        "charge": "Potential capital allocation",
        "scan_finished_at": scan_finished_at,
        "scan_age_hours": round(scan_age, 2) if scan_age is not None else None,
        "pm_action": pm_row.get("action"),
        "pm_score": pm_row.get("pm_score"),
        "price": pm_row.get("price") or scan_row.get("price"),
        "sector": pm_row.get("sector") or scan_row.get("sector"),
        "signals": _signals(scan_row),
        "defense": defense,
        "prosecution": prosecution,
        "witnesses": witnesses,
        "judge": posture,
        "appeal_triggers": _appeal_triggers(scan_row, pm_row, options_row, posture),
        "generated_at": _now().isoformat(),
    }


def _appeal_triggers(scan_row: dict[str, Any], pm_row: dict[str, Any], options_row: dict[str, Any] | None, posture: dict[str, Any]) -> list[str]:
    triggers = []
    if posture.get("advisory_posture") in {"COURT_OBJECTS", "EVIDENCE_CONFLICT", "BULLISH_WATCH"}:
        triggers.append("fresh scan upgrades PM score or signal stack")
    if _entry_position(scan_row, pm_row) == "ABOVE_BAND":
        triggers.append("price returns inside PM entry band")
    if (options_row or {}).get("blocked_reasons"):
        triggers.append("options chain refresh clears liquidity/data blockers")
    triggers.append("QC instant repull clears stale or fallback data")
    triggers.append("new SEC, earnings, macro, or Kronos evidence changes witness testimony")
    return triggers[:5]


def _summary(trials: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    postures = [((t.get("judge") or {}).get("advisory_posture") or "UNKNOWN") for t in trials]
    return {
        "authority": "READ_ONLY_NO_EXECUTION",
        "trials": len(trials),
        "supports_pm": postures.count("COURT_SUPPORTS_PM"),
        "bullish_watch": postures.count("BULLISH_WATCH"),
        "objects": postures.count("COURT_OBJECTS"),
        "conflicts": postures.count("EVIDENCE_CONFLICT"),
        "requires_cleaner_data": postures.count("REQUIRES_CLEANER_DATA"),
        "scan_finished_at": context.get("scan_finished_at"),
        "qc_decision": (((context.get("qc") or {}).get("trading_gate") or {}).get("decision")),
        "generated_at": _now().isoformat(),
    }


async def run_trials(limit: int = MAX_TRIALS, persist: bool = False) -> dict[str, Any]:
    from . import data_quality, kronos, options_desk, portfolio_manager, scanner

    limit = max(1, min(int(limit or MAX_TRIALS), 75))
    scan_task = _bounded("scan", scanner.latest_scan(), timeout=4.0)
    pm_task = _bounded("pm", portfolio_manager.latest_portfolio_plan(), timeout=8.0)
    options_task = _bounded("options", options_desk.candidates(), timeout=8.0)
    kronos_task = _bounded("kronos", kronos.forecast(persist=False), timeout=8.0)
    qc_task = _bounded("qc", data_quality.overview(force_refresh=False), timeout=15.0)
    scan, pm, options, kronos_payload, qc = await asyncio.gather(scan_task, pm_task, options_task, kronos_task, qc_task)

    scan_rows = _scan_by_ticker(scan if isinstance(scan, dict) else {})
    pm_rows = _rows(pm, "recommendations")
    options_rows = _candidate_by_ticker(options if isinstance(options, dict) else {})
    kronos_rows = _kronos_by_ticker(kronos_payload if isinstance(kronos_payload, dict) else {})
    scan_finished_at = (scan if isinstance(scan, dict) else {}).get("finished_at")

    trials = []
    for pm_row in pm_rows[:limit]:
        ticker = _ticker(pm_row.get("ticker"))
        scan_row = scan_rows.get(ticker, {"ticker": ticker, "signals": pm_row.get("signals") or []})
        trials.append(_trial(scan_row, pm_row, options_rows.get(ticker), kronos_rows.get(ticker), qc if isinstance(qc, dict) else {}, scan_finished_at))

    context = {
        "scan_finished_at": scan_finished_at,
        "scan_error": scan.get("error") if isinstance(scan, dict) else None,
        "pm_error": pm.get("error") if isinstance(pm, dict) else None,
        "options_error": options.get("error") if isinstance(options, dict) else None,
        "kronos_error": kronos_payload.get("error") if isinstance(kronos_payload, dict) else None,
        "qc": qc if isinstance(qc, dict) else {},
    }
    payload = {
        "ok": True,
        "mode": "advisory_only",
        "summary": _summary(trials, context),
        "context": {k: v for k, v in context.items() if k != "qc"},
        "trials": trials,
    }
    if persist:
        db = get_db()
        await db.case_court_trials.delete_many({})
        if trials:
            await db.case_court_trials.insert_many([stamped(t) for t in trials])
        await db.bot_state.update_one(
            {"_id": "case_court_latest"},
            {"$set": stamped({"summary": payload["summary"], "updated_at": _now().isoformat()})},
            upsert=True,
        )
    return payload


async def latest(limit: int = MAX_TRIALS) -> dict[str, Any]:
    db = get_db()
    rows = await db.case_court_trials.find({}, {"_id": 0}).sort("generated_at", -1).to_list(limit)
    if rows:
        return {
            "ok": True,
            "mode": "advisory_only",
            "summary": _summary(rows, {"scan_finished_at": rows[0].get("scan_finished_at"), "qc": {}}),
            "trials": rows,
            "source": "persisted",
        }
    return await run_trials(limit=limit, persist=False)


async def trial(ticker: str) -> dict[str, Any]:
    t = _ticker(ticker)
    if not t:
        return {"ok": False, "error": "ticker_required"}
    latest_payload = await latest(limit=75)
    row = next((r for r in latest_payload.get("trials", []) if _ticker(r.get("ticker")) == t), None)
    if row:
        return {"ok": True, "trial": row, "source": latest_payload.get("source", "live")}
    return {"ok": False, "error": "trial_not_found", "ticker": t}
