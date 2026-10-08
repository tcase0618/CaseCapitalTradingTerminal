"""Pure research measurements, never order pricing or execution authority.

Positive implementation cost is adverse; positive limit improvement is favorable.
Fees must be explicit totals in USD (including explicit zero), not estimates
masquerading as paid fees. Missing/invalid inputs stay unknown.
"""
from __future__ import annotations

import math
from typing import Any


def finite_number(value: Any, *, positive: bool = False) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    if not math.isfinite(number) or (number <= 0 if positive else number < 0):
        return None
    return number


def _sign(side: Any) -> int | None:
    return {"BUY": 1, "SELL": -1}.get(str(side).strip().upper())


def measure_fill_costs(*, side: Any, fill_price: Any, arrival_mid: Any = None,
                       limit_price: Any = None, filled_quantity: Any = None,
                       actual_fees_usd: Any = None, multiplier: Any = 1) -> dict[str, Any]:
    """One filled leg vs arrival mid; net cost adds paid fees exactly once.

    Fill prices already embed spread/impact. No additional spread is charged.
    Quantity is executed quantity, never requested or remaining quantity.
    """
    sign = _sign(side)
    fill = finite_number(fill_price, positive=True)
    mid = finite_number(arrival_mid, positive=True)
    limit = finite_number(limit_price, positive=True)
    qty = finite_number(filled_quantity, positive=True)
    mult = finite_number(multiplier, positive=True)
    fees = finite_number(actual_fees_usd)
    arrival_bps = sign * (fill - mid) / mid * 10000 if sign and fill and mid else None
    limit_bps = sign * (fill - limit) / limit * 10000 if sign and fill and limit else None
    gross = sign * (fill - mid) * qty * mult if sign and fill and mid and qty and mult else None
    net = gross + fees if gross is not None and fees is not None else None
    return {
        "side": "BUY" if sign == 1 else "SELL" if sign == -1 else "UNKNOWN",
        "unknown_reason": "unknown_side" if sign is None else "invalid_fill_price" if fill is None else None,
        "arrival_mid_slippage_bps": arrival_bps,
        "limit_relative_cost_bps": limit_bps,
        "limit_price_improvement_bps": -limit_bps if limit_bps is not None else None,
        "implementation_cost_gross_usd": gross,
        "actual_fees_usd": fees,
        "implementation_cost_net_usd": net,
    }


def measure_fill_pnl(*, entry_side: Any, entry_fill: Any, exit_fill: Any,
                     quantity: Any, entry_fees_usd: Any = None,
                     exit_fees_usd: Any = None, multiplier: Any = 1) -> dict[str, Any]:
    """Matched round-trip fill P&L; net = gross minus both paid fee totals.

    No spread deduction: both fills already include it. Caller must match the
    executed quantity and allocate fees to that quantity (including partials).
    """
    sign = _sign(entry_side)
    entry = finite_number(entry_fill, positive=True)
    exit_price = finite_number(exit_fill, positive=True)
    qty = finite_number(quantity, positive=True)
    mult = finite_number(multiplier, positive=True)
    entry_fee = finite_number(entry_fees_usd)
    exit_fee = finite_number(exit_fees_usd)
    gross = sign * (exit_price - entry) * qty * mult if sign and entry and exit_price and qty and mult else None
    fees = entry_fee + exit_fee if entry_fee is not None and exit_fee is not None else None
    return {"gross_pnl_usd": gross, "actual_fees_usd": fees,
            "net_pnl_usd": gross - fees if gross is not None and fees is not None else None,
            "spread_already_embedded": True}


def estimate_spread_fees(*, bid: Any, ask: Any, quantity: Any,
                         fees_usd: Any = None, legs: int = 1,
                         multiplier: Any = 1) -> dict[str, Any]:
    """Research-only crossing estimate, not an impact model or realized cost.

    Each leg crosses half the same supplied spread vs its midpoint. Two legs
    assume unchanged spread and matched size. fees_usd is the caller's TOTAL
    for all legs, not per-share/per-contract. No impact coefficient is fitted.
    """
    bid_value = finite_number(bid, positive=True)
    ask_value = finite_number(ask, positive=True)
    qty = finite_number(quantity, positive=True)
    mult = finite_number(multiplier, positive=True)
    fees = finite_number(fees_usd)
    valid = (bid_value is not None and ask_value is not None and ask_value >= bid_value
             and qty is not None and mult is not None and type(legs) is int and legs in (1, 2))
    spread = (ask_value - bid_value) / 2 * qty * mult * legs if valid else None
    total = spread + fees if spread is not None and fees is not None else None
    return {
        "research_only": True,
        "estimated_spread_cost_usd": spread,
        "assumed_fees_usd": fees,
        "estimated_total_cost_usd": total,
        "impact_cost_usd": None,
        "impact_model": "not_estimated",
        "assumptions": ["cross_half_spread_per_leg", "unchanged_quote_and_matched_size",
                        "fees_are_total_usd_all_legs", "nonnegative_fees_no_rebates",
                        "no_impact_latency_or_adverse_selection_model", "not_added_to_fill_based_pnl"],
    }
