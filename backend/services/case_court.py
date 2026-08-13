"""Case Court: read-only adversarial review for scanner candidates.

V3 turns exhibits into facts with contested doctrine claims. The Defense and
Prosecutor read the same evidence board, argue from admissible claims, and the
PM-mode standard of proof decides whether the record supports, objects, or
requires a mistrial. Case Court remains advisory and never grants execution
authority.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import get_db, stamped


MAX_TRIALS = 30
RUBRIC_VERSION = "case-court-v3-contested-exhibits"
DEFENSE = "DEFENSE"
PROSECUTOR = "PROSECUTOR"
NEUTRAL = "NEUTRAL"
NOT_APPLICABLE = "NOT_APPLICABLE"
MISSING_REQUIRED = "MISSING_REQUIRED"
STALE_REQUIRED = "STALE_REQUIRED"

STANDARD_OF_PROOF = {
    "RISK_OFF": {
        "standard": "BEYOND_REASONABLE_DOUBT",
        "defense_must_exceed": 3.0,
        "min_affirmative_defense_classes": 3,
        "contested_exhibit_goes_to": PROSECUTOR,
    },
    "CONSERVATIVE": {
        "standard": "CLEAR_AND_CONVINCING",
        "defense_must_exceed": 2.0,
        "min_affirmative_defense_classes": 2,
        "contested_exhibit_goes_to": PROSECUTOR,
    },
    "BALANCED": {
        "standard": "PREPONDERANCE",
        "defense_must_exceed": 1.35,
        "min_affirmative_defense_classes": 2,
        "contested_exhibit_goes_to": "SPLIT",
    },
    "AGGRESSIVE": {
        "standard": "PROBABLE_CAUSE",
        "defense_must_exceed": 1.0,
        "min_affirmative_defense_classes": 1,
        "contested_exhibit_goes_to": DEFENSE,
    },
}

DISPOSITIVE_PROSECUTION = "DIRECTED_VERDICT_PROSECUTION"
MISTRIAL = "MISTRIAL"

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


def _date_prefix(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        return str(iso)[:10]
    except Exception:
        return None


def _session_id(scan_finished_at: str | None, generated_at: str | None = None) -> str:
    basis = f"{scan_finished_at or 'unknown-scan'}|{generated_at or _now().isoformat()}"
    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:10]
    safe_ts = basis.replace(":", "").replace(".", "").replace("+", "z")[:24]
    return f"cc-{safe_ts}-{digest}"


def _case_id(ticker: str, session_id: str, scan_row: dict[str, Any], pm_row: dict[str, Any]) -> str:
    material = "|".join([
        ticker,
        session_id,
        str(pm_row.get("action") or ""),
        str(pm_row.get("pm_score") or ""),
        ",".join(_signals(scan_row)),
    ])
    return f"court-{ticker}-{session_id}-{hashlib.sha1(material.encode('utf-8')).hexdigest()[:8]}"


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
    fact: dict[str, Any] | str | None = None,
    doctrine_class: str | None = None,
    rule: str | None = None,
) -> dict[str, Any]:
    score = max(0.0, min(25.0, float(score or 0.0)))
    data = data or {}
    if not applicable:
        status, side, score = NOT_APPLICABLE, NEUTRAL, 0.0
    elif status in {NOT_APPLICABLE, "MISSING_OPTIONAL", "STALE_OPTIONAL"}:
        side, score = NEUTRAL, 0.0
    elif status in {MISSING_REQUIRED, STALE_REQUIRED}:
        side = NEUTRAL
        score = 0.0
    elif side not in {DEFENSE, PROSECUTOR}:
        side, score = NEUTRAL, 0.0
    claims: list[dict[str, Any]] = []
    if side in {DEFENSE, PROSECUTOR} and score > 0:
        claims.append(_claim(side, score, detail, rule or f"{key}:{status}", doctrine_class or key.split(":")[0]))
    exhibit = {
        "key": key,
        "label": label,
        "status": status,
        "fact": fact if fact is not None else data,
        "applicable": applicable,
        "required": required,
        "freshness": freshness,
        "source": source,
        "data": data,
        "claims": claims,
        "contested": False,
        # Legacy display fields: keep existing frontend/Telegram consumers alive.
        "side": side,
        "score": round(score, 1),
        "detail": detail,
    }
    _refresh_legacy_from_claims(exhibit)
    return exhibit


def _claim(side: str, weight: float, argument: str, doctrine_rule: str, doctrine_class: str, admissible: bool = True) -> dict[str, Any]:
    return {
        "side": side,
        "weight": round(max(0.0, min(30.0, float(weight or 0.0))), 1),
        "argument": argument,
        "rule": doctrine_rule,
        "class": doctrine_class,
        "admissible": admissible,
    }


def _add_claim(exhibit: dict[str, Any], side: str, weight: float, argument: str, doctrine_rule: str, doctrine_class: str, admissible: bool = True) -> dict[str, Any]:
    if not exhibit.get("applicable") or exhibit.get("status") in {NOT_APPLICABLE, "MISSING_OPTIONAL", "STALE_OPTIONAL"}:
        admissible = False
        weight = 0.0
    exhibit.setdefault("claims", []).append(_claim(side, weight, argument, doctrine_rule, doctrine_class, admissible))
    _refresh_legacy_from_claims(exhibit)
    return exhibit


def _admissible_claims(exhibit: dict[str, Any], side: str | None = None) -> list[dict[str, Any]]:
    claims = [c for c in exhibit.get("claims") or [] if c.get("admissible", True) and _num(c.get("weight")) > 0]
    if side:
        claims = [c for c in claims if c.get("side") == side]
    return claims


def _refresh_legacy_from_claims(exhibit: dict[str, Any]) -> None:
    d = sum(_num(c.get("weight")) for c in _admissible_claims(exhibit, DEFENSE))
    p = sum(_num(c.get("weight")) for c in _admissible_claims(exhibit, PROSECUTOR))
    exhibit["defense_weight"] = round(d, 1)
    exhibit["prosecution_weight"] = round(p, 1)
    exhibit["contested"] = d > 0 and p > 0
    if d > p:
        exhibit["side"] = DEFENSE
        exhibit["score"] = round(d, 1)
        exhibit["detail"] = next((c.get("argument") for c in _admissible_claims(exhibit, DEFENSE)), exhibit.get("detail") or "")
        exhibit["status"] = "CONTESTED" if p > 0 else "BULLISH"
    elif p > d:
        exhibit["side"] = PROSECUTOR
        exhibit["score"] = round(p, 1)
        exhibit["detail"] = next((c.get("argument") for c in _admissible_claims(exhibit, PROSECUTOR)), exhibit.get("detail") or "")
        exhibit["status"] = "CONTESTED" if d > 0 else "BEARISH"
    else:
        if exhibit.get("status") not in {MISSING_REQUIRED, STALE_REQUIRED, NOT_APPLICABLE, "MISSING_OPTIONAL", "STALE_OPTIONAL"}:
            exhibit["status"] = "NEUTRAL"
        exhibit["side"] = NEUTRAL
        exhibit["score"] = 0.0
    exhibit["claim_count"] = len(_admissible_claims(exhibit))


def _claim_score(exhibits: list[dict[str, Any]], side: str) -> float:
    return round(sum(_num(c.get("weight")) for e in exhibits for c in _admissible_claims(e, side)), 1)


def _defense_classes(exhibits: list[dict[str, Any]]) -> set[str]:
    return {
        str(c.get("class") or "")
        for e in exhibits
        for c in _admissible_claims(e, DEFENSE)
        if c.get("class")
    }


def _dispositive_flags(exhibits: list[dict[str, Any]], expression: str) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    for e in exhibits:
        data = e.get("data") or {}
        text = " ".join(str(x or "") for x in [e.get("label"), e.get("detail"), data.get("form"), data.get("title"), data.get("description")]).upper()
        if any(tok in text for tok in ["424B5", "ATM AGREEMENT", "AT THE MARKET", "GOING CONCERN", "ITEM 4.02", "NON-RELIANCE", "RESTATEMENT"]):
            flags.append({"verdict": DISPOSITIVE_PROSECUTION, "label": e.get("label"), "reason": "Fresh capital-structure or accounting red flag is dispositive."})
        if data.get("reg_sho_threshold_days") and _num(data.get("reg_sho_threshold_days")) >= 5:
            flags.append({"verdict": DISPOSITIVE_PROSECUTION, "label": e.get("label"), "reason": "Reg SHO threshold persistence is dispositive."})
        if data.get("intended_size_adv_pct") and _num(data.get("intended_size_adv_pct")) > 8:
            flags.append({"verdict": DISPOSITIVE_PROSECUTION, "label": e.get("label"), "reason": "Intended size exceeds exit-liquidity mandate."})
    required_missing = [e.get("label") for e in exhibits if e.get("required") and e.get("status") in {MISSING_REQUIRED, STALE_REQUIRED}]
    if required_missing:
        flags.append({"verdict": MISTRIAL, "label": "Required Evidence", "reason": "Required exhibit missing or stale: " + ", ".join(required_missing[:4])})
    return flags


def _scanner_exhibit(row: dict[str, Any], scan_age: float | None) -> dict[str, Any]:
    if row.get("synthetic_from_pm"):
        return _exhibit("scanner", "Scanner Evidence", MISSING_REQUIRED, score=14, detail="Ticker was not present in the latest stock scan; PM-only row cannot borrow scanner freshness.", source="scan_results", required=True)
    sigs = _signals(row)
    if not sigs:
        return _exhibit("scanner", "Scanner Evidence", MISSING_REQUIRED, score=14, detail="No scanner signal stack was found.", source="scan_results", required=True)
    if scan_age is None or scan_age > 26:
        return _exhibit("scanner", "Scanner Evidence", STALE_REQUIRED, score=14, detail=f"Latest stock scan age is {scan_age}h.", source="scan_results", required=True)
    score = min(22, sum(SIGNAL_QUALITY.get(s, 4) for s in sigs))
    ex = _exhibit(
        "scanner",
        "Scanner Evidence",
        "NEUTRAL",
        NEUTRAL,
        0,
        f"{len(sigs)} signals: {', '.join(sigs[:5])}.",
        "scan_results",
        required=True,
        freshness=f"{scan_age:.1f}h old" if scan_age is not None else "unknown",
        fact={"signals": sigs, "scan_age_hours": scan_age, "trade_score": row.get("trade_score"), "signal_score": row.get("signal_score")},
        data={"signals": sigs, "trade_score": row.get("trade_score"), "signal_score": row.get("signal_score")},
    )
    _add_claim(ex, DEFENSE, score, f"Signal stack is actionable: {', '.join(sigs[:5])}.", "defense.signal_stack.rarity", "signal_stack")
    if "high_short_interest" in sigs:
        _add_claim(ex, PROSECUTOR, 9, "High short interest can be informed capital positioned against the setup.", "prosecutor.short_conviction.priced", "short_conviction")
    if "upcoming_earnings" in sigs:
        _add_claim(ex, PROSECUTOR, 7, "Upcoming earnings can gap through stops or crush option premium.", "prosecutor.catalyst.binary_risk", "structural_decay")
    return ex


def _pm_exhibit(pm_row: dict[str, Any]) -> dict[str, Any]:
    action = str(pm_row.get("action") or "UNKNOWN").upper()
    score = _num(pm_row.get("pm_score"))
    rr = _num(pm_row.get("risk_reward"))
    if action in {"ACCUMULATE", "STARTER"}:
        return _exhibit("pm", "Portfolio Manager", "NEUTRAL", NEUTRAL, 0, f"PM says {action}; score {score:.1f}, RR {rr:.2f}. PM is the judge, not evidence.", "portfolio_manager", required=True, fact={"action": action, "pm_score": score, "risk_reward": rr}, data={"action": action, "pm_score": score, "risk_reward": rr})
    if action == "REJECT":
        pm_gap = max(0.0, 60.0 - score)
        rr_penalty = 10.0 if rr < 1.0 else 6.0 if rr < 1.3 else 0.0
        return _exhibit("pm", "Portfolio Manager", "BEARISH", PROSECUTOR, min(22, max(14, pm_gap * 0.45 + rr_penalty)), f"PM rejected sizing; score {score:.1f}, RR {rr:.2f}.", "portfolio_manager", required=True, doctrine_class="pm_rejection", rule="prosecutor.pm_reject.capital_allocator", fact={"action": action, "pm_score": score, "risk_reward": rr}, data={"action": action, "pm_score": score, "risk_reward": rr})
    return _exhibit("pm", "Portfolio Manager", "NEUTRAL", NEUTRAL, 0, f"PM is watching; score {score:.1f}, RR {rr:.2f}.", "portfolio_manager", required=True, fact={"action": action, "pm_score": score, "risk_reward": rr}, data={"action": action, "pm_score": score, "risk_reward": rr})


def _entry_exhibit(scan_row: dict[str, Any], pm_row: dict[str, Any]) -> dict[str, Any]:
    action = str(pm_row.get("action") or "UNKNOWN").upper()
    entry = _entry_position(scan_row, pm_row)
    price = _num(pm_row.get("price") or scan_row.get("price"))
    low = _num(pm_row.get("entry_low") or scan_row.get("entry_low"))
    high = _num(pm_row.get("entry_high") or scan_row.get("entry_high"))
    if entry == "IN_BAND":
        if action == "REJECT":
            return _exhibit("entry", "Entry Band", "NEUTRAL", NEUTRAL, 0, f"Price {price:.2f} is inside {low:.2f}-{high:.2f}, but PM rejected the trade.", "portfolio_manager")
        return _exhibit("entry", "Entry Band", "BULLISH", DEFENSE, 10, f"Price {price:.2f} is inside {low:.2f}-{high:.2f}.", "portfolio_manager", doctrine_class="bounded_downside", rule="defense.entry.in_band", fact={"entry_position": entry, "price": price, "entry_low": low, "entry_high": high})
    if entry == "ABOVE_BAND":
        ex = _exhibit("entry", "Entry Band", "NEUTRAL", NEUTRAL, 0, f"Price {price:.2f} is above entry band {low:.2f}-{high:.2f}.", "portfolio_manager", fact={"entry_position": entry, "price": price, "entry_low": low, "entry_high": high})
        _add_claim(ex, PROSECUTOR, 16, f"Price {price:.2f} is above entry band {low:.2f}-{high:.2f}; this is chase risk.", "prosecutor.liquidity.chasing", "crowding_exit_liquidity")
        _add_claim(ex, DEFENSE, 6, "Above-band price can be momentum confirmation if the band is stale.", "defense.relative_strength.above_band", "relative_strength")
        return ex
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
    return _exhibit("risk", "Risk Engine", "NEUTRAL", NEUTRAL, 0, f"Risk score is contained at {risk_score:.1f}; downside to stop {downside:.1f}%. Risk containment supports sizing discipline but is not a buy thesis.", "risk_target", fact={"risk_score": risk_score, "downside_pct": downside})


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
        ex = _exhibit("options", "Options Contract", "BULLISH", DEFENSE, 16, f"Options ticket ready; route {row.get('route')}, risk budget ${row.get('risk_budget')}.", "options_desk", required=True, doctrine_class="structural_mispricing", rule="defense.options.defined_risk_ready", data={"route": row.get("route"), "strategy": row.get("strategy"), "quality": quality, "contracts": row.get("contracts")})
        if row.get("spread_cost_paid") or row.get("spread_pct"):
            _add_claim(ex, PROSECUTOR, 5, "Option spread/market structure still taxes the expected payoff.", "prosecutor.options.spread_drag", "structural_decay")
        return ex
    return _exhibit("options", "Options Contract", "BEARISH", PROSECUTOR, min(20, 8 + len(blocks) * 4), "; ".join(str(b) for b in blocks[:4]) or f"Options ticket is not ready; quality {quality}.", "options_desk", required=True, doctrine_class="structural_decay", rule="prosecutor.options.not_decision_grade", data={"route": row.get("route"), "strategy": row.get("strategy"), "quality": quality, "blocks": blocks})


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
    text = " ".join(str((r or {}).get("form") or "") + " " + str((r or {}).get("title") or "") + " " + str((r or {}).get("description") or "") for r in sec_rows).upper()
    dilution = any(tok in text for tok in ["S-3", "S-3ASR", "424B5", "ATM", "WARRANT", "CONVERTIBLE"])
    if dilution:
        side, score, status = PROSECUTOR, max(score, 18), "BEARISH"
    return _exhibit("sec", "SEC Filings", status, side, score, f"{len(sec_rows)} recent filing(s): {forms}. Max significance {max_sig:.0f}.", "sec_filings", doctrine_class="dilution_capital_structure" if side == PROSECUTOR else "sec_context", rule="prosecutor.sec.dilution_or_material_filing" if side == PROSECUTOR else "sec.neutral.context", data={"recent": sec_rows[:5], "forms": forms, "dilution_watch": dilution})


def _precedent_exhibit(count: int, row: dict[str, Any]) -> dict[str, Any]:
    if count <= 0:
        return _exhibit("precedent", "Historical Precedent", "MISSING_OPTIONAL", detail="No historical signal record for this ticker yet.", source="signal_performance")
    score = min(14, 5 + count * 0.6)
    ex = _exhibit("precedent", "Historical Precedent", "NEUTRAL", NEUTRAL, 0, f"{count} stored signal-performance record(s) exist for precedent review.", "signal_performance", data={"records": count})
    if count >= 5:
        _add_claim(ex, DEFENSE, score, "Stored precedent exists for this ticker/signal family; prior record deserves a defense reading until outcome data disproves it.", "defense.precedent.exists", "precedent")
        _add_claim(ex, PROSECUTOR, min(10, count * 0.4), "Precedent count without attached win-rate/return remains base-rate risk, not proof.", "prosecutor.precedent.unproven_base_rate", "base_rate_failure")
    return ex


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
    return _claim_score(exhibits, side)


def _coverage(exhibits: list[dict[str, Any]]) -> dict[str, Any]:
    applicable = [e for e in exhibits if e.get("applicable")]
    required = [e for e in exhibits if e.get("required")]
    missing_required = [e for e in exhibits if e.get("status") in {MISSING_REQUIRED, STALE_REQUIRED}]
    scored = [e for e in applicable if _admissible_claims(e)]
    contested = [e for e in applicable if e.get("contested")]
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
        "contested": len(contested),
        "affirmative_defense_classes": len(_defense_classes(exhibits)),
        "defense_classes": sorted(_defense_classes(exhibits)),
        "coverage_label": f"{len(scored)}/{len(applicable)} scored",
        "missing_required_labels": [e.get("label") for e in missing_required],
        "certification": "CERTIFIED" if certified else "ADVISORY_ONLY",
    }


def _brief(side: str, exhibits: list[dict[str, Any]]) -> dict[str, Any]:
    points = []
    for e in exhibits:
        for c in _admissible_claims(e, side):
            points.append({**c, "label": e.get("label"), "source": e.get("source"), "status": e.get("status"), "contested": e.get("contested")})
    points = sorted(points, key=lambda c: _num(c.get("weight")), reverse=True)
    if side == DEFENSE:
        opening = "The Defense must prove asymmetric payoff with non-consensus evidence; PM approval itself earns no points."
        name = "Defense"
        stance = "BULLISH"
    else:
        opening = "The Prosecutor argues capital is guilty until the Defense proves the risk budget can absorb residual uncertainty."
        name = "Prosecutor"
        stance = "BEARISH"
    return {
        "name": name,
        "stance": stance,
        "score": _score_side(exhibits, side),
        "classes": sorted({str(p.get("class") or "") for p in points if p.get("class")}),
        "opening_argument": opening if side == DEFENSE else None,
        "opening_objection": opening if side == PROSECUTOR else None,
        "points": [
            {
                "label": p["label"],
                "weight": p["weight"],
                "detail": p["argument"],
                "source": p["source"],
                "status": p["status"],
                "rule": p.get("rule"),
                "class": p.get("class"),
                "contested": p.get("contested"),
            }
            for p in points[:10]
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


def _judge(mini_trials: list[dict[str, Any]], defense: dict[str, Any], prosecution: dict[str, Any], pm_row: dict[str, Any], expression: str, exhibits: list[dict[str, Any]] | None = None, mode: str = "BALANCED") -> dict[str, Any]:
    exhibits = exhibits or []
    d = _num(defense.get("score"))
    p = _num(prosecution.get("score"))
    spread = round(d - p, 1)
    ratio = round(d / max(1.0, p), 2)
    pm_action = str(pm_row.get("action") or "UNKNOWN").upper()
    not_grade = [t for t in mini_trials if not t.get("decision_grade")]
    option_trial = next((t for t in mini_trials if t["name"] == "options_contract"), None)
    equity_ok = all(t.get("decision_grade") for t in mini_trials if t["name"] in {"ticker_quality", "trade_quality"})
    standard = STANDARD_OF_PROOF.get(str(mode or "BALANCED").upper(), STANDARD_OF_PROOF["BALANCED"])
    defense_classes = _defense_classes(exhibits)
    flags = _dispositive_flags(exhibits, expression)
    directed = next((f for f in flags if f.get("verdict") == DISPOSITIVE_PROSECUTION), None)
    mistrial = next((f for f in flags if f.get("verdict") == MISTRIAL), None)
    meets_standard = (
        ratio >= _num(standard.get("defense_must_exceed"), 1.35)
        and len(defense_classes) >= int(standard.get("min_affirmative_defense_classes") or 1)
    )

    if directed:
        posture = "COURT_OBJECTS"
        detail = directed.get("reason") or "Dispositive prosecution exhibit controls the record."
    elif mistrial and not equity_ok:
        posture = "REQUIRES_CLEANER_DATA"
        detail = mistrial.get("reason") or "Required evidence is missing; the trial is a mistrial, not a prosecution win."
    elif pm_action == "REJECT" or expression == "PASS":
        posture = "PM_REJECTED"
        detail = "PM rejected or passed on the setup; Case Court cannot elevate scanner evidence into authority."
    elif not_grade and not equity_ok:
        posture = "REQUIRES_CLEANER_DATA"
        detail = "Required evidence is missing for the ticker or equity trade trial; this is a mistrial, not conviction."
    elif expression in {"OPTION", "BOTH", "OPTION_OR_BOTH_ADVISORY"} and option_trial and not option_trial.get("decision_grade"):
        posture = "EQUITY_ONLY_UNTIL_OPTIONS_CLEAN"
        detail = "Equity evidence can stand, but options evidence is not decision-grade."
    elif meets_standard and pm_action in {"ACCUMULATE", "STARTER"}:
        posture = "COURT_SUPPORTS_PM"
        detail = f"Defense meets {standard['standard']} with {ratio}x prosecution and {len(defense_classes)} affirmative class(es)."
    elif ratio >= 1.0 and d > p:
        posture = "BULLISH_WATCH"
        detail = f"Defense leads, but proof standard is not met: {ratio}x vs required {standard['defense_must_exceed']}x and {len(defense_classes)}/{standard['min_affirmative_defense_classes']} classes."
    elif p > d:
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
        "defense_to_prosecutor_ratio": ratio,
        "standard_of_proof": standard,
        "affirmative_defense_classes": sorted(defense_classes),
        "dispositive_flags": flags,
        "detail": detail,
        "authority": "READ_ONLY_NO_EXECUTION_NO_PM_OVERRIDE",
        "advisory_alignment_ok": posture in {"COURT_SUPPORTS_PM", "EQUITY_ONLY_UNTIL_OPTIONS_CLEAN"} and pm_action in {"ACCUMULATE", "STARTER"} and equity_ok,
    }


def _witnesses(exhibits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for e in exhibits:
        d = _num(e.get("defense_weight"))
        p = _num(e.get("prosecution_weight"))
        stance = "CONTESTED" if e.get("contested") else "BULL" if d > p else "BEAR" if p > d else "NEUTRAL"
        testimony = e.get("detail")
        if e.get("contested"):
            testimony = " | ".join(f"{c.get('side')}: {c.get('argument')}" for c in _admissible_claims(e)[:2])
        rows.append({
            "name": e["label"],
            "stance": stance,
            "score": max(d, p),
            "defense_weight": d,
            "prosecution_weight": p,
            "contested": bool(e.get("contested")),
            "status": e.get("status"),
            "testimony": testimony,
            "source": e.get("source"),
        })
    return sorted(rows, key=lambda r: (r.get("contested"), _num(r.get("score"))), reverse=True)


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
    session_id: str,
    mode: str = "BALANCED",
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
    judge = _judge(mini_trials, defense, prosecution, pm_row, expression, exhibits=exhibits, mode=mode)
    if coverage.get("certification") != "CERTIFIED" and judge.get("advisory_alignment_ok"):
        judge = {
            **judge,
            "advisory_alignment_ok": False,
            "authority": "READ_ONLY_INSUFFICIENT_CERTIFICATION",
            "detail": f"{judge.get('detail')} Evidence coverage is {coverage.get('coverage_label')}; court remains advisory.",
        }

    return {
        "case_id": _case_id(ticker, session_id, scan_row, pm_row),
        "session_id": session_id,
        "rubric_version": RUBRIC_VERSION,
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
            "clerk_notes": "Read-only advisory record. Facts are argued by both sides; contested exhibits are surfaced first. Missing required evidence is a mistrial, not a prosecution win.",
            "evidence_standard": f"{judge.get('standard_of_proof', {}).get('standard', 'PREPONDERANCE')} under {mode or 'BALANCED'} mode.",
        },
        "generated_at": _now().isoformat(),
    }


def _summary(trials: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    postures = [((t.get("judge") or {}).get("advisory_posture") or "UNKNOWN") for t in trials]
    coverage_counts = Counter()
    neutralized = 0
    contested = 0
    standards = Counter()
    for t in trials:
        standard = (((t.get("judge") or {}).get("standard_of_proof") or {}).get("standard"))
        if standard:
            standards[standard] += 1
        for e in t.get("exhibits") or []:
            if _admissible_claims(e):
                coverage_counts[e.get("key", "").split(":")[0]] += 1
            if e.get("contested"):
                contested += 1
            if e.get("status") in {NOT_APPLICABLE, "MISSING_OPTIONAL", "STALE_OPTIONAL"}:
                neutralized += 1
    return {
        "authority": "READ_ONLY_NO_EXECUTION",
        "rubric_version": RUBRIC_VERSION,
        "trials": len(trials),
        "supports_pm": postures.count("COURT_SUPPORTS_PM"),
        "bullish_watch": postures.count("BULLISH_WATCH"),
        "objects": postures.count("COURT_OBJECTS"),
        "pm_rejected": postures.count("PM_REJECTED"),
        "conflicts": postures.count("EVIDENCE_CONFLICT"),
        "requires_cleaner_data": postures.count("REQUIRES_CLEANER_DATA"),
        "equity_only_until_options_clean": postures.count("EQUITY_ONLY_UNTIL_OPTIONS_CLEAN"),
        "advisory_alignment_ok": sum(1 for t in trials if (t.get("judge") or {}).get("advisory_alignment_ok")),
        "decision_grade": sum(1 for t in trials if (t.get("evidence_coverage") or {}).get("decision_grade")),
        "neutralized_exhibits": neutralized,
        "contested_exhibits": contested,
        "standards": dict(standards),
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
    mode = str((pm if isinstance(pm, dict) else {}).get("mode") or "BALANCED").upper()
    options_rows = _by_ticker(options if isinstance(options, dict) else {}, "candidates")
    kronos_rows = _by_ticker(kronos_payload if isinstance(kronos_payload, dict) else {}, "forecasts")
    scan_finished_at = (scan if isinstance(scan, dict) else {}).get("finished_at")
    news_list = news_rows if isinstance(news_rows, list) else []
    generated_at = _now().isoformat()
    session_id = _session_id(scan_finished_at, generated_at)

    trials = []
    for pm_row in pm_rows[:limit]:
        ticker = _ticker(pm_row.get("ticker"))
        scan_row = scan_rows.get(ticker, {"ticker": ticker, "signals": pm_row.get("signals") or [], "synthetic_from_pm": True})
        trials.append(await _trial(scan_row, pm_row, options_rows.get(ticker), kronos_rows.get(ticker), qc if isinstance(qc, dict) else {}, scan_finished_at, news_list, session_id, mode=mode))

    context = {
        "scan_finished_at": scan_finished_at,
        "scan_error": scan.get("error") if isinstance(scan, dict) else None,
        "pm_error": pm.get("error") if isinstance(pm, dict) else None,
        "options_error": options.get("error") if isinstance(options, dict) else None,
        "kronos_error": kronos_payload.get("error") if isinstance(kronos_payload, dict) else None,
        "qc": qc if isinstance(qc, dict) else {},
        "pm_mode": mode,
    }
    payload = {
        "ok": True,
        "mode": "advisory_only",
        "summary": _summary(trials, context),
        "context": {k: v for k, v in context.items() if k != "qc"},
        "trials": trials,
        "session_id": session_id,
        "generated_at": generated_at,
    }
    if persist:
        db = get_db()
        if trials:
            await db.case_court_trials.insert_many([stamped({**t, "session_id": session_id, "session_generated_at": generated_at}) for t in trials])
        await db.bot_state.update_one(
            {"_id": "case_court_latest"},
            {"$set": stamped({"session_id": session_id, "summary": payload["summary"], "updated_at": generated_at})},
            upsert=True,
        )
        await _prune_old_sessions(days=90)
    return payload


async def _prune_old_sessions(days: int = 90) -> None:
    try:
        cutoff = (_now() - timedelta(days=days)).isoformat()
        db = get_db()
        await db.case_court_trials.delete_many({"session_generated_at": {"$lt": cutoff}})
    except Exception:
        return


async def _latest_session_id() -> str | None:
    db = get_db()
    state = await db.bot_state.find_one({"_id": "case_court_latest"}, {"_id": 0, "session_id": 1})
    if state and state.get("session_id"):
        return str(state["session_id"])
    row = await db.case_court_trials.find_one({"rubric_version": RUBRIC_VERSION}, {"_id": 0, "session_id": 1}, sort=[("session_generated_at", -1), ("generated_at", -1)])
    return str(row["session_id"]) if row and row.get("session_id") else None


async def sessions(limit: int = 12) -> dict[str, Any]:
    db = get_db()
    pipeline = [
        {"$match": {"rubric_version": RUBRIC_VERSION, "session_id": {"$exists": True}}},
        {"$group": {
            "_id": "$session_id",
            "session_id": {"$first": "$session_id"},
            "session_generated_at": {"$max": "$session_generated_at"},
            "scan_finished_at": {"$first": "$scan_finished_at"},
            "trials": {"$sum": 1},
        }},
        {"$sort": {"session_generated_at": -1}},
        {"$limit": max(1, min(int(limit or 12), 50))},
    ]
    rows = await db.case_court_trials.aggregate(pipeline).to_list(max(1, min(int(limit or 12), 50)))
    return {"ok": True, "sessions": rows, "rubric_version": RUBRIC_VERSION}


async def latest(limit: int = MAX_TRIALS, session_id: str | None = None) -> dict[str, Any]:
    db = get_db()
    sid = session_id or await _latest_session_id()
    if not sid:
        return {
            "ok": True,
            "mode": "advisory_only",
            "summary": _summary([], {"scan_finished_at": None, "qc": {}}),
            "trials": [],
            "source": "persisted",
            "stale": True,
            "reason": "no_persisted_session",
            "rubric_version": RUBRIC_VERSION,
        }
    rows = await db.case_court_trials.find(
        {"session_id": sid, "rubric_version": RUBRIC_VERSION},
        {"_id": 0},
    ).sort("generated_at", -1).to_list(limit)
    if rows:
        return {
            "ok": True,
            "mode": "advisory_only",
            "summary": _summary(rows, {"scan_finished_at": rows[0].get("scan_finished_at"), "qc": {}}),
            "trials": rows,
            "source": "persisted",
            "session_id": sid,
        }
    return {
        "ok": True,
        "mode": "advisory_only",
        "summary": _summary([], {"scan_finished_at": None, "qc": {}}),
        "trials": [],
        "source": "persisted",
        "stale": True,
        "reason": "session_not_found",
        "session_id": sid,
        "rubric_version": RUBRIC_VERSION,
    }


async def trial(ticker: str) -> dict[str, Any]:
    t = _ticker(ticker)
    if not t:
        return {"ok": False, "error": "ticker_required"}
    latest_payload = await latest(limit=75)
    row = next((r for r in latest_payload.get("trials", []) if _ticker(r.get("ticker")) == t), None)
    if row:
        return {"ok": True, "trial": row, "source": latest_payload.get("source", "live")}
    return {"ok": False, "error": "trial_not_found", "ticker": t}


async def record(days: int = 30) -> dict[str, Any]:
    db = get_db()
    days = max(1, min(int(days or 30), 180))
    cutoff = (_now() - timedelta(days=days)).isoformat()
    trials = await db.case_court_trials.find(
        {
            "rubric_version": RUBRIC_VERSION,
            "generated_at": {"$gte": cutoff},
            "judge.advisory_posture": {"$in": ["COURT_SUPPORTS_PM", "COURT_OBJECTS"]},
        },
        {"_id": 0},
    ).to_list(1000)

    graded = []
    for t in trials:
        ticker = _ticker(t.get("ticker"))
        generated = str(t.get("generated_at") or t.get("session_generated_at") or "")
        query = {"ticker": ticker}
        generated_date = _date_prefix(generated)
        if generated_date:
            query["date"] = {"$gte": generated_date}
        perf = await db.signal_performance.find_one(
            query,
            {"_id": 0},
            sort=[("date", -1), ("ts", -1)],
        )
        if not perf:
            continue
        ret = perf.get("return_30d")
        horizon = "30d"
        if ret is None:
            ret = perf.get("return_7d")
            horizon = "7d"
        if ret is None:
            continue
        posture = (t.get("judge") or {}).get("advisory_posture")
        win = bool(ret > 0) if posture == "COURT_SUPPORTS_PM" else bool(ret < 0)
        graded.append({
            "ticker": ticker,
            "case_id": t.get("case_id"),
            "session_id": t.get("session_id"),
            "posture": posture,
            "return_pct": ret,
            "horizon": horizon,
            "win": win,
            "generated_at": generated,
        })

    by_posture: dict[str, dict[str, Any]] = {}
    for posture in ["COURT_SUPPORTS_PM", "COURT_OBJECTS"]:
        rows = [r for r in graded if r["posture"] == posture]
        returns = [_num(r.get("return_pct")) for r in rows]
        wins = sum(1 for r in rows if r.get("win"))
        by_posture[posture] = {
            "n": len(rows),
            "wins": wins,
            "losses": len(rows) - wins,
            "hit_rate": round(wins / max(1, len(rows)) * 100, 2) if rows else None,
            "avg_return_pct": round(sum(returns) / max(1, len(returns)), 2) if returns else None,
            "sample_note": "Sample under 30; read as directional only." if len(rows) < 30 else "",
        }

    return {
        "ok": True,
        "days": days,
        "rubric_version": RUBRIC_VERSION,
        "graded": len(graded),
        "open_trials": len(trials) - len(graded),
        "by_posture": by_posture,
        "rows": graded[:100],
        "sample_note": "Court record is still building; require n>=30 before treating hit rates as reliable." if len(graded) < 30 else "",
    }
