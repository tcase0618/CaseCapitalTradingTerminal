"""Grouped Telegram operations feed.

This layer is intentionally separate from command handling in
telegram_service.py. Services emit structured events here; this module batches,
dedupes, stores an audit trail, and sends only operationally meaningful
messages.
"""
from __future__ import annotations

import hashlib
import html
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import get_db, log_activity, stamped


SEND_ENABLED = os.environ.get("TELEGRAM_EVENTS_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
MAX_EVENTS_PER_BATCH = int(os.environ.get("TELEGRAM_EVENTS_MAX_BATCH", "18") or 18)
INFO_COOLDOWN_MINUTES = int(os.environ.get("TELEGRAM_INFO_COOLDOWN_MINUTES", "60") or 60)
WATCH_COOLDOWN_MINUTES = int(os.environ.get("TELEGRAM_WATCH_COOLDOWN_MINUTES", "30") or 30)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=False)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt_money(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "--"


def _fmt_pct(value: Any) -> str:
    try:
        f = float(value)
        return f"{'+' if f >= 0 else ''}{f:.2f}%"
    except (TypeError, ValueError):
        return "--"


def _event_fingerprint(event: dict[str, Any]) -> str:
    base = "|".join(
        str(event.get(k) or "")
        for k in ("event_type", "scope", "ticker", "scan_id", "severity", "status", "action")
    )
    if not base.strip("|"):
        base = str(event)
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:24]


def _cooldown_for(severity: str) -> timedelta:
    sev = str(severity or "info").lower()
    if sev == "critical":
        return timedelta(seconds=0)
    if sev == "watch":
        return timedelta(minutes=WATCH_COOLDOWN_MINUTES)
    return timedelta(minutes=INFO_COOLDOWN_MINUTES)


async def emit_event(
    event_type: str,
    *,
    severity: str = "info",
    scope: str = "system",
    ticker: str | None = None,
    scan_id: str | None = None,
    batch_id: str | None = None,
    title: str = "",
    summary: str = "",
    details: dict[str, Any] | None = None,
    priority: str = "summary",
    send_now: bool = False,
) -> dict[str, Any]:
    event = stamped({
        "event_type": event_type,
        "severity": severity,
        "scope": scope,
        "ticker": ticker,
        "scan_id": scan_id,
        "batch_id": batch_id or scan_id or f"{scope}:{_now().date().isoformat()}",
        "title": title or event_type.replace("_", " ").title(),
        "summary": summary,
        "details": details or {},
        "priority": priority,
        "created_at": _now_iso(),
    })
    event["fingerprint"] = _event_fingerprint(event)
    db = get_db()
    await db.telegram_events.insert_one(event)
    event.pop("_id", None)
    if send_now or priority == "critical" or severity == "critical":
        sent = await send_event_immediate(event)
        event["sent"] = sent.get("sent")
    return event


async def _recently_sent(fingerprint: str, severity: str) -> bool:
    if not fingerprint:
        return False
    cutoff = _now() - _cooldown_for(severity)
    db = get_db()
    recent = await db.telegram_deliveries.find_one(
        {
            "fingerprints": fingerprint,
            "sent": True,
            "created_at": {"$gte": cutoff.isoformat()},
        },
        {"_id": 1},
    )
    return bool(recent)


async def _send(text: str) -> bool:
    if not SEND_ENABLED:
        return False
    from . import telegram_service

    return await telegram_service.send_message(text)


async def send_event_immediate(event: dict[str, Any]) -> dict[str, Any]:
    if await _recently_sent(event.get("fingerprint", ""), event.get("severity", "info")):
        return {"ok": True, "sent": False, "deduped": True}
    text = "\n".join([
        f"<b>CASE CAPITAL {str(event.get('severity') or 'INFO').upper()}</b>",
        "",
        f"<b>{_esc(event.get('title'))}</b>",
        _esc(event.get("summary") or ""),
        "",
        f"Scope: <b>{_esc(event.get('scope'))}</b>",
        f"Trading impact: <b>{_esc((event.get('details') or {}).get('trading_impact') or 'review')}</b>",
    ]).strip()
    sent = await _send(text)
    await _record_delivery(
        batch_type="immediate",
        title=event.get("title") or event.get("event_type"),
        text=text,
        events=[event],
        sent=sent,
    )
    return {"ok": True, "sent": sent, "deduped": False}


async def _record_delivery(batch_type: str, title: str, text: str, events: list[dict[str, Any]], sent: bool) -> None:
    db = get_db()
    await db.telegram_deliveries.insert_one(stamped({
        "batch_type": batch_type,
        "title": title,
        "event_count": len(events),
        "fingerprints": [e.get("fingerprint") for e in events if e.get("fingerprint")],
        "sent": bool(sent),
        "message_preview": text[:900],
        "created_at": _now_iso(),
    }))


def _route_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"EQUITY": 0, "OPTION": 0, "BOTH": 0, "PASS": 0, "OTHER": 0}
    for row in rows:
        route = str(row.get("route") or row.get("pm_route") or row.get("decision") or "OTHER").upper()
        counts[route if route in counts else "OTHER"] += 1
    return counts


def _top_rows(rows: list[dict[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: _num(r.get("pm_score") or r.get("signal_score") or r.get("score")), reverse=True)[:limit]


async def build_scan_report(scan: dict[str, Any]) -> dict[str, Any]:
    from . import case_court, data_quality, options_desk, portfolio_manager

    results = scan.get("results") or []
    scan_id = str(scan.get("finished_at") or scan.get("created_at") or _now_iso())
    qc = await data_quality.overview(force_refresh=False, record_event=False)
    pm = await portfolio_manager.latest_portfolio_plan()
    court = await case_court.latest()
    options = await options_desk.candidates()
    pm_rows = pm.get("decisions") or pm.get("rows") or []
    court_rows = court.get("trials") or []
    opt_summary = options.get("summary") or {}
    routes = _route_counts(pm_rows)
    blockers = (qc.get("summary") or {}).get("blockers", 0)
    qc_decision = (qc.get("trading_gate") or {}).get("decision") or "UNKNOWN"
    severity = "critical" if qc_decision == "BLOCK" else "watch" if blockers else "info"

    top = _top_rows(pm_rows or results, 6)
    top_lines = [
        f"${_esc(r.get('ticker'))} {str(r.get('action') or r.get('route') or 'SCAN').upper()} "
        f"{_num(r.get('pm_score') or r.get('signal_score')):.1f}"
        for r in top
        if r.get("ticker")
    ]
    court_counts = {
        "ready": sum(1 for r in court_rows if r.get("live_run_ready")),
        "rejected": sum(1 for r in court_rows if str(r.get("posture") or "").upper() in {"PM_REJECTED", "REJECTED"}),
        "needs_data": sum(1 for r in court_rows if str(r.get("posture") or "").upper() == "REQUIRES_CLEANER_DATA"),
    }
    text = "\n".join([
        "<b>CASE CAPITAL SCAN REPORT</b>",
        "",
        "<b>SCAN</b>",
        f"Trigger: <b>{_esc(scan.get('triggered_by') or 'unknown')}</b>",
        f"Duration: <b>{scan.get('duration_sec', '--')}s</b>",
        f"Universe: <b>{scan.get('universe_size', '--')}</b>",
        f"Passed scanner: <b>{len(results)}</b>",
        "",
        "<b>PM ROUTING</b>",
        f"Equity: <b>{routes['EQUITY']}</b> | Options: <b>{routes['OPTION']}</b> | Both: <b>{routes['BOTH']}</b> | Pass: <b>{routes['PASS']}</b>",
        f"Options ready: <b>{opt_summary.get('ready', 0)}</b> / {opt_summary.get('total', 0)}",
        "",
        "<b>CASE COURT</b>",
        f"Live-ready: <b>{court_counts['ready']}</b> | Needs data: <b>{court_counts['needs_data']}</b> | Rejected: <b>{court_counts['rejected']}</b>",
        "",
        "<b>QC</b>",
        f"Decision: <b>{_esc(qc_decision)}</b> | Score: <b>{qc.get('score', '--')}</b> | Blockers: <b>{blockers}</b>",
        "",
        "<b>TOP DOCKET</b>",
        "\n".join(top_lines) or "No PM rows yet.",
    ])
    return {
        "batch_type": "scan_report",
        "scan_id": scan_id,
        "severity": severity,
        "text": text,
        "summary": {
            "results": len(results),
            "routes": routes,
            "qc_decision": qc_decision,
            "qc_blockers": blockers,
            "options": opt_summary,
            "court": court_counts,
        },
    }


async def dispatch_scan_report(scan: dict[str, Any]) -> dict[str, Any]:
    report = await build_scan_report(scan)
    event = await emit_event(
        "scan_complete",
        severity=report["severity"],
        scope="scanner",
        scan_id=report["scan_id"],
        batch_id=f"scan:{report['scan_id']}",
        title="Scan complete",
        summary=f"{report['summary']['results']} rows; QC {report['summary']['qc_decision']}",
        details=report["summary"],
        priority="summary",
    )
    if await _recently_sent(event.get("fingerprint", ""), event.get("severity", "info")):
        return {"ok": True, "sent": False, "deduped": True, **report}
    sent = await _send(report["text"])
    await _record_delivery("scan_report", "Case Capital Scan Report", report["text"], [event], sent)
    return {"ok": True, "sent": sent, "deduped": False, **report}


async def dispatch_options_execution_report(result: dict[str, Any]) -> dict[str, Any]:
    submitted = result.get("submitted") or []
    skipped = result.get("skipped") or []
    if not submitted and not skipped:
        return {"ok": True, "sent": False, "reason": "no_execution_changes"}
    severity = "watch" if skipped and not submitted else "info"
    lines = [
        "<b>CASE CAPITAL OPTIONS EXECUTION</b>",
        "",
        f"Ready: <b>{result.get('ready', 0)}</b>",
        f"Submitted: <b>{len(submitted)}</b>",
        f"Blocked/skipped: <b>{len(skipped)}</b>",
    ]
    if submitted:
        lines.extend(["", "<b>SUBMITTED</b>"])
        for item in submitted[:10]:
            lines.append(
                f"${_esc(item.get('ticker'))} {_esc(item.get('symbol'))} "
                f"x{item.get('contracts')} risk {_fmt_money(item.get('risk_budget'))}"
            )
    if skipped:
        lines.extend(["", "<b>BLOCKED / SKIPPED</b>"])
        for item in skipped[:10]:
            lines.append(f"${_esc(item.get('ticker'))} {_esc(item.get('reason'))}")
    event = await emit_event(
        "options_execution_batch",
        severity=severity,
        scope="options",
        title="Options execution batch",
        summary=f"{len(submitted)} submitted, {len(skipped)} blocked/skipped",
        details={"submitted": submitted[:20], "skipped": skipped[:20], "summary": result.get("summary") or {}},
        priority="summary",
    )
    if await _recently_sent(event.get("fingerprint", ""), severity):
        return {"ok": True, "sent": False, "deduped": True}
    text = "\n".join(lines)
    sent = await _send(text)
    await _record_delivery("options_execution", "Options Execution Batch", text, [event], sent)
    return {"ok": True, "sent": sent, "submitted": len(submitted), "skipped": len(skipped)}


async def dispatch_qc_report(force_refresh: bool = False, send_if_clean: bool = False) -> dict[str, Any]:
    from . import data_quality

    qc = await data_quality.overview(force_refresh=force_refresh, record_event=False)
    gate = qc.get("trading_gate") or {}
    summary = qc.get("summary") or {}
    blockers = [r for r in qc.get("checks", []) if r.get("blocks_trading")]
    warnings = [r for r in qc.get("checks", []) if r.get("warnings") or r.get("status") in {"WARN", "FALLBACK", "STALE"}]
    decision = gate.get("decision") or "UNKNOWN"
    if decision not in {"BLOCK", "WATCH"} and not send_if_clean:
        return {"ok": True, "sent": False, "reason": "qc_clean"}
    severity = "critical" if decision == "BLOCK" else "watch"
    lines = [
        "<b>CASE CAPITAL QUALITY CONTROL</b>",
        "",
        f"Decision: <b>{_esc(decision)}</b>",
        f"Score: <b>{qc.get('score', '--')}</b> | Critical: <b>{qc.get('critical_score', '--')}</b>",
        f"Live: <b>{summary.get('live', 0)}</b> | Warnings: <b>{summary.get('warnings', 0)}</b> | Fallbacks: <b>{summary.get('fallbacks', 0)}</b> | Down: <b>{summary.get('down', 0)}</b>",
    ]
    if blockers:
        lines.extend(["", "<b>BLOCKERS</b>"])
        for row in blockers[:8]:
            lines.append(f"{_esc(row.get('label'))}: {_esc(row.get('status'))} - {_esc(row.get('detail'))}")
    elif warnings:
        lines.extend(["", "<b>WATCH ITEMS</b>"])
        for row in warnings[:8]:
            warn = "; ".join(str(x) for x in (row.get("warnings") or [])) or row.get("detail") or row.get("status")
            lines.append(f"{_esc(row.get('label'))}: {_esc(warn)}")
    event = await emit_event(
        "quality_report",
        severity=severity,
        scope="qc",
        title=f"QC {decision}",
        summary=f"{summary.get('blockers', 0)} blockers, {summary.get('warnings', 0)} warnings",
        details={"gate": gate, "summary": summary, "blockers": blockers[:10], "warnings": warnings[:10]},
        priority="summary",
    )
    if await _recently_sent(event.get("fingerprint", ""), severity):
        return {"ok": True, "sent": False, "deduped": True}
    text = "\n".join(lines)
    sent = await _send(text)
    await _record_delivery("qc_report", "Quality Control", text, [event], sent)
    return {"ok": True, "sent": sent, "decision": decision}


async def dispatch_daily_report() -> dict[str, Any]:
    from . import data_quality, pnl_tracker, scheduler

    qc = await data_quality.overview(force_refresh=False, record_event=False)
    tracker_rows = await pnl_tracker.signals_tracker_summary(limit=300)
    tracker = {"rows": tracker_rows, "tracked": len(tracker_rows)}
    snapshot = await scheduler.persist_live_position_snapshot(triggered_by="telegram_daily_report")
    rows = tracker.get("rows") or []
    best = max(rows, key=lambda r: _num(r.get("gain_pct")), default={})
    worst = min(rows, key=lambda r: _num(r.get("gain_pct")), default={})
    text = "\n".join([
        "<b>CASE CAPITAL DAILY OPS REPORT</b>",
        "",
        "<b>FUNDS</b>",
        f"Positions: <b>{snapshot.get('totals', {}).get('positions', 0)}</b>",
        f"Open orders: <b>{snapshot.get('totals', {}).get('open_orders', 0)}</b>",
        f"Unrealized: <b>{_fmt_money(snapshot.get('totals', {}).get('unrealized_pl'))}</b>",
        "",
        "<b>TRACKER</b>",
        f"Tracked: <b>{tracker.get('tracked', 0)}</b>",
        f"Biggest gain: <b>${_esc(best.get('ticker') or '--')} {_fmt_pct(best.get('gain_pct'))}</b>",
        f"Biggest loser: <b>${_esc(worst.get('ticker') or '--')} {_fmt_pct(worst.get('gain_pct'))}</b>",
        "",
        "<b>QC</b>",
        f"Decision: <b>{_esc((qc.get('trading_gate') or {}).get('decision') or 'UNKNOWN')}</b> | Score: <b>{qc.get('score', '--')}</b>",
    ])
    event = await emit_event(
        "daily_ops_report",
        severity="info",
        scope="system",
        title="Daily ops report",
        summary=f"{snapshot.get('totals', {}).get('positions', 0)} positions; QC {(qc.get('trading_gate') or {}).get('decision')}",
        details={"snapshot": snapshot, "qc_summary": qc.get("summary") or {}, "tracker": {"tracked": tracker.get("tracked")}},
        priority="summary",
    )
    sent = await _send(text)
    await _record_delivery("daily_report", "Daily Ops Report", text, [event], sent)
    return {"ok": True, "sent": sent}


async def dispatch_weekly_report() -> dict[str, Any]:
    from . import case_court, data_quality, pnl_tracker

    qc = await data_quality.overview(force_refresh=False, record_event=False)
    tracker_rows = await pnl_tracker.signals_tracker_summary(limit=500)
    tracker = {"rows": tracker_rows, "tracked": len(tracker_rows)}
    court = await case_court.latest()
    rows = tracker.get("rows") or []
    wins = sum(1 for r in rows if _num(r.get("gain_pct")) > 0)
    losses = sum(1 for r in rows if _num(r.get("gain_pct")) < 0)
    avg = sum(_num(r.get("gain_pct")) for r in rows) / max(1, len(rows))
    text = "\n".join([
        "<b>CASE CAPITAL WEEKLY OPS REPORT</b>",
        "",
        f"Tracked rows: <b>{len(rows)}</b>",
        f"Win/loss: <b>{wins}W / {losses}L</b>",
        f"Average since alert: <b>{_fmt_pct(avg)}</b>",
        "",
        "<b>CASE COURT</b>",
        f"Trials: <b>{len(court.get('trials') or [])}</b>",
        f"Live-ready: <b>{sum(1 for r in court.get('trials', []) if r.get('live_run_ready'))}</b>",
        "",
        "<b>QC</b>",
        f"Decision: <b>{_esc((qc.get('trading_gate') or {}).get('decision') or 'UNKNOWN')}</b>",
        f"Blockers: <b>{(qc.get('summary') or {}).get('blockers', 0)}</b>",
    ])
    event = await emit_event(
        "weekly_ops_report",
        severity="info",
        scope="system",
        title="Weekly ops report",
        summary=f"{wins}W/{losses}L; QC {(qc.get('trading_gate') or {}).get('decision')}",
        details={"qc_summary": qc.get("summary") or {}, "tracked": len(rows), "wins": wins, "losses": losses},
        priority="summary",
    )
    sent = await _send(text)
    await _record_delivery("weekly_report", "Weekly Ops Report", text, [event], sent)
    return {"ok": True, "sent": sent}


async def preview_latest(batch_type: str = "scan_report") -> dict[str, Any]:
    db = get_db()
    delivery = await db.telegram_deliveries.find_one(
        {"batch_type": batch_type},
        {"_id": 0},
        sort=[("created_at", -1)],
    )
    return {"ok": True, "batch_type": batch_type, "delivery": delivery}


async def recent_events(limit: int = 80) -> dict[str, Any]:
    db = get_db()
    limit = max(1, min(int(limit or 80), 300))
    events = await db.telegram_events.find({}, {"_id": 0}).sort("created_at", -1).to_list(limit)
    deliveries = await db.telegram_deliveries.find({}, {"_id": 0}).sort("created_at", -1).to_list(30)
    return {"ok": True, "events": events, "deliveries": deliveries}
