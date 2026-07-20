"""Unified audit log read model.

This is intentionally read-only. Trade Journal can stay trader-facing; this
view is the backend truth trail across scans, PM/execution, fills, exits, and
notifications.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .db import get_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pick_time(doc: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = doc.get(key)
        if value:
            return str(value)
    return None


def _ticker_from_doc(doc: dict[str, Any]) -> str | None:
    for key in ("ticker", "symbol"):
        value = doc.get(key)
        if value:
            return str(value).upper()
    candidate = doc.get("candidate") or {}
    if candidate.get("ticker"):
        return str(candidate["ticker"]).upper()
    instrument = candidate.get("instrument") or {}
    symbol = instrument.get("symbol") or instrument.get("contractSymbol")
    if symbol:
        return str(symbol).upper()
    order = doc.get("order") or {}
    if order.get("symbol"):
        return str(order["symbol"]).upper()
    return None


def _event(
    *,
    ts: str | None,
    source: str,
    event_type: str,
    title: str,
    ticker: str | None = None,
    severity: str = "info",
    status: str | None = None,
    summary: str | None = None,
    ref_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ts": ts or _now(),
        "source": source,
        "event_type": event_type,
        "title": title,
        "ticker": ticker,
        "severity": severity,
        "status": status,
        "summary": summary,
        "ref_id": ref_id,
        "payload": payload or {},
    }


async def _activity_events(limit: int) -> list[dict[str, Any]]:
    db = get_db()
    rows = await db.activity_log.find({}, {"_id": 0}).sort("ts", -1).to_list(limit)
    out = []
    for row in rows:
        level = str(row.get("level") or "info").lower()
        out.append(_event(
            ts=row.get("ts"),
            source="system",
            event_type="activity",
            title=row.get("message") or "Activity",
            severity="warn" if level in {"warn", "warning"} else "error" if level == "error" else "info",
            status=level,
            payload=row,
        ))
    return out


async def _scan_events(limit: int) -> list[dict[str, Any]]:
    db = get_db()
    rows = await db.scan_results.find({}, {"_id": 0, "results": 0}).sort("finished_at", -1).to_list(limit)
    out = []
    for row in rows:
        count = row.get("pre_filter_passed") or row.get("results_count") or 0
        out.append(_event(
            ts=_pick_time(row, "finished_at", "created_at", "started_at"),
            source="scanner",
            event_type="scan_complete",
            title="Scanner run complete",
            severity="success",
            status=row.get("triggered_by"),
            summary=f"{count} candidates, {row.get('claude_calls_made', 0)} Claude calls",
            ref_id=str(row.get("scan_id") or row.get("finished_at") or ""),
            payload=row,
        ))
    return out


async def _trade_floor_events(limit: int) -> list[dict[str, Any]]:
    db = get_db()
    rows = await db.tf_trade_decisions.find({}, {"_id": 0}).sort("created_at", -1).to_list(limit)
    out = []
    for row in rows:
        executed = bool(row.get("executed") or row.get("order_id"))
        ticker = _ticker_from_doc(row)
        out.append(_event(
            ts=_pick_time(row, "created_at", "decided_at", "submitted_at"),
            source="trade_floor",
            event_type="equity_decision",
            title=f"${ticker or '-'} equity {'order' if executed else 'decision'}",
            ticker=ticker,
            severity="success" if executed else "info",
            status="executed" if executed else row.get("reason") or "rejected",
            summary=f"score={row.get('trade_score') or row.get('score') or '-'} limit={row.get('limit_price') or '-'} stop={row.get('stop_price') or '-'}",
            ref_id=str(row.get("order_id") or row.get("decision_id") or ""),
            payload=row,
        ))
    return out


async def _options_order_events(limit: int) -> list[dict[str, Any]]:
    db = get_db()
    rows = await db.options_desk_orders.find({}, {"_id": 0}).sort("submitted_at", -1).to_list(limit)
    out = []
    for row in rows:
        order = row.get("order") or {}
        candidate = row.get("candidate") or {}
        instrument = candidate.get("instrument") or {}
        ticker = candidate.get("ticker") or _ticker_from_doc(row)
        symbol = order.get("symbol") or instrument.get("symbol") or instrument.get("contractSymbol")
        status = row.get("status") or order.get("status")
        close_reason = row.get("close_reason")
        out.append(_event(
            ts=_pick_time(row, "submitted_at", "auto_submitted_at", "created_at"),
            source="options_desk",
            event_type="option_order",
            title=f"${ticker or '-'} option order",
            ticker=str(ticker).upper() if ticker else None,
            severity="warn" if close_reason else "success",
            status=status,
            summary=f"{symbol or '-'} qty={order.get('qty') or candidate.get('contracts') or '-'} limit={order.get('limit_price') or '-'}",
            ref_id=str(order.get("id") or ""),
            payload=row,
        ))
    return out


async def _options_trade_events(limit: int) -> list[dict[str, Any]]:
    db = get_db()
    rows = await db.options_desk_trades.find({}, {"_id": 0}).sort("last_synced_at", -1).to_list(limit)
    out = []
    for row in rows:
        status = row.get("status")
        ticker = _ticker_from_doc(row)
        event_type = "option_fill" if status in {"active", "flat_no_position"} else "option_exit"
        pnl = row.get("realized_pnl") if status == "closed" else row.get("unrealized_pnl")
        pct = row.get("realized_pct") if status == "closed" else row.get("unrealized_pct")
        out.append(_event(
            ts=_pick_time(row, "last_synced_at", "entry_filled_at", "closed_at"),
            source="options_desk",
            event_type=event_type,
            title=f"${ticker or '-'} option trade {status or ''}".strip(),
            ticker=ticker,
            severity="success" if (pct or 0) >= 0 else "warn",
            status=status,
            summary=f"{row.get('symbol') or '-'} P/L={pnl if pnl is not None else '-'} pct={pct if pct is not None else '-'}",
            ref_id=str(row.get("trade_id") or row.get("entry_order_id") or ""),
            payload=row,
        ))
    return out


async def _options_risk_events(limit: int) -> list[dict[str, Any]]:
    db = get_db()
    rows = await db.options_desk_risk_checks.find({}, {"_id": 0, "checks.snapshot": 0}).sort("checked_at", -1).to_list(limit)
    out = []
    for row in rows:
        closed = row.get("closed") or []
        errors = row.get("errors") or []
        checks = row.get("checks") or []
        severity = "error" if errors else "warn" if closed else "info"
        out.append(_event(
            ts=row.get("checked_at"),
            source="options_desk",
            event_type="risk_check",
            title="Options risk monitor",
            severity=severity,
            status="closed" if closed else "errors" if errors else "checked",
            summary=f"{len(checks)} positions checked, {len(closed)} closes, {len(errors)} errors",
            payload=row,
        ))
    return out


async def _telegram_events(limit: int) -> list[dict[str, Any]]:
    db = get_db()
    rows = await db.bot_state.find(
        {"_id": {"$regex": "options_(daily|weekly)_report:"}},
        {"_id": 0},
    ).sort("sent_at", -1).to_list(limit)
    out = []
    for row in rows:
        payload = row.get("payload") or {}
        out.append(_event(
            ts=row.get("sent_at"),
            source="telegram",
            event_type="telegram_report",
            title="Options report dispatched",
            severity="success" if row.get("sent") else "warn",
            status="sent" if row.get("sent") else "not_sent",
            summary=f"realized={payload.get('realized_gain', '-')} unrealized={payload.get('unrealized_gain', '-')}",
            payload=row,
        ))
    return out


async def _lse_health_events() -> list[dict[str, Any]]:
    try:
        from . import london_strategic_edge as lse_svc
        health = await lse_svc.health_probe()
        ok = bool(health.get("ok"))
        return [_event(
            ts=_now(),
            source="london_strategic_edge",
            event_type="provider_health",
            title="London Strategic Edge provider health",
            severity="info" if ok else "warn",
            status="live" if ok else "degraded",
            summary=health.get("reason"),
            payload=health,
        )]
    except Exception as exc:
        return [_event(
            ts=_now(),
            source="london_strategic_edge",
            event_type="provider_health",
            title="London Strategic Edge provider health",
            severity="warn",
            status="down",
            summary=str(exc)[:180],
            payload={"ok": False, "reason": str(exc)[:180]},
        )]


async def list_events(
    limit: int = 250,
    source: str | None = None,
    event_type: str | None = None,
    ticker: str | None = None,
) -> dict[str, Any]:
    limit = max(25, min(int(limit or 250), 1000))
    chunks = await _collect(limit)
    events = [event for chunk in chunks for event in chunk]
    if source and source != "all":
        events = [e for e in events if e.get("source") == source]
    if event_type and event_type != "all":
        events = [e for e in events if e.get("event_type") == event_type]
    if ticker:
        t = ticker.upper().replace("$", "")
        events = [e for e in events if str(e.get("ticker") or "").upper().replace("$", "") == t]
    events.sort(key=lambda x: str(x.get("ts") or ""), reverse=True)
    events = events[:limit]
    return {
        "ok": True,
        "generated_at": _now(),
        "count": len(events),
        "filters": {"source": source or "all", "event_type": event_type or "all", "ticker": ticker or ""},
        "source_counts": _counts(events, "source"),
        "type_counts": _counts(events, "event_type"),
        "events": events,
    }


async def _collect(limit: int) -> list[list[dict[str, Any]]]:
    return [
        await _activity_events(limit),
        await _scan_events(limit),
        await _trade_floor_events(limit),
        await _options_order_events(limit),
        await _options_trade_events(limit),
        await _options_risk_events(limit),
        await _telegram_events(limit),
        await _lse_health_events(),
    ]


def _counts(events: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for event in events:
        value = str(event.get(key) or "unknown")
        out[value] = out.get(value, 0) + 1
    return out
