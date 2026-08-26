"""Strategy ideology registry used by scanner fan-out and PM allocation.

These definitions are deliberately deterministic. They give each strategy a
native meaning for case score, confidence, risk shape, turnover behavior, and
invalidation without letting the scanner execute orders.
"""
from __future__ import annotations

from typing import Any


IDEOLOGY_VERSION = "strategy-ideology-v1.0"


STRATEGIES: dict[str, dict[str, Any]] = {
    "lottery_day2_continuation": {
        "mission": "Capture second-leg continuation after a fresh runner appears.",
        "ideal_setup": ["fresh gap or surge", "RVOL expanding", "price holding structure"],
        "risk_shape": "high_turnover_fat_tail",
        "preferred_expression": "equity_first_options_if_liquid",
        "turnover_policy": "fast_invalidations_keep_runner_tranche",
        "max_confidence": 0.82,
        "max_sleeve_risk_pct": 0.35,
        "invalidation": ["VWAP failure", "RVOL collapse", "failed high reclaim", "offering filed"],
    },
    "lottery_red_green": {
        "mission": "Trade red-to-green reclaim behavior in active small-cap names.",
        "ideal_setup": ["early weakness", "reclaim", "volume confirms", "spread remains tradable"],
        "risk_shape": "intraday_tactical",
        "preferred_expression": "equity",
        "turnover_policy": "quick_exit_on_reclaim_failure",
        "max_confidence": 0.74,
        "max_sleeve_risk_pct": 0.25,
        "invalidation": ["red-green reclaim failure", "VWAP rejection", "liquidity fades"],
    },
    "lottery_supernova": {
        "mission": "Find rare multi-leg runners where attention and float rotation can compound.",
        "ideal_setup": ["float rotation", "extreme RVOL", "clean catalyst or attention shock", "no active dilution"],
        "risk_shape": "very_high_variance_fat_tail",
        "preferred_expression": "equity_or_tight_call",
        "turnover_policy": "scale_out_aggressively_leave_runner_tranche",
        "max_confidence": 0.78,
        "max_sleeve_risk_pct": 0.40,
        "invalidation": ["rotation stalls", "halt dump", "volume cliff", "offering filed"],
    },
    "lottery_catalyst_runner": {
        "mission": "Catch low-float names where a fresh catalyst can force repricing.",
        "ideal_setup": ["catalyst novelty", "volume expansion", "structure holds", "no immediate financing risk"],
        "risk_shape": "catalyst_reflexive",
        "preferred_expression": "equity_or_defined_risk_option",
        "turnover_policy": "hold while catalyst attention persists",
        "max_confidence": 0.80,
        "max_sleeve_risk_pct": 0.32,
        "invalidation": ["catalyst disproven", "news fades", "VWAP loss", "filing risk appears"],
    },
    "lottery_serial_runner": {
        "mission": "Exploit ticker memory when prior runners show repeated attention cycles.",
        "ideal_setup": ["prior runner history", "new attention wave", "tradable spread"],
        "risk_shape": "serial_attention",
        "preferred_expression": "equity",
        "turnover_policy": "recycle capital quickly if recurrence fails",
        "max_confidence": 0.76,
        "max_sleeve_risk_pct": 0.28,
        "invalidation": ["no follow-through", "failed prior-high test", "volume fades"],
    },
    "options_native": {
        "mission": "Express high-conviction directional setups only when option market quality supports it.",
        "ideal_setup": ["underlying PM edge", "tight spread", "adequate OI", "delta in band", "premium inside budget"],
        "risk_shape": "defined_premium",
        "preferred_expression": "option_if_contract_clean_else_equity",
        "turnover_policy": "avoid_churn_wide_spreads",
        "max_confidence": 0.84,
        "max_sleeve_risk_pct": 0.22,
        "invalidation": ["spread widens", "OI too thin", "delta out of band", "underlying thesis fails"],
    },
    "options_tactical_momentum_call": {
        "mission": "Buy directional call exposure only when underlying momentum and option liquidity line up.",
        "ideal_setup": ["relative volume rising", "short-term trend confirms", "spread tradable", "delta in band"],
        "risk_shape": "defined_premium_momentum",
        "preferred_expression": "long_call_or_call_debit_spread",
        "turnover_policy": "fast_entry_fast_invalidation_no_chasing",
        "max_confidence": 0.82,
        "max_sleeve_risk_pct": 0.20,
        "invalidation": ["momentum stalls", "spread widens", "delta leaves band", "premium exceeds budget"],
    },
    "options_breakout_call": {
        "mission": "Route liquid breakout candidates into options PM review for convex upside.",
        "ideal_setup": ["new high or range break", "volume confirmation", "liquid common stock", "contract OI adequate"],
        "risk_shape": "defined_premium_breakout",
        "preferred_expression": "long_call_if_iv_reasonable_else_debit_spread",
        "turnover_policy": "cancel quickly if breakout fails",
        "max_confidence": 0.80,
        "max_sleeve_risk_pct": 0.18,
        "invalidation": ["failed breakout retest", "volume fades", "wide option spread"],
    },
    "options_leaps_trend": {
        "mission": "Find durable trend candidates where longer dated calls can replace equity exposure.",
        "ideal_setup": ["larger liquid company", "multi-month trend", "less binary risk", "long-dated chain available"],
        "risk_shape": "delta_notional_long_duration",
        "preferred_expression": "itm_leaps_or_no_trade",
        "turnover_policy": "low_turnover_roll_before_theta_accelerates",
        "max_confidence": 0.78,
        "max_sleeve_risk_pct": 0.16,
        "invalidation": ["trend breaks", "thesis weakens", "IV too high for stock replacement"],
    },
    "options_event_defined_risk": {
        "mission": "Use options only when a dated event gives a defined risk/reward setup.",
        "ideal_setup": ["dated catalyst", "known event window", "priced contract", "binary risk explicitly capped"],
        "risk_shape": "event_defined_premium",
        "preferred_expression": "debit_spread_or_small_long_call",
        "turnover_policy": "exit before event unless PM approves binary hold",
        "max_confidence": 0.76,
        "max_sleeve_risk_pct": 0.14,
        "invalidation": ["event date slips", "IV explodes", "contract market thins"],
    },
    "options_squeeze_call": {
        "mission": "Scout high-short-interest names where call convexity may be worth the spread.",
        "ideal_setup": ["high short interest", "attention rising", "liquid chain", "borrow/squeeze pressure plausible"],
        "risk_shape": "defined_premium_squeeze",
        "preferred_expression": "call_or_call_spread_only_if_contract_clean",
        "turnover_policy": "small_size_take_profit_fast",
        "max_confidence": 0.77,
        "max_sleeve_risk_pct": 0.14,
        "invalidation": ["squeeze fails", "OI thin", "spread widens", "underlying reverses"],
    },
    "pharma_calendar": {
        "mission": "Route dated biotech catalysts into PM with binary risk explicitly sized.",
        "ideal_setup": ["dated FDA/catalyst event", "liquidity present", "run-up window active", "no near-term dilution"],
        "risk_shape": "binary_catalyst",
        "preferred_expression": "defined_risk_option_or_small_equity",
        "turnover_policy": "runup_exit_before_binary_unless_odds_gap_real",
        "max_confidence": 0.72,
        "max_sleeve_risk_pct": 0.18,
        "invalidation": ["date slip", "trial risk worsens", "financing filed", "spread untradeable"],
    },
    "pharma_core_overlap": {
        "mission": "Promote core scan names with pharma-specific evidence to catalyst review.",
        "ideal_setup": ["core setup", "pharma signal", "tradable price action"],
        "risk_shape": "catalyst_overlay",
        "preferred_expression": "defined_risk_option_or_equity",
        "turnover_policy": "size_small_until_catalyst_quality_confirmed",
        "max_confidence": 0.70,
        "max_sleeve_risk_pct": 0.16,
        "invalidation": ["catalyst data missing", "date slip", "liquidity fails"],
    },
}


DEFAULT_IDEOLOGY = {
    "mission": "Generic strategy scanner candidate for PM review.",
    "ideal_setup": ["multi-source evidence", "tradable price", "clear invalidation"],
    "risk_shape": "generic",
    "preferred_expression": "pm_decides",
    "turnover_policy": "only_replace_when_edge_gap_is_material",
    "max_confidence": 0.65,
    "max_sleeve_risk_pct": 0.15,
    "invalidation": ["setup fails", "data quality degrades", "fresh quote unavailable"],
}


def get(strategy_id: str | None) -> dict[str, Any]:
    sid = str(strategy_id or "").strip()
    return {"strategy_id": sid or "unknown", **DEFAULT_IDEOLOGY, **STRATEGIES.get(sid, {})}


def case_score(
    *,
    strategy_id: str | None,
    native_score: float,
    row: dict[str, Any],
    family: str,
    lane: str,
) -> dict[str, Any]:
    ideology = get(strategy_id)
    native = max(0.0, min(100.0, float(native_score or 0)))
    signals = row.get("signals") or []
    if isinstance(signals, dict):
        signals = [k for k, v in signals.items() if v]
    triggers = row.get("triggers") or []
    components = row.get("components") or {}
    evidence_count = len(set(str(x) for x in [*signals, *triggers] if x))
    volume_component = max(float(components.get("rvol") or 0), float(components.get("rotation") or 0))
    catalyst_component = float(components.get("catalyst") or 0)
    structure_component = float(components.get("structure") or 0)
    data_penalty = 0.0
    if row.get("price") in {None, "", 0}:
        data_penalty += 10.0
    if row.get("quote_age_seconds") and float(row.get("quote_age_seconds") or 0) > 900:
        data_penalty += 8.0
    if (row.get("dilution") or {}).get("active"):
        data_penalty += 14.0
    score = native * 0.72 + min(12.0, evidence_count * 2.0) + min(8.0, volume_component * 0.55) + min(5.0, catalyst_component * 0.12) + min(3.0, structure_component * 0.3) - data_penalty
    score = round(max(0.0, min(100.0, score)), 1)
    confidence = 0.38 + min(0.22, evidence_count * 0.035) + min(0.16, native / 100.0 * 0.16)
    if volume_component > 0:
        confidence += min(0.08, volume_component / 100.0)
    if row.get("price"):
        confidence += 0.05
    if data_penalty:
        confidence -= min(0.22, data_penalty / 100.0)
    confidence = round(max(0.15, min(float(ideology["max_confidence"]), confidence)), 2)
    return {
        "version": IDEOLOGY_VERSION,
        "strategy_id": ideology["strategy_id"],
        "family": family,
        "lane": lane,
        "case_score": score,
        "confidence": confidence,
        "data_quality": round(max(0.0, min(100.0, 100.0 - data_penalty)), 1),
        "volume_intensity_score": round(min(100.0, volume_component * 5.0), 1),
        "evidence_count": evidence_count,
        "mission": ideology["mission"],
        "ideal_setup": ideology["ideal_setup"],
        "risk_shape": ideology["risk_shape"],
        "preferred_expression": ideology["preferred_expression"],
        "turnover_policy": ideology["turnover_policy"],
        "max_sleeve_risk_pct": ideology["max_sleeve_risk_pct"],
        "invalidation": ideology["invalidation"],
    }


def apply_lottery_learning(
    case: dict[str, Any],
    *,
    native_score: float,
    row: dict[str, Any],
    learned_config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Adjust the visible Lottery case badge from closed-ticket learning.

    This is deliberately modest. Learning may tune badges and confidence, but it
    does not create order authority or bypass PM/execution gates.
    """
    if not learned_config:
        return {**case, "learning_adjustment": {"active": False, "badges": ["LEARNING_ARMED"]}}

    score = float(case.get("case_score") or 0)
    confidence = float(case.get("confidence") or 0)
    badges: list[str] = []
    delta = 0.0

    min_score = float(learned_config.get("min_ticket_score") or 60)
    if native_score < min_score:
        delta -= 8.0
        confidence -= 0.05
        badges.append(f"BELOW_LEARNED_MIN_{int(min_score)}")

    strategy_id = str(case.get("strategy_id") or "")
    retired = {str(x) for x in learned_config.get("retired_variants") or []}
    if strategy_id in retired:
        delta -= 20.0
        confidence -= 0.18
        badges.append("VARIANT_RETIRED")

    score_bucket = (
        "90+" if native_score >= 90 else
        "80-89" if native_score >= 80 else
        "70-79" if native_score >= 70 else
        "60-69" if native_score >= 60 else
        "<60"
    )
    triggers = {str(x).upper() for x in (row.get("triggers") or row.get("signals") or [])}
    catalyst_class = "PHARMA_FDA" if {"PHARMA/FDA", "PDUFA", "FDA_CALENDAR"} & triggers else "ATTENTION" if {"ATTENTION", "X_FACTOR"} & triggers else "UNCLASSIFIED"

    for item in learned_config.get("preferred_segments") or []:
        dim = str(item.get("dimension") or "")
        seg = str(item.get("segment") or "")
        if (dim == "score_bucket" and seg == score_bucket) or (dim == "catalyst_class" and seg == catalyst_class):
            delta += 4.0
            confidence += 0.03
            badges.append(f"LEARNED_EDGE_{seg}")

    for item in learned_config.get("penalized_segments") or []:
        dim = str(item.get("dimension") or "")
        seg = str(item.get("segment") or "")
        if (dim == "score_bucket" and seg == score_bucket) or (dim == "catalyst_class" and seg == catalyst_class):
            delta -= 5.0
            confidence -= 0.04
            badges.append(f"LEARNED_DRAG_{seg}")

    if not badges:
        badges.append(str(learned_config.get("status") or "GATHERING"))

    updated = {
        **case,
        "case_score": round(max(0.0, min(100.0, score + delta)), 1),
        "confidence": round(max(0.15, min(float(case.get("confidence") or 0.65) + 0.12, confidence)), 2),
        "learning_adjustment": {
            "active": True,
            "delta": round(delta, 1),
            "badges": badges[:4],
            "config_version": learned_config.get("version"),
            "sample_count": learned_config.get("sample_count"),
        },
    }
    return updated
