"""Strategy-specific lifecycle contracts for PM recommendations.

The terminal historically produced one generic target/stop/hold contract for
very different setups.  This module makes the intended lifecycle explicit.
It is advisory only: callers may persist and display a plan, but it never
changes an entry, order, stop, or exit by itself.
"""
from __future__ import annotations

from typing import Any


LIFECYCLE_VERSION = "strategy-lifecycle-v1"


def _contract(
    *,
    horizon: str,
    max_hold_sessions: int | None,
    max_hold_trading_days: int | None,
    checkpoints: list[str],
    forecast_timeframes: list[str],
    invalidations: list[str],
    target_policy: str,
    overnight_allowed: bool,
) -> dict[str, Any]:
    return {
        "horizon": horizon,
        "max_hold_sessions": max_hold_sessions,
        "max_hold_trading_days": max_hold_trading_days,
        "checkpoints": checkpoints,
        "forecast_timeframes": forecast_timeframes,
        "invalidation_checks": invalidations,
        "target_policy": target_policy,
        "overnight_allowed": overnight_allowed,
    }


CONTRACTS: dict[str, dict[str, Any]] = {
    "lottery_day2_continuation": _contract(
        horizon="TACTICAL_1_TO_2_SESSIONS",
        max_hold_sessions=2,
        max_hold_trading_days=2,
        checkpoints=["entry_plus_5m", "entry_plus_15m", "hourly_rth", "session_close"],
        forecast_timeframes=["5m", "15m", "1h"],
        invalidations=["vwap_failure", "relative_volume_collapse", "failed_high_reclaim", "financing_or_offering"],
        target_policy="ratchet_only_after_continuation_confirms",
        overnight_allowed=True,
    ),
    "lottery_red_green": _contract(
        horizon="INTRADAY",
        max_hold_sessions=1,
        max_hold_trading_days=1,
        checkpoints=["entry_plus_5m", "entry_plus_15m", "hourly_rth", "session_close"],
        forecast_timeframes=["5m", "15m"],
        invalidations=["reclaim_failure", "vwap_rejection", "liquidity_fade"],
        target_policy="take_strength_or_exit_on_reclaim_failure",
        overnight_allowed=False,
    ),
    "lottery_supernova": _contract(
        horizon="RUNNER_1_TO_5_SESSIONS",
        max_hold_sessions=5,
        max_hold_trading_days=5,
        checkpoints=["entry_plus_5m", "hourly_rth", "session_close", "premarket"],
        forecast_timeframes=["5m", "15m", "1h", "1d"],
        invalidations=["float_rotation_stall", "volume_cliff", "halt_dump", "financing_or_offering"],
        target_policy="uncapped_ratchet_with_runner_tranche",
        overnight_allowed=True,
    ),
    "lottery_catalyst_runner": _contract(
        horizon="CATALYST_1_TO_3_SESSIONS",
        max_hold_sessions=3,
        max_hold_trading_days=3,
        checkpoints=["entry_plus_15m", "hourly_rth", "session_close", "catalyst_recheck"],
        forecast_timeframes=["15m", "1h", "1d"],
        invalidations=["catalyst_disproved", "news_attention_fade", "vwap_loss", "financing_or_offering"],
        target_policy="ratchet_while_catalyst_attention_persists",
        overnight_allowed=True,
    ),
    "lottery_serial_runner": _contract(
        horizon="TACTICAL_1_TO_3_SESSIONS",
        max_hold_sessions=3,
        max_hold_trading_days=3,
        checkpoints=["entry_plus_15m", "hourly_rth", "session_close"],
        forecast_timeframes=["5m", "15m", "1h"],
        invalidations=["follow_through_failure", "prior_high_failure", "volume_fade"],
        target_policy="recycle_capital_when_recurrence_fails",
        overnight_allowed=True,
    ),
    "options_tactical_momentum_call": _contract(
        horizon="TACTICAL_1_TO_3_SESSIONS",
        max_hold_sessions=3,
        max_hold_trading_days=3,
        checkpoints=["entry_plus_15m", "hourly_rth", "session_close"],
        forecast_timeframes=["5m", "15m", "1h"],
        invalidations=["underlying_momentum_failure", "spread_widening", "delta_drift"],
        target_policy="premium_ratchet_and_time_stop",
        overnight_allowed=True,
    ),
    "options_breakout_call": _contract(
        horizon="TACTICAL_1_TO_5_SESSIONS",
        max_hold_sessions=5,
        max_hold_trading_days=5,
        checkpoints=["entry_plus_15m", "hourly_rth", "session_close"],
        forecast_timeframes=["15m", "1h", "1d"],
        invalidations=["breakout_retest_failure", "volume_fade", "spread_widening"],
        target_policy="premium_ratchet_and_breakout_invalidation",
        overnight_allowed=True,
    ),
    "options_event_defined_risk": _contract(
        horizon="EVENT_WINDOW",
        max_hold_sessions=None,
        max_hold_trading_days=10,
        checkpoints=["daily", "event_minus_1_session", "event_time"],
        forecast_timeframes=["1h", "1d"],
        invalidations=["event_date_slip", "iv_explosion", "contract_liquidity_failure"],
        target_policy="exit_before_binary_unless_explicit_binary_approval",
        overnight_allowed=True,
    ),
    "options_leaps_trend": _contract(
        horizon="SWING_30_TO_180_SESSIONS",
        max_hold_sessions=None,
        max_hold_trading_days=180,
        checkpoints=["weekly", "earnings_minus_2_sessions", "roll_window"],
        forecast_timeframes=["1d"],
        invalidations=["trend_break", "thesis_deterioration", "iv_overpayment"],
        target_policy="trend_hold_with_roll_and_thesis_review",
        overnight_allowed=True,
    ),
    "pharma_calendar": _contract(
        horizon="CATALYST_WINDOW",
        max_hold_sessions=None,
        max_hold_trading_days=20,
        checkpoints=["daily", "catalyst_date_recheck", "event_minus_1_session"],
        forecast_timeframes=["1h", "1d"],
        invalidations=["event_date_slip", "trial_risk_change", "financing_or_offering"],
        target_policy="runup_exit_before_binary_without_explicit_approval",
        overnight_allowed=True,
    ),
    "CORE": _contract(
        horizon="SWING_10_TO_40_SESSIONS",
        max_hold_sessions=None,
        max_hold_trading_days=40,
        checkpoints=["daily", "weekly", "earnings_minus_2_sessions"],
        forecast_timeframes=["1h", "1d"],
        invalidations=["trend_break", "relative_strength_failure", "thesis_change"],
        target_policy="structure_target_then_trailing_stop",
        overnight_allowed=True,
    ),
}


def _strategy_id(row: dict[str, Any]) -> str:
    scanner = row.get("strategy_scanner") if isinstance(row.get("strategy_scanner"), dict) else {}
    for value in (
        row.get("strategy_id"), row.get("screener_id"), row.get("source_scan"), scanner.get("screener_id"),
    ):
        if value:
            return str(value).strip().lower()
    family = str(row.get("scanner_family") or scanner.get("family") or "").upper()
    return "CORE" if family == "CORE" else "CORE"


def plan_for(row: dict[str, Any], *, action: str, regime: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a display-only lifecycle contract for a PM recommendation."""
    strategy_id = _strategy_id(row)
    contract = CONTRACTS.get(strategy_id, CONTRACTS["CORE"])
    status = "SHADOW_ONLY"
    cautions: list[str] = []
    if action not in {"ACCUMULATE", "STARTER"}:
        status = "NOT_ACTIONABLE"
    if str((regime or {}).get("status") or "").lower() in {"red", "downtrend", "doomsday", "unknown"}:
        cautions.append("regime_requires_strategy_horizon_validation")
    if contract["horizon"].startswith("TACTICAL") or contract["horizon"] == "INTRADAY":
        cautions.append("generic_30_day_hold_is_incompatible")
    return {
        "version": LIFECYCLE_VERSION,
        "status": status,
        "strategy_id": strategy_id,
        **contract,
        "cautions": cautions,
        "execution_effect": "none",
    }
