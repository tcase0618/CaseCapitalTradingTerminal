"""PM-owned dynamic exit ratchet.

This updates active stop/target levels on open Trade Floor positions using
the ratchet plan decided by the Portfolio Manager. It does not open trades.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from .db import get_db, stamped


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def compute_active_levels(entry: float, current: float, plan: dict[str, Any], previous_stop: float = 0.0) -> dict[str, Any]:
    if entry <= 0 or current <= 0 or not plan.get("enabled"):
        return {"enabled": False}
    gain_pct = ((current - entry) / entry) * 100.0
    trigger_step = _num(plan.get("trigger_step_pct"), 5.0)
    max_ratchets = int(_num(plan.get("max_ratchets"), 0))
    ratchet_level = 0
    if trigger_step > 0:
        ratchet_level = min(max_ratchets, max(0, int(gain_pct // trigger_step)))
    initial_stop_pct = _num(plan.get("initial_stop_pct"), 10.0)
    no_capped_tp = bool(plan.get("no_capped_tp"))
    initial_target_pct = _num(plan.get("initial_target_pct"), 15.0)
    stop_gain_pct = -initial_stop_pct + ratchet_level * _num(plan.get("stop_raise_pct"), 5.0)
    target_gain_pct = None if no_capped_tp else initial_target_pct + ratchet_level * _num(plan.get("target_raise_pct"), 10.0)
    active_stop = round(entry * (1 + stop_gain_pct / 100.0), 4)
    active_target = None if target_gain_pct is None else round(entry * (1 + target_gain_pct / 100.0), 4)
    if previous_stop > 0:
        active_stop = max(previous_stop, active_stop)
    return {
        "enabled": True,
        "gain_pct": round(gain_pct, 2),
        "ratchet_level": ratchet_level,
        "active_stop": active_stop,
        "active_target": active_target,
        "stop_gain_pct": round(((active_stop - entry) / entry) * 100.0, 2),
        "target_gain_pct": round(target_gain_pct, 2) if target_gain_pct is not None else None,
    }


def _public_account_protection_plan(entry: float, current: float) -> dict[str, Any]:
    """Create a no-cap protection ratchet for an existing Public holding.

    Holdings imported from the broker have no trustworthy original PM thesis.
    When such a holding is already below the default initial stop, anchoring at
    its original cost would create a retroactive stop above the live price and
    cause an immediate, invented liquidation.  Anchor the migration at the
    fresh mark instead; subsequent losses are protected and later gains still
    ratchet normally.  Terminal-owned positions retain their PM-authored plan.
    """
    initial_stop_pct = max(1.0, min(50.0, _num(os.environ.get("PUBLIC_ACCOUNT_RATCHET_INITIAL_STOP_PCT"), 10.0)))
    trigger_step_pct = max(0.5, min(25.0, _num(os.environ.get("PUBLIC_ACCOUNT_RATCHET_TRIGGER_STEP_PCT"), 3.0)))
    stop_raise_pct = max(0.25, min(25.0, _num(os.environ.get("PUBLIC_ACCOUNT_RATCHET_STOP_RAISE_PCT"), 3.0)))
    max_ratchets = max(1, min(100, int(_num(os.environ.get("PUBLIC_ACCOUNT_RATCHET_MAX_LEVELS"), 20))))
    anchor = entry if entry > 0 else current
    migrated_at_loss = current < anchor * (1.0 - initial_stop_pct / 100.0)
    if migrated_at_loss:
        anchor = current
    return {
        "enabled": True,
        "profile": "ACCOUNT_PROTECTION",
        "anchor_price": round(anchor, 6),
        "initial_stop_pct": round(initial_stop_pct, 3),
        "trigger_step_pct": round(trigger_step_pct, 3),
        "stop_raise_pct": round(stop_raise_pct, 3),
        "max_ratchets": max_ratchets,
        "no_capped_tp": True,
        "exit_policy": "STOP_RATCHET_ONLY",
        "migration": "fresh_mark_anchor" if migrated_at_loss else "cost_basis_anchor",
    }


def _fresh_public_execution_mark(row: dict[str, Any]) -> float | None:
    """Return a ratchet mark only when it is safe to price a Public order.

    A quote that is old or so wide that an order would be rejected must not
    advance an active stop. Using its midpoint would make the monitor look
    protected while anchoring the ratchet to an untradeable price.
    """
    from . import public_execution, safety

    quote, _reason = public_execution._execution_quote(row, side="SELL")
    fresh, _age = safety.quote_is_fresh({"ts": public_execution._execution_quote_timestamp(row)})
    if not fresh or not quote:
        return None
    mark = _num(quote.get("mid"))
    return mark if mark > 0 else None


async def _public_prices(tickers: list[str]) -> dict[str, float]:
    """Return only fresh, executable Public marks for Public-broker ratchets."""
    from . import public_api, public_execution

    if not tickers:
        return {}
    async with public_api.PublicAPIClient(use_sdk=False) as client:
        rows = public_execution._quotes(await client.quotes(tickers))
    prices: dict[str, float] = {}
    for row in rows:
        ticker = str(row.get("ticker") or row.get("symbol") or "").upper()
        price = _fresh_public_execution_mark(row)
        if ticker and price:
            prices[ticker] = price
    return prices


async def _apply_public_ratchet_marks(
    open_trades: list[dict[str, Any]],
    prices: dict[str, float],
    *,
    source: str,
) -> dict[str, Any]:
    """Apply already-validated Public marks to the authoritative ratchet ledger.

    Both the one-minute REST monitor and the SDK price subscription use this
    path.  The caller must have already rejected stale, one-sided, or
    untradeably wide quotes; this function never treats a portfolio mark as an
    executable price.
    """
    db = get_db()
    actions: list[dict[str, Any]] = []
    coverage_initialized: list[str] = []
    unpriced: list[str] = []
    broker_scope = {"broker_base": "public"}
    for trade in open_trades:
        ticker = (trade.get("ticker") or "").upper()
        entry = _num(trade.get("filled_avg_price") or trade.get("entry_price_ref"))
        if not ticker or entry <= 0:
            continue
        current = prices.get(ticker)
        if not current or current <= 0:
            unpriced.append(ticker)
            continue
        plan = trade.get("pm_ratchet_plan") or {}
        initialized = False
        if not plan.get("enabled"):
            plan = _public_account_protection_plan(entry, float(current))
            initialized = True
            coverage_initialized.append(ticker)
        ratchet_entry = _num(plan.get("anchor_price")) or entry
        if ratchet_entry <= 0:
            unpriced.append(ticker)
            continue
        previous_stop = _num(trade.get("current_stop") or trade.get("stop_price"))
        observed_peak = max(_num(trade.get("peak_price_since_entry"), ratchet_entry), float(current))
        levels = compute_active_levels(ratchet_entry, observed_peak, plan, previous_stop)
        if not levels.get("enabled"):
            continue
        current_level = int(_num(trade.get("pm_ratchet_level"), 0))
        updates = {
            "pm_active_target": levels["active_target"],
            "pm_active_stop": levels["active_stop"],
            "pm_last_ratchet_check": _now().isoformat(),
            "pm_last_ratchet_source": source,
            "peak_price_since_entry": observed_peak,
            "pm_last_ratchet_mark": float(current),
        }
        if initialized:
            updates.update({
                "pm_ratchet_plan": plan,
                "management_state": "PUBLIC_ACCOUNT_RATCHET",
                "protection_state": "MONITORED_EXIT_ONLY",
                "protection_note": "Account-protection ratchet initialized from a fresh Public mark; no original PM thesis was assumed.",
            })
        if levels["active_stop"] > previous_stop:
            updates["current_stop"] = levels["active_stop"]
        if levels["ratchet_level"] > current_level:
            updates["pm_ratchet_level"] = levels["ratchet_level"]
            await db.pm_ratchet_events.insert_one(stamped({
                "client_order_id": trade.get("client_order_id"),
                "ticker": ticker,
                "entry": entry,
                "current": float(current),
                "peak_price": observed_peak,
                "previous_level": current_level,
                "new_level": levels["ratchet_level"],
                "active_stop": levels["active_stop"],
                "active_target": levels["active_target"],
                "gain_pct": levels["gain_pct"],
                "profile": (trade.get("pm_ratchet_plan") or {}).get("profile"),
                "broker_base": "public",
                "source": source,
                "created_at": _now().isoformat(),
            }))
            actions.append({
                "ticker": ticker,
                "level": levels["ratchet_level"],
                "active_stop": levels["active_stop"],
                "active_target": levels["active_target"],
                "gain_pct": levels["gain_pct"],
            })
        await db.tf_trades.update_one(
            {"$and": [{"client_order_id": trade.get("client_order_id")}, broker_scope]},
            {"$set": updates},
        )
    return {
        "checked": len(open_trades),
        "ratcheted": len(actions),
        "coverage_initialized": len(coverage_initialized),
        "coverage_initialized_tickers": coverage_initialized,
        "unpriced": sorted(set(unpriced)),
        "actions": actions,
        "ran_at": _now().isoformat(),
    }


async def process_public_ratchet_marks(marks: dict[str, float], *, source: str = "public_price_stream") -> dict[str, Any]:
    """Apply callback marks from Public's SDK subscription to held positions."""
    prices = {
        str(ticker).upper(): float(price)
        for ticker, price in marks.items()
        if str(ticker).strip() and _num(price) > 0
    }
    if not prices:
        return {"ok": True, "checked": 0, "ratcheted": 0, "source": source}
    db = get_db()
    filters = {
        "broker_base": "public",
        "status": "OPEN",
        "fill_status": "FILLED",
        "qty_remaining": {"$gt": 0},
        "ticker": {"$in": list(prices)},
    }
    rows = await db.tf_trades.find(filters, {"_id": 0}).to_list(500)
    result = await _apply_public_ratchet_marks(rows, prices, source=source)
    return {"ok": True, "source": source, **result}


async def process_open_ratchets(*, broker_base: str | None = None) -> dict[str, Any]:
    """Raise stops for filled positions with an enabled PM ratchet plan.

    Public and Alpaca use distinct quote and account paths. The Public branch
    deliberately uses only fresh broker quotes; it cannot silently ratchet
    from an old portfolio mark.
    """
    db = get_db()
    if broker_base == "public":
        broker_scope = {"broker_base": "public"}
    else:
        from .trade_floor_phases import _alpaca_trade_base, _broker_scope
        broker_base = _alpaca_trade_base()
        broker_scope = _broker_scope()
    filters = {
        "status": "OPEN",
        "fill_status": "FILLED",
        "qty_remaining": {"$gt": 0},
    }
    # Every broker-reconciled Public holding must be covered. Alpaca retains
    # its PM-only scope because its equity execution path is retired.
    if broker_base != "public":
        filters["pm_ratchet_plan.enabled"] = True
    open_trades = await db.tf_trades.find(
        {"$and": [filters, broker_scope]},
        {"_id": 0},
    ).to_list(500)
    if broker_base == "public":
        public_prices = await _public_prices([str(trade.get("ticker") or "") for trade in open_trades])
        result = await _apply_public_ratchet_marks(open_trades, public_prices, source="public_rest_monitor")
        return {"broker_base": broker_base, **result}
    actions: list[dict[str, Any]] = []
    coverage_initialized: list[str] = []
    unpriced: list[str] = []
    try:
        from .trade_floor_phases import _current_price
    except Exception:
        _current_price = None
    for trade in open_trades:
        ticker = (trade.get("ticker") or "").upper()
        entry = _num(trade.get("filled_avg_price") or trade.get("entry_price_ref"))
        if not ticker or entry <= 0:
            continue
        if _current_price is not None:
            current = await _current_price(ticker)
        else:
            current = None
        if not current or current <= 0:
            if broker_base == "public" and ticker:
                unpriced.append(ticker)
            continue
        plan = trade.get("pm_ratchet_plan") or {}
        initialized = False
        ratchet_entry = _num(plan.get("anchor_price")) or entry
        if ratchet_entry <= 0:
            unpriced.append(ticker)
            continue
        previous_stop = _num(trade.get("current_stop") or trade.get("stop_price"))
        observed_peak = max(_num(trade.get("peak_price_since_entry"), entry), float(current))
        levels = compute_active_levels(ratchet_entry, observed_peak, plan, previous_stop)
        if not levels.get("enabled"):
            continue
        current_level = int(_num(trade.get("pm_ratchet_level"), 0))
        updates = {
            "pm_active_target": levels["active_target"],
            "pm_active_stop": levels["active_stop"],
            "pm_last_ratchet_check": _now().isoformat(),
            "peak_price_since_entry": observed_peak,
            "pm_last_ratchet_mark": float(current),
        }
        if initialized:
            updates.update({
                "pm_ratchet_plan": plan,
                "management_state": "PUBLIC_ACCOUNT_RATCHET",
                "protection_state": "MONITORED_EXIT_ONLY",
                "protection_note": "Account-protection ratchet initialized from a fresh Public mark; no original PM thesis was assumed.",
            })
        if levels["active_stop"] > previous_stop:
            updates["current_stop"] = levels["active_stop"]
        if levels["ratchet_level"] > current_level:
            updates["pm_ratchet_level"] = levels["ratchet_level"]
            await db.pm_ratchet_events.insert_one(stamped({
                "client_order_id": trade.get("client_order_id"),
                "ticker": ticker,
                "entry": entry,
                "current": float(current),
                "peak_price": observed_peak,
                "previous_level": current_level,
                "new_level": levels["ratchet_level"],
                "active_stop": levels["active_stop"],
                "active_target": levels["active_target"],
                "gain_pct": levels["gain_pct"],
                "profile": (trade.get("pm_ratchet_plan") or {}).get("profile"),
                "broker_base": broker_base,
                "created_at": _now().isoformat(),
            }))
            actions.append({
                "ticker": ticker,
                "level": levels["ratchet_level"],
                "active_stop": levels["active_stop"],
                "active_target": levels["active_target"],
                "gain_pct": levels["gain_pct"],
            })
        await db.tf_trades.update_one(
            {"$and": [{"client_order_id": trade.get("client_order_id")}, broker_scope]},
            {"$set": updates},
        )
    return {
        "broker_base": broker_base,
        "checked": len(open_trades),
        "ratcheted": len(actions),
        "coverage_initialized": len(coverage_initialized),
        "coverage_initialized_tickers": coverage_initialized,
        "unpriced": sorted(set(unpriced)),
        "actions": actions,
        "ran_at": _now().isoformat(),
    }


async def recent_events(limit: int = 50, *, broker_base: str | None = None) -> dict[str, Any]:
    db = get_db()
    if broker_base == "public":
        scope = {"broker_base": "public"}
    else:
        from .trade_floor_phases import _broker_scope
        scope = _broker_scope()
    rows = await db.pm_ratchet_events.find(scope, {"_id": 0}).sort("created_at", -1).to_list(limit)
    return {"events": rows, "count": len(rows)}
