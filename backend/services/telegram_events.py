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
PHARMA_SHOCK_BATCH_MAX = int(os.environ.get("TELEGRAM_PHARMA_SHOCK_BATCH_MAX", "20") or 20)
# Standalone pharma alerts are opt-in.  Normal operation publishes pharma data
# inside the consolidated terminal scan report instead of sending a second feed.
STANDALONE_PHARMA_ALERTS_ENABLED = os.environ.get("TELEGRAM_STANDALONE_PHARMA_ALERTS", "false").strip().lower() in {"1", "true", "yes", "on"}
TELEGRAM_TEXT_LIMIT = 3900


def _scheduled_standalone_alert_suppressed(triggered_by: Any) -> bool:
    """Keep scheduled scan output on the consolidated terminal report only."""
    trigger = str(triggered_by or "").strip().lower()
    return trigger.startswith(("scheduler", "full_terminal", "launch_control_full_terminal"))


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


def _short_text(value: Any, limit: int = 110) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


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


def _scan_report_suppressed_reason(scan: dict[str, Any]) -> str | None:
    trigger = str(scan.get("triggered_by") or "").strip().lower()
    variant = str(scan.get("telegram_report_variant") or "").strip().lower()
    if trigger == "scheduler" and variant != "full_terminal":
        return "scheduled_core_scan_report_suppressed"
    return None


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


def _section_rows(rows: Any, *, score_keys: tuple[str, ...], limit: int = 5) -> list[str]:
    """Render a short, bounded ticker list for the consolidated scan digest."""
    if not isinstance(rows, list):
        return []
    usable = [row for row in rows if isinstance(row, dict) and row.get("ticker")]
    usable.sort(key=lambda row: max((_num(row.get(key), -1) for key in score_keys), default=-1), reverse=True)
    lines: list[str] = []
    for row in usable[:limit]:
        score = next((row.get(key) for key in score_keys if row.get(key) is not None), None)
        detail = row.get("tier") or row.get("action") or row.get("direction") or row.get("status")
        suffix = f" · {_esc(detail)}" if detail else ""
        score_text = f" · {_num(score):.1f}" if score is not None else ""
        lines.append(f"${_esc(str(row.get('ticker')).upper())}{score_text}{suffix}")
    return lines


def _strategy_code(row: dict[str, Any]) -> str:
    """Return the first-letter strategy code used in execution summaries."""
    strategy = row.get("strategy") or row.get("strategy_label") or row.get("source_scan")
    scanner = row.get("strategy_scanner")
    if not strategy and isinstance(scanner, dict):
        strategy = scanner.get("screener_id") or scanner.get("family")
    if not strategy:
        strategy_views = row.get("strategy_views") or row.get("strategy_scanners")
        if isinstance(strategy_views, list) and strategy_views:
            first = strategy_views[0]
            if isinstance(first, dict):
                strategy = first.get("screener_id") or first.get("family") or first.get("strategy")
    value = str(strategy or "CORE").strip().upper()
    return value[:1] if value else "C"


def _execution_breakdown(rows: Any) -> str:
    if not isinstance(rows, list):
        return "none"
    counts: dict[str, int] = {}
    for row in rows:
        if isinstance(row, dict):
            code = _strategy_code(row)
            counts[code] = counts.get(code, 0) + 1
    return " / ".join(f"{count}{code}" for code, count in sorted(counts.items())) or "none"


def _consolidated_scan_report_text(
    scan: dict[str, Any],
    *,
    results: list[dict[str, Any]],
    lottery: dict[str, Any],
    pharma: dict[str, Any],
    shocks: dict[str, Any],
    screener_summary: dict[str, Any],
    new_scan: dict[str, Any],
    pm: dict[str, Any],
    pm_rows: list[dict[str, Any]],
    routes: dict[str, int],
    pm_actions: dict[str, int],
    opt_summary: dict[str, Any],
    gate: dict[str, Any],
    qc: dict[str, Any],
    edge: dict[str, Any],
    blockers: int,
    execution_summary: dict[str, Any],
) -> str:
    """Build exactly one scheduled digest from the same-cycle payloads."""
    lottery_rows = lottery.get("candidates") or []
    pharma_rows = pharma.get("results") or []
    shock_rows = shocks.get("results") or []
    core_tickers = {str(row.get("ticker") or "").upper() for row in results if row.get("ticker")}
    all_tickers = core_tickers | {
        str(row.get("ticker") or "").upper()
        for row in [*lottery_rows, *pharma_rows, *shock_rows]
        if isinstance(row, dict) and row.get("ticker")
    }
    freshness = scan.get("freshness") or {}
    stale = int(freshness.get("stale_price_rows") or 0)
    price_rows = int(freshness.get("price_rows") or 0)
    fresh = int(freshness.get("fresh_price_rows") or 0)
    scan_status = "FRESH" if not stale else "CHECK"
    family_total = int(screener_summary.get("total") or 0)
    family_pm = int(screener_summary.get("pm_routable") or 0)
    research_only = int(screener_summary.get("read_only") or 0)
    top_core = _section_rows(results, score_keys=("pm_score", "signal_score", "score"), limit=5)
    top_lottery = _section_rows(lottery_rows, score_keys=("score", "pm_score"), limit=5)
    top_pharma = _section_rows(pharma_rows, score_keys=("binary_event_score", "score"), limit=5)
    top_shocks = _section_rows(shock_rows, score_keys=("shock_score", "score"), limit=4)
    equity_orders = execution_summary.get("equity_submitted_rows") or []
    options_orders = execution_summary.get("options_submitted_rows") or []
    equity_codes = _execution_breakdown(equity_orders)
    options_codes = _execution_breakdown(options_orders)
    lines = [
        "<b>CASE CAPITAL | SCHEDULED TERMINAL REPORT</b>",
        f"<code>{_now_et()}</code>",
        f"Trigger: <b>{_esc(scan.get('triggered_by') or 'scheduler')}</b> · Cycle: <b>{_esc(scan.get('finished_at') or '--')}</b>",
        "",
        "<b>CORE SCAN</b>",
        f"Universe: <b>{scan.get('universe_size', '--')}</b> · Passed: <b>{len(results)}</b> · New: <b>{new_scan.get('count', 0)}</b>",
        f"Status: <b>{scan_status}</b> · Duration: <b>{scan.get('duration_sec', '--')}s</b>",
        *( [f"New tickers: {', '.join('$' + _esc(t) for t in new_scan.get('display') or [])}"] if new_scan.get('display') else [] ),
        *( ["Top: " + " | ".join(top_core)] if top_core else ["Top: none"] ),
        "",
        "<b>LOTTERY SCAN</b>",
        f"Status: <b>{'OK' if lottery.get('ok', True) else 'FAILED'}</b> · Candidates: <b>{len(lottery_rows)}</b> · Sources: <b>{len(lottery.get('source_counts') or (lottery.get('scan') or {}).get('source_counts') or {})}</b>",
        *( ["Top: " + " | ".join(top_lottery)] if top_lottery else ["Top: none"] ),
        "",
        "<b>PHARMA SCAN</b>",
        f"Calendar: <b>{len(pharma_rows)}</b> · Catalyst shocks: <b>{len(shock_rows)}</b> · Hot shocks: <b>{shocks.get('hot_count', 0)}</b>",
        f"Calendar status: <b>{'OK' if pharma.get('results') is not None else 'FAILED'}</b> · Shock status: <b>{'OK' if shocks.get('ok', True) else 'FAILED'}</b>",
        *( ["Calendar top: " + " | ".join(top_pharma)] if top_pharma else ["Calendar top: none"] ),
        *( ["Shock top: " + " | ".join(top_shocks)] if top_shocks else [] ),
        "",
        "<b>BUY ORDERS SENT</b>",
        f"Equities: <b>{len(equity_orders)}</b> · by strategy: <b>{equity_codes}</b>",
        f"Options: <b>{len(options_orders)}</b> · by strategy: <b>{options_codes}</b>",
        "Code = first letter of the originating strategy.",
        "",
        "<b>TOTAL SUMMARY</b>",
        f"Scan observations: <b>{len(results) + len(lottery_rows) + len(pharma_rows) + len(shock_rows)}</b> · Unique tickers: <b>{len(all_tickers)}</b>",
        f"Strategy scanners: <b>{family_pm}</b> PM-routable / <b>{family_total}</b> total · Research-only: <b>{research_only}</b>",
        f"PM routes: Equity <b>{routes['EQUITY']}</b> · Options <b>{routes['OPTION']}</b> · Both <b>{routes['BOTH']}</b> · Watch <b>{routes['WATCH']}</b> · Reject <b>{routes['REJECT']}</b>",
        f"PM actions: <b>{pm_actions['ACCUMULATE']}</b> accumulate · <b>{pm_actions['STARTER']}</b> starter · <b>{pm_actions['WATCH']}</b> watch · <b>{pm_actions['REJECT']}</b> reject · Docket <b>{len(pm_rows)}</b>",
        f"Options: <b>{opt_summary.get('contract_selected', 0)}</b> selected · <b>{opt_summary.get('ready', 0)}</b> ready · <b>{opt_summary.get('execution_grade', 0)}</b> execution-grade",
        f"QC: <b>{_esc((qc.get('trading_gate') or {}).get('decision') or 'UNKNOWN')}</b> · Gate: <b>{_esc(gate.get('decision') or 'UNKNOWN')}</b> · Blockers: <b>{blockers}</b>",
        f"Price freshness: <b>{fresh}</b> / {price_rows} live · Stale: <b>{stale}</b> · Edge sample: <b>{(edge.get('edge') or {}).get('sample', 0)}</b> · Alpha: <b>{_esc((edge.get('edge') or {}).get('alpha_grade') or 'UNPROVEN')}</b>",
        "<i>One message represents this scheduled cycle. Detail views retain the full scan outputs.</i>",
    ]
    return "\n".join(lines)


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


def _reason_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows or []:
        key = str(row.get("reason") or row.get("status") or "unknown").strip()
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
    pm = scan.get("pm_payload")
    if not isinstance(pm, dict):
        pm = await portfolio_manager.latest_portfolio_plan()
    alignment_notes: list[str] = []
    if not _same_scan(pm.get("scan_finished_at"), scan.get("finished_at")):
        alignment_notes.append("pm_scan_mismatch")
    screeners = scan.get("strategy_payload")
    if not isinstance(screeners, dict):
        screeners = await strategy_screeners.run_all(scan=scan, persist=True)
    screener_summary = screeners.get("summary") or {}
    if not _same_scan(screeners.get("scan_finished_at"), scan.get("finished_at")):
        alignment_notes.append("strategy_screeners_scan_mismatch")
    qc = await data_quality.overview(force_refresh=False, record_event=False)
    gate = await execution_gate.overview(force_refresh=False)
    edge = await edge_dashboard.overview()
    options = scan.get("options_payload")
    if not isinstance(options, dict):
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
    exec_summary = scan.get("execution_summary") or {}
    equity_reasons = exec_summary.get("equity_rejection_reason_counts") or _reason_counts(exec_summary.get("equity_rejected_sample") or [])
    equity_reason_line = " | ".join(f"{_esc(k)}: <b>{v}</b>" for k, v in list(equity_reasons.items())[:4])
    option_skip_reasons = _reason_counts(exec_summary.get("options_skipped_sample") or [])
    option_skip_line = " | ".join(f"{_esc(k)}: <b>{v}</b>" for k, v in list(option_skip_reasons.items())[:4])

    by_family_pm = screener_summary.get("by_pm_family") or {}
    by_family_read = screener_summary.get("by_read_only_family") or {}
    strategy_total = int(screener_summary.get("total") or 0)
    strategy_pm_total = int(screener_summary.get("pm_routable") or 0)
    terminal_docket_total = len(pm_rows)
    family_scan_lines = [
        f"{_esc(k)}: <b>{v}</b>"
        for k, v in sorted(by_family_pm.items(), key=lambda item: item[0])
        if k not in {"EARNINGS", "SEC"}
    ]
    research_lines = [
        f"{_esc(k)}: <b>{v}</b>"
        for k, v in sorted(by_family_read.items(), key=lambda item: item[0])
    ]

    top = _top_rows(pm_rows or results, 6)
    top_lines = []
    for r in top:
        if not r.get("ticker"):
            continue
        expression = _route_for_pm_row(r, opt_by_ticker)
        action = str(r.get("action") or r.get("route") or "SCAN").upper()
        label = action if expression == action else f"{expression} {action}"
        top_lines.append(
            f"${_esc(r.get('ticker'))} {_esc(label)} "
            f"{_num(r.get('pm_score') or r.get('signal_score')):.1f}"
        )
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
        f"Core scanner passed: <b>{len(results)}</b>",
        f"Strategy scanners: <b>{strategy_pm_total}</b> PM-routable / <b>{strategy_total}</b> total",
        *([f"PM-routable families: {' | '.join(family_scan_lines)}"] if family_scan_lines else []),
        *([f"Research-only scanners: {' | '.join(research_lines)}"] if research_lines else []),
        f"Terminal PM docket: <b>{terminal_docket_total}</b>",
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
        f"Options contracts ready: <b>{opt_summary.get('ready', 0)}</b> / {opt_summary.get('routed', routes['OPTION'] + routes['BOTH'])} routed | Contracts selected: <b>{opt_summary.get('contract_selected', 0)}</b> | Execution-grade: <b>{opt_summary.get('execution_grade', 0)}</b>",
        f"Turnover engine: <b>{opp.get('positions_reviewed', 0)}</b> holdings reviewed | Replace: <b>{len(opp.get('replacement_candidates') or [])}</b> | Trim/exit: <b>{len(opp.get('trim_reviews') or [])}</b>",
        *([f"Options execution blockers: {' | '.join(option_blocker_lines)}"] if option_blocker_lines else []),
        *([f"Options not routed by PM: <b>{non_option_routed}</b> equity/pass/watch lane(s)"] if non_option_routed else []),
        "",
        "<b>EXECUTION OUTCOME</b>",
        f"Equity submitted: <b>{exec_summary.get('equity_executed', 0)}</b> | Rejected: <b>{exec_summary.get('equity_rejected', 0)}</b>",
        *([f"Equity rejection reasons: {equity_reason_line}"] if equity_reason_line else []),
        f"Options submitted: <b>{exec_summary.get('options_submitted', 0)}</b> | Ready: <b>{exec_summary.get('options_ready', opt_summary.get('ready', 0))}</b> | Skipped: <b>{exec_summary.get('options_skipped', 0)}</b>",
        *([f"Options skip sample: {option_skip_line}"] if option_skip_line else []),
        "",
        "<b>SCANNER FAMILIES</b>",
        f"PM-routable strategy candidates: <b>{screener_summary.get('pm_routable', 0)}</b>",
        *([f"Families: {' | '.join(screener_lines)}"] if screener_lines else []),
        "Earnings + SEC: <b>RESEARCH-ONLY</b> | PM routing: <b>OFF</b> | Detail digest: <b>OFF</b>",
        "Case Court: <b>OFF ACTIVE ROUTING</b>",
        "",
        "<b>QC</b>",
        f"Decision: <b>{_esc(qc_decision)}</b> | Score: <b>{qc.get('score', '--')}</b> | Blockers: <b>{blockers}</b>",
        f"Execution gate: <b>{_esc(gate.get('decision') or 'UNKNOWN')}</b> | Truth: <b>{_esc(gate.get('truth_grade') or '--')}</b>",
        f"Ticker rejects: <b>{(scan.get('ticker_hygiene') or {}).get('rejected_count', 0)}</b>",
        *([f"Core display price freshness: <b>{fresh_price_rows}</b> / {price_rows} live | Stale rows: <b>{stale_price_rows}</b>"] if price_rows else []),
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
    if str(scan.get("telegram_report_variant") or "").strip().lower() == "full_terminal":
        text = _consolidated_scan_report_text(
            scan,
            results=results,
            lottery=scan.get("lottery_result") if isinstance(scan.get("lottery_result"), dict) else {},
            pharma=scan.get("pharma_result") if isinstance(scan.get("pharma_result"), dict) else {},
            shocks=scan.get("pharma_shock_result") if isinstance(scan.get("pharma_shock_result"), dict) else {},
            screener_summary=screener_summary,
            new_scan=new_scan,
            pm=pm,
            pm_rows=pm_rows,
            routes=routes,
            pm_actions=pm_actions,
            opt_summary=opt_summary,
            gate=gate,
            qc=qc,
            edge=edge,
            blockers=blockers,
            execution_summary=exec_summary,
        )
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
    suppressed_reason = _scan_report_suppressed_reason(scan)
    if suppressed_reason:
        await log_activity(
            f"Telegram scan report suppressed: {suppressed_reason}",
            "info",
            {
                "scan_id": str(scan.get("finished_at") or scan.get("created_at") or _now_iso()),
                "triggered_by": scan.get("triggered_by"),
                "telegram_report_variant": scan.get("telegram_report_variant"),
            },
        )
        return {"ok": True, "sent": False, "suppressed": True, "reason": suppressed_reason}
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
    if not STANDALONE_PHARMA_ALERTS_ENABLED or _scheduled_standalone_alert_suppressed(triggered_by):
        await log_activity("Standalone scheduled pharma alert suppressed; included in terminal scan report", "info", {
            "triggered_by": triggered_by,
            "count": len(rows or []),
        })
        return {"ok": True, "sent": False, "suppressed": True, "count": 0, "reason": "consolidated_terminal_report"}
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
    if not STANDALONE_PHARMA_ALERTS_ENABLED or _scheduled_standalone_alert_suppressed(triggered_by):
        await log_activity("Standalone scheduled pharma shock suppressed; included in terminal scan report", "info", {
            "triggered_by": triggered_by,
            "count": len(rows or []),
        })
        return {"ok": True, "sent": False, "suppressed": True, "count": 0, "reason": "consolidated_terminal_report"}
    hot = [r for r in rows if _num(r.get("shock_score")) >= 75]
    if not hot:
        return {"ok": True, "sent": False, "count": 0, "reason": "no_hot_pharma_shocks"}

    db = get_db()
    sent_rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    cutoff = _now() - timedelta(minutes=max(1, PHARMA_ALERT_COOLDOWN_MINUTES))

    def row_rank(row: dict[str, Any]) -> tuple[float, int, float]:
        ticker = str(row.get("ticker") or "").upper()
        title = str(row.get("title") or "").upper()
        explicit_title_match = 1 if ticker and (f"({ticker})" in title or f"${ticker}" in title) else 0
        age = _num(row.get("age_minutes"), 999999)
        return (_num(row.get("shock_score")), explicit_title_match, -age)

    by_article: dict[str, dict[str, Any]] = {}
    for row in hot:
        article_key = hashlib.sha1(str(row.get("url") or row.get("title") or row.get("ticker") or "").encode("utf-8")).hexdigest()[:16]
        current = by_article.get(article_key)
        if current is None or row_rank(row) > row_rank(current):
            by_article[article_key] = row

    by_ticker: dict[str, dict[str, Any]] = {}
    for row in by_article.values():
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            continue
        current = by_ticker.get(ticker)
        if current is None or row_rank(row) > row_rank(current):
            by_ticker[ticker] = row

    batch_rows = sorted(by_ticker.values(), key=lambda r: row_rank(r), reverse=True)[: max(1, PHARMA_SHOCK_BATCH_MAX)]
    if not batch_rows:
        return {"ok": True, "sent": False, "count": 0, "reason": "no_deduped_pharma_shocks"}

    events: list[dict[str, Any]] = []
    dedupe_keys: list[str] = []
    message_parts: list[str] = [
        "<b>CASE CAPITAL | PHARMA CATALYST SHOCKS</b>",
        f"<code>{_now_et()}</code>",
        "",
        f"Hot tickers: <b>{len(batch_rows)}</b>"
        + (f" / {len(hot)} raw alerts" if len(hot) != len(batch_rows) else ""),
        "",
    ]

    for idx, row in enumerate(batch_rows, start=1):
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
        dedupe_keys.append(dedupe_key)
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
        events.append(event)
        terms = [*row.get("bullish_terms", []), *row.get("bearish_terms", [])][:3]
        source = row.get("source") or "news"
        price = _fmt_money(row.get("current_price")) if row.get("current_price") not in {None, ""} else "--"
        title = _short_text(row.get("title") or "Clinical/FDA catalyst detected", 125)
        line = (
            f"{idx}. <b>${_esc(ticker)}</b> · <code>{_num(row.get('shock_score')):.0f}/100</code> · "
            f"<b>{_esc(row.get('direction') or 'WATCH')}</b>\n"
            f"{_esc(title)}\n"
            f"Price: <b>{_esc(price)}</b> · Evidence: {_esc(', '.join(terms) or 'pharma catalyst terms')} · "
            f"Source: {_esc(source)}"
        )
        if row.get("url"):
            line += f" · <a href=\"{_esc(row.get('url'))}\">source</a>"
        message_parts.extend([line, ""])
        sent_rows.append({"ticker": ticker, "sent": None, "dedupe_key": dedupe_key})

    if not events:
        return {
            "ok": True,
            "sent": False,
            "count": 0,
            "sent_rows": sent_rows,
            "skipped": skipped,
            "reason": "all_hot_pharma_shocks_in_cooldown",
        }

    message_parts.append("<i>Research alert only: clinical shocks require PM confirmation before execution.</i>")
    text = "\n".join(message_parts).strip()
    if len(text) > TELEGRAM_TEXT_LIMIT:
        footer = "\n\n<i>Message truncated for Telegram length. Open Pharma tab for the full shock tape.</i>"
        text = text[: TELEGRAM_TEXT_LIMIT - len(footer)].rstrip() + footer
    sent = await _send(text)
    await _record_delivery(
        "pharma_shock_alert",
        "Case Capital Pharma Catalyst Shocks",
        text,
        events,
        sent,
        metadata={
            "dedupe_keys": dedupe_keys,
            "tickers": [r.get("ticker") for r in sent_rows],
            "raw_count": len(hot),
            "batched_count": len(events),
            "triggered_by": triggered_by,
            "cooldown_minutes": PHARMA_ALERT_COOLDOWN_MINUTES,
        },
    )
    for row in sent_rows:
        row["sent"] = sent

    return {
        "ok": True,
        "sent": sent,
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
