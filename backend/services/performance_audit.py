"""Read-only reconciliation of signal, PM, order, fill, and P&L records.

This module deliberately has no execution imports and performs no writes.  It
exists so performance claims can be separated from hypothetical forward
returns and traced back to broker-backed order evidence.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from .db import get_db


def _status(row: dict[str, Any]) -> str:
    return str(row.get("status") or "UNKNOWN").upper()


def _has_order_id(row: dict[str, Any]) -> bool:
    order = row.get("order") or {}
    return bool(row.get("order_id") or row.get("id") or order.get("id"))


def _number(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number == number else None
    except (TypeError, ValueError):
        return None


async def overview(limit: int = 2000) -> dict[str, Any]:
    """Return reconciliation counts without mutating runtime state."""
    db = get_db()
    scans = await db.scan_results.find(
        {}, {"_id": 0, "finished_at": 1, "results": 1, "pre_filter_passed": 1}
    ).sort("finished_at", -1).to_list(min(limit, 500))
    tf_trades = await db.tf_trades.find({}, {"_id": 0}).sort("submitted_at", -1).to_list(limit)
    option_orders = await db.options_desk_orders.find({}, {"_id": 0}).sort("submitted_at", -1).to_list(limit)
    option_trades = await db.options_desk_trades.find({}, {"_id": 0}).sort("last_synced_at", -1).to_list(limit)
    signal_rows = await db.signal_performance.find({}, {"_id": 0}).sort("ts", -1).to_list(limit)
    option_perf = await db.options_performance.find({}, {"_id": 0}).sort("ts", -1).to_list(limit)

    tf_status = Counter(_status(row) for row in tf_trades)
    option_order_status = Counter(_status(row) for row in option_orders)
    option_trade_status = Counter(_status(row) for row in option_trades)
    latest_scan = scans[0] if scans else {}
    latest_rows = latest_scan.get("results") or []
    approved = [
        row for row in latest_rows
        if str(row.get("action") or row.get("pm_action") or "").upper()
        in {"ACCUMULATE", "STARTER", "BUY", "ADD"}
    ]
    filled_equity = [row for row in tf_trades if _status(row) in {"FILLED", "CLOSED", "EXITED", "FILLED_CLOSED"}]
    filled_options = [row for row in option_trades if _status(row) in {"FILLED", "OPEN", "CLOSED"}]
    equity_pct = [value for row in tf_trades if (value := _number(row.get("realized_pl_pct"))) is not None]
    option_dollars = [value for row in option_trades if (value := _number(row.get("realized_pnl"))) is not None]

    return {
        "ok": True,
        "read_only": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "latest_scan": {
            "finished_at": latest_scan.get("finished_at"),
            "rows": len(latest_rows),
            "pre_filter_passed": latest_scan.get("pre_filter_passed"),
            "embedded_pm_approved_rows": len(approved),
        },
        "signal_layer": {
            "records": len(signal_rows),
            "warning": "forward signal returns are hypothetical and are not broker fills",
        },
        "equity_execution": {
            "records": len(tf_trades),
            "status_counts": dict(tf_status),
            "order_linked_records": sum(1 for row in tf_trades if _has_order_id(row)),
            "filled_or_closed_records": len(filled_equity),
        },
        "options_execution": {
            "order_records": len(option_orders),
            "order_status_counts": dict(option_order_status),
            "trade_records": len(option_trades),
            "trade_status_counts": dict(option_trade_status),
            "filled_or_open_records": len(filled_options),
            "performance_records": len(option_perf),
            "warning": "option proxy returns are not broker fills unless linked to an options trade",
        },
        "realized_evidence": {
            "equity_records_with_realized_pct": len(equity_pct),
            "equity_sum_realized_pct": round(sum(equity_pct), 4) if equity_pct else None,
            "options_records_with_realized_dollars": len(option_dollars),
            "options_sum_realized_dollars": round(sum(option_dollars), 4) if option_dollars else None,
            "warning": "these are separate units; use broker cash activity for account-level P&L",
        },
        "consistency_flags": [
            "embedded_pm_approvals_need_cycle_join" if approved else None,
            "options_performance_without_options_trade" if option_perf and not option_trades else None,
        ],
    }
