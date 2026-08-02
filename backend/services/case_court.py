"""Case Court: read-only adversarial review for scanner candidates.

V2 uses scoped evidence exhibits. Non-applicable, missing optional, or stale
optional evidence is neutral and does not award points to either side. Missing
required evidence only helps the Prosecutor when that evidence is required for
the specific mini-trial being judged.
"""
from __future__ import annotations

import asyncio
import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from .db import get_db, stamped


MAX_TRIALS = 30
DEFENSE = "DEFENSE"
PROSECUTOR = "PROSECUTOR"
NEUTRAL = "NEUTRAL"
NOT_APPLICABLE = "NOT_APPLICABLE"
MISSING_REQUIRED = "MISSING_REQUIRED"
STALE_REQUIRED = "STALE_REQUIRED"

SIGNAL_QUALITY = {
    "CONTRACT_SURGE": 15,
    "NEW_WINNER": 11,
    "MOMENTUM_STACK": 12,
    "insider_cluster_buy": 13,
    "high_short_interest": 7,
    "upcoming_earnings": 5,
    "UNUSUAL_FLOW": 12,
    "DARK_POOL": 10,
    "NARRATIVE_LOCK": 9,
}


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
    for fallback in ("rows", "data", "candidates", "recommendations", "forecasts"):
        val = payload.get(fallback)
        if isinstance(val, list):
            return [r for r in val if isinstance(r, dict)]
    return []


def _by_ticker(payload: dict[str, Any], key: str) -> dict[str, dict[str, Any]]:
    return {_ticker(r.get("ticker")): r for r in _rows(payload, key) if _ticker(r.get("ticker"))}


def _field(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if "." in key:
            cur: Any = row
            ok = True
            for part in key.split("."):
                if not isinstance(cur, dict) or part not in cur:
                    ok = False
                    break
                cur = cur.get(part)
            if ok and cur not in (None, "", [], {}):
                return cur
        elif row.get(key) not in (None, "", [], {}):
            return row.get(key)
    return None


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


def _required_for(exhibit: str, mini_trial: str, expression: str) -> bool:
    if exhibit in {"scanner", "pm"}:
        return True
    if exhibit == "options":
        return mini_trial in {"options_contract", "execution_quality"} and expression in {"OPTION", "BOTH", "OPTION_OR_BOTH_ADVISORY"}
    return False


def _exhibit(
    key: str,
    label: str,
    status: str,
    side: str = NEUTRAL,
    score: float = 0.0,
    detail: str = "",
    source: str = "",
    applicable: bool = True,
    required: bool = False,
    freshness: str = "",
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    score = max(0.0, min(25.0, float(score or 0.0)))
    if not applicable:
        status, side, score = NOT_APPLICABLE, NEUTRAL, 0.0
    elif status in {NOT_APPLICABLE, "MISSING_OPTIONAL", "STALE_OPTIONAL"}:
        side, score = NEUTRAL, 0.0
    elif status in {MISSING_REQUIRED, STALE_REQUIRED}:
        side = PROSECUTOR
        score = max(score, 10.0)
    elif side not in {DEFENSE, PROSECUTOR}:
        side, score = NEUTRAL, 0.0
    return {
        "key": key,
        "label": label,
        "status": status,
        "side": side,
        "score": round(score, 1),
        "detail": detail,
        "source": source,
        "applicable": applicable,
        "required": required,
        "freshness": freshness,
        "data": data or {},
    }


def _scanner_exhibit(row: dict[str, Any], scan_age: float | None) -> dict[str, Any]:
    sigs = _signals(row)
    score = min(25, sum(SIGNAL_QUALITY.get(s, 4) for s in sigs))
    if not sigs:
        return _exhibit("scanner", "Scanner Evidence", MISSING_REQUIRED, score=14, detail="No scanner signal stack was found.", source="scan_results", required=True)
    if scan_age is None or scan_age > 26:
        return _exhibit("scanner", "Scanner Evidence", STALE_REQUIRED, score=14, detail=f"Latest stock scan age is {scan_age}h.", source="scan_results", required=True)
    return _exhibit(
        "scanner",
        "Scanner Evidence",
        "BULLISH",
        DEFENSE,
        score,
        f"{len(sigs)} signals: {', '.join(sigs[:5])}.",
        "scan_results",
        required=True,
        freshness=f"{scan_age:.1f}h old" if scan_age is not None else "unknown",
        data={"signals": sigs, "trade_score": row.get("trade_score"), "signal_score": row.get("signal_score")},
    )


def _pm_exhibit(pm_row: dict[str, Any]) -> dict[str, Any]:
    action = str(pm_row.get("action") or "UNKNOWN").upper()
    score = _num(pm_row.get("pm_score"))
    rr = _num(pm_row.get("risk_reward"))
    if action in {"ACCUMULATE", "STARTER"}:
        return _exhibit("pm", "Portfolio Manager", "BULLISH", DEFENSE, min(25, score * 0.28), f"PM says {action}; score {score:.1f}, RR {rr:.2f}.", "portfolio_manager", required=True, data={"action": action, "pm_score": score, "risk_reward": rr})
    if action == "REJECT":
        pm_gap = max(0.0, 60.0 - score)
        rr_penalty = 10.0 if rr < 1.0 else 6.0 if rr < 1.3 else 0.0
        return _exhibit("pm", "Portfolio Manager", "BEARISH", PROSECUTOR, min(32, max(18, pm_gap * 0.55 + rr_penalty)), f"PM rejects sizing; score {score:.1f}, RR {rr:.2f}.", "portfolio_manager", required=True, data={"action": action, "pm_score": score, "risk_reward": rr})
    return _exhibit("pm", "Portfolio Manager", "NEUTRAL", NEUTRAL, 0, f"PM is watching; score {score:.1f}, RR {rr:.2f}.", "portfolio_manager", required=True, data={"action": action, "pm_score": score, "risk_reward": rr})


def _entry_exhibit(scan_row: dict[str, Any], pm_row: dict[str, Any]) -> dict[str, Any]:
    action = str(pm_row.get("action") or "UNKNOWN").upper()
    entry = _entry_position(scan_row, pm_row)
    price = _num(pm_row.get("price") or scan_row.get("price"))
    low = _num(pm_row.get("entry_low") or scan_row.get("entry_low"))
    high = _num(pm_row.get("entry_high") or scan_row.get("entry_high"))
    if entry == "IN_BAND":
        if action == "REJECT":
            return _exhibit("entry", "Entry Band", "NEUTRAL", NEUTRAL, 0, f"Price {price:.2f} is inside {low:.2f}-{high:.2f}, but PM rejected the trade.", "portfolio_manager")
        return _exhibit("entry", "Entry Band", "BULLISH", DEFENSE, 10, f"Price {price:.2f} is inside {low:.2f}-{high:.2f}.", "portfolio_manager")
    if entry == "ABOVE_BAND":
        return _exhibit("entry", "Entry Band", "BEARISH", PROSECUTOR, 16, f"Price {price:.2f} is above entry band {low:.2f}-{high:.2f}.", "portfolio_manager")
    return _exhibit("entry", "Entry Band", "NEUTRAL", NEUTRAL, 0, f"Entry band status is {entry}.", "portfolio_manager")


def _risk_exhibit(scan_row: dict[str, Any], pm_row: dict[str, Any]) -> dict[str, Any]:
    action = str(pm_row.get("action") or "UNKNOWN").upper()
    risk_score = _num((scan_row.get("risk") or {}).get("score"))
    downside = _num(pm_row.get("downside_pct"))
    if risk_score >= 75:
        return _exhibit("risk", "Risk Engine", "BEARISH", PROSECUTOR, 20, f"Risk score is elevated at {risk_score:.1f}; downside to stop {downside:.1f}%.", "risk_target")
    if risk_score >= 55:
        return _exhibit("risk", "Risk Engine", "NEUTRAL", NEUTRAL, 0, f"Risk score is watch-level at {risk_score:.1f}.", "risk_target")
    if action == "REJECT":
        return _exhibit("risk", "Risk Engine", "NEUTRAL", NEUTRAL, 0, f"Risk score is contained at {risk_score:.1f}, but PM rejected sizing; risk containment alone cannot support the Defense.", "risk_target")
    return _exhibit("risk", "Risk Engine", "BULLISH", DEFENSE, 8, f"Risk score is contained at {risk_score:.1f}; downside to stop {downside:.1f}%.", "risk_target")


def _qc_exhibits(qc: dict[str, Any], expression: str) -> list[dict[str, Any]]:
    gate = (qc.get("trading_gate") or {}) if isinstance(qc, dict) else {}
    blockers = gate.get("blockers") or []
    out = []
    if not isinstance(qc, dict) or not gate:
        return [_exhibit("qc", "QC Gate", MISSING_REQUIRED, score=14, detail="QC overview was unavailable.", source="data_quality", required=True)]
    blocking_labels = []
    for b in blockers:
        key = str(b.get("key") or "")
        label = str(b.get("label") or key)
        affects_options = "option" in key.lower() or "option" in label.lower()
        affects_lse = "london_strategic_edge" in key.lower() or "lse" in label.lower()
        required = True
        applicable = True
        if affects_options and expression not in {"OPTION", "BOTH", "OPTION_OR_BOTH_ADVISORY"}:
            applicable = False
            required = False
        if affects_lse:
            required = False
        if applicable and required:
            blocking_labels.append(label)
        status = "BEARISH" if applicable and required else "MISSING_OPTIONAL" if affects_lse else NOT_APPLICABLE
        side = PROSECUTOR if applicable and required else NEUTRAL
        out.append(_exhibit(
            f"qc:{key}",
            f"QC - {label}",
            status,
            side,
            18 if applicable and required else 0,
            _clean_blocker(b),
            "data_quality",
            applicable=applicable,
            required=required,
            freshness=f"{b.get('age_minutes')}m" if b.get("age_minutes") is not None else "",
            data={"refresh_endpoint": b.get("refresh_endpoint"), "warnings": b.get("warnings") or []},
        ))
    decision = str(gate.get("decision") or "").upper()
    if decision == "BLOCK":
        out.insert(0, _exhibit("qc", "QC Gate", "BEARISH", PROSECUTOR, 20, f"QC decision BLOCK for {expression}; execution cannot be treated as clean even if no scoped blocker was parsed.", "data_quality", required=True, data={"decision": gate.get("decision")}))
    elif not blockers or not blocking_labels:
        out.insert(0, _exhibit("qc", "QC Gate", "BULLISH", DEFENSE, 9, f"QC decision {gate.get('decision')}; no required blocker for {expression}.", "data_quality", required=True, data={"decision": gate.get("decision")}))
    return out


def _kronos_exhibit(row: dict[str, Any], required: bool) -> dict[str, Any]:
    if not row:
        status = MISSING_REQUIRED if required else "MISSING_OPTIONAL"
        return _exhibit("kronos", "Kronos Forecast", status, score=10 if required else 0, detail="No Kronos forecast was available for this ticker.", source="kronos", required=required)
    bias = str(row.get("bias") or row.get("forecast_bias") or "UNKNOWN").upper()
    confidence = _num(row.get("confidence"), default=-1)
    score = min(14, max(4, confidence * 0.14 if confidence >= 0 else 4))
    if bias == "BULLISH":
        return _exhibit("kronos", "Kronos Forecast", "BULLISH", DEFENSE, score, f"Kronos is bullish with {confidence if confidence >= 0 else '-'} confidence.", "kronos", required=required, data=row)
    if bias == "BEARISH":
        return _exhibit("kronos", "Kronos Forecast", "BEARISH", PROSECUTOR, score, f"Kronos is bearish with {confidence if confidence >= 0 else '-'} confidence.", "kronos", required=required, data=row)
    return _exhibit("kronos", "Kronos Forecast", "NEUTRAL", NEUTRAL, 0, f"Kronos bias is {bias}.", "kronos", required=required, data=row)


def _options_exhibit(row: dict[str, Any] | None, expression: str) -> dict[str, Any]:
    required = expression in {"OPTION", "BOTH", "OPTION_OR_BOTH_ADVISORY"}
    if not required:
        return _exhibit("options", "Options Contract", NOT_APPLICABLE, detail="PM expression is equity/watch/pass; options data does not score this case.", source="options_desk", applicable=False)
    if not row:
        return _exhibit("options", "Options Contract", MISSING_REQUIRED, score=16, detail="PM route needs options, but no options candidate exists.", source="options_desk", required=True)
    blocks = row.get("blocked_reasons") or []
    quality = row.get("quality_state") or row.get("data_quality") or "UNKNOWN"
    if row.get("manual_fire_ready"):
        return _exhibit("options", "Options Contract", "BULLISH", DEFENSE, 16, f"Options ticket ready; route {row.get('route')}, risk budget ${row.get('risk_budget')}.", "options_desk", required=True, data={"route": row.get("route"), "strategy": row.get("strategy"), "quality": quality, "contracts": row.get("contracts")})
    return _exhibit("options", "Options Contract", "BEARISH", PROSECUTOR, min(20, 8 + len(blocks) * 4), "; ".join(str(b) for b in blocks[:4]) or f"Options ticket is not ready; quality {quality}.", "options_desk", required=True, data={"route": row.get("route"), "strategy": row.get("strategy"), "quality": quality, "blocks": blocks})


def _fundamentals_exhibit(row: dict[str, Any]) -> dict[str, Any]:
    sector = row.get("sector")
    market_cap = _field(row, "market_cap", "fundamentals.market_cap")
    if not sector and market_cap is None:
        return _exhibit("fundamentals", "Fundamentals", "MISSING_OPTIONAL", detail="Fundamental profile is not populated; neutral until company data is available.", source="scanner/free_data")
    risk = _num((row.get("risk") or {}).get("score"))
    if risk >= 75:
        return _exhibit("fundamentals", "Fundamentals", "BEARISH", PROSECUTOR, 10, f"Fundamental/risk profile is elevated; sector {sector or 'unknown'}.", "scanner/free_data", data={"sector": sector, "market_cap": market_cap})
    return _exhibit("fundamentals", "Fundamentals", "NEUTRAL", NEUTRAL, 0, f"Sector {sector or 'unknown'}; no disqualifying fundamental flag in scanner row.", "scanner/free_data", data={"sector": sector, "market_cap": market_cap})


def _earnings_exhibit(row: dict[str, Any]) -> dict[str, Any]:
    earnings = row.get("earnings_summary") or {}
    has_earnings = bool(earnings or row.get("earnings_this_week") or "upcoming_earnings" in _signals(row))
    if not has_earnings:
        return _exhibit("earnings", "Earnings", NOT_APPLICABLE, detail="No near-term earnings catalyst in this scan row.", source="earnings_engine", applicable=False)
    detail = f"Earnings catalyst present: {earnings.get('earnings_date') or 'date not supplied'}."
    return _exhibit("earnings", "Earnings", "NEUTRAL", NEUTRAL, 0, detail, "earnings_engine", data={"earnings_summary": earnings, "earnings_this_week": row.get("earnings_this_week")})


def _sec_exhibit(sec_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not sec_rows:
        return _exhibit("sec", "SEC Filings", "MISSING_OPTIONAL", detail="No recent SEC filing match found; neutral unless SEC feed itself fails.", source="sec_filings")
    forms = ", ".join(str(r.get("form") or "?") for r in sec_rows[:3])
    max_sig = max(_num(r.get("significance")) for r in sec_rows)
    side = PROSECUTOR if max_sig >= 80 else NEUTRAL
    score = 10 if side == PROSECUTOR else 0
    status = "BEARISH" if side == PROSECUTOR else "NEUTRAL"
    return _exhibit("sec", "SEC Filings", status, side, score, f"{len(sec_rows)} recent filing(s): {forms}. Max significance {max_sig:.0f}.", "sec_filings", data={"recent": sec_rows[:5]})


def _precedent_exhibit(count: int, row: dict[str, Any]) -> dict[str, Any]:
    if count <= 0:
        return _exhibit("precedent", "Historical Precedent", "MISSING_OPTIONAL", detail="No historical signal record for this ticker yet.", source="signal_performance")
    return _exhibit("precedent", "Historical Precedent", "NEUTRAL", NEUTRAL, 0, f"{count} stored signal-performance record(s) exist, but precedent is neutral until win-rate/return evidence is attached.", "signal_performance", data={"records": count})


def _intel_exhibit(ticker: str, news_rows: list[dict[str, Any]]) -> dict[str, Any]:
    hay = [r for r in news_rows if f"${ticker}" in str(r).upper() or f'"{ticker}"' in str(r).upper()]
    if not hay:
        return _exhibit("intel", "Intel Feed", "MISSING_OPTIONAL", detail="No ticker-specific active/discovery headline matched this case.", source="news_intel")
    return _exhibit("intel", "Intel Feed", "BULLISH", DEFENSE, min(8, len(hay) * 2), f"{len(hay)} matching intel headline(s) found.", "news_intel", data={"headlines": hay[:5]})


def _clean_blocker(blocker: dict[str, Any]) -> str:
    label = blocker.get("label") or blocker.get("key") or "QC blocker"
    warnings = blocker.get("warnings") or []
    if warnings:
        return f"{label}: {'; '.join(str(w) for w in warnings[:3])}."
    return f"{label}: {blocker.get('detail') or 'QC blocker is active'}."


def _score_side(exhibits: list[dict[str, Any]], side: str) -> float:
    return round(sum(float(e.get("score") or 0) for e in exhibits if e.get("side") == side), 1)


def _coverage(exhibits: list[dict[str, Any]]) -> dict[str, Any]:
    applicable = [e for e in exhibits if e.get("applicable")]
    required = [e for e in exhibits if e.get("required")]
    missing_required = [e for e in exhibits if e.get("status") in {MISSING_REQUIRED, STALE_REQUIRED}]
    scored = [e for e in applicable if e.get("side") in {DEFENSE, PROSECUTOR}]
    coverage_pct = round(len(scored) / max(1, len(applicable)) * 100, 1)
    required_pct = round((len(required) - len(missing_required)) / max(1, len(required)) * 100, 1)
    certified = not missing_required and coverage_pct >= 45 and required_pct >= 100
    return {
        "applicable": len(applicable),
        "scored": len(scored),
        "required": len(required),
        "missing_required": len(missing_required),
        "decision_grade": certified,
        "coverage_pct": coverage_pct,
        "required_pct": required_pct,
        "coverage_label": f"{len(scored)}/{len(applicable)} scored",
        "missing_required_labels": [e.get("label") for e in missing_required],
        "certification": "CERTIFIED" if certified else "ADVISORY_ONLY",
    }


def _brief(side: str, exhibits: list[dict[str, Any]]) -> dict[str, Any]:
    points = sorted([e for e in exhibits if e.get("side") == side], key=lambda e: e.get("score") or 0, reverse=True)
    if side == DEFENSE:
        opening = "The Defense argues only from applicable bullish evidence and receives no benefit from missing optional exhibits."
        name = "Defense"
        stance = "BULLISH"
    else:
        opening = "The Prosecutor argues only from applicable bearish evidence, missing required exhibits, and execution blockers."
        name = "Prosecutor"
        stance = "BEARISH"
    return {
        "name": name,
        "stance": stance,
        "score": _score_side(exhibits, side),
        "opening_argument": opening if side == DEFENSE else None,
        "opening_objection": opening if side == PROSECUTOR else None,
        "points": [
            {
                "label": e["label"],
                "weight": e["score"],
                "detail": e["detail"],
                "source": e["source"],
                "status": e["status"],
            }
            for e in points[:10]
        ],
    }


def _mini_trial(name: str, expression: str, exhibits: list[dict[str, Any]], pm_action: str) -> dict[str, Any]:
    if name == "options_contract" and expression not in {"OPTION", "BOTH", "OPTION_OR_BOTH_ADVISORY"}:
        return {
            "name": name,
            "verdict": NOT_APPLICABLE,
            "defense_score": 0.0,
            "prosecution_score": 0.0,
            "spread": 0.0,
            "decision_grade": True,
            "missing_required": [],
        }
    if name == "leaps" and expression != "LEAPS":
        return {
            "name": name,
            "verdict": NOT_APPLICABLE,
            "defense_score": 0.0,
            "prosecution_score": 0.0,
            "spread": 0.0,
            "decision_grade": True,
            "missing_required": [],
        }
    scoped = []
    for e in exhibits:
        required = bool(e.get("required")) or _required_for(e["key"].split(":")[0], name, expression)
        if required and e["status"] in {"MISSING_OPTIONAL", NOT_APPLICABLE}:
            scoped.append({**e, "status": MISSING_REQUIRED, "side": PROSECUTOR, "score": 10.0, "required": True, "applicable": True})
        elif name == "options_contract" and e["key"].split(":")[0] != "options":
            continue
        elif name == "execution_quality" and e["key"].split(":")[0] not in {"qc", "options", "entry"}:
            continue
        elif name == "ticker_quality" and e["key"].split(":")[0] in {"options", "entry"}:
            continue
        else:
            scoped.append(e)
    defense = _score_side(scoped, DEFENSE)
    prosecution = _score_side(scoped, PROSECUTOR)
    spread = round(defense - prosecution, 1)
    missing_required = [e for e in scoped if e.get("status") in {MISSING_REQUIRED, STALE_REQUIRED}]
    if missing_required:
        verdict = "NOT_DECISION_GRADE"
    elif spread >= 25 and (name != "trade_quality" or pm_action in {"ACCUMULATE", "STARTER"}):
        verdict = "SUPPORTS_PM"
    elif spread >= 10:
        verdict = "BULLISH_WATCH"
    elif spread <= -12:
        verdict = "OBJECTS"
    else:
        verdict = "EVIDENCE_CONFLICT"
    return {
        "name": name,
        "verdict": verdict,
        "defense_score": defense,
        "prosecution_score": prosecution,
        "spread": spread,
        "decision_grade": not missing_required,
        "missing_required": [e["label"] for e in missing_required],
    }


def _judge(mini_trials: list[dict[str, Any]], defense: dict[str, Any], prosecution: dict[str, Any], pm_row: dict[str, Any], expression: str) -> dict[str, Any]:
    d = _num(defense.get("score"))
    p = _num(prosecution.get("score"))
    spread = round(d - p, 1)
    pm_action = str(pm_row.get("action") or "UNKNOWN").upper()
    not_grade = [t for t in mini_trials if not t.get("decision_grade")]
    option_trial = next((t for t in mini_trials if t["name"] == "options_contract"), None)
    equity_ok = all(t.get("decision_grade") for t in mini_trials if t["name"] in {"ticker_quality", "trade_quality"})

    if pm_action == "REJECT" or expression == "PASS":
        posture = "PM_REJECTED"
        detail = "PM rejected or passed on the setup; Case Court cannot elevate scanner evidence into authority."
    elif not_grade and not equity_ok:
        posture = "REQUIRES_CLEANER_DATA"
        detail = "Required evidence is missing for the ticker or equity trade trial."
    elif expression in {"OPTION", "BOTH", "OPTION_OR_BOTH_ADVISORY"} and option_trial and not option_trial.get("decision_grade"):
        posture = "EQUITY_ONLY_UNTIL_OPTIONS_CLEAN"
        detail = "Equity evidence can stand, but options evidence is not decision-grade."
    elif spread >= 25 and pm_action in {"ACCUMULATE", "STARTER"}:
        posture = "COURT_SUPPORTS_PM"
        detail = "Defense materially outweighs prosecution and PM is already constructive."
    elif spread >= 10:
        posture = "BULLISH_WATCH"
        detail = "Defense leads, but PM has not approved capital; this stays watch-only."
    elif spread <= -12:
        posture = "COURT_OBJECTS"
        detail = "Prosecution has enough applicable evidence to challenge capital allocation."
    else:
        posture = "EVIDENCE_CONFLICT"
        detail = "The record is mixed; preserve for appeal rather than authority."

    if posture == "COURT_SUPPORTS_PM" and expression in {"OPTION", "BOTH", "OPTION_OR_BOTH_ADVISORY"}:
        expression_hint = "OPTION_OR_BOTH_ADVISORY"
    elif posture in {"COURT_SUPPORTS_PM", "EQUITY_ONLY_UNTIL_OPTIONS_CLEAN"} and pm_action in {"ACCUMULATE", "STARTER"}:
        expression_hint = "EQUITY_ADVISORY"
    elif posture == "BULLISH_WATCH":
        expression_hint = "WATCHLIST_ONLY"
    else:
        expression_hint = "NO_AUTHORITY"
    return {
        "advisory_posture": posture,
        "expression_hint": expression_hint,
        "defense_minus_prosecutor": spread,
        "detail": detail,
        "authority": "READ_ONLY_NO_EXECUTION_NO_PM_OVERRIDE",
        "live_run_ready": posture in {"COURT_SUPPORTS_PM", "EQUITY_ONLY_UNTIL_OPTIONS_CLEAN"} and pm_action in {"ACCUMULATE", "STARTER"} and equity_ok,
    }


def _witnesses(exhibits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stance_map = {DEFENSE: "BULL", PROSECUTOR: "BEAR", NEUTRAL: "NEUTRAL"}
    return [
        {
            "name": e["label"],
            "stance": stance_map.get(e.get("side"), "NEUTRAL"),
            "score": e.get("score"),
            "status": e.get("status"),
            "testimony": e.get("detail"),
            "source": e.get("source"),
        }
        for e in exhibits
    ]


def _appeal_triggers(scan_row: dict[str, Any], pm_row: dict[str, Any], options_row: dict[str, Any] | None, judge: dict[str, Any], coverage: dict[str, Any]) -> list[str]:
    triggers = []
    if coverage.get("missing_required_labels"):
        triggers.append("required evidence becomes decision-grade: " + ", ".join(coverage["missing_required_labels"][:3]))
    if _entry_position(scan_row, pm_row) == "ABOVE_BAND":
        triggers.append("price returns inside PM entry band")
    if (options_row or {}).get("blocked_reasons"):
        triggers.append("options chain refresh clears liquidity/data blockers")
    if judge.get("advisory_posture") in {"COURT_OBJECTS", "EVIDENCE_CONFLICT", "BULLISH_WATCH"}:
        triggers.append("fresh scan upgrades PM score, signal quality, or precedent")
    triggers.append("new SEC, earnings, macro, Intel, or Kronos exhibit changes the court record")
    return triggers[:5]


async def _sec_rows(ticker: str, limit: int = 5) -> list[dict[str, Any]]:
    db = get_db()
    return await db.sec_filings.find({"ticker": ticker}, {"_id": 0}).sort("accepted_at", -1).to_list(limit)


async def _precedent_count(ticker: str) -> int:
    db = get_db()
    return await db.signal_performance.count_documents({"ticker": ticker})


async def _latest_news_rows() -> list[dict[str, Any]]:
    db = get_db()
    snap = await db.news_intel_snapshots.find_one({}, {"_id": 0}, sort=[("created_at", -1)]) or {}
    rows: list[dict[str, Any]] = []
    for key in ("items", "rows", "headlines", "active", "discovery"):
        val = snap.get(key)
        if isinstance(val, list):
            rows.extend([r for r in val if isinstance(r, dict)])
    return rows


def _expression(pm_row: dict[str, Any], options_row: dict[str, Any] | None) -> str:
    route = str((options_row or {}).get("route") or "").upper()
    if route in {"OPTION", "BOTH"}:
        return route
    view = str(pm_row.get("option_view") or "").upper()
    if view in {"CALL_ALLOWED", "SPREAD_ONLY"}:
        return "OPTION"
    if str(pm_row.get("action") or "").upper() in {"ACCUMULATE", "STARTER", "WATCH"}:
        return "EQUITY"
    return "PASS"


async def _trial(
    scan_row: dict[str, Any],
    pm_row: dict[str, Any],
    options_row: dict[str, Any] | None,
    kronos_row: dict[str, Any] | None,
    qc: dict[str, Any],
    scan_finished_at: str | None,
    news_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    ticker = _ticker(scan_row.get("ticker") or pm_row.get("ticker"))
    scan_age = _age_hours(scan_finished_at)
    expression = _expression(pm_row, options_row)
    sec_rows, precedent_count = await asyncio.gather(_sec_rows(ticker), _precedent_count(ticker))

    exhibits: list[dict[str, Any]] = [
        _scanner_exhibit(scan_row, scan_age),
        _pm_exhibit(pm_row),
        _entry_exhibit(scan_row, pm_row),
        _risk_exhibit(scan_row, pm_row),
        _kronos_exhibit(kronos_row or {}, required=False),
        _options_exhibit(options_row, expression),
        _fundamentals_exhibit(scan_row),
        _earnings_exhibit(scan_row),
        _sec_exhibit(sec_rows),
        _precedent_exhibit(precedent_count, scan_row),
        _intel_exhibit(ticker, news_rows),
    ]
    exhibits.extend(_qc_exhibits(qc if isinstance(qc, dict) else {}, expression))

    defense = _brief(DEFENSE, exhibits)
    prosecution = _brief(PROSECUTOR, exhibits)
    pm_action = str(pm_row.get("action") or "").upper()
    mini_trials = [
        _mini_trial("ticker_quality", expression, exhibits, pm_action),
        _mini_trial("trade_quality", expression, exhibits, pm_action),
        _mini_trial("options_contract", expression, exhibits, pm_action),
        _mini_trial("execution_quality", expression, exhibits, pm_action),
    ]
    if expression in {"EQUITY", "OPTION", "BOTH"}:
        mini_trials.append(_mini_trial("leaps", expression, exhibits, pm_action))
    coverage = _coverage(exhibits)
    judge = _judge(mini_trials, defense, prosecution, pm_row, expression)
    if coverage.get("certification") != "CERTIFIED" and judge.get("live_run_ready"):
        judge = {
            **judge,
            "live_run_ready": False,
            "authority": "READ_ONLY_INSUFFICIENT_CERTIFICATION",
            "detail": f"{judge.get('detail')} Evidence coverage is {coverage.get('coverage_label')}; court remains advisory.",
        }

    return {
        "case_id": f"court-{ticker}-{str(scan_finished_at or _now().isoformat())[:19]}",
        "rubric_version": "case-court-v2-scoped-exhibits",
        "ticker": ticker,
        "court_status": "ADVISORY_TRIAL",
        "charge": "Potential capital allocation",
        "expression_under_review": expression,
        "scan_finished_at": scan_finished_at,
        "scan_age_hours": round(scan_age, 2) if scan_age is not None else None,
        "pm_action": pm_row.get("action"),
        "pm_score": pm_row.get("pm_score"),
        "price": pm_row.get("price") or scan_row.get("price"),
        "sector": pm_row.get("sector") or scan_row.get("sector"),
        "signals": _signals(scan_row),
        "defense": defense,
        "prosecution": prosecution,
        "witnesses": _witnesses(exhibits),
        "exhibits": exhibits,
        "mini_trials": mini_trials,
        "evidence_coverage": coverage,
        "judge": judge,
        "appeal_triggers": _appeal_triggers(scan_row, pm_row, options_row, judge, coverage),
        "court_docs": {
            "caption": f"Case Capital Court v. {ticker}",
            "docket_entry": f"{ticker} reviewed from scan {scan_finished_at or 'unknown'} as {expression}.",
            "clerk_notes": "Read-only advisory record. Neutral exhibits do not score either side.",
            "evidence_standard": "Applicable evidence only. Missing optional and not-applicable exhibits are neutral.",
        },
        "generated_at": _now().isoformat(),
    }


def _summary(trials: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    postures = [((t.get("judge") or {}).get("advisory_posture") or "UNKNOWN") for t in trials]
    coverage_counts = Counter()
    neutralized = 0
    for t in trials:
        for e in t.get("exhibits") or []:
            if e.get("side") in {DEFENSE, PROSECUTOR}:
                coverage_counts[e.get("key", "").split(":")[0]] += 1
            if e.get("status") in {NOT_APPLICABLE, "MISSING_OPTIONAL", "STALE_OPTIONAL"}:
                neutralized += 1
    return {
        "authority": "READ_ONLY_NO_EXECUTION",
        "rubric_version": "case-court-v2-scoped-exhibits",
        "trials": len(trials),
        "supports_pm": postures.count("COURT_SUPPORTS_PM"),
        "bullish_watch": postures.count("BULLISH_WATCH"),
        "objects": postures.count("COURT_OBJECTS"),
        "pm_rejected": postures.count("PM_REJECTED"),
        "conflicts": postures.count("EVIDENCE_CONFLICT"),
        "requires_cleaner_data": postures.count("REQUIRES_CLEANER_DATA"),
        "equity_only_until_options_clean": postures.count("EQUITY_ONLY_UNTIL_OPTIONS_CLEAN"),
        "live_run_ready": sum(1 for t in trials if (t.get("judge") or {}).get("live_run_ready")),
        "decision_grade": sum(1 for t in trials if (t.get("evidence_coverage") or {}).get("decision_grade")),
        "neutralized_exhibits": neutralized,
        "scan_finished_at": context.get("scan_finished_at"),
        "qc_decision": (((context.get("qc") or {}).get("trading_gate") or {}).get("decision")),
        "scored_exhibit_counts": dict(coverage_counts),
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
    news_task = _bounded("news", _latest_news_rows(), timeout=3.0)
    scan, pm, options, kronos_payload, qc, news_rows = await asyncio.gather(scan_task, pm_task, options_task, kronos_task, qc_task, news_task)

    scan_rows = _by_ticker(scan if isinstance(scan, dict) else {}, "results")
    pm_rows = _rows(pm, "recommendations")
    options_rows = _by_ticker(options if isinstance(options, dict) else {}, "candidates")
    kronos_rows = _by_ticker(kronos_payload if isinstance(kronos_payload, dict) else {}, "forecasts")
    scan_finished_at = (scan if isinstance(scan, dict) else {}).get("finished_at")
    news_list = news_rows if isinstance(news_rows, list) else []

    trials = []
    for pm_row in pm_rows[:limit]:
        ticker = _ticker(pm_row.get("ticker"))
        scan_row = scan_rows.get(ticker, {"ticker": ticker, "signals": pm_row.get("signals") or []})
        trials.append(await _trial(scan_row, pm_row, options_rows.get(ticker), kronos_rows.get(ticker), qc if isinstance(qc, dict) else {}, scan_finished_at, news_list))

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
    if rows and rows[0].get("rubric_version") == "case-court-v2-scoped-exhibits":
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
