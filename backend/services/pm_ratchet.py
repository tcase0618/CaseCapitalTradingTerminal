"""PM-owned dynamic exit ratchet.

This updates active stop/target levels on open Trade Floor positions using
the ratchet plan decided by the Portfolio Manager. It does not open trades.
"""
from __future__ import annotations

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
    initial_target_pct = _num(plan.get("initial_target_pct"), 15.0)
    stop_gain_pct = -initial_stop_pct + ratchet_level * _num(plan.get("stop_raise_pct"), 5.0)
    target_gain_pct = initial_target_pct + ratchet_level * _num(plan.get("target_raise_pct"), 10.0)
    active_stop = round(entry * (1 + stop_gain_pct / 100.0), 4)
    active_target = round(entry * (1 + target_gain_pct / 100.0), 4)
    if previous_stop > 0:
        active_stop = max(previous_stop, active_stop)
    return {
        "enabled": True,
        "gain_pct": round(gain_pct, 2),
        "ratchet_level": ratchet_level,
        "active_stop": active_stop,
        "active_target": active_target,
        "stop_gain_pct": round(((active_stop - entry) / entry) * 100.0, 2),
        "target_gain_pct": round(target_gain_pct, 2),
    }


async def process_open_ratchets() -> dict[str, Any]:
    db = get_db()
    open_trades = await db.tf_trades.find(
        {
            "status": "OPEN",
            "fill_status": "FILLED",
            "qty_remaining": {"$gt": 0},
            "pm_ratchet_plan.enabled": True,
        },
        {"_id": 0},
    ).to_list(500)
    actions: list[dict[str, Any]] = []
    try:
        from .trade_floor_phases import _current_price
    except Exception:
        _current_price = None
    for trade in open_trades:
        ticker = (trade.get("ticker") or "").upper()
        entry = _num(trade.get("filled_avg_price") or trade.get("entry_price_ref"))
        if not ticker or entry <= 0 or _current_price is None:
            continue
        current = await _current_price(ticker)
        if not current or current <= 0:
            continue
        previous_stop = _num(trade.get("current_stop") or trade.get("stop_price"))
        levels = compute_active_levels(entry, float(current), trade.get("pm_ratchet_plan") or {}, previous_stop)
        if not levels.get("enabled"):
            continue
        current_level = int(_num(trade.get("pm_ratchet_level"), 0))
        updates = {
            "pm_active_target": levels["active_target"],
            "pm_active_stop": levels["active_stop"],
            "pm_last_ratchet_check": _now().isoformat(),
            "peak_price_since_entry": max(_num(trade.get("peak_price_since_entry"), entry), float(current)),
        }
        if levels["active_stop"] > previous_stop:
            updates["current_stop"] = levels["active_stop"]
        if levels["ratchet_level"] > current_level:
            updates["pm_ratchet_level"] = levels["ratchet_level"]
            await db.pm_ratchet_events.insert_one(stamped({
                "client_order_id": trade.get("client_order_id"),
                "ticker": ticker,
                "entry": entry,
                "current": float(current),
                "previous_level": current_level,
                "new_level": levels["ratchet_level"],
                "active_stop": levels["active_stop"],
                "active_target": levels["active_target"],
                "gain_pct": levels["gain_pct"],
                "profile": (trade.get("pm_ratchet_plan") or {}).get("profile"),
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
            {"client_order_id": trade.get("client_order_id")},
            {"$set": updates},
        )
    return {"checked": len(open_trades), "ratcheted": len(actions), "actions": actions, "ran_at": _now().isoformat()}


async def recent_events(limit: int = 50) -> dict[str, Any]:
    db = get_db()
    rows = await db.pm_ratchet_events.find({}, {"_id": 0}).sort("created_at", -1).to_list(limit)
    return {"events": rows, "count": len(rows)}
