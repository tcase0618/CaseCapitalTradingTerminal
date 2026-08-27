"""Shared options filter policy for selection and execution."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _number(name: str, default: float, integer: bool = False):
    try:
        value = float(os.environ.get(name, default))
        return int(value) if integer else value
    except (TypeError, ValueError):
        return int(default) if integer else default


@dataclass(frozen=True)
class OptionsPolicy:
    mode: str
    min_open_interest: int
    min_volume_when_low_oi: int
    min_volume_if_oi_unknown: int
    max_spread_abs: float
    max_spread_pct: float
    max_indicative_spread_pct: float
    min_premium: float
    min_abs_delta: float
    max_abs_delta: float
    target_delta: float


def get_policy() -> OptionsPolicy:
    requested = os.environ.get("OPTIONS_FILTER_POLICY", "standard").strip().lower()
    mode = requested if requested in {"standard", "paper_scout"} else "standard"
    scout = mode == "paper_scout"
    return OptionsPolicy(
        mode=mode,
        min_open_interest=int(_number("OPTIONS_MIN_OPEN_INTEREST", 100 if scout else 300, True)),
        min_volume_when_low_oi=int(_number("OPTIONS_MIN_VOLUME_WHEN_LOW_OI", 50 if scout else 100, True)),
        min_volume_if_oi_unknown=int(_number("OPTIONS_MIN_VOLUME_IF_OI_UNKNOWN", 50 if scout else 100, True)),
        max_spread_abs=_number("OPTIONS_MAX_SPREAD_ABS", 1.50 if scout else 0.75),
        max_spread_pct=_number("OPTIONS_MAX_SPREAD_PCT", 0.15 if scout else 0.12),
        max_indicative_spread_pct=_number("OPTIONS_MAX_INDICATIVE_SPREAD_PCT", 0.30 if scout else 0.25),
        min_premium=_number("OPTIONS_MIN_PREMIUM", 0.05),
        min_abs_delta=_number("OPTIONS_MIN_ABS_DELTA", 0.25 if scout else 0.35),
        max_abs_delta=_number("OPTIONS_MAX_ABS_DELTA", 0.80 if scout else 0.75),
        target_delta=_number("OPTIONS_TARGET_DELTA", 0.50 if scout else 0.55),
    )


def paper_scout_allowed() -> bool:
    base = os.environ.get("OPTIONS_APCA_API_BASE_URL", "").lower()
    return (
        os.environ.get("OPTIONS_FILTER_POLICY", "standard").strip().lower() == "paper_scout"
        and "paper-api.alpaca.markets" in base
        and os.environ.get("OPTIONS_ALLOW_INDICATIVE_EXECUTION", "false").strip().lower()
        in {"1", "true", "yes", "on"}
    )
