"""Time target & FY seasonality. Pure Python."""
from __future__ import annotations
from datetime import date, datetime, timedelta
from typing import Any

# Per-signal historical resolution windows (low, high days)
SIGNAL_TIMING: dict[str, tuple[int, int]] = {
    "CONTRACT_SURGE": (21, 45),
    "NEW_WINNER": (30, 60),
    "CONCENTRATION_WIN": (14, 45),
    "MOMENTUM_STACK": (21, 60),
    "BUDGET_SURGE": (30, 90),
    "PRE_AWARD": (45, 90),
    "CONGRESSIONAL_BUY": (14, 30),
    "high_short_interest": (3, 14),
    "upcoming_earnings": (1, 3),
    "insider_cluster_buy": (30, 60),
    "SUB_BENEFICIARY": (30, 60),
}


def _next_business_day(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _add_business_days(d: date, n: int) -> date:
    cur = d
    while n > 0:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            n -= 1
    return cur


def _parse_catalyst(s: str) -> date | None:
    if not s:
        return None
    s = s.strip()
    # Try ISO
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        pass
    # Tokens like "nextweek", "thismonth"
    today = date.today()
    if "nextweek" in s.lower() or "next week" in s.lower():
        return today + timedelta(days=7)
    if "thismonth" in s.lower() or "this month" in s.lower():
        return today + timedelta(days=14)
    if "nextmonth" in s.lower():
        return today + timedelta(days=30)
    return None


def compute_time_target(signals: list[str], catalyst_text: str) -> dict[str, Any]:
    today = date.today()
    catalyst = _parse_catalyst(catalyst_text)
    # Clamp catalyst to future only — Claude sometimes returns past dates
    # from its training data
    if catalyst and catalyst <= today:
        catalyst = None

    # Catalyst-based target
    cat_target: date | None = None
    if catalyst:
        days_to = (catalyst - today).days
        if days_to <= 7:
            cat_target = catalyst
        elif days_to <= 30:
            cat_target = _add_business_days(catalyst, 3)
        else:
            full = _add_business_days(catalyst, 5)
            cat_target = full

    # Signal-based target (use shortest+longest windows averaged)
    timings = [SIGNAL_TIMING[s] for s in signals if s in SIGNAL_TIMING]
    if timings:
        avg_low = sum(t[0] for t in timings) / len(timings)
        avg_high = sum(t[1] for t in timings) / len(timings)
        sig_low = today + timedelta(days=int(avg_low))
        sig_high = today + timedelta(days=int(avg_high))
        sig_mid = today + timedelta(days=int((avg_low + avg_high) / 2))
    else:
        sig_low = sig_mid = sig_high = None
    candidates = [d for d in [cat_target, sig_mid] if d and d > today]
    if candidates:
        avg_days = sum((d - today).days for d in candidates) / len(candidates)
        target = _next_business_day(today + timedelta(days=int(round(avg_days))))
    elif cat_target and cat_target > today:
        target = cat_target
    elif sig_mid:
        target = sig_mid
    else:
        target = today + timedelta(days=30)

    days_low = (sig_low - today).days if sig_low else (target - today).days - 7
    days_high = (sig_high - today).days if sig_high else (target - today).days + 14
    return {
        "target_date": target.isoformat(),
        "days_remaining": (target - today).days,
        "hold_period_low": max(1, days_low),
        "hold_period_high": max(days_low + 1, days_high),
    }


def fiscal_year_multiplier_active() -> bool:
    """True for July, August, September (US fed FY ends Sept 30)."""
    return datetime.now().month in (7, 8, 9)


def fy_days_remaining() -> int:
    today = date.today()
    fy_end = date(today.year, 9, 30) if today.month <= 9 else date(today.year + 1, 9, 30)
    return (fy_end - today).days


GOV_SIGNALS = {"CONTRACT_SURGE", "NEW_WINNER", "CONCENTRATION_WIN",
                "MOMENTUM_STACK", "BUDGET_SURGE", "PRE_AWARD"}


def apply_fy_multiplier(signals: list[str], base_score: int) -> tuple[int, bool]:
    """Apply 1.5x to gov signal contributions if FY-end window. Returns
    (adjusted_score, multiplier_applied)."""
    if not fiscal_year_multiplier_active():
        return base_score, False
    gov_count = sum(1 for s in signals if s in GOV_SIGNALS)
    if gov_count == 0:
        return base_score, False
    bump = int(round(gov_count * 1.0))  # add gov_count points (representing 1.5x weighting)
    return min(10, base_score + bump), True
