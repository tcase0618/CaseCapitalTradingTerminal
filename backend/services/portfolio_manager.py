"""Algorithmic portfolio manager.

Turns scan rows into deterministic portfolio recommendations. This service
does not call Claude and does not execute trades.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from .db import get_db

DEFAULT_EQUITY = 1000.0
MODE_PROFILES = {
    "RISK_OFF": {
        "max_position_pct": 0.025,
        "max_single_name_risk_pct": 0.004,
        "max_gross_deployment_pct": 0.10,
        "max_sector_deployment_pct": 0.18,
        "accumulate_score": 82,
        "accumulate_rr": 2.4,
        "starter_score": 72,
        "starter_rr": 1.9,
        "watch_score": 55,
    },
    "CONSERVATIVE": {
        "max_position_pct": 0.05,
        "max_single_name_risk_pct": 0.008,
        "max_gross_deployment_pct": 0.22,
        "max_sector_deployment_pct": 0.22,
        "accumulate_score": 76,
        "accumulate_rr": 2.1,
        "starter_score": 64,
        "starter_rr": 1.6,
        "watch_score": 50,
    },
    "BALANCED": {
        "max_position_pct": 0.08,
        "max_single_name_risk_pct": 0.0125,
        "max_gross_deployment_pct": 0.35,
        "max_sector_deployment_pct": 0.25,
        "accumulate_score": 70,
        "accumulate_rr": 1.8,
        "starter_score": 58,
        "starter_rr": 1.3,
        "watch_score": 45,
    },
    "AGGRESSIVE": {
        "max_position_pct": 0.12,
        "max_single_name_risk_pct": 0.018,
        "max_gross_deployment_pct": 0.55,
        "max_sector_deployment_pct": 0.30,
        "accumulate_score": 64,
        "accumulate_rr": 1.5,
        "starter_score": 52,
        "starter_rr": 1.1,
        "watch_score": 40,
    },
}

# Lottery is a high-variance sleeve, so it gets its own admissibility and
# sizing profile instead of inheriting the broad Core Scan thresholds. The
# profile increases qualified starter participation without bypassing data,
# regime, liquidity, or portfolio caps.
LOTTERY_PROFILES = {
    "RISK_OFF": {
        "max_position_pct": 0.02,
        "max_single_name_risk_pct": 0.003,
        "max_gross_deployment_pct": 0.08,
        "max_sector_deployment_pct": 0.10,
        "accumulate_score": 84,
        "accumulate_rr": 2.4,
        "starter_score": 76,
        "starter_rr": 1.9,
        "watch_score": 55,
    },
    "CONSERVATIVE": {
        "max_position_pct": 0.035,
        "max_single_name_risk_pct": 0.005,
        "max_gross_deployment_pct": 0.12,
        "max_sector_deployment_pct": 0.15,
        "accumulate_score": 78,
        "accumulate_rr": 2.0,
        "starter_score": 58,
        "starter_rr": 1.3,
        "watch_score": 40,
    },
    "BALANCED": {
        "max_position_pct": 0.05,
        "max_single_name_risk_pct": 0.006,
        "max_gross_deployment_pct": 0.15,
        "max_sector_deployment_pct": 0.18,
        "accumulate_score": 72,
        "accumulate_rr": 1.8,
        "starter_score": 52,
        "starter_rr": 1.15,
        "watch_score": 38,
    },
    "AGGRESSIVE": {
        "max_position_pct": 0.07,
        "max_single_name_risk_pct": 0.008,
        "max_gross_deployment_pct": 0.20,
        "max_sector_deployment_pct": 0.20,
        "accumulate_score": 66,
        "accumulate_rr": 1.55,
        "starter_score": 48,
        "starter_rr": 1.05,
        "watch_score": 35,
    },
}


def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _signals(row: dict[str, Any]) -> list[str]:
    sigs = row.get("signals") or []
    if isinstance(sigs, dict):
        return sorted(str(k) for k, v in sigs.items() if v)
    return sorted(str(s) for s in sigs)


def _is_lottery_row(row: dict[str, Any]) -> bool:
    """Detect Lottery evidence even after Core/strategy rows are merged."""
    families = {
        str(row.get("scanner_family") or "").upper(),
        str((row.get("strategy_scanner") or {}).get("family") or "").upper(),
    }
    for scanner in row.get("strategy_screeners") or []:
        if isinstance(scanner, dict):
            families.add(str(scanner.get("family") or "").upper())
    for view in row.get("strategy_views") or []:
        if isinstance(view, dict):
            families.add(str(view.get("family") or "").upper())
    return "LOTTERY" in families


def _target(row: dict[str, Any]) -> float:
    targets = row.get("targets") or {}
    return _num(
        row.get("target_blended")
        or targets.get("target_blended")
        or row.get("target_high")
        or targets.get("target_high")
    )


def _stop(row: dict[str, Any], price: float) -> float:
    stop = _num(row.get("stop_loss"))
    if stop > 0:
        return stop
    risk = row.get("risk") or {}
    risk_stop = _num(risk.get("stop_loss"))
    if risk_stop > 0:
        return risk_stop
    return round(price * 0.88, 2) if price > 0 else 0.0


def _upside_pct(price: float, target: float) -> float:
    if price <= 0 or target <= 0:
        return 0.0
    return ((target - price) / price) * 100.0


def _downside_pct(price: float, stop: float) -> float:
    if price <= 0 or stop <= 0:
        return 0.0
    return max(0.0, ((price - stop) / price) * 100.0)


def _rr(upside: float, downside: float) -> float:
    if downside <= 0:
        return 0.0
    return max(0.0, upside / downside)


def _pm_score(row: dict[str, Any], price: float, target: float, stop: float) -> tuple[float, dict[str, float]]:
    sigs = _signals(row)
    signal_score = _num(row.get("signal_score"))
    trade_score = _num(row.get("trade_score"))
    learning_score = _num(row.get("learning_score"))
    squeeze_score = _num((row.get("squeeze") or {}).get("score"))
    risk_score = _num((row.get("risk") or {}).get("score"))
    strategy_case = row.get("strategy_case") or {}
    case_score = _num(row.get("case_score") or strategy_case.get("case_score"))
    confidence = _num(row.get("strategy_confidence") or strategy_case.get("confidence"))
    upside = _upside_pct(price, target)
    downside = _downside_pct(price, stop)
    rr = _rr(upside, downside)

    signal_component = min(30.0, len(set(sigs)) * 7.5)
    trade_component = min(22.0, trade_score * 0.55)
    analyst_component = min(14.0, signal_score * 1.4)
    learning_component = min(10.0, max(0.0, learning_score))
    squeeze_component = min(8.0, squeeze_score * 0.08)
    rr_component = min(16.0, rr * 5.0)
    case_component = min(10.0, case_score * 0.10) if case_score else 0.0
    confidence_component = min(6.0, confidence * 6.0) if confidence else 0.0
    low_confidence_penalty = max(0.0, (0.45 - confidence) * 12.0) if confidence else 2.0
    penalty = min(25.0, risk_score * 0.12)
    score = (
        signal_component
        + trade_component
        + analyst_component
        + learning_component
        + squeeze_component
        + rr_component
        + case_component
        + confidence_component
        - penalty
        - low_confidence_penalty
    )
    score = max(0.0, min(100.0, score))
    return round(score, 1), {
        "signal_component": round(signal_component, 1),
        "trade_component": round(trade_component, 1),
        "analyst_component": round(analyst_component, 1),
        "learning_component": round(learning_component, 1),
        "squeeze_component": round(squeeze_component, 1),
        "risk_reward_component": round(rr_component, 1),
        "strategy_case_component": round(case_component, 1),
        "strategy_confidence_component": round(confidence_component, 1),
        "low_confidence_penalty": round(low_confidence_penalty, 1),
        "risk_penalty": round(penalty, 1),
    }


def _action(score: float, rr: float, signal_count: int, risk_score: float, profile: dict[str, Any]) -> str:
    if (
        score >= profile["accumulate_score"]
        and rr >= profile["accumulate_rr"]
        and signal_count >= 3
        and risk_score < 75
    ):
        return "ACCUMULATE"
    if score >= profile["starter_score"] and rr >= profile["starter_rr"] and signal_count >= 2:
        return "STARTER"
    if score >= profile["watch_score"]:
        return "WATCH"
    return "REJECT"


def _case_confidence(row: dict[str, Any]) -> tuple[float, float]:
    strategy_case = row.get("strategy_case") or {}
    case_score = _num(row.get("case_score") or strategy_case.get("case_score"), 0)
    confidence = _num(row.get("strategy_confidence") or strategy_case.get("confidence"), 0)
    return case_score, confidence


def _sizing(action: str, score: float, price: float, stop: float, equity: float, profile: dict[str, Any], row: dict[str, Any] | None = None) -> dict[str, Any]:
    if action in {"WATCH", "REJECT"} or price <= 0:
        return {"allocation_usd": 0.0, "shares": 0.0, "risk_usd": 0.0, "position_pct": 0.0}
    max_position = equity * profile["max_position_pct"]
    risk_per_share = max(0.01, price - stop) if stop > 0 else price * 0.12
    risk_budget = equity * profile["max_single_name_risk_pct"]
    if action == "STARTER":
        risk_budget *= 0.55
        max_position *= 0.55
    score_multiplier = 0.65 + min(0.35, max(0.0, score - 58.0) / 42.0)
    case_score, confidence = _case_confidence(row or {})
    case_multiplier = 1.0
    confidence_multiplier = 1.0
    if case_score:
        case_multiplier = max(0.65, min(1.18, 0.75 + case_score / 200.0))
    if confidence:
        confidence_multiplier = max(0.48, min(1.12, 0.45 + confidence))
    risk_budget *= score_multiplier * case_multiplier * confidence_multiplier
    max_position *= min(1.15, case_multiplier * confidence_multiplier)
    shares_by_risk = risk_budget / risk_per_share
    shares_by_position = max_position / price
    shares = max(0.0, min(shares_by_risk, shares_by_position))
    allocation = shares * price
    return {
        "allocation_usd": round(allocation, 2),
        "shares": round(shares, 4),
        "risk_usd": round(shares * risk_per_share, 2),
        "position_pct": round((allocation / equity) * 100.0, 2) if equity > 0 else 0.0,
        "sizing_multipliers": {
            "pm_score": round(score_multiplier, 3),
            "strategy_case": round(case_multiplier, 3),
            "confidence": round(confidence_multiplier, 3),
        },
    }


def _option_view(row: dict[str, Any], rr: float) -> str:
    opts = row.get("options") or {}
    scanner = row.get("strategy_scanner") or {}
    families = {
        str(scanner.get("family") or "").upper(),
        *(str((s or {}).get("family") or "").upper() for s in row.get("strategy_screeners") or [] if isinstance(s, dict)),
    }
    if opts.get("options_intent") or opts.get("preferred_route") == "OPTION" or "OPTIONS" in families:
        return "CALL_ALLOWED"
    if opts.get("hold_stock_instead") or opts.get("strategy") == "AVOID_OPTIONS":
        return "STOCK_ONLY"
    iv_rank = _num(opts.get("iv_rank"), default=-1)
    strategy = str(opts.get("strategy") or "").upper()
    if strategy in {"LONG_CALL", "LONG_PUT", "LONG_CALL_SCOUT", "LONG_CALL_EVENT_SCOUT", "LEAPS_CALL_CANDIDATE"}:
        return "CALL_ALLOWED"
    if iv_rank >= 80:
        return "SPREAD_ONLY"
    if rr >= 1.5 and iv_rank >= 0 and iv_rank < 75:
        return "CALL_ALLOWED"
    if rr >= 1.15 and iv_rank >= 0 and iv_rank < 65:
        return "CALL_ALLOWED"
    return "STOCK_PREFERRED"


def _has_anchor_signal(signals: list[str], row: dict[str, Any]) -> bool:
    sig_text = " ".join(str(s).lower() for s in signals)
    if any(k in sig_text for k in ["insider", "contract", "gov", "pead", "post_earnings"]):
        return True
    if row.get("insider_summary") or row.get("gov_summary") or row.get("contracts"):
        return True
    pead = row.get("pead") or {}
    return bool(pead.get("active"))


def _regime_adjusted_action(action: str, score: float, rr: float, signals: list[str],
                            row: dict[str, Any], regime: dict[str, Any] | None,
                            profile: dict[str, Any] | None = None) -> tuple[str, str | None]:
    status = str((regime or {}).get("status") or "green").lower()
    if action not in {"ACCUMULATE", "STARTER"}:
        return action, None
    if status in {"unknown", "doomsday"}:
        return "WATCH", f"regime {status} blocks new sizing"
    if status == "red":
        if _has_anchor_signal(signals, row) and score >= 72 and rr >= 1.7:
            return "STARTER", "red regime: anchor signal allowed at starter posture"
        return "WATCH", "red regime whitelist: only insider/contract/PEAD anchors can size"
    if status == "downtrend":
        if _has_anchor_signal(signals, row):
            return action, "downtrend: anchored thesis allowed"
        if _is_lottery_row(row) and score >= float((profile or {}).get("starter_score", 58)) + 10 and rr >= 1.8:
            return "STARTER", "downtrend: Lottery evidence cleared elevated starter bar"
        if score >= 84 and rr >= 2.2:
            return "STARTER", "downtrend: raised long bar met"
        return "WATCH", "downtrend whitelist: unanchored longs stay watch-only"
    return action, None


def _ratchet_profile(action: str, upside_pct: float, rr: float, signals: list[str]) -> dict[str, Any]:
    normalized = {str(signal).upper() for signal in signals}
    high_vol = bool({"HIGH_SHORT_INTEREST", "UNUSUAL_FLOW", "OPTION_SQUEEZE"} & normalized)
    if upside_pct >= 60 or (high_vol and upside_pct >= 35):
        profile = {
            "name": "RUNNER",
            "initial_tp_pct": 25.0,
            "initial_sl_pct": 15.0,
            "trigger_step_pct": 10.0,
            "stop_raise_pct": 7.5,
            "target_raise_pct": 18.0,
            "max_ratchets": 8,
        }
    elif upside_pct >= 25 or rr >= 2.0:
        profile = {
            "name": "CORE",
            "initial_tp_pct": 15.0,
            "initial_sl_pct": 10.0,
            "trigger_step_pct": 5.0,
            "stop_raise_pct": 5.0,
            "target_raise_pct": 10.0,
            "max_ratchets": 6,
        }
    else:
        profile = {
            "name": "TACTICAL",
            "initial_tp_pct": 10.0,
            "initial_sl_pct": 7.0,
            "trigger_step_pct": 3.0,
            "stop_raise_pct": 3.0,
            "target_raise_pct": 5.0,
            "max_ratchets": 4,
        }
    if action == "STARTER":
        profile = {**profile}
        profile["initial_tp_pct"] = max(8.0, profile["initial_tp_pct"] - 3.0)
        profile["initial_sl_pct"] = max(5.0, profile["initial_sl_pct"] - 2.0)
        profile["max_ratchets"] = max(3, profile["max_ratchets"] - 1)
        profile["name"] = f"{profile['name']}_STARTER"
    return profile


def _ratchet_plan(
    action: str,
    price: float,
    target: float,
    stop: float,
    upside_pct: float,
    rr: float,
    signals: list[str],
    no_capped_tp: bool = False,
) -> dict[str, Any]:
    if action not in {"ACCUMULATE", "STARTER"} or price <= 0:
        return {"enabled": False}
    profile = _ratchet_profile(action, upside_pct, rr, signals)
    initial_stop_pct = profile["initial_sl_pct"]
    if stop > 0 and stop < price:
        initial_stop_pct = round(((price - stop) / price) * 100.0, 1)
        initial_stop_pct = min(profile["initial_sl_pct"], max(5.0, initial_stop_pct))
    initial_tp_pct = min(max(profile["initial_tp_pct"], 6.0), max(6.0, upside_pct))
    if target > price and not no_capped_tp:
        initial_tp_pct = min(initial_tp_pct, round(((target - price) / price) * 100.0, 1))
    levels = []
    for level in range(1, int(profile["max_ratchets"]) + 1):
        trigger_pct = round(level * profile["trigger_step_pct"], 1)
        stop_pct = round(-initial_stop_pct + level * profile["stop_raise_pct"], 1)
        target_pct = None if no_capped_tp else round(initial_tp_pct + level * profile["target_raise_pct"], 1)
        levels.append({
            "level": level,
            "trigger_gain_pct": trigger_pct,
            "stop_gain_pct": stop_pct,
            "target_gain_pct": target_pct,
        })
    return {
        "enabled": True,
        "profile": profile["name"],
        "initial_target_pct": None if no_capped_tp else round(initial_tp_pct, 1),
        "initial_stop_pct": round(initial_stop_pct, 1),
        "initial_target_price": None if no_capped_tp else round(price * (1 + initial_tp_pct / 100.0), 2),
        "initial_stop_price": round(price * (1 - initial_stop_pct / 100.0), 2),
        "trigger_step_pct": profile["trigger_step_pct"],
        "stop_raise_pct": profile["stop_raise_pct"],
        "target_raise_pct": profile["target_raise_pct"],
        "max_ratchets": profile["max_ratchets"],
        "levels": levels,
        "no_capped_tp": bool(no_capped_tp),
        "exit_policy": "STOP_RATCHET_ONLY" if no_capped_tp else "TARGET_AND_STOP_RATCHET",
        "notes": (
            "PM-owned stop ratchet; no take-profit cap, position exits only on protective/risk rules."
            if no_capped_tp
            else "PM-owned dynamic exit ladder; stop only moves favorably."
        ),
    }


def _reasons(row: dict[str, Any], action: str, rr: float, upside: float, risk_score: float) -> tuple[list[str], list[str]]:
    sigs = _signals(row)
    reasons = []
    cautions = []
    if sigs:
        reasons.append(f"{len(set(sigs))} confirmed signal types: {', '.join(sigs[:4])}")
    trade_score = _num(row.get("trade_score"))
    if trade_score:
        reasons.append(f"trade score {trade_score:.1f}")
    if upside > 0:
        reasons.append(f"{upside:.1f}% blended upside")
    if rr > 0:
        reasons.append(f"{rr:.2f} risk/reward")
    if risk_score >= 70:
        cautions.append(f"risk score elevated at {risk_score:.1f}")
    if action == "WATCH":
        cautions.append("score is not high enough for algorithmic sizing")
    if action == "REJECT":
        cautions.append("fails current portfolio-manager score threshold")
    earnings = row.get("earnings_summary") or {}
    if earnings.get("earnings_date"):
        cautions.append(f"earnings date {earnings.get('earnings_date')}")
    return reasons, cautions


def _profile_for(mode: str, profile_override: dict[str, Any] | None = None) -> dict[str, Any]:
    mode = (mode or "BALANCED").upper()
    profile = MODE_PROFILES.get(mode, MODE_PROFILES["BALANCED"])
    if not profile_override:
        return profile
    return {**profile, **{k: v for k, v in profile_override.items() if v is not None}}


def _profile_for_row(
    mode: str,
    row: dict[str, Any],
    profile_override: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], str]:
    base = _profile_for(mode, profile_override)
    if not _is_lottery_row(row):
        return base, f"CORE_{(mode or 'BALANCED').upper()}"
    lottery = {**LOTTERY_PROFILES.get((mode or "BALANCED").upper(), LOTTERY_PROFILES["BALANCED"])}
    # Explicit PM rules remain authoritative over the sleeve defaults.
    if profile_override:
        lottery.update({k: v for k, v in profile_override.items() if v is not None})
    return lottery, f"LOTTERY_{(mode or 'BALANCED').upper()}"


def evaluate_rows(
    rows: list[dict[str, Any]],
    equity: float = DEFAULT_EQUITY,
    mode: str = "BALANCED",
    profile_override: dict[str, Any] | None = None,
    regime: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    mode = (mode or "BALANCED").upper()
    out: list[dict[str, Any]] = []
    for row in rows:
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            continue
        price = _num(row.get("price"))
        row_profile, strategy_profile = _profile_for_row(mode, row, profile_override)
        target = _target(row)
        stop = _stop(row, price)
        upside = _upside_pct(price, target)
        downside = _downside_pct(price, stop)
        rr = _rr(upside, downside)
        score, breakdown = _pm_score(row, price, target, stop)
        risk_score = _num((row.get("risk") or {}).get("score"))
        action = _action(score, rr, len(set(_signals(row))), risk_score, row_profile)
        reasons, cautions = _reasons(row, action, rr, upside, risk_score)
        signals = _signals(row)
        action, regime_note = _regime_adjusted_action(action, score, rr, signals, row, regime, row_profile)
        if regime_note:
            cautions.append(regime_note)
        sizing = _sizing(action, score, price, stop, equity, row_profile, row)
        ratchet = _ratchet_plan(
            action,
            price,
            target,
            stop,
            upside,
            rr,
            signals,
            no_capped_tp=_is_lottery_row(row),
        )
        strategy_case = row.get("strategy_case") or {}
        out.append({
            "ticker": ticker,
            "action": action,
            "pm_score": score,
            "score_breakdown": breakdown,
            "price": price,
            "entry_low": row.get("entry_low"),
            "entry_high": row.get("entry_high"),
            "target": target,
            "stop": stop,
            "upside_pct": round(upside, 1),
            "downside_pct": round(downside, 1),
            "risk_reward": round(rr, 2),
            "allocation_usd": sizing["allocation_usd"],
            "shares": sizing["shares"],
            "risk_usd": sizing["risk_usd"],
            "position_pct": sizing["position_pct"],
            "option_view": _option_view(row, rr),
            "preferred_route": (row.get("options") or {}).get("preferred_route"),
            "scanner_family": row.get("scanner_family") or (row.get("strategy_scanner") or {}).get("family"),
            "strategy_profile": strategy_profile,
            "source_scan": row.get("source_scan"),
            "signals": signals,
            "ratchet_plan": ratchet,
            "strategy_case": strategy_case,
            "case_score": _num(row.get("case_score") or strategy_case.get("case_score")),
            "strategy_confidence": _num(row.get("strategy_confidence") or strategy_case.get("confidence")),
            "strategy_views": row.get("strategy_views") or [],
            "scanner_sources": row.get("scanner_sources") or [],
            "sizing_multipliers": sizing.get("sizing_multipliers") or {},
            "trade_score": row.get("trade_score"),
            "signal_score": row.get("signal_score"),
            "learning_score": row.get("learning_score"),
            "sector": row.get("sector"),
            "regime": (regime or {}).get("status"),
            "regime_playbook": (regime or {}).get("playbook"),
            "reasons": reasons,
            "cautions": cautions,
        })
    # Lottery is the designated high-alpha sleeve. Once a Lottery row has
    # already cleared its own PM gates, give it priority within the account
    # deployment budget; this prevents generic higher scores from consuming
    # all cash before the specialist sleeve is considered.
    out.sort(key=lambda r: (
        _is_lottery_row(r),
        r["action"] == "ACCUMULATE",
        r["action"] == "STARTER",
        r["pm_score"],
    ), reverse=True)
    # The outer cap remains the broad PM account cap. Per-sleeve caps are
    # enforced during sizing, while this pass prevents the merged docket from
    # exceeding the account-wide deployment limit.
    account_profile = _profile_for(mode, profile_override)
    remaining_deploy = equity * account_profile["max_gross_deployment_pct"]
    max_sector_deploy = equity * float(account_profile.get("max_sector_deployment_pct") or 0.25)
    sector_used: dict[str, float] = {}
    for row in out:
        if row["action"] not in {"ACCUMULATE", "STARTER"}:
            continue
        desired = float(row["allocation_usd"] or 0)
        if desired <= 0:
            continue
        sector = (row.get("sector") or "Unknown").title()
        sector_room = max(0.0, max_sector_deploy - sector_used.get(sector, 0.0))
        approved = min(desired, max(0.0, remaining_deploy), sector_room)
        if approved <= 0:
            row["allocation_usd"] = 0.0
            row["shares"] = 0.0
            row["risk_usd"] = 0.0
            row["position_pct"] = 0.0
            row["action"] = "WATCH"
            row["ratchet_plan"] = {"enabled": False}
            cap_reason = "sector exposure cap reached" if sector_room <= 0 else "portfolio gross deployment cap reached"
            row["cautions"].append(cap_reason)
            continue
        if approved < desired:
            scale = approved / desired
            row["allocation_usd"] = round(approved, 2)
            row["shares"] = round(float(row["shares"] or 0) * scale, 4)
            row["risk_usd"] = round(float(row["risk_usd"] or 0) * scale, 2)
            row["position_pct"] = round((approved / equity) * 100.0, 2) if equity > 0 else 0.0
            cap_reason = "sized down by sector exposure cap" if approved == sector_room else "sized down by portfolio gross deployment cap"
            row["cautions"].append(cap_reason)
        remaining_deploy -= approved
        sector_used[sector] = sector_used.get(sector, 0.0) + approved
    return out


def _summary(rows: list[dict[str, Any]], equity: float, mode: str, equity_source: str, regime: dict[str, Any]) -> dict[str, Any]:
    deployable = [r for r in rows if r["action"] in {"ACCUMULATE", "STARTER"}]
    planned_deployment = sum(r["allocation_usd"] for r in deployable)
    planned_risk = sum(r["risk_usd"] for r in deployable)
    target_upside = sum(max(0.0, (r["target"] - r["price"]) * r["shares"]) for r in deployable)
    high_short_loss = sum(
        min(float(r["allocation_usd"] or 0) * 0.12, float(r["risk_usd"] or 0) * 1.5)
        for r in deployable
        if "high_short_interest" in (r.get("signals") or [])
    )
    sector_allocations: dict[str, float] = {}
    for r in deployable:
        sector = (r.get("sector") or "Unknown").title()
        sector_allocations[sector] = sector_allocations.get(sector, 0.0) + float(r["allocation_usd"] or 0)
    largest_sector, largest_sector_allocation = ("None", 0.0)
    if sector_allocations:
        largest_sector, largest_sector_allocation = max(sector_allocations.items(), key=lambda kv: kv[1])
    return {
        "equity_basis": round(equity, 2),
        "equity_source": equity_source,
        "mode": mode,
        "regime": regime,
        "rows": len(rows),
        "accumulate": sum(1 for r in rows if r["action"] == "ACCUMULATE"),
        "starter": sum(1 for r in rows if r["action"] == "STARTER"),
        "watch": sum(1 for r in rows if r["action"] == "WATCH"),
        "reject": sum(1 for r in rows if r["action"] == "REJECT"),
        "planned_deployment": round(planned_deployment, 2),
        "planned_risk": round(planned_risk, 2),
        "cash_reserved": round(max(0.0, equity - planned_deployment), 2),
        "target_upside_usd": round(target_upside, 2),
        "shock_tests": [
            {
                "name": "ALL STOPS HIT",
                "loss_usd": round(planned_risk, 2),
                "equity_pct": round((planned_risk / equity) * 100.0, 2) if equity > 0 else 0.0,
                "detail": "Every active PM stop is hit.",
            },
            {
                "name": "MARKET GAP -5%",
                "loss_usd": round(planned_deployment * 0.05, 2),
                "equity_pct": round(((planned_deployment * 0.05) / equity) * 100.0, 2) if equity > 0 else 0.0,
                "detail": "Active basket gaps down 5% before exits.",
            },
            {
                "name": "SHORT-SQUEEZE FAIL",
                "loss_usd": round(high_short_loss, 2),
                "equity_pct": round((high_short_loss / equity) * 100.0, 2) if equity > 0 else 0.0,
                "detail": "High-short-interest names reverse hard.",
            },
            {
                "name": f"{largest_sector.upper()} -7%",
                "loss_usd": round(largest_sector_allocation * 0.07, 2),
                "equity_pct": round(((largest_sector_allocation * 0.07) / equity) * 100.0, 2) if equity > 0 else 0.0,
                "detail": "Largest sector sleeve pulls back 7%.",
            },
        ],
    }


def _mode_from_regime(regime: dict[str, Any]) -> str:
    status = (regime or {}).get("status")
    if status in {"red", "doomsday", "unknown"} or (regime or {}).get("halt_new_entries"):
        return "RISK_OFF"
    if status in {"yellow", "downtrend"}:
        return "CONSERVATIVE"
    return "BALANCED"


def _exposure(rows: list[dict[str, Any]]) -> dict[str, Any]:
    active = [r for r in rows if r["allocation_usd"] > 0]
    by_sector: dict[str, float] = {}
    by_action: dict[str, float] = {}
    by_option_view: dict[str, float] = {}
    for r in active:
        allocation = float(r["allocation_usd"] or 0)
        sector = (r.get("sector") or "Unknown").title()
        by_sector[sector] = by_sector.get(sector, 0.0) + allocation
        by_action[r["action"]] = by_action.get(r["action"], 0.0) + allocation
        by_option_view[r["option_view"]] = by_option_view.get(r["option_view"], 0.0) + allocation
    def _rows(d: dict[str, float]) -> list[dict[str, Any]]:
        total = sum(d.values()) or 1.0
        return [
            {"name": k, "value": round(v, 2), "pct": round((v / total) * 100, 1)}
            for k, v in sorted(d.items(), key=lambda kv: kv[1], reverse=True)
        ]
    return {"by_sector": _rows(by_sector), "by_action": _rows(by_action), "by_option_view": _rows(by_option_view)}


def _pct(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        val = float(value)
        if abs(val) <= 2:
            val *= 100.0
        return val
    except Exception:
        return None


def _position_ticker(position: dict[str, Any]) -> str:
    return str(position.get("symbol") or position.get("ticker") or position.get("underlying") or "").upper()


def _holding_edge(position: dict[str, Any], pm_row: dict[str, Any] | None) -> float:
    unrealized_pct = _pct(position.get("unrealized_plpc") or position.get("unrealized_pct"))
    if pm_row:
        edge = _num(pm_row.get("pm_score"), 45)
    else:
        edge = 45.0
    if unrealized_pct is not None:
        edge += max(-18.0, min(12.0, unrealized_pct * 0.65))
    return round(max(0.0, min(100.0, edge)), 1)


def _opportunity_cost_review(recommendations: list[dict[str, Any]], positions: list[dict[str, Any]], equity: float) -> dict[str, Any]:
    rec_by_ticker = {str(r.get("ticker") or "").upper(): r for r in recommendations if r.get("ticker")}
    held = {_position_ticker(p) for p in positions or [] if _position_ticker(p)}
    deployable = [
        r for r in recommendations
        if r.get("ticker") not in held
        and r.get("action") in {"ACCUMULATE", "STARTER"}
        and float(r.get("allocation_usd") or 0) > 0
    ]
    deployable.sort(key=lambda r: (_num(r.get("pm_score")), _num(r.get("case_score")), _num(r.get("strategy_confidence"))), reverse=True)
    best_new = deployable[0] if deployable else None
    reviews: list[dict[str, Any]] = []
    replacement_candidates: list[dict[str, Any]] = []
    trim_reviews: list[dict[str, Any]] = []
    for position in positions or []:
        ticker = _position_ticker(position)
        if not ticker:
            continue
        pm_row = rec_by_ticker.get(ticker)
        unrealized_pct = _pct(position.get("unrealized_plpc") or position.get("unrealized_pct"))
        edge = _holding_edge(position, pm_row)
        protected_winner = bool(unrealized_pct is not None and unrealized_pct >= 8 and edge >= 50)
        action = "HOLD"
        reason = "holding edge acceptable"
        required_gap = 18.0 if not protected_winner else 28.0
        if unrealized_pct is not None and unrealized_pct <= -7 and edge < 48:
            action = "EXIT_REVIEW"
            reason = "loser below invalidation band with weak forward edge"
            trim_reviews.append({"ticker": ticker, "action": action, "reason": reason, "holding_edge": edge, "unrealized_pct": round(unrealized_pct, 2)})
        elif unrealized_pct is not None and unrealized_pct <= -3 and edge < 55:
            action = "TRIM_REVIEW"
            reason = "weak holding can fund stronger current setup"
            trim_reviews.append({"ticker": ticker, "action": action, "reason": reason, "holding_edge": edge, "unrealized_pct": round(unrealized_pct, 2)})
        if best_new and ticker != best_new.get("ticker"):
            new_edge = _num(best_new.get("pm_score")) + min(6.0, _num(best_new.get("case_score")) * 0.04) + min(4.0, _num(best_new.get("strategy_confidence")) * 4.0)
            edge_gap = round(new_edge - edge, 1)
            if edge_gap >= required_gap and not protected_winner:
                action = "REPLACE_REVIEW" if action == "HOLD" else action
                reason = f"best new setup beats holding by {edge_gap} points"
                replacement_candidates.append({
                    "sell_review": ticker,
                    "buy_candidate": best_new.get("ticker"),
                    "edge_gap": edge_gap,
                    "holding_edge": edge,
                    "new_pm_score": best_new.get("pm_score"),
                    "new_case_score": best_new.get("case_score"),
                    "new_confidence": best_new.get("strategy_confidence"),
                })
        reviews.append({
            "ticker": ticker,
            "action": action,
            "reason": reason,
            "holding_edge": edge,
            "pm_score": (pm_row or {}).get("pm_score"),
            "unrealized_pct": round(unrealized_pct, 2) if unrealized_pct is not None else None,
            "protected_winner": protected_winner,
            "market_value": _num(position.get("market_value")),
        })
    for idx, candidate in enumerate(deployable[:5]):
        candidate["opportunity_cost"] = {
            "rank": idx + 1,
            "best_available": idx == 0,
            "funding_candidates": replacement_candidates[:3] if idx == 0 else [],
            "churn_guard": "requires_material_edge_gap_before_replacing_current_holding",
        }
    return {
        "enabled": True,
        "policy": "controlled_high_turnover",
        "rules": {
            "fast_loser_review_pct": -7,
            "trim_review_pct": -3,
            "replacement_edge_gap": 18,
            "protected_winner_gap": 28,
            "runner_tranche_policy": "do not replace protected winners unless edge gap is extreme",
        },
        "positions_reviewed": len(reviews),
        "replacement_candidates": replacement_candidates[:8],
        "trim_reviews": trim_reviews[:8],
        "holding_reviews": reviews,
        "cash_equity_basis": round(equity, 2),
    }


def _merge_strategy_rows(core_rows: list[dict[str, Any]], strategy_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One PM row per ticker, with Core rows kept as the base when present."""
    merged: dict[str, dict[str, Any]] = {}
    for row in core_rows or []:
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            continue
        merged[ticker] = {
            **row,
            "strategy_screeners": list(row.get("strategy_screeners") or []),
            "scanner_sources": list(row.get("scanner_sources") or ["core_scan"]),
            "strategy_views": list(row.get("strategy_views") or []),
        }
    for row in strategy_rows or []:
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            continue
        scanner = row.get("strategy_scanner") or {}
        screener_id = str(scanner.get("screener_id") or row.get("source_scan") or "strategy_screener")
        if ticker not in merged:
            merged[ticker] = {
                **row,
                "strategy_screeners": [scanner],
                "scanner_sources": [screener_id],
                "strategy_views": [_strategy_view(row, scanner)],
            }
            continue
        base = merged[ticker]
        base.setdefault("strategy_screeners", []).append(scanner)
        base.setdefault("strategy_views", []).append(_strategy_view(row, scanner))
        sources = list(base.get("scanner_sources") or [])
        if screener_id not in sources:
            sources.append(screener_id)
        base["scanner_sources"] = sources
        base["signals"] = list(dict.fromkeys([*(_signals(base)), *(_signals(row))]))
        base["signal_score"] = max(_num(base.get("signal_score")), _num(row.get("signal_score")))
        base["trade_score"] = max(_num(base.get("trade_score")), _num(row.get("trade_score")))
        incoming_case = row.get("strategy_case") or {}
        base_case = base.get("strategy_case") or {}
        if _num(incoming_case.get("case_score")) > _num(base_case.get("case_score")):
            base["strategy_case"] = incoming_case
            base["case_score"] = incoming_case.get("case_score")
            base["strategy_confidence"] = incoming_case.get("confidence")
        if not base.get("price") and row.get("price"):
            base["price"] = row.get("price")
        if not base.get("sector") and row.get("sector"):
            base["sector"] = row.get("sector")
        base["targets"] = base.get("targets") or row.get("targets") or {}
        if not base.get("stop_loss") and row.get("stop_loss"):
            base["stop_loss"] = row.get("stop_loss")
        base["strategy_scanner_overlay"] = True
    return list(merged.values())


def _strategy_view(row: dict[str, Any], scanner: dict[str, Any]) -> dict[str, Any]:
    """Keep each specialist opinion visible after ticker-level PM merging."""
    case = row.get("strategy_case") or {}
    return {
        "screener_id": scanner.get("screener_id") or row.get("source_scan"),
        "family": scanner.get("family") or row.get("scanner_family"),
        "lane": scanner.get("lane"),
        "native_score": scanner.get("native_score"),
        "case_score": row.get("case_score") or case.get("case_score"),
        "confidence": row.get("strategy_confidence") or case.get("confidence"),
        "badges": scanner.get("badges") or [],
        "pm_routable": bool(scanner.get("pm_routable", row.get("pm_routable", True))),
        "read_only": bool(scanner.get("read_only", row.get("read_only", False))),
    }


async def _account_equity() -> tuple[float | None, str]:
    try:
        from . import trade_floor
        account = await asyncio.wait_for(trade_floor.get_account(), timeout=4.0)
        if account and account.get("equity"):
            return float(account["equity"]), "alpaca"
    except Exception:
        pass
    return None, "fallback"


async def latest_portfolio_plan(
    equity: float | None = None,
    mode: str = "AUTO",
    ruleset_id: str | None = None,
    *,
    scan: dict[str, Any] | None = None,
    strategy_payload: dict[str, Any] | None = None,
    lottery_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    db = get_db()
    if scan is None:
        scan = await db.scan_results.find_one({}, {"_id": 0}, sort=[("finished_at", -1)])
    core_rows = (scan or {}).get("results") or []
    strategy_rows: list[dict[str, Any]] = []
    try:
        if strategy_payload is None:
            from . import strategy_screeners

            strategy_payload = await strategy_screeners.pm_rows(
                scan=scan,
                persist=True,
                lottery_result=lottery_result,
            )
        strategy_payload = strategy_payload or {}
        strategy_rows = strategy_payload.get("rows") or []
    except Exception as exc:
        strategy_payload = {
            "ok": False,
            "error": str(exc),
            "summary": {"pm_rows": 0, "case_court_active_routing": False, "sec_bearish_veto_enabled": False},
        }
    rows = _merge_strategy_rows(core_rows, strategy_rows)
    account_equity, equity_source = await _account_equity()
    equity_basis = float(equity or account_equity or DEFAULT_EQUITY)
    if equity:
        equity_source = "manual"
    try:
        from . import trade_floor
        regime = await asyncio.wait_for(trade_floor.regime_status(), timeout=8.0)
    except Exception:
        regime = {"status": "unknown", "halt_new_entries": False, "source": "timeout_fallback"}
    requested_mode = (mode or "AUTO").upper()
    active_mode = _mode_from_regime(regime) if requested_mode == "AUTO" else requested_mode
    if active_mode not in MODE_PROFILES:
        active_mode = "BALANCED"
    try:
        from . import pm_rules
        ruleset = await pm_rules.get_ruleset(ruleset_id)
        profile_override = await pm_rules.profile_override_for(active_mode, ruleset_id)
    except Exception:
        ruleset = {"ruleset_id": "pm-default-v1", "name": "PM Default v1", "active": True}
        profile_override = {}
    profile = _profile_for(active_mode, profile_override)
    recommendations = evaluate_rows(rows, equity=equity_basis, mode=active_mode, profile_override=profile_override, regime=regime)
    if scan and scan.get("finished_at"):
        try:
            await db.portfolio_manager_history.update_one(
                {"_id": f"pm:{scan['finished_at']}"},
                {"$set": {
                    "scan_finished_at": scan.get("finished_at"),
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "summary": _summary(recommendations, equity_basis, active_mode, equity_source, regime),
                    "recommendations": recommendations,
                }},
                upsert=True,
            )
        except Exception:
            # Historical funnel telemetry must never block PM recommendations.
            pass
    try:
        from . import trade_floor

        live_positions = await asyncio.wait_for(trade_floor.list_positions(), timeout=5.0)
        opportunity_cost = _opportunity_cost_review(recommendations, live_positions, equity_basis)
    except Exception as exc:
        live_positions = []
        opportunity_cost = {
            "enabled": False,
            "reason": f"position_review_unavailable:{exc.__class__.__name__}",
            "positions_reviewed": 0,
            "replacement_candidates": [],
            "trim_reviews": [],
            "holding_reviews": [],
        }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scan_finished_at": (scan or {}).get("finished_at"),
        "mode": active_mode,
        "requested_mode": requested_mode,
        "ruleset": {
            "ruleset_id": ruleset.get("ruleset_id"),
            "name": ruleset.get("name"),
            "active": ruleset.get("active"),
        },
        "claude_required": False,
        "summary": _summary(recommendations, equity_basis, active_mode, equity_source, regime),
        "input_rows": {
            "core": len(core_rows),
            "strategy_pm": len(strategy_rows),
            "merged": len(rows),
            "case_court_active_routing": False,
            "sec_bearish_veto_enabled": False,
        },
        "strategy_screeners": strategy_payload.get("summary") or {},
        "opportunity_cost": opportunity_cost,
        "exposure": _exposure(recommendations),
        "recommendations": recommendations,
        "rules": {
            "max_position_pct": profile["max_position_pct"],
            "max_single_name_risk_pct": profile["max_single_name_risk_pct"],
            "max_gross_deployment_pct": profile["max_gross_deployment_pct"],
            "max_sector_deployment_pct": profile.get("max_sector_deployment_pct"),
            "regime_whitelist": {
                "red": "starter sizing only for insider, contract, or PEAD anchors",
                "downtrend": "anchored longs allowed; unanchored longs require raised score and RR",
                "doomsday": "watch-only; no new sizing",
            },
            "mode_profiles": MODE_PROFILES,
            "actions": {
                "ACCUMULATE": f"pm_score >= {profile['accumulate_score']}, risk/reward >= {profile['accumulate_rr']}, at least 3 signals, risk_score < 75",
                "STARTER": f"pm_score >= {profile['starter_score']}, risk/reward >= {profile['starter_rr']}, at least 2 signals",
                "WATCH": f"pm_score >= {profile['watch_score']} but not sized",
                "REJECT": "below watch threshold",
            },
        },
    }
