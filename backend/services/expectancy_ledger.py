"""Options expectancy ledger.

Closed option trades are recorded here from actual broker fills plus the
mid-price context captured by Options Desk V2. The ledger is intentionally
append/upsert by trade id so repeated fill syncs do not duplicate rows.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .db import get_db, stamped


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _signals(trade_doc: dict[str, Any]) -> list[str]:
    candidate = trade_doc.get("candidate") or {}
    sigs = candidate.get("signals") or trade_doc.get("signals_at_entry") or []
    if isinstance(sigs, dict):
        return sorted(str(k) for k, v in sigs.items() if v)
    return sorted(str(s) for s in sigs)


def _lane(trade_doc: dict[str, Any]) -> str:
    lane = ((trade_doc.get("strategy_lane") or {}).get("lane")
            or (((trade_doc.get("candidate") or {}).get("strategy_lane") or {}).get("lane"))
            or "")
    return "TAIL" if "TAIL" in str(lane).upper() or str(trade_doc.get("lane") or "").upper() == "TAIL" else "GRIND"


async def record_closed_trade(trade_doc: dict[str, Any]) -> dict[str, Any]:
    db = get_db()
    if not trade_doc:
        return {"ok": False, "reason": "missing_trade_doc"}
    trade_id = str(trade_doc.get("trade_id") or trade_doc.get("entry_order_id") or trade_doc.get("symbol") or "")
    if not trade_id:
        return {"ok": False, "reason": "missing_trade_id"}

    entry_mid = _num(trade_doc.get("mid_at_fill") or trade_doc.get("entry_premium"))
    entry_fill = _num(trade_doc.get("fill_price") or trade_doc.get("entry_premium"))
    exit_mid = _num(trade_doc.get("exit_mid") or trade_doc.get("exit_premium"))
    exit_price = _num(trade_doc.get("exit_premium"))
    qty = int(_num(trade_doc.get("qty"), 0))
    pnl_pct_mid = ((exit_mid - entry_mid) / entry_mid * 100.0) if entry_mid > 0 and exit_mid > 0 else None
    pnl_pct_fill = ((exit_price - entry_fill) / entry_fill * 100.0) if entry_fill > 0 and exit_price > 0 else None
    pnl_usd = (exit_price - entry_fill) * qty * 100 if entry_fill > 0 and exit_price > 0 else None
    strategy_lane = (
        ((trade_doc.get("strategy_lane") or {}).get("lane"))
        or (((trade_doc.get("candidate") or {}).get("strategy_lane") or {}).get("lane"))
        or "UNKNOWN"
    )
    candidate = trade_doc.get("candidate") or {}
    instrument = candidate.get("instrument") or {}
    doc = stamped({
        "trade_id": trade_id,
        "ticker": trade_doc.get("ticker"),
        "symbol": trade_doc.get("symbol"),
        "lane": _lane(trade_doc),
        "strategy_lane": strategy_lane,
        "signals_at_entry": _signals(trade_doc),
        "iv_rank_at_entry": _num(candidate.get("iv_rank")),
        "delta_at_entry": _num(instrument.get("delta")),
        "dte_at_entry": int(_num(trade_doc.get("dte_at_entry") or instrument.get("days_to_expiration"), 0)),
        "mid_at_fill": entry_mid or None,
        "fill_price": entry_fill or None,
        "spread_cost_paid": trade_doc.get("spread_cost_paid"),
        "exit_price": exit_price or None,
        "exit_mid": exit_mid or None,
        "exit_reason": trade_doc.get("close_reason") or trade_doc.get("exit_reason"),
        "hold_days": _num(trade_doc.get("hold_days")),
        "pnl_pct": round(pnl_pct_mid, 2) if pnl_pct_mid is not None else None,
        "pnl_pct_actual_fill": round(pnl_pct_fill, 2) if pnl_pct_fill is not None else None,
        "pnl_usd": round(pnl_usd, 2) if pnl_usd is not None else None,
        "closed_at": trade_doc.get("closed_at") or _now(),
        "source": "options_desk_sync_fills",
    })
    await db.options_expectancy.update_one({"trade_id": trade_id}, {"$set": doc}, upsert=True)
    return {"ok": True, "trade_id": trade_id, "ledger": doc}


async def lane_expectancy(lane: str = "GRIND", strategy_lane: str | None = None, window: int = 20) -> dict[str, Any]:
    db = get_db()
    query: dict[str, Any] = {"lane": lane}
    if strategy_lane:
        query["strategy_lane"] = strategy_lane
    rows = await db.options_expectancy.find(query, {"_id": 0}).sort("closed_at", -1).to_list(window)
    vals = [r for r in rows if r.get("pnl_pct_actual_fill") is not None or r.get("pnl_pct") is not None]
    pnls = [_num(r.get("pnl_pct_actual_fill") if r.get("pnl_pct_actual_fill") is not None else r.get("pnl_pct")) for r in vals]
    wins = [x for x in pnls if x > 0]
    losses = [abs(x) for x in pnls if x <= 0]
    sample = len(pnls)
    win_rate = len(wins) / sample if sample else 0.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    spread_costs = [_num(r.get("spread_cost_paid")) for r in vals]
    spread_cost_pcts = [
        (_num(r.get("spread_cost_paid")) / _num(r.get("mid_at_fill")) * 100.0)
        for r in vals
        if _num(r.get("spread_cost_paid")) > 0 and _num(r.get("mid_at_fill")) > 0
    ]
    avg_spread_cost = sum(spread_costs) / sample if sample else 0.0
    avg_spread_cost_pct = sum(spread_cost_pcts) / len(spread_cost_pcts) if spread_cost_pcts else 0.0
    expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss - avg_spread_cost_pct
    recent = pnls[:10]
    prior = pnls[10:20]
    recent_avg = sum(recent) / len(recent) if recent else 0.0
    prior_avg = sum(prior) / len(prior) if prior else 0.0
    return {
        "ok": True,
        "lane": lane,
        "strategy_lane": strategy_lane,
        "sample_size": sample,
        "win_rate": round(win_rate * 100, 2),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "avg_spread_cost": round(avg_spread_cost, 4),
        "avg_spread_cost_pct": round(avg_spread_cost_pct, 2),
        "expectancy_pct": round(expectancy, 2),
        "recent_10_avg_pct": round(recent_avg, 2),
        "prior_10_avg_pct": round(prior_avg, 2),
        "trend_pct": round(recent_avg - prior_avg, 2) if recent and prior else None,
        "rows": rows,
    }


async def check_lane_throttle() -> dict[str, Any]:
    db = get_db()
    lanes = await db.options_expectancy.distinct("strategy_lane")
    throttles: dict[str, dict[str, Any]] = {}
    for lane_name in lanes:
        stats = await lane_expectancy(strategy_lane=lane_name)
        mult = 1.0
        if stats["sample_size"] >= 40 and stats["expectancy_pct"] < 0:
            mult = 0.0
        elif stats["sample_size"] >= 20 and stats["expectancy_pct"] < 0:
            mult = 0.5
        throttles[str(lane_name)] = {"multiplier": mult, "stats": stats}
    await db.options_lane_throttle.update_one(
        {"_id": "latest"},
        {"$set": {"generated_at": _now(), "throttles": throttles}},
        upsert=True,
    )
    return {"ok": True, "generated_at": _now(), "throttles": throttles}


async def weekly_expectancy_report() -> dict[str, Any]:
    return {
        "ok": True,
        "generated_at": _now(),
        "grind": await lane_expectancy("GRIND", window=100),
        "tail": await lane_expectancy("TAIL", window=100),
        "throttle": await check_lane_throttle(),
    }
