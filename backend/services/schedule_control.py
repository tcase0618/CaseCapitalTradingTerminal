"""Scheduler control plane and stale-source watchdog.

This module is intentionally separate from APScheduler. APScheduler answers
"when will code run next"; this service answers "is the data it produces still
fresh enough to trust, and what repair path exists if it is not?"
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from .db import get_db, log_activity, stamped


RepairFn = Callable[[], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ScheduleSource:
    key: str
    label: str
    domain: str
    cadence: str
    max_age_minutes: int
    collection: str
    timestamp_fields: tuple[str, ...]
    query: dict[str, Any] | None = None
    sort: tuple[str, int] | None = None
    critical: bool = False
    execution_scopes: tuple[str, ...] = ()
    repair: str = ""
    market_session_only: bool = False


SOURCES: tuple[ScheduleSource, ...] = (
    ScheduleSource("latest_scan", "Core Stock Scan", "SCANNER", "00:00 / 08:00 / 10:00 / 12:00 / 15:00 / 18:30 ET on market days", 510, "scan_results", ("finished_at", "created_at"), sort=("finished_at", -1), critical=True, execution_scopes=("equity", "options"), repair="run_stock_scan_if_market_open", market_session_only=True),
    ScheduleSource("live_positions", "Live Position Snapshot", "EXECUTION", "Every 5 minutes from Alpaca, all sessions", 8, "bot_state", ("snapshot_at", "created_at"), query={"_id": "live_position_snapshot_latest"}, critical=True, execution_scopes=("equity", "options"), repair="repull_alpaca_positions"),
    ScheduleSource("options_candidates", "Options Candidate Scan", "OPTIONS", "09:35 / 10:00 ET auto scan + execution preflight on market days", 390, "options_desk_candidates", ("generated_at", "created_at"), sort=("generated_at", -1), critical=True, execution_scopes=("options",), repair="refresh_options_candidates"),
    ScheduleSource("options_risk", "Options Risk Marks", "EXECUTION", "Every 5 minutes from Alpaca position authority", 8, "options_desk_risk_checks", ("checked_at", "created_at"), sort=("checked_at", -1), critical=True, execution_scopes=("options",), repair="recheck_options_positions"),
    ScheduleSource("earnings_week", "Earnings Calendar", "CATALYST", "Cached UI refresh with background repull", 180, "earnings_snapshots", ("created_at", "generated_at"), sort=("created_at", -1), repair="refresh_current_earnings_week"),
    ScheduleSource("kronos", "Kronos Forecast", "FORECAST", "Every 5 minutes, plus 09:30 ET Telegram market brief", 8, "kronos_forecast_runs", ("generated_at", "created_at"), sort=("generated_at", -1), repair="refresh_kronos_snapshot"),
    ScheduleSource("news_active", "Active News Wire", "INTEL", "Cache TTL + watchdog refresh", 45, "bot_state", ("generated_at", "created_at"), query={"_id": "news_intel_latest_active"}, repair="refresh_active_news"),
    ScheduleSource("news_discovery", "Discovery News Wire", "INTEL", "Cache TTL + watchdog refresh", 90, "bot_state", ("generated_at", "created_at"), query={"_id": "news_intel_latest_discovery"}, repair="refresh_discovery_news"),
    ScheduleSource("georisk", "GeoRisk Map Feed", "GEORISK", "20-minute cache + watchdog refresh", 60, "georisk_snapshots", ("generated_at", "created_at"), sort=("created_at", -1), repair="refresh_georisk"),
    ScheduleSource("pharma", "Pharma Pipeline", "PHARMA", "On scan + watchdog refresh", 720, "pharma_pdufa", ("evaluated_at", "created_at"), sort=("created_at", -1), repair="refresh_pharma_pipeline"),
    ScheduleSource("lottery", "Lottery League Scan", "LOTTERY", "08:45 / 09:36 / 10:00 / 12:00 / 15:35 ET on market days", 390, "ll_scans", ("scanned_at", "created_at"), query={"_id": "current"}, repair="run_lottery_scan_if_market_open", market_session_only=True),
    ScheduleSource("strategy_screeners", "Strategy Scanner Fan-Out", "SCANNER", "After every coordinated stock scan", 540, "strategy_screeners", ("generated_at", "created_at"), query={"_id": "latest"}, repair="refresh_strategy_screeners"),
    ScheduleSource("research_lab", "R&D Lab Snapshot", "RESEARCH", "Hourly", 90, "bot_state", ("snapshot_at", "created_at"), query={"_id": "research_lab_latest"}, repair="refresh_research_lab"),
    ScheduleSource("truth_review", "Truth Review Ledger", "TRUTH", "Weekly packet + watchdog ledger refresh", 720, "bot_state", ("generated_at", "created_at"), query={"_id": "truth_review_latest"}, repair="refresh_truth_review"),
    ScheduleSource("pnl_refresh", "P&L / Return Tracker", "PERFORMANCE", "02:00 and 23:00 ET", 1560, "activity_log", ("ts", "created_at"), query={"message": {"$regex": "P&L refresh", "$options": "i"}}, sort=("ts", -1), repair="refresh_due_pnl_returns"),
    ScheduleSource("data_quality", "QC Overview", "QUALITY", "Passive 30-second UI checks + watchdog events", 30, "data_quality_events", ("generated_at", "created_at"), query={"event_type": "quality_overview"}, sort=("created_at", -1), repair="force_qc_repull"),
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _age_minutes(value: Any) -> float | None:
    dt = _parse_dt(value)
    if not dt:
        return None
    return max(0.0, (_now() - dt).total_seconds() / 60.0)


async def _market_day_now() -> tuple[bool, str]:
    from . import scheduler

    return await scheduler.stock_scan_market_day_now()


async def _latest_doc(src: ScheduleSource) -> dict[str, Any]:
    db = get_db()
    query = src.query or {}
    projection = {"_id": 0}
    sort = [src.sort] if src.sort else None
    if sort:
        return await db[src.collection].find_one(query, projection, sort=sort) or {}
    return await db[src.collection].find_one(query, projection) or {}


async def _latest_scan_finished_at() -> Any:
    db = get_db()
    row = await db.scan_results.find_one({}, {"_id": 0, "finished_at": 1}, sort=[("finished_at", -1)])
    return (row or {}).get("finished_at")


def _timestamp_from_doc(doc: dict[str, Any], fields: tuple[str, ...]) -> Any:
    for field in fields:
        if doc.get(field):
            return doc.get(field)
    return None


def _status(age: float | None, max_age: int, *, market_paused: bool = False) -> str:
    if market_paused:
        return "STANDBY"
    if age is None:
        return "MISSING"
    if age <= max_age:
        return "LIVE"
    if age <= max_age * 2:
        return "STALE"
    return "DOWN"


def _same_scan(left: Any, right: Any) -> bool:
    if not left or not right:
        return False
    return str(left).replace("Z", "+00:00") == str(right).replace("Z", "+00:00")


async def _repair_latest_scan() -> dict[str, Any]:
    market_day, reason = await _market_day_now()
    if not market_day:
        return {"ok": True, "outcome": "standby_market_closed", "detail": reason}
    from . import scanner

    payload = await scanner.run_scan(triggered_by="schedule_watchdog")
    return {"ok": True, "outcome": "refreshed", "detail": f"{len(payload.get('results') or [])} scan rows", "finished_at": payload.get("finished_at")}


async def _repair_live_positions() -> dict[str, Any]:
    from . import scheduler

    payload = await scheduler.persist_live_position_snapshot(triggered_by="schedule_watchdog")
    return {"ok": True, "outcome": "repulled", "detail": payload.get("totals"), "snapshot_at": payload.get("snapshot_at")}


async def _repair_options_risk() -> dict[str, Any]:
    from . import options_desk

    payload = await options_desk.monitor_open_positions(enforce_hard_stop=False)
    return {"ok": True, "outcome": "rechecked", "detail": {"positions_checked": payload.get("positions_checked"), "errors": payload.get("errors")}, "checked_at": payload.get("checked_at")}


async def _repair_earnings() -> dict[str, Any]:
    from . import earnings_engine, scanner

    latest = await scanner.latest_scan()
    scan_set = {str(r.get("ticker")).upper() for r in (latest or {}).get("results", []) if r.get("ticker")}
    payload = await earnings_engine.current_week_cached(scan_tickers=scan_set, max_age_minutes=0)
    return {"ok": True, "outcome": "refreshed", "detail": {"total": payload.get("total"), "cache_status": payload.get("cache_status")}, "week_of": payload.get("week_of")}


async def _repair_kronos() -> dict[str, Any]:
    from . import kronos

    payload = await kronos.refresh_snapshot()
    status = payload.get("status") or {}
    return {"ok": True, "outcome": "refreshed", "detail": {"health": status.get("health"), "positions": status.get("positions")}, "generated_at": ((payload.get("forecast") or {}).get("generated_at"))}


async def _repair_news(lane: str) -> dict[str, Any]:
    from . import news_intel

    payload = await news_intel.latest(force_refresh=True, limit=80, lane=lane)
    return {"ok": True, "outcome": "refreshed", "detail": payload.get("summary"), "generated_at": payload.get("generated_at")}


async def _repair_georisk() -> dict[str, Any]:
    from . import georisk

    payload = await georisk.live_georisk(max_age_minutes=0)
    return {"ok": True, "outcome": "refreshed", "detail": {"events": payload.get("total"), "cache": payload.get("cache_status")}, "generated_at": payload.get("generated_at")}


async def _repair_pharma() -> dict[str, Any]:
    from . import pharma

    payload = await pharma.run_pharma_scan(triggered_by="schedule_watchdog")
    rows = payload.get("results") or payload.get("rows") or []
    return {"ok": True, "outcome": "refreshed", "detail": {"rows": len(rows)}, "generated_at": payload.get("generated_at") or payload.get("finished_at")}


async def _repair_lottery() -> dict[str, Any]:
    market_day, reason = await _market_day_now()
    if not market_day:
        return {"ok": True, "outcome": "standby_market_closed", "detail": reason}
    from . import lottery

    payload = await lottery.run_dedicated_lottery_scan(triggered_by="schedule_watchdog")
    return {"ok": True, "outcome": "refreshed", "detail": {"count": payload.get("count")}, "generated_at": ((payload.get("scan") or {}).get("scanned_at"))}


async def _repair_strategy_screeners() -> dict[str, Any]:
    from . import scanner, strategy_screeners

    scan = await scanner.latest_scan()
    payload = await strategy_screeners.run_all(scan=scan, persist=True)
    return {"ok": True, "outcome": "refreshed", "detail": payload.get("summary"), "generated_at": payload.get("generated_at")}


async def _repair_options_candidates() -> dict[str, Any]:
    market_day, reason = await _market_day_now()
    if not market_day:
        return {"ok": True, "outcome": "standby_market_closed", "detail": reason}
    from . import options_desk

    payload = await options_desk.build_candidates(limit=100, persist=True)
    return {
        "ok": True,
        "outcome": "refreshed",
        "detail": payload.get("summary"),
        "generated_at": payload.get("generated_at"),
    }


async def _repair_research_lab() -> dict[str, Any]:
    from . import research_lab

    payload = await research_lab.refresh_snapshot(triggered_by="schedule_watchdog")
    return {"ok": True, "outcome": "refreshed", "detail": payload.get("stats")}


async def _repair_truth_review() -> dict[str, Any]:
    from . import truth_review

    ledger = await truth_review.refresh_ledger()
    overview = await truth_review.overview(force_refresh=False, persist=True)
    return {"ok": True, "outcome": "refreshed", "detail": {"ledger": ledger, "rating": (overview.get("overall") or {}).get("rating")}}


async def _repair_pnl() -> dict[str, Any]:
    from . import lottery, pnl_tracker

    sig, opt, lot = await asyncio.gather(
        pnl_tracker.refresh_due_returns(),
        pnl_tracker.refresh_due_options_returns(),
        lottery.refresh_settlements(),
        return_exceptions=True,
    )
    return {"ok": True, "outcome": "refreshed", "detail": {"signals": str(sig), "options": str(opt), "lottery": str(lot)}}


async def _repair_data_quality() -> dict[str, Any]:
    from . import data_quality

    payload = await data_quality.overview(force_refresh=True, record_event=True)
    return {"ok": True, "outcome": "repulled", "detail": {"score": payload.get("score"), "gate": (payload.get("trading_gate") or {}).get("decision")}}


def _repair_registry() -> dict[str, RepairFn]:
    return {
        "latest_scan": _repair_latest_scan,
        "live_positions": _repair_live_positions,
        "options_candidates": _repair_options_candidates,
        "options_risk": _repair_options_risk,
        "earnings_week": _repair_earnings,
        "kronos": _repair_kronos,
        "news_active": lambda: _repair_news("active"),
        "news_discovery": lambda: _repair_news("discovery"),
        "georisk": _repair_georisk,
        "pharma": _repair_pharma,
        "lottery": _repair_lottery,
        "strategy_screeners": _repair_strategy_screeners,
        "research_lab": _repair_research_lab,
        "truth_review": _repair_truth_review,
        "pnl_refresh": _repair_pnl,
        "data_quality": _repair_data_quality,
    }


async def rows() -> list[dict[str, Any]]:
    market_day, market_reason = await _market_day_now()
    latest_scan_at = await _latest_scan_finished_at()
    out: list[dict[str, Any]] = []
    for src in SOURCES:
        doc = await _latest_doc(src)
        ts = _timestamp_from_doc(doc, src.timestamp_fields)
        age = _age_minutes(ts)
        paused = bool(src.market_session_only and not market_day)
        status = _status(age, src.max_age_minutes, market_paused=paused)
        notes: list[str] = []
        if src.key == "strategy_screeners" and latest_scan_at:
            screener_scan_at = doc.get("scan_finished_at")
            if not _same_scan(screener_scan_at, latest_scan_at):
                status = "STALE"
                notes.append(f"scan_mismatch: strategy_screeners={screener_scan_at or 'unknown'} latest={latest_scan_at}")
        stale = status in {"STALE", "MISSING", "DOWN"}
        out.append({
            "key": src.key,
            "label": src.label,
            "domain": src.domain,
            "cadence": src.cadence,
            "max_age_minutes": src.max_age_minutes,
            "last_evidence_at": ts,
            "age_minutes": round(age, 2) if age is not None else None,
            "status": status,
            "stale": stale,
            "critical": src.critical,
            "execution_scopes": list(src.execution_scopes),
            "repair": src.repair,
            "market_session_only": src.market_session_only,
            "market_state": market_reason if src.market_session_only else "runs independent of stock market session",
            "latest_scan_finished_at": latest_scan_at if src.key == "strategy_screeners" else None,
            "notes": notes,
        })
    return out


async def runtime_jobs() -> list[dict[str, Any]]:
    from . import scheduler

    return scheduler.jobs_status()


async def latest_watchdog_event() -> dict[str, Any] | None:
    db = get_db()
    return await db.schedule_watchdog_events.find_one({}, {"_id": 0}, sort=[("created_at", -1)])


async def overview() -> dict[str, Any]:
    source_rows, jobs, last_event = await asyncio.gather(rows(), runtime_jobs(), latest_watchdog_event())
    stale = [r for r in source_rows if r["stale"]]
    critical_stale = [r for r in stale if r["critical"]]
    return {
        "ok": not critical_stale,
        "generated_at": _now_iso(),
        "summary": {
            "sources": len(source_rows),
            "live": sum(1 for r in source_rows if r["status"] == "LIVE"),
            "standby": sum(1 for r in source_rows if r["status"] == "STANDBY"),
            "stale": len(stale),
            "critical_stale": len(critical_stale),
            "scheduled_jobs": len(jobs),
        },
        "rows": source_rows,
        "jobs": jobs,
        "last_watchdog": last_event,
        "policy": "Every scheduled source declares a max-age SLA, persisted evidence field, and repair action. Watchdog repairs stale rows out-of-band so execution paths do not wait on display data.",
    }


async def repair(key: str, *, timeout_seconds: float = 45.0) -> dict[str, Any]:
    key = str(key or "").strip()
    registry = _repair_registry()
    if key not in registry:
        return {"ok": False, "key": key, "outcome": "unknown_source", "detail": "No repair action registered."}
    started = _now_iso()
    try:
        payload = await asyncio.wait_for(registry[key](), timeout=timeout_seconds)
        result = {"ok": bool(payload.get("ok", True)), "key": key, "started_at": started, "finished_at": _now_iso(), **payload}
    except asyncio.TimeoutError:
        result = {"ok": False, "key": key, "started_at": started, "finished_at": _now_iso(), "outcome": "timeout", "detail": f"Repair exceeded {timeout_seconds}s"}
    except Exception as exc:
        result = {"ok": False, "key": key, "started_at": started, "finished_at": _now_iso(), "outcome": "error", "detail": str(exc)[:500]}
    db = get_db()
    await db.schedule_repair_events.insert_one(stamped(result))
    await log_activity(f"Scheduler repair {key}: {result.get('outcome')}", "info" if result.get("ok") else "warn", result)
    return result


async def watchdog(*, auto_fix: bool = True, max_repairs: int = 4, critical_only: bool = False) -> dict[str, Any]:
    started = _now_iso()
    before = await rows()
    stale = [r for r in before if r["stale"] and (r["critical"] or not critical_only)]
    stale.sort(key=lambda r: (not r["critical"], r.get("age_minutes") or 999999))
    selected = stale[: max(0, min(int(max_repairs or 0), 12))] if auto_fix else []
    repairs = []
    for row in selected:
        repairs.append(await repair(row["key"], timeout_seconds=45.0))
    after = await rows()
    event = stamped({
        "event_type": "schedule_watchdog",
        "generated_at": started,
        "finished_at": _now_iso(),
        "auto_fix": auto_fix,
        "critical_only": critical_only,
        "summary": {
            "stale_before": len(stale),
            "critical_stale_before": sum(1 for r in stale if r["critical"]),
            "repairs_attempted": len(repairs),
            "repairs_ok": sum(1 for r in repairs if r.get("ok")),
            "stale_after": sum(1 for r in after if r["stale"] and (r["critical"] or not critical_only)),
            "critical_stale_after": sum(1 for r in after if r["stale"] and r["critical"]),
        },
        "stale_before": stale,
        "repairs": repairs,
    })
    db = get_db()
    await db.schedule_watchdog_events.insert_one(event)
    await log_activity("Scheduler watchdog completed", "info", event["summary"])
    return {"ok": True, "event": {k: v for k, v in event.items() if k != "_id"}, "rows": after}
