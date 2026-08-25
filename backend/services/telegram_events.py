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
from zoneinfo import ZoneInfo

from .db import get_db, log_activity, stamped


SEND_ENABLED = os.environ.get("TELEGRAM_EVENTS_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
MAX_EVENTS_PER_BATCH = int(os.environ.get("TELEGRAM_EVENTS_MAX_BATCH", "18") or 18)
INFO_COOLDOWN_MINUTES = int(os.environ.get("TELEGRAM_INFO_COOLDOWN_MINUTES", "60") or 60)
WATCH_COOLDOWN_MINUTES = int(os.environ.get("TELEGRAM_WATCH_COOLDOWN_MINUTES", "30") or 30)
SCAN_REPORT_THROTTLE_MINUTES = int(os.environ.get("TELEGRAM_SCAN_REPORT_THROTTLE_MINUTES", "10") or 10)
PHARMA_ALERT_COOLDOWN_MINUTES = int(os.environ.get("TELEGRAM_PHARMA_ALERT_COOLDOWN_MINUTES", "360") or 360)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _now_et() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%b %d %H:%M ET")


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


def _gain_line(label: str, row: dict[str, Any] | None) -> str:
    if not row:
        return f"{label}: <b>-</b>"
    return (
        f"{label}: <b>${_esc(row.get('ticker'))}</b> "
        f"{_fmt_pct(row.get('pct'))} / {_fmt_money(row.get('dollars'))}"
    )


def _same_scan(left: Any, right: Any) -> bool:
    if not left or not right:
        return False
    return str(left).replace("Z", "+00:00") == str(right).replace("Z", "+00:00")


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


def _scan_report_throttle_enabled(triggered_by: Any) -> bool:
    trigger = str(triggered_by or "").strip().lower()
    if not trigger:
        return True
    if trigger in {"scheduler", "main_scan", "quality_auto_remediation", "schedule_watchdog"}:
        return True
    if trigger.startswith("scheduler") or trigger.endswith("_scan"):
        return True
    return False


async def _scan_report_dedupe_reason(scan_id: str, triggered_by: Any) -> str | None:
    db = get_db()
    if scan_id:
        exact = await db.telegram_deliveries.find_one(
            {
                "batch_type": "scan_report",
                "sent": True,
                "metadata.scan_id": scan_id,
            },
            {"_id": 1},
        )
        if exact:
            return "same_scan_already_sent"

    if not _scan_report_throttle_enabled(triggered_by):
        return None

    cutoff = _now() - timedelta(minutes=max(1, SCAN_REPORT_THROTTLE_MINUTES))
    recent = await db.telegram_deliveries.find_one(
        {
            "batch_type": "scan_report",
            "sent": True,
            "created_at": {"$gte": cutoff.isoformat()},
        },
        {"_id": 1, "metadata": 1, "created_at": 1},
        sort=[("created_at", -1)],
    )
    if recent:
        return f"scheduled_scan_report_window_{SCAN_REPORT_THROTTLE_MINUTES}m"
    return None


async def _send(text: str) -> bool:
    if not SEND_ENABLED:
        return False
    from . import telegram_service

    return await telegram_service.send_message(text)


async def send_event_immediate(event: dict[str, Any]) -> dict[str, Any]:
    if await _recently_sent(event.get("fingerprint", ""), event.get("severity", "info")):
        return {"ok": True, "sent": False, "deduped": True}
    text = "\n".join([
        f"<b>CASE CAPITAL | {str(event.get('severity') or 'INFO').upper()}</b>",
        f"<code>{_now_et()}</code>",
        "",
        f"Event: <b>{_esc(event.get('title'))}</b>",
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


async def _record_delivery(
    batch_type: str,
    title: str,
    text: str,
    events: list[dict[str, Any]],
    sent: bool,
    metadata: dict[str, Any] | None = None,
) -> None:
    db = get_db()
    await db.telegram_deliveries.insert_one(stamped({
        "batch_type": batch_type,
        "title": title,
        "event_count": len(events),
        "fingerprints": [e.get("fingerprint") for e in events if e.get("fingerprint")],
        "sent": bool(sent),
        "metadata": metadata or {},
        "message_preview": text[:900],
        "created_at": _now_iso(),
    }))


def _route_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"EQUITY": 0, "OPTION": 0, "BOTH": 0, "PASS": 0, "OTHER": 0}
    for row in rows:
        route = str(row.get("route") or row.get("pm_route") or row.get("decision") or "OTHER").upper()
        counts[route if route in counts else "OTHER"] += 1
    return counts


def _pm_rows(pm: dict[str, Any]) -> list[dict[str, Any]]:
    return pm.get("recommendations") or pm.get("decisions") or pm.get("rows") or []


def _pm_action_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"ACCUMULATE": 0, "STARTER": 0, "WATCH": 0, "REJECT": 0, "OTHER": 0}
    for row in rows:
        action = str(row.get("action") or row.get("decision") or "OTHER").upper()
        counts[action if action in counts else "OTHER"] += 1
    return counts


def _expression_counts(pm_rows: list[dict[str, Any]], opt_rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"EQUITY": 0, "OPTION": 0, "BOTH": 0, "REJECT": 0, "PASS": 0, "WATCH": 0, "OTHER": 0}
    opt_by_ticker = {
        str(row.get("ticker") or "").upper(): str(row.get("route") or "").upper()
        for row in opt_rows
        if row.get("ticker")
    }
    for row in pm_rows:
        ticker = str(row.get("ticker") or "").upper()
        opt_route = opt_by_ticker.get(ticker)
        action = str(row.get("action") or row.get("decision") or "").upper()
        if opt_route in {"OPTION", "BOTH"}:
            counts[opt_route] += 1
        elif action in {"ACCUMULATE", "STARTER"}:
            counts["EQUITY"] += 1
        elif action == "WATCH":
            counts["WATCH"] += 1
        elif action in {"REJECT", "PASS"}:
            counts["REJECT"] += 1
            counts["PASS"] += 1
        else:
            counts["OTHER"] += 1
    return counts


def _top_rows(rows: list[dict[str, Any]], limit: int = 6) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: _num(r.get("pm_score") or r.get("signal_score") or r.get("score")), reverse=True)[:limit]


def _option_blocker_counts(rows: list[dict[str, Any]], *, executable_only: bool = False) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows or []:
        if row.get("manual_fire_ready"):
            continue
        route = str(row.get("route") or "").upper()
        if executable_only and route not in {"OPTION", "BOTH"}:
            continue
        for reason in row.get("blocked_reasons") or []:
            key = str(reason or "").strip()
            if key:
                counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True))


def _route_for_pm_row(row: dict[str, Any], opt_by_ticker: dict[str, dict[str, Any]]) -> str:
    ticker = str(row.get("ticker") or "").upper()
    opt_route = str((opt_by_ticker.get(ticker) or {}).get("route") or "").upper()
    action = str(row.get("action") or row.get("decision") or "").upper()
    if opt_route in {"OPTION", "BOTH"}:
        return opt_route
    if action in {"ACCUMULATE", "STARTER"}:
        return "EQUITY"
    if action == "WATCH":
        return "WATCH"
    if action in {"REJECT", "PASS"}:
        return "REJECT"
    return "SCAN"


def _court_posture_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows or []:
        posture = str((row.get("judge") or {}).get("advisory_posture") or "UNKNOWN").upper()
        counts[posture] = counts.get(posture, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True))


async def _new_scan_tickers(scan: dict[str, Any], results: list[dict[str, Any]], limit: int = 12) -> dict[str, Any]:
    """Compare the current core scan against the previous saved scan.

    This is intentionally based on scanner results only. Specialist-family
    counts live in the candidate ledger, while this line answers the operator's
    direct question: which symbols newly appeared in the current stock scan.
    """
    tickers = sorted({str(r.get("ticker") or "").upper() for r in results if r.get("ticker")})
    finished_at = scan.get("finished_at")
    query: dict[str, Any] = {}
    if finished_at:
        query = {"finished_at": {"$lt": finished_at}}
    previous = await get_db().scan_results.find_one(
        query,
        {"_id": 0, "finished_at": 1, "results.ticker": 1},
        sort=[("finished_at", -1)],
    )
    previous_tickers = {
        str(r.get("ticker") or "").upper()
        for r in ((previous or {}).get("results") or [])
        if r.get("ticker")
    }
    new_tickers = [t for t in tickers if t not in previous_tickers]
    return {
        "count": len(new_tickers),
        "tickers": new_tickers,
        "display": new_tickers[:limit],
        "truncated": max(0, len(new_tickers) - limit),
        "previous_scan_at": (previous or {}).get("finished_at"),
    }


async def build_scan_report(scan: dict[str, Any]) -> dict[str, Any]:
    from . import data_quality, edge_dashboard, execution_gate, options_desk, portfolio_manager, strategy_screeners

    results = scan.get("results") or []
    scan_id = str(scan.get("finished_at") or scan.get("created_at") or _now_iso())
    new_scan = await _new_scan_tickers(scan, results)
    pm = await portfolio_manager.latest_portfolio_plan()
    alignment_notes: list[str] = []
    if not _same_scan(pm.get("scan_finished_at"), scan.get("finished_at")):
        alignment_notes.append("pm_scan_mismatch")
    screeners = await strategy_screeners.run_all(scan=scan, persist=True)
    screener_summary = screeners.get("summary") or {}
    if not _same_scan(screeners.get("scan_finished_at"), scan.get("finished_at")):
        alignment_notes.append("strategy_screeners_scan_mismatch")
    qc = await data_quality.overview(force_refresh=False, record_event=False)
    gate = await execution_gate.overview(force_refresh=False)
    edge = await edge_dashboard.overview()
    options = await options_desk.candidates()
    pm_rows = _pm_rows(pm)
    opt_summary = options.get("summary") or {}
    opt_rows = options.get("candidates") or []
    opt_by_ticker = {
        str(row.get("ticker") or "").upper(): row
        for row in opt_rows
        if row.get("ticker")
    }
    lane_counts: dict[str, int] = {}
    for row in opt_rows:
        lane = ((row.get("strategy_lane") or {}).get("lane") or "UNKNOWN")
        lane_counts[lane] = lane_counts.get(lane, 0) + 1
    lane_lines = [
        f"{_esc(k)}: <b>{v}</b>"
        for k, v in sorted(lane_counts.items(), key=lambda item: item[1], reverse=True)[:5]
    ]
    option_blockers = _option_blocker_counts(opt_rows, executable_only=True)
    non_option_routed = sum(1 for row in opt_rows if str(row.get("route") or "").upper() not in {"OPTION", "BOTH"})
    option_blocker_lines = [
        f"{_esc(k)}: <b>{v}</b>"
        for k, v in list(option_blockers.items())[:6]
    ]
    routes = _expression_counts(pm_rows, opt_rows)
    pm_actions = _pm_action_counts(pm_rows)
    opp = pm.get("opportunity_cost") or {}
    pm_action_total = sum(pm_actions.values())
    blockers = (qc.get("summary") or {}).get("blockers", 0)
    qc_decision = (qc.get("trading_gate") or {}).get("decision") or "UNKNOWN"
    scan_freshness = scan.get("freshness") or {}
    stale_price_rows = int(scan_freshness.get("stale_price_rows") or 0)
    fresh_price_rows = int(scan_freshness.get("fresh_price_rows") or 0)
    price_rows = int(scan_freshness.get("price_rows") or 0)
    severity = "critical" if qc_decision == "BLOCK" or alignment_notes else "watch" if blockers or stale_price_rows else "info"

    top = _top_rows(pm_rows or results, 6)
    top_lines = [
        f"${_esc(r.get('ticker'))} {_esc(_route_for_pm_row(r, opt_by_ticker))} {str(r.get('action') or r.get('route') or 'SCAN').upper()} "
        f"{_num(r.get('pm_score') or r.get('signal_score')):.1f}"
        for r in top
        if r.get("ticker")
    ]
    family_counts = {
        k: v
        for k, v in ((screener_summary.get("by_pm_family") or {}).items())
        if k not in {"EARNINGS", "SEC"}
    }
    screener_counts = screener_summary.get("by_screener") or {}
    screener_lines = [
        f"{_esc(k)}: <b>{v}</b>"
        for k, v in sorted(family_counts.items(), key=lambda item: item[0])[:8]
    ]
    text = "\n".join([
        "<b>CASE CAPITAL | SCAN REPORT</b>",
        f"<code>{_now_et()}</code>",
        "",
        "<b>SCAN</b>",
        f"Trigger: <b>{_esc(scan.get('triggered_by') or 'unknown')}</b>",
        f"Duration: <b>{scan.get('duration_sec', '--')}s</b>",
        f"Universe: <b>{scan.get('universe_size', '--')}</b>",
        f"Passed scanner: <b>{len(results)}</b>",
        (
            "New stocks found this scan: "
            f"<b>{new_scan['count']}</b>"
            + (
                f" | {', '.join('$' + _esc(t) for t in new_scan['display'])}"
                if new_scan["display"]
                else ""
            )
            + (f" +{new_scan['truncated']} more" if new_scan["truncated"] else "")
        ),
        "",
        "<b>PM ROUTING</b>",
        f"Routed: Equity <b>{routes['EQUITY']}</b> | Options <b>{routes['OPTION']}</b> | Both <b>{routes['BOTH']}</b> | Watch <b>{routes['WATCH']}</b> | Rejected <b>{routes['REJECT']}</b>",
        f"PM actions: <b>{pm_actions['ACCUMULATE']}</b> accumulate | <b>{pm_actions['STARTER']}</b> starter | <b>{pm_actions['WATCH']}</b> watch | <b>{pm_actions['REJECT']}</b> reject | Total <b>{pm_action_total}</b>",
        f"Options ready: <b>{opt_summary.get('ready', 0)}</b> / {opt_summary.get('total', 0)} | Routed option names: <b>{routes['OPTION'] + routes['BOTH']}</b>",
        f"Turnover engine: <b>{opp.get('positions_reviewed', 0)}</b> holdings reviewed | Replace: <b>{len(opp.get('replacement_candidates') or [])}</b> | Trim/exit: <b>{len(opp.get('trim_reviews') or [])}</b>",
        *([f"Options execution blockers: {' | '.join(option_blocker_lines)}"] if option_blocker_lines else []),
        *([f"Options not routed by PM: <b>{non_option_routed}</b> equity/pass/watch lane(s)"] if non_option_routed else []),
        "",
        "<b>SCANNER FAMILIES</b>",
        f"PM-routable strategy candidates: <b>{screener_summary.get('pm_routable', 0)}</b>",
        *([f"Families: {' | '.join(screener_lines)}"] if screener_lines else []),
        "Earnings + SEC: <b>RESEARCH-ONLY</b> | PM: <b>OFF</b> | Telegram: <b>OFF</b>",
        "Case Court: <b>OFF ACTIVE ROUTING</b>",
        "",
        "<b>QC</b>",
        f"Decision: <b>{_esc(qc_decision)}</b> | Score: <b>{qc.get('score', '--')}</b> | Blockers: <b>{blockers}</b>",
        f"Execution gate: <b>{_esc(gate.get('decision') or 'UNKNOWN')}</b> | Truth: <b>{_esc(gate.get('truth_grade') or '--')}</b>",
        f"Ticker rejects: <b>{(scan.get('ticker_hygiene') or {}).get('rejected_count', 0)}</b>",
        *([f"Price freshness: <b>{fresh_price_rows}</b> / {price_rows} live | Stale rows: <b>{stale_price_rows}</b>"] if price_rows else []),
        *([f"Freshness: <b>CHECK</b> | {'; '.join(_esc(n) for n in alignment_notes)}"] if alignment_notes else []),
        *([f"Scan fingerprint: <b>{_esc(scan_freshness.get('status'))}</b>"] if scan.get("freshness") else []),
        "",
        "<b>EDGE PROOF</b>",
        f"Sample: <b>{(edge.get('edge') or {}).get('sample', 0)}</b> | Win rate: <b>{_fmt_pct((edge.get('edge') or {}).get('win_rate'))}</b> | Expectancy: <b>{_fmt_pct((edge.get('edge') or {}).get('expectancy_pct'))}</b>",
        f"Alpha: <b>{_esc((edge.get('edge') or {}).get('alpha_grade') or 'UNPROVEN')}</b>",
        "",
        "<b>OPTIONS PLAYBOOK LANES</b>",
        "\n".join(lane_lines) or "No option lanes built.",
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
            "pm_actions": pm_actions,
            "qc_decision": qc_decision,
            "qc_blockers": blockers,
            "execution_gate": {"decision": gate.get("decision"), "truth_grade": gate.get("truth_grade"), "blockers": gate.get("blockers")},
            "freshness": {
                "scan": scan.get("freshness") or {},
                "alignment_notes": alignment_notes,
                "scan_finished_at": scan.get("finished_at"),
                "pm_scan_finished_at": pm.get("scan_finished_at"),
                "strategy_screeners_scan_finished_at": screeners.get("scan_finished_at"),
            },
            "new_scan_tickers": new_scan,
            "edge": edge.get("edge") or {},
            "strategy_lanes": lane_counts,
            "option_blockers": option_blockers,
            "options_not_routed_by_pm": non_option_routed,
            "options": opt_summary,
            "opportunity_cost": {
                "positions_reviewed": opp.get("positions_reviewed", 0),
                "replacement_candidates": opp.get("replacement_candidates") or [],
                "trim_reviews": opp.get("trim_reviews") or [],
            },
            "strategy_screeners": {
                **screener_summary,
                "by_screener": screener_counts,
            },
            "case_court": {"active_routing": False},
        },
    }


async def dispatch_scan_report(scan: dict[str, Any]) -> dict[str, Any]:
    report = await build_scan_report(scan)
    dedupe_reason = await _scan_report_dedupe_reason(report["scan_id"], scan.get("triggered_by"))
    if dedupe_reason:
        await log_activity(f"Telegram scan report deduped: {dedupe_reason}", "info", {
            "scan_id": report["scan_id"],
            "triggered_by": scan.get("triggered_by"),
        })
        return {"ok": True, "sent": False, "deduped": True, "dedupe_reason": dedupe_reason, **report}
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
    await _record_delivery(
        "scan_report",
        "Case Capital Scan Report",
        report["text"],
        [event],
        sent,
        metadata={
            "scan_id": report["scan_id"],
            "triggered_by": scan.get("triggered_by"),
            "throttle_minutes": SCAN_REPORT_THROTTLE_MINUTES,
        },
    )
    return {"ok": True, "sent": sent, "deduped": False, **report}


async def dispatch_pharma_alerts(rows: list[dict[str, Any]], *, triggered_by: str = "unknown") -> dict[str, Any]:
    hot = [r for r in rows if _num(r.get("binary_event_score")) >= 70]
    if not hot:
        return {"ok": True, "sent": False, "count": 0, "reason": "no_hot_pharma"}

    db = get_db()
    sent_rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    cutoff = _now() - timedelta(minutes=max(1, PHARMA_ALERT_COOLDOWN_MINUTES))

    from . import pharma

    for row in hot[:5]:
        ticker = str(row.get("ticker") or "").upper()
        pdufa_date = str(row.get("pdufa_date") or "")
        dedupe_key = f"pharma:{ticker}:{pdufa_date}"
        recent = await db.telegram_deliveries.find_one(
            {
                "batch_type": "pharma_alert",
                "sent": True,
                "metadata.dedupe_key": dedupe_key,
                "created_at": {"$gte": cutoff.isoformat()},
            },
            {"_id": 1},
        )
        if recent:
            skipped.append({"ticker": ticker, "reason": "cooldown", "dedupe_key": dedupe_key})
            continue
        event = await emit_event(
            "pharma_binary_alert",
            severity="watch",
            scope="pharma",
            ticker=ticker,
            batch_id=dedupe_key,
            title="Pharma binary setup",
            summary=f"{ticker} score {_num(row.get('binary_event_score')):.0f}; PDUFA {pdufa_date or 'n/a'}",
            details={
                "ticker": ticker,
                "score": row.get("binary_event_score"),
                "pdufa_date": pdufa_date,
                "drug": row.get("drug"),
                "triggered_by": triggered_by,
                "trading_impact": "watch_only",
            },
            priority="summary",
        )
        text = pharma.format_pharma_alert(row)
        sent = await _send(text)
        await _record_delivery(
            "pharma_alert",
            "Case Capital Pharma Alert",
            text,
            [event],
            sent,
            metadata={
                "dedupe_key": dedupe_key,
                "ticker": ticker,
                "pdufa_date": pdufa_date,
                "triggered_by": triggered_by,
                "cooldown_minutes": PHARMA_ALERT_COOLDOWN_MINUTES,
            },
        )
        sent_rows.append({"ticker": ticker, "sent": sent, "dedupe_key": dedupe_key})

    return {
        "ok": True,
        "sent": any(r.get("sent") for r in sent_rows),
        "count": len(sent_rows),
        "sent_rows": sent_rows,
        "skipped": skipped,
    }


async def dispatch_pharma_shock_alerts(rows: list[dict[str, Any]], *, triggered_by: str = "unknown") -> dict[str, Any]:
    hot = [r for r in rows if _num(r.get("shock_score")) >= 75]
    if not hot:
        return {"ok": True, "sent": False, "count": 0, "reason": "no_hot_pharma_shocks"}

    db = get_db()
    sent_rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    cutoff = _now() - timedelta(minutes=max(1, PHARMA_ALERT_COOLDOWN_MINUTES))

    from . import pharma

    for row in hot[:5]:
        ticker = str(row.get("ticker") or "").upper()
        url_key = hashlib.sha1(str(row.get("url") or row.get("title") or "").encode("utf-8")).hexdigest()[:12]
        dedupe_key = f"pharma_shock:{ticker}:{url_key}"
        recent = await db.telegram_deliveries.find_one(
            {
                "batch_type": "pharma_shock_alert",
                "sent": True,
                "metadata.dedupe_key": dedupe_key,
                "created_at": {"$gte": cutoff.isoformat()},
            },
            {"_id": 1},
        )
        if recent:
            skipped.append({"ticker": ticker, "reason": "cooldown", "dedupe_key": dedupe_key})
            continue
        event = await emit_event(
            "pharma_catalyst_shock",
            severity="watch",
            scope="pharma",
            ticker=ticker,
            batch_id=dedupe_key,
            title="Pharma catalyst shock",
            summary=f"{ticker} shock {_num(row.get('shock_score')):.0f}; {row.get('direction') or 'WATCH'}",
            details={
                "ticker": ticker,
                "shock_score": row.get("shock_score"),
                "direction": row.get("direction"),
                "title": row.get("title"),
                "source": row.get("source"),
                "url": row.get("url"),
                "triggered_by": triggered_by,
                "trading_impact": "watch_only_pm_required",
            },
            priority="summary",
        )
        text = pharma.format_pharma_shock_alert(row)
        sent = await _send(text)
        await _record_delivery(
            "pharma_shock_alert",
            "Case Capital Pharma Catalyst Shock",
            text,
            [event],
            sent,
            metadata={
                "dedupe_key": dedupe_key,
                "ticker": ticker,
                "shock_score": row.get("shock_score"),
                "direction": row.get("direction"),
                "triggered_by": triggered_by,
                "cooldown_minutes": PHARMA_ALERT_COOLDOWN_MINUTES,
            },
        )
        sent_rows.append({"ticker": ticker, "sent": sent, "dedupe_key": dedupe_key})

    return {
        "ok": True,
        "sent": any(r.get("sent") for r in sent_rows),
        "count": len(sent_rows),
        "sent_rows": sent_rows,
        "skipped": skipped,
    }


async def dispatch_options_execution_report(result: dict[str, Any]) -> dict[str, Any]:
    submitted = result.get("submitted") or []
    skipped = result.get("skipped") or []
    if not submitted and not skipped:
        return {"ok": True, "sent": False, "reason": "no_execution_changes"}
    severity = "watch" if skipped and not submitted else "info"
    lines = [
        "<b>CASE CAPITAL | OPTIONS ORDERS</b>",
        f"<code>{_now_et()}</code>",
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
        "<b>CASE CAPITAL | QC GATE</b>",
        f"<code>{_now_et()}</code>",
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
    from . import data_quality, edge_dashboard, execution_gate, options_desk, pnl_tracker, scheduler

    qc = await data_quality.overview(force_refresh=False, record_event=False)
    gate = await execution_gate.overview(force_refresh=False)
    edge = await edge_dashboard.overview()
    opt_risk = await options_desk.latest_risk_check()
    try:
        opt_report = await options_desk.options_daily_report_payload()
    except Exception as exc:
        opt_report = {"ok": False, "reason": exc.__class__.__name__}
    tracker_rows = await pnl_tracker.signals_tracker_summary(limit=300)
    tracker = {"rows": tracker_rows, "tracked": len(tracker_rows)}
    snapshot = await scheduler.persist_live_position_snapshot(triggered_by="telegram_daily_report")
    rows = tracker.get("rows") or []
    best = max(rows, key=lambda r: _num(r.get("gain_pct")), default={})
    worst = min(rows, key=lambda r: _num(r.get("gain_pct")), default={})
    text = "\n".join([
        "<b>CASE CAPITAL | DAILY OPS</b>",
        f"<code>{_now_et()}</code>",
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
        f"Execution gate: <b>{_esc(gate.get('decision') or 'UNKNOWN')}</b> | Truth: <b>{_esc(gate.get('truth_grade') or '--')}</b>",
        "",
        "<b>EDGE / OPTIONS RISK</b>",
        f"Edge sample: <b>{(edge.get('edge') or {}).get('sample', 0)}</b> | Expectancy: <b>{_fmt_pct((edge.get('edge') or {}).get('expectancy_pct'))}</b>",
        f"Options checked: <b>{opt_risk.get('positions_checked', 0)}</b> | Hard stops: <b>{len(opt_risk.get('closed') or [])}</b>",
        "",
        "<b>OPTIONS DESK</b>",
        f"Open contracts: <b>{opt_report.get('active_count', 0)}</b> | Closed today: <b>{opt_report.get('closed_today_count', 0)}</b>",
        f"Unrealized gains: <b>{_fmt_money(opt_report.get('unrealized_gain'))}</b> | Realized gains: <b>{_fmt_money(opt_report.get('realized_gain'))}</b>",
        f"Risk deployed: <b>{_fmt_money(opt_report.get('risk_deployed'))}</b> / {_fmt_money(opt_report.get('daily_premium_cap'))}",
        _gain_line("Biggest option gain", opt_report.get("biggest_gain")),
        _gain_line("Biggest option loser", opt_report.get("biggest_loser")),
    ])
    event = await emit_event(
        "daily_ops_report",
        severity="info",
        scope="system",
        title="Daily ops report",
        summary=f"{snapshot.get('totals', {}).get('positions', 0)} positions; QC {(qc.get('trading_gate') or {}).get('decision')}",
        details={"snapshot": snapshot, "qc_summary": qc.get("summary") or {}, "gate": gate, "edge": edge.get("edge") or {}, "tracker": {"tracked": tracker.get("tracked")}, "options_report": opt_report},
        priority="summary",
    )
    sent = await _send(text)
    await _record_delivery("daily_report", "Daily Ops Report", text, [event], sent)
    return {"ok": True, "sent": sent}


async def dispatch_weekly_report() -> dict[str, Any]:
    from . import case_court, data_quality, edge_dashboard, options_desk, pnl_tracker

    qc = await data_quality.overview(force_refresh=False, record_event=False)
    edge = await edge_dashboard.overview()
    try:
        opt_report = await options_desk.options_weekly_report_payload()
    except Exception as exc:
        opt_report = {"ok": False, "reason": exc.__class__.__name__}
    tracker_rows = await pnl_tracker.signals_tracker_summary(limit=500)
    tracker = {"rows": tracker_rows, "tracked": len(tracker_rows)}
    court = await case_court.latest()
    rows = tracker.get("rows") or []
    wins = sum(1 for r in rows if _num(r.get("gain_pct")) > 0)
    losses = sum(1 for r in rows if _num(r.get("gain_pct")) < 0)
    avg = sum(_num(r.get("gain_pct")) for r in rows) / max(1, len(rows))
    text = "\n".join([
        "<b>CASE CAPITAL | WEEKLY TRUTH REPORT</b>",
        f"<code>{_now_et()}</code>",
        "",
        f"Tracked signal rows: <b>{len(rows)}</b>",
        f"Win/loss: <b>{wins}W / {losses}L</b>",
        f"Average since alert: <b>{_fmt_pct(avg)}</b>",
        f"Expectancy: <b>{_fmt_pct((edge.get('edge') or {}).get('expectancy_pct'))}</b> | Alpha: <b>{_esc((edge.get('edge') or {}).get('alpha_grade') or 'UNPROVEN')}</b>",
        "",
        "<b>CASE COURT</b>",
        f"Trials: <b>{len(court.get('trials') or [])}</b>",
        f"Advisory aligned: <b>{sum(1 for r in court.get('trials', []) if (r.get('judge') or {}).get('advisory_alignment_ok'))}</b>",
        f"Decision-grade: <b>{(edge.get('case_court') or {}).get('decision_grade', 0)}</b>",
        "",
        "<b>QC</b>",
        f"Decision: <b>{_esc((qc.get('trading_gate') or {}).get('decision') or 'UNKNOWN')}</b>",
        f"Blockers: <b>{(qc.get('summary') or {}).get('blockers', 0)}</b>",
        "",
        "<b>OPTIONS DESK</b>",
        f"Open contracts: <b>{opt_report.get('active_count', 0)}</b> | Closed this week: <b>{opt_report.get('closed_week_count', 0)}</b>",
        f"Unrealized gains: <b>{_fmt_money(opt_report.get('unrealized_gain'))}</b> | Realized gains: <b>{_fmt_money(opt_report.get('realized_gain'))}</b>",
        f"Risk deployed: <b>{_fmt_money(opt_report.get('risk_deployed'))}</b> / {_fmt_money(opt_report.get('daily_premium_cap'))}",
        _gain_line("Biggest option gain", opt_report.get("biggest_gain")),
        _gain_line("Biggest option loser", opt_report.get("biggest_loser")),
    ])
    event = await emit_event(
        "weekly_ops_report",
        severity="info",
        scope="system",
        title="Weekly ops report",
        summary=f"{wins}W/{losses}L; QC {(qc.get('trading_gate') or {}).get('decision')}",
        details={"qc_summary": qc.get("summary") or {}, "tracked": len(rows), "wins": wins, "losses": losses, "options_report": opt_report},
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
