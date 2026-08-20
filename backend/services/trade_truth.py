"""Broker-fill truth helpers for Trade Floor outcomes.

Alpaca filled orders are the source of truth for realized P/L. Display marks
and latest-price fallbacks are useful for open risk, but they must not train
the learning engine as realized outcomes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def parse_trade_floor_sell_reason(client_order_id: str | None, ticker: str) -> str | None:
    """Extract the reason from `tf-sell-TICKER-reason-timestamp` ids."""
    raw = str(client_order_id or "")
    prefix = f"tf-sell-{ticker.upper()}-"
    if not raw.startswith(prefix):
        return None
    rest = raw[len(prefix):]
    if "-" not in rest:
        return rest or None
    reason, maybe_ts = rest.rsplit("-", 1)
    return reason or maybe_ts or None


def resolve_equity_close_from_alpaca_sells(
    trade: dict[str, Any],
    orders: list[dict[str, Any]],
    *,
    fallback_price: float | None = None,
    fallback_reason: str = "broker_position_missing_unverified",
) -> dict[str, Any]:
    """Resolve a closed equity trade from Alpaca sell fills.

    Returns a payload that can be persisted directly to `tf_trades`. If no
    Alpaca sell fills can be matched, the result is explicitly unverified and
    marked learning-excluded.
    """
    ticker = str(trade.get("ticker") or trade.get("symbol") or "").upper()
    entry = _num(trade.get("filled_avg_price") or trade.get("entry_price_ref") or trade.get("limit_price"))
    entry_time = _dt(trade.get("filled_at") or trade.get("submitted_at"))
    if entry_time and entry_time.tzinfo is None:
        entry_time = entry_time.replace(tzinfo=timezone.utc)

    matched: list[dict[str, Any]] = []
    for order in orders or []:
        if str(order.get("symbol") or "").upper() != ticker:
            continue
        if str(order.get("side") or "").lower() != "sell":
            continue
        if not order.get("filled_at") or not order.get("filled_avg_price"):
            continue
        filled_at = _dt(order.get("filled_at"))
        if entry_time and filled_at and filled_at < entry_time:
            continue
        cid = str(order.get("client_order_id") or "")
        if cid and not cid.startswith(f"tf-sell-{ticker}-"):
            continue
        qty = _num(order.get("filled_qty") or order.get("qty"))
        price = _num(order.get("filled_avg_price"))
        if qty <= 0 or price <= 0:
            continue
        matched.append(order)

    if matched and entry > 0:
        matched.sort(key=lambda o: str(o.get("filled_at") or ""))
        total_qty = sum(_num(o.get("filled_qty") or o.get("qty")) for o in matched)
        gross = sum(
            (_num(o.get("filled_avg_price")) - entry) * _num(o.get("filled_qty") or o.get("qty"))
            for o in matched
        )
        exit_price = (
            sum(_num(o.get("filled_avg_price")) * _num(o.get("filled_qty") or o.get("qty")) for o in matched)
            / total_qty
        ) if total_qty > 0 else _num(matched[-1].get("filled_avg_price"))
        reasons = [
            parse_trade_floor_sell_reason(o.get("client_order_id"), ticker)
            for o in matched
        ]
        reasons = [r for r in reasons if r]
        close_reason = reasons[-1] if len(set(reasons)) == 1 else "+".join(dict.fromkeys(reasons))
        return {
            "exit_price": round(exit_price, 6),
            "realized_pct": round((gross / (entry * total_qty)) * 100.0, 4) if total_qty > 0 else None,
            "close_reason": close_reason or "alpaca_sell_fill",
            "closed_at": matched[-1].get("filled_at"),
            "fill_truth_status": "verified_alpaca_sell_fill",
            "fill_truth_source": "alpaca_orders",
            "learning_excluded": False,
            "exit_order_ids": [o.get("id") for o in matched if o.get("id")],
            "exit_client_order_ids": [o.get("client_order_id") for o in matched if o.get("client_order_id")],
            "exit_fills": [
                {
                    "order_id": o.get("id"),
                    "client_order_id": o.get("client_order_id"),
                    "filled_at": o.get("filled_at"),
                    "qty": _num(o.get("filled_qty") or o.get("qty")),
                    "price": _num(o.get("filled_avg_price")),
                }
                for o in matched
            ],
        }

    exit_price = _num(fallback_price)
    realized = ((exit_price - entry) / entry * 100.0) if (entry > 0 and exit_price > 0) else None
    return {
        "exit_price": exit_price or None,
        "realized_pct": round(realized, 4) if realized is not None else None,
        "close_reason": trade.get("close_reason") or fallback_reason,
        "closed_at": datetime.now(timezone.utc).isoformat(),
        "fill_truth_status": "unverified_no_alpaca_sell_fill",
        "fill_truth_source": "fallback_mark",
        "learning_excluded": True,
        "learning_excluded_reason": "missing_verified_alpaca_sell_fill",
    }
