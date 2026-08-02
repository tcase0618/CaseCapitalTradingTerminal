"""FastAPI entrypoint: Stock Intelligence Telegram Bot backend."""
from __future__ import annotations
import asyncio
import hashlib
import hmac
import logging
import os
import platform
import secrets
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import APIRouter, BackgroundTasks, FastAPI, HTTPException, Request
from pydantic import BaseModel
from starlette.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

from services import claude_service, risk_target, scanner, scheduler, telegram_service, usaspending  # noqa: E402
from services.db import get_db, log_activity  # noqa: E402
from services.scrapers import fetch_quote  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("server")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

app = FastAPI(title="Stock Intel Bot")
api = APIRouter(prefix="/api")
OPERATOR_SESSIONS: set[str] = set()
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
AUTH_EXEMPT_PATHS = {
    "/api/auth/config",
    "/api/auth/login",
    "/api/auth/preview",
    "/api/telegram/webhook",
}


def _cloud_mode() -> bool:
    return os.environ.get("APP_ENV", "").strip().lower() == "cloud"


def _operator_hash() -> str:
    configured_hash = os.environ.get("TERMINAL_ACCESS_CODE_HASH", "").strip()
    if configured_hash:
        return configured_hash
    configured_code = os.environ.get("TERMINAL_ACCESS_CODE", "").strip()
    if configured_code:
        return hashlib.sha256(f"case-capital:{configured_code}".encode("utf-8")).hexdigest()
    return ""


def _operator_configured() -> bool:
    return bool(_operator_hash())


def _authorized_request(request: Request) -> bool:
    auth = request.headers.get("authorization", "")
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer" or not token:
        token = request.headers.get("x-terminal-session", "").strip()
    return bool(token and token in OPERATOR_SESSIONS)


@app.middleware("http")
async def cloud_operator_write_gate(request: Request, call_next):
    if (
        _cloud_mode()
        and request.method.upper() in MUTATING_METHODS
        and request.url.path not in AUTH_EXEMPT_PATHS
        and not _authorized_request(request)
    ):
        return JSONResponse(
            {"ok": False, "detail": "Operator session required. Preview mode is read-only."},
            status_code=403,
        )
    return await call_next(request)


# ---------- Schemas ----------
class WatchlistItem(BaseModel):
    ticker: str


class AlertItem(BaseModel):
    ticker: str
    target_price: float


class AuthLoginRequest(BaseModel):
    code: str


# ---------- Routes ----------
@api.get("/")
async def root():
    return {"name": "Stock Intel Bot API", "status": "ok"}


@api.get("/auth/config")
async def auth_config():
    return {
        "ok": True,
        "cloud": _cloud_mode(),
        "operator_login_enabled": _operator_configured() or not _cloud_mode(),
        "preview_enabled": True,
        "setup_enabled": not _cloud_mode(),
    }


@api.post("/auth/login")
async def auth_login(payload: AuthLoginRequest):
    code = (payload.code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="Access code required.")
    expected = _operator_hash()
    if not expected and _cloud_mode():
        raise HTTPException(status_code=403, detail="Operator access code is not configured on the server.")
    attempted = hashlib.sha256(f"case-capital:{code}".encode("utf-8")).hexdigest()
    if expected and not hmac.compare_digest(attempted, expected):
        raise HTTPException(status_code=403, detail="Access denied.")
    token = secrets.token_urlsafe(32)
    OPERATOR_SESSIONS.add(token)
    return {
        "ok": True,
        "mode": "operator",
        "token": token,
        "name": os.environ.get("TERMINAL_OPERATOR_NAME", "CASE CAPITAL OPERATOR"),
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }


@api.post("/auth/preview")
async def auth_preview():
    return {
        "ok": True,
        "mode": "preview",
        "name": "CASE CAPITAL PREVIEW",
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }


@api.get("/status")
async def status():
    db_available = True
    state = {}
    cache_today = 0
    last_scan = None
    watchlist_count = 0
    alerts_count = 0
    try:
        db = get_db()
        today_iso_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        state = await db.bot_state.find_one({"_id": "state"}, {"_id": 0}) or {}
        cache_today = await db.claude_cache.count_documents({"date_key": today_iso_prefix})
        last_scan = await db.scan_results.find_one({}, {"_id": 0}, sort=[("finished_at", -1)])
        watchlist_count = await db.watchlist.count_documents({})
        alerts_count = await db.alerts.count_documents({"triggered": False})
    except Exception as e:
        db_available = False
        logger.warning("status degraded; MongoDB unavailable: %s", e)

    return {
        "bot": {
            "online": True,
            "db_available": db_available,
            "telegram_configured": bool(os.environ.get("TELEGRAM_BOT_TOKEN")),
            "claude_configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "default_chat_id_set": bool(os.environ.get("TELEGRAM_CHAT_ID")),
        },
        "webhook_url": state.get("webhook_url"),
        "webhook_set_at": state.get("webhook_set_at"),
        "last_scan_at": state.get("last_scan_at"),
        "last_scan_summary": state.get("last_scan_summary"),
        "stats": {
            "cached_analyses_today": cache_today,
            "watchlist_count": watchlist_count,
            "active_alerts": alerts_count,
            "tickers_in_last_scan": (last_scan or {}).get("pre_filter_passed", 0),
            "claude_calls_in_last_scan": (last_scan or {}).get("claude_calls_made", 0),
            "claude_cache_hits_in_last_scan": (last_scan or {}).get("claude_cache_hits", 0),
        },
        "now": datetime.now(timezone.utc).isoformat(),
    }


@api.get("/admin/trading_status")
async def admin_trading_status():
    from services import safety
    return await safety.trading_status()


@api.post("/admin/halt")
async def admin_halt(reason: str = "operator_halt"):
    from services import safety, trade_floor
    status = await safety.set_trading(False, reason=reason)
    cancelled = await trade_floor.cancel_stale_orders(max_age_hours=0)
    try:
        from services import telegram_events
        await telegram_events.emit_event(
            "operator_trading_halt",
            severity="critical",
            scope="risk",
            title="Trading halted",
            summary=f"Trading halted by operator: {reason}",
            details={"status": status, "cancelled": cancelled},
            priority="critical",
        )
    except Exception:
        pass
    return {"ok": True, "status": status, "cancelled": cancelled}


@api.post("/admin/resume")
async def admin_resume(reason: str = "operator_resume"):
    from services import safety
    status = await safety.set_trading(True, reason=reason)
    baseline = await safety.snapshot_day_start_equity(source="operator_resume")
    return {"ok": True, "status": status, "daily_loss_baseline": baseline}


@api.get("/position_monitor/latest")
async def position_monitor_latest():
    db = get_db()
    row = await db.bot_state.find_one({"_id": "live_position_snapshot_latest"}, {"_id": 0})
    if row:
        return {"ok": True, **row}
    return {"ok": True, "snapshot_at": None, "totals": {"positions": 0, "open_orders": 0, "market_value": 0, "unrealized_pl": 0}, "equities": {}, "options": {}}


@api.get("/position_monitor/history")
async def position_monitor_history(limit: int = 96):
    db = get_db()
    limit = max(1, min(int(limit or 96), 500))
    rows = await db.live_position_snapshots.find({}, {"_id": 0}).sort("snapshot_at", -1).to_list(limit)
    return {"ok": True, "count": len(rows), "snapshots": rows}


@api.post("/position_monitor/refresh")
async def position_monitor_refresh():
    return await scheduler.persist_live_position_snapshot(triggered_by="manual_refresh")


@api.get("/data_quality/overview")
async def data_quality_overview(force_refresh: bool = False):
    from services import data_quality
    return await data_quality.overview(force_refresh=force_refresh)


@api.post("/data_quality/refresh")
async def data_quality_refresh():
    from services import data_quality
    return await data_quality.overview(force_refresh=True)


@api.post("/data_quality/remediate")
async def data_quality_remediate(limit: int = 16):
    from services import data_quality
    return await data_quality.remediate(limit=limit)


@api.get("/data_quality/events")
async def data_quality_events(limit: int = 100):
    from services import data_quality
    return await data_quality.events(limit=limit)


@api.get("/data_truth/overview")
async def data_truth_overview(force_refresh: bool = False):
    from services import data_truth
    return await data_truth.overview(force_refresh=force_refresh)


@api.get("/execution_gate/overview")
async def execution_gate_overview(force_refresh: bool = False):
    from services import execution_gate
    return await execution_gate.overview(force_refresh=force_refresh)


@api.get("/execution_gate/check")
async def execution_gate_check(scope: str = "system", ticker: str | None = None, sector: str | None = None):
    from services import execution_gate
    return await execution_gate.check(scope=scope, ticker=ticker, sector=sector)


@api.get("/readiness/overview")
async def readiness_overview(force_refresh: bool = False):
    from services import readiness
    return await readiness.run(force_refresh=force_refresh, persist=False)


@api.post("/readiness/run")
async def readiness_run(force_refresh: bool = False):
    from services import readiness
    return await readiness.run(force_refresh=force_refresh, persist=True)


@api.get("/edge/overview")
async def edge_overview():
    from services import edge_dashboard
    return await edge_dashboard.overview()


@api.get("/telegram/events")
async def telegram_events_recent(limit: int = 80):
    from services import telegram_events
    return await telegram_events.recent_events(limit=limit)


@api.get("/telegram/events/preview")
async def telegram_events_preview(batch_type: str = "scan_report"):
    from services import telegram_events
    return await telegram_events.preview_latest(batch_type=batch_type)


@api.post("/telegram/events/flush_scan")
async def telegram_events_flush_scan():
    from services import scanner, telegram_events
    scan = await scanner.latest_scan()
    if not scan:
        return {"ok": False, "reason": "no_latest_scan"}
    return await telegram_events.dispatch_scan_report(scan)


@api.post("/telegram/events/qc")
async def telegram_events_qc(force_refresh: bool = False, send_if_clean: bool = True):
    from services import telegram_events
    return await telegram_events.dispatch_qc_report(force_refresh=force_refresh, send_if_clean=send_if_clean)


@api.post("/telegram/events/daily_report")
async def telegram_events_daily_report():
    from services import telegram_events
    return await telegram_events.dispatch_daily_report()


@api.post("/telegram/events/weekly_report")
async def telegram_events_weekly_report():
    from services import telegram_events
    return await telegram_events.dispatch_weekly_report()


@api.post("/trading_halts/check")
async def trading_halts_check(force_alert: bool = False):
    from services import trading_halts
    return await trading_halts.check_and_alert(force_alert=force_alert)


@api.get("/trading_halts/latest")
async def trading_halts_latest(limit: int = 25):
    from services import trading_halts
    return await trading_halts.latest(limit=limit)


@api.get("/news_intel/latest")
async def news_intel_latest(force_refresh: bool = False, limit: int = 60, lane: str = "active"):
    from services import news_intel
    return await news_intel.latest(force_refresh=force_refresh, limit=limit, lane=lane)


@api.post("/news_intel/refresh")
async def news_intel_refresh(limit: int = 60, lane: str = "active"):
    from services import news_intel
    return await news_intel.latest(force_refresh=True, limit=limit, lane=lane)


@api.get("/news_intel/snapshots")
async def news_intel_snapshots(limit: int = 25):
    db = get_db()
    limit = max(1, min(int(limit or 25), 100))
    rows = await db.news_intel_snapshots.find({}, {"_id": 0}).sort("generated_at", -1).to_list(limit)
    return {"ok": True, "count": len(rows), "snapshots": rows}


@api.get("/system/health")
async def system_health():
    from services import system_health as svc
    return await svc.overview()


@api.post("/admin/backend_refresh")
async def admin_backend_refresh():
    from services import pricer
    from services import system_health as health_svc

    health = await health_svc.overview()
    integrations = await integration_svc.integration_status()
    status_payload = await status()
    payload = {
        "ok": True,
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
        "status": status_payload,
        "health": health,
        "integrations": integrations,
        "jobs": integration_svc.scheduled_jobs(),
        "commands": integration_svc.telegram_commands(),
        "price_source": {
            "source": pricer.source_label(),
            "massive_available": pricer.has_massive(),
            "finnhub_available": pricer.has_finnhub(),
        },
    }
    try:
        await log_activity("Backend link refresh completed from desktop UI", "info")
    except Exception:
        pass
    return payload


@api.get("/desktop/diagnostics")
async def desktop_diagnostics():
    """Single fast desktop readiness payload for startup + Settings diagnostics."""
    from services import integration_status as integration_svc, pricer
    from services import system_health as health_svc

    generated_at = datetime.now(timezone.utc).isoformat()
    async def _status_probe():
        return await asyncio.wait_for(status(), timeout=3.0)

    async def _health_probe():
        return await asyncio.wait_for(health_svc.overview(), timeout=3.5)

    status_result, health_result = await asyncio.gather(
        _status_probe(),
        _health_probe(),
        return_exceptions=True,
    )
    if isinstance(status_result, Exception):
        status_payload = {
            "bot": {"online": True, "db_available": False, "telegram_configured": bool(os.environ.get("TELEGRAM_BOT_TOKEN"))},
            "last_scan_at": None,
            "last_scan_summary": {},
            "stats": {},
            "diagnostics_error": str(status_result)[:180],
        }
    else:
        status_payload = status_result
    if isinstance(health_result, Exception):
        health = {
            "generated_at": generated_at,
            "ready_for_scanning": bool(status_payload.get("bot", {}).get("db_available")),
            "ready_for_pm": bool(status_payload.get("last_scan_at")),
            "ready_for_trade_floor": False,
            "ready_for_journal_learning": False,
            "blockers": [f"System health timed out: {str(health_result)[:120]}"],
            "database": {"ok": bool(status_payload.get("bot", {}).get("db_available")), "latest_scan_at": status_payload.get("last_scan_at")},
            "alpaca": {"ok": False, "reason": "health_probe_timeout"},
            "env": {},
        }
    else:
        health = health_result

    db = get_db()
    xfactor_count = 0
    earnings_cache = None
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        xfactor_count = await db.x_factor_alerts.count_documents({"fired_at": {"$gte": cutoff}})
    except Exception:
        xfactor_count = 0
    try:
        earnings_cache = await db.earnings_snapshots.find_one(
            {},
            {"_id": 0, "week_of": 1, "created_at": 1, "feature_version": 1, "total": 1},
            sort=[("created_at", -1)],
        )
    except Exception:
        earnings_cache = None

    installer = ROOT_DIR.parent / "frontend" / "src-tauri" / "target" / "x86_64-pc-windows-msvc" / "release" / "bundle" / "nsis" / "CaseCapitalTradingTerminal_0.1.1_x64-setup.exe"
    source_state = {
        "backend_root": str(ROOT_DIR),
        "frontend_root": str(ROOT_DIR.parent / "frontend"),
        "installer_path": str(installer),
        "installer_exists": installer.exists(),
    }

    checklist = [
        {"key": "backend", "label": "Backend API", "ok": True, "detail": "FastAPI responding on 127.0.0.1:8001"},
        {"key": "mongo", "label": "MongoDB", "ok": bool(status_payload.get("bot", {}).get("db_available")), "detail": health.get("database", {}).get("reason") or "database reachable"},
        {"key": "latest_scan", "label": "Latest Scan", "ok": bool(status_payload.get("last_scan_at") or health.get("database", {}).get("latest_scan_at")), "detail": status_payload.get("last_scan_at") or health.get("database", {}).get("latest_scan_at") or "no scan saved"},
        {"key": "pm", "label": "Portfolio Manager", "ok": bool(health.get("ready_for_pm")), "detail": "latest scan available" if health.get("ready_for_pm") else "waiting for scan state"},
        {"key": "trade_floor", "label": "Trade Floor", "ok": bool(health.get("ready_for_trade_floor")), "detail": health.get("alpaca", {}).get("reason") or "execution account reachable"},
        {"key": "telegram", "label": "Telegram", "ok": bool(status_payload.get("bot", {}).get("telegram_configured")), "detail": "bot token configured" if status_payload.get("bot", {}).get("telegram_configured") else "bot token missing"},
        {"key": "earnings_cache", "label": "Earnings Cache", "ok": bool(earnings_cache), "detail": (earnings_cache or {}).get("created_at") or "no earnings cache"},
        {"key": "xfactor", "label": "X Factor Alerts", "ok": xfactor_count > 0, "detail": f"{xfactor_count} alerts in 2 days"},
    ]

    core_keys = {"backend", "mongo", "latest_scan", "pm"}
    core_ready = all(item["ok"] for item in checklist if item["key"] in core_keys)

    return {
        "ok": core_ready,
        "generated_at": generated_at,
        "app": {
            "name": "CaseCapitalTradingTerminal",
            "version": "0.1.1",
            "build_channel": "local-desktop",
            "backend_pid": os.getpid(),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "backend": {
            "url": "http://127.0.0.1:8001",
            "pid": os.getpid(),
            "cwd": os.getcwd(),
            "root": str(ROOT_DIR),
            "price_source": pricer.source_label(),
        },
        "status": status_payload,
        "health": health,
        "signals": {
            "xfactor_2d": xfactor_count,
            "latest_scan_at": status_payload.get("last_scan_at"),
            "latest_scan_results": (status_payload.get("last_scan_summary") or {}).get("results_count"),
        },
        "earnings_cache": earnings_cache,
        "checklist": checklist,
        "integrations_count": None,
        "source_state": source_state,
    }


@api.get("/desktop/update_strategy")
async def desktop_update_strategy():
    installer = ROOT_DIR.parent / "frontend" / "src-tauri" / "target" / "x86_64-pc-windows-msvc" / "release" / "bundle" / "nsis" / "CaseCapitalTradingTerminal_0.1.1_x64-setup.exe"
    return {
        "current_version": "0.1.1",
        "channel": "local",
        "installer_path": str(installer),
        "installer_exists": installer.exists(),
        "recommended_strategy": "Local installer now; GitHub Releases updater once the repo/release flow is stable.",
        "next_steps": [
            "Keep every stable desktop build as a GitHub Release artifact.",
            "Show current version and latest available version in Settings.",
            "Download updates only from the configured repository release URL.",
            "Never overwrite local .env secrets during app updates.",
            "Keep backend migrations backwards compatible before enabling auto-update.",
        ],
    }


@api.get("/data/lse/applicability")
async def lse_applicability():
    from services import london_strategic_edge as lse_svc
    return {"provider": "london_strategic_edge", "uses": lse_svc.applicability_map()}


@api.get("/data/lse/health")
async def lse_health():
    from services import london_strategic_edge as lse_svc
    return await lse_svc.health_probe()


@api.get("/data/lse/candles/{symbol}")
async def lse_candles(
    symbol: str,
    timeframe: str = "1d",
    start: str | None = None,
    end: str | None = None,
    limit: int = 5000,
    order: str = "asc",
    dataset: str | None = None,
):
    from services import london_strategic_edge as lse_svc
    return await lse_svc.candles(symbol, timeframe, start, end, limit, order, dataset)


@api.get("/data/lse/options/{underlying}")
async def lse_options(
    underlying: str,
    option_type: str | None = None,
    min_dte: int | None = None,
    max_dte: int | None = None,
    limit: int = 5000,
):
    from services import london_strategic_edge as lse_svc
    return await lse_svc.options_chain(underlying, option_type, min_dte, max_dte, limit)


@api.get("/data/lse/options_flow")
async def lse_options_flow(
    underlying: str | None = None,
    option_type: str | None = None,
    min_premium: float | None = None,
    max_dte: int | None = None,
    limit: int = 5000,
):
    from services import london_strategic_edge as lse_svc
    return await lse_svc.options_flow(underlying, option_type, min_premium, max_dte, limit)


@api.get("/data/lse/ticker/{symbol}")
async def lse_ticker(symbol: str):
    from services import london_strategic_edge as lse_svc
    return await lse_svc.ticker_context(symbol)


@api.get("/data/lse/macro")
async def lse_macro(limit: int = 100):
    from services import london_strategic_edge as lse_svc
    return await lse_svc.macro_context(limit)


@api.get("/macro/overview")
async def macro_overview():
    from services import macro_intel
    return await macro_intel.overview()


@api.get("/data/free/catalog")
async def free_data_catalog():
    from services import free_data
    return {"sources": free_data.catalog()}


@api.get("/data/free/sec/companyfacts/{cik}")
async def free_data_sec_companyfacts(cik: str):
    from services import free_data
    return await free_data.sec_companyfacts(cik)


@api.get("/data/free/ticker/{ticker}")
async def free_data_ticker(ticker: str):
    from services import free_data

    t = ticker.upper()
    company_name = None
    try:
        fund = await risk_target.fetch_fundamentals(t)
        company_name = (fund or {}).get("name")
    except Exception:
        company_name = None
    return await free_data.ticker_free_data(t, company_name=company_name)


@api.get("/data/free/fred/latest/{series_id}")
async def free_data_fred_latest(series_id: str):
    from services import free_data
    return await free_data.fred_latest(series_id)


@api.post("/scan/run")
async def run_scan_now():
    scan = await scanner.run_scan(triggered_by="admin_dashboard")
    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        from services import telegram_events
        await telegram_events.dispatch_scan_report(scan)
    return scan


@api.post("/scan/dispatch")
async def scan_dispatch():
    scan = await scanner.latest_scan()
    if not scan:
        raise HTTPException(404, "no scan available")
    from services import telegram_events
    result = await telegram_events.dispatch_scan_report(scan)
    return {
        **result,
        "scan_finished_at": scan.get("finished_at"),
        "scan_started_at": scan.get("started_at"),
        "result_count": len(scan.get("results") or []),
        "top_tickers": [r.get("ticker") for r in (scan.get("results") or [])[:5]],
    }


@api.get("/scan/preview")
async def scan_preview():
    scan = await scanner.latest_scan()
    if not scan:
        return {"messages": [], "char_counts": [], "total_chars": 0}
    msgs = telegram_service.build_consolidated_messages(scan, title="CASE CAPITAL INTEL")
    return {"messages": msgs, "char_counts": [len(m) for m in msgs],
             "total_chars": sum(len(m) for m in msgs),
             "scan_finished_at": scan.get("finished_at"),
             "result_count": len(scan.get("results") or [])}


@api.post("/scan/gov")
async def scan_gov():
    return await scanner.run_gov_scan_only(triggered_by="admin_dashboard")


@api.get("/contracts/recent")
async def contracts_recent(limit: int = 5):
    """Legacy compact view used by the Dashboard 'recent gov contracts' tile."""
    return await usaspending.list_recent_contracts_for_tickers(limit=limit)


@api.get("/agency/{name}")
async def agency_awards(name: str, days: int = 30):
    return await usaspending.awards_for_agency(name, days=days, limit=50)


@api.get("/risk/{ticker}")
async def risk_breakdown(ticker: str):
    fund = await risk_target.fetch_fundamentals(ticker.upper())
    if not fund:
        raise HTTPException(404, "no data")
    risk = risk_target.compute_risk(fund, [], None, None, 0)
    return {"ticker": ticker.upper(), "fundamentals": fund, "risk": risk}


@api.get("/target/{ticker}")
async def target_breakdown(ticker: str):
    fund = await risk_target.fetch_fundamentals(ticker.upper())
    if not fund or not fund.get("price"):
        raise HTTPException(404, "no price data")
    targets = risk_target.compute_targets(fund, [], None)
    return {"ticker": ticker.upper(), "fundamentals": fund, "targets": targets}


@api.get("/compare/{t1}/{t2}")
async def compare_two(t1: str, t2: str):
    import asyncio as _aio
    f1, f2 = await _aio.gather(
        risk_target.fetch_fundamentals(t1.upper()),
        risk_target.fetch_fundamentals(t2.upper()),
    )
    return {
        t1.upper(): {
            "fundamentals": f1, "risk": risk_target.compute_risk(f1 or {}, [], None, None, 0),
            "targets": risk_target.compute_targets(f1 or {}, [], None),
        },
        t2.upper(): {
            "fundamentals": f2, "risk": risk_target.compute_risk(f2 or {}, [], None, None, 0),
            "targets": risk_target.compute_targets(f2 or {}, [], None),
        },
    }


@api.get("/squeeze/{ticker}")
async def squeeze_breakdown(ticker: str):
    from services.squeeze import compute_squeeze
    fund = await risk_target.fetch_fundamentals(ticker.upper())
    sq = await compute_squeeze(ticker.upper(), None, fund or {})
    return {"ticker": ticker.upper(), "squeeze": sq, "fundamentals": fund}


@api.get("/squeeze/leaderboard/top")
async def squeeze_leaderboard(limit: int = 10):
    from services.squeeze import squeeze_leaderboard as _lb
    return await _lb(limit=limit)


@api.get("/congress/recent")
async def congress_recent(days: int = 30):
    from services.congress import fetch_recent_buys
    return await fetch_recent_buys(days=days)


@api.get("/performance/summary")
async def performance_summary():
    from services import metrics, pnl_tracker
    proof = await metrics.summary()
    sig = await pnl_tracker.performance_by_signals()
    opt = await pnl_tracker.options_performance_summary()
    return {"signals": sig, "options": opt, "proof": proof}


@api.get("/signals/curve")
async def signals_curve(days: int = 90):
    """Daily P/L curve — Robinhood-style line. Avg % gain across all tracked
    signals on each day in the window."""
    from services import pnl_tracker
    return {"days": days, "curve": await pnl_tracker.daily_pnl_curve(days=days)}


@api.get("/signals/options_curve")
async def signals_options_curve(days: int = 90):
    """Robinhood-style OPTIONS P/L curve. Avg proxy % return across all
    tracked options positions on each day in the window. Uses
    (current_spot - entry_spot) * delta / premium."""
    from services import pnl_tracker
    return {"days": days, "curve": await pnl_tracker.daily_options_pnl_curve(days=days)}


@api.get("/signals/benchmark_curve")
async def signals_benchmark_curve(days: int = 90):
    """Total terminal performance versus SPY as the S&P 500 proxy."""
    from services import pnl_tracker
    return await pnl_tracker.daily_total_vs_spy_curve(days=days)


@api.post("/admin/refresh_prices")
async def admin_refresh_prices():
    """Refresh CURRENT prices for every tracked ticker (yfinance batch +
    Massive grouped backfill). Does NOT touch entry prices — those were
    captured intraday at scan time and are the truth-of-entry."""
    from services import pnl_tracker, pricer
    result = await pnl_tracker.refresh_current_prices_only()
    return {"ok": True, "massive_available": pricer.has_massive(), **result}


@api.post("/admin/restore_entry_prices")
async def admin_restore_entry_prices():
    """Restore first_seen_price from the original intraday scan_results
    record. Use if entry prices look stale or were overwritten."""
    from services import pnl_tracker
    return await pnl_tracker.restore_intraday_entry_prices()


@api.post("/admin/fill_missing_entry_prices")
async def admin_fill_missing_entry_prices():
    """Fill ONLY missing/null entry prices using Massive historical close."""
    from services import pnl_tracker
    return await pnl_tracker.refresh_all_entry_prices(force=False)


@api.get("/admin/price_source")
async def admin_price_source():
    from services import pricer
    return {
        "source": pricer.source_label(),
        "massive_available": pricer.has_massive(),
        "finnhub_available": pricer.has_finnhub(),
    }


@api.get("/price/history/{ticker}")
async def price_history(ticker: str, days: int = 140, force: bool = False):
    from services import pricer
    symbol = ticker.upper().strip()
    history = await pricer.get_history(symbol, days=max(5, min(int(days or 140), 900)), force=force)
    rows = [
        {"date": d, "close": v, "source": pricer.source_label()}
        for d, v in sorted(history.items())
        if v is not None
    ]
    return {"ok": bool(rows), "ticker": symbol, "days": days, "source": pricer.source_label(), "rows": rows}



@api.get("/signals/tracker")
async def signals_tracker(limit: int = 200):
    """Every signal we've ever surfaced, treated as 'bought immediately on
    signal'. Returns first-seen price + current price + gain since signal.
    Used by Performance page 'ALL BUY SIGNALS — DAILY P/L' section."""
    from services import pnl_tracker
    rows = await pnl_tracker.signals_tracker_summary(limit=limit)
    # Summary stats
    total = len(rows)
    with_gain = [r for r in rows if r.get("gain_pct") is not None]
    winners = [r for r in with_gain if r["gain_pct"] > 0]
    losers = [r for r in with_gain if r["gain_pct"] < 0]
    avg_gain = round(sum(r["gain_pct"] for r in with_gain) / len(with_gain), 2) if with_gain else None
    best = max(with_gain, key=lambda r: r["gain_pct"]) if with_gain else None
    worst = min(with_gain, key=lambda r: r["gain_pct"]) if with_gain else None
    return {
        "rows": rows,
        "total": total,
        "tracked": len(with_gain),
        "winners": len(winners),
        "losers": len(losers),
        "avg_gain_pct": avg_gain,
        "best": {"ticker": best["ticker"], "gain_pct": best["gain_pct"]} if best else None,
        "worst": {"ticker": worst["ticker"], "gain_pct": worst["gain_pct"]} if worst else None,
    }


@api.get("/options/{ticker}")
async def options_endpoint(ticker: str):
    from services import options_engine
    fund = await risk_target.fetch_fundamentals(ticker.upper())
    signals: list[str] = []
    stock = {
        "ticker": ticker.upper(), "signals": signals,
        "risk": risk_target.compute_risk(fund or {}, signals, None, None, 0),
        "squeeze": {}, "time_target": {"days_remaining": 30},
    }
    opts = await options_engine.analyze_ticker(stock)
    if not opts:
        raise HTTPException(404, "no options data")
    return {"ticker": ticker.upper(), "options": opts}


@api.get("/flow/{ticker}")
async def flow_endpoint(ticker: str):
    from services import options_engine
    return {"ticker": ticker.upper(), "flow": await options_engine.detect_unusual_flow(ticker.upper())}


@api.get("/iv/{ticker}")
async def iv_endpoint(ticker: str):
    from services import options_engine
    iv = await options_engine.calculate_iv_rank(ticker.upper())
    if iv.get("iv_rank") is None:
        raise HTTPException(404, "no IV data")
    stock = {"ticker": ticker.upper(), "signals": [], "time_target": {"days_remaining": 30}}
    crush = options_engine.assess_iv_crush_risk(stock, {"iv_rank": iv["iv_rank"]})
    return {"ticker": ticker.upper(), **iv, **crush}


@api.get("/spread/{ticker}")
async def spread_endpoint(ticker: str):
    from services import options_engine
    chain = await options_engine.get_options_data(ticker.upper())
    if not chain:
        raise HTTPException(404, "no options chain")
    return {
        "ticker": ticker.upper(),
        "bull": options_engine.build_spread(chain, "BULL"),
        "bear": options_engine.build_spread(chain, "BEAR"),
        "expiration": chain.get("expiration"),
    }


@api.get("/options/flow/today")
async def options_flow_today():
    """Latest scan tickers + their flow data for OPTIONS FLOW MONITOR panel."""
    s = await scanner.latest_scan()
    if not s:
        return {"rows": []}
    rows = []
    for r in s.get("results", []):
        opts = r.get("options") or {}
        flow = opts.get("flow") or {}
        if not flow:
            continue
        rows.append({
            "ticker": r["ticker"],
            "call_volume": flow.get("total_call_volume", 0),
            "put_volume": flow.get("total_put_volume", 0),
            "call_put_ratio": flow.get("call_put_ratio", 0),
            "flow_bias": flow.get("flow_bias", "NEUTRAL"),
            "iv_rank": opts.get("iv_rank"),
            "signal": "CALL_SWEEP" if flow.get("call_sweep")
                      else ("UNUSUAL" if flow.get("unusual_calls") or flow.get("unusual_puts") else "NORMAL"),
        })
    rows.sort(key=lambda r: r["call_volume"], reverse=True)
    return {"rows": rows}


@api.get("/options/low_iv")
async def options_low_iv():
    """LOW IV ENTRIES panel: today's stocks with iv_rank<35 and LONG_CALL strategy."""
    s = await scanner.latest_scan()
    if not s:
        return {"rows": []}
    rows = []
    for r in s.get("results", []):
        opts = r.get("options") or {}
        if opts.get("iv_rank") is None or opts.get("iv_rank") >= 35:
            continue
        if opts.get("strategy") != "LONG_CALL":
            continue
        ct = opts.get("contract") or {}
        rows.append({
            "ticker": r["ticker"],
            "iv_rank": opts.get("iv_rank"),
            "strategy": opts.get("strategy_name") or opts.get("strategy"),
            "premium": ct.get("premium"),
            "max_loss": ct.get("max_loss"),
            "catalyst_date": (r.get("time_target") or {}).get("target_date"),
        })
    rows.sort(key=lambda r: r["iv_rank"] or 100)
    return {"rows": rows}


@api.post("/backtest/seed")
async def backtest_seed():
    from services import backtest as _bt
    return await _bt.synthetic_congress_backtest()


@api.get("/backtest/summary")
async def backtest_summary():
    from services import backtest as _bt
    return await _bt.backtest_summary()


@api.post("/pnl/refresh")
async def pnl_refresh():
    from services import pnl_tracker
    sig = await pnl_tracker.refresh_due_returns()
    opt = await pnl_tracker.refresh_due_options_returns()
    return {"signals_refreshed": sig, "options_rows_refreshed": opt}


# ---------------- Learning Engine endpoints ----------------
@api.get("/learning/status")
async def learning_status():
    db = get_db()
    last_run = await db.learning_runs.find_one({}, {"_id": 0}, sort=[("run_at", -1)])
    weights = await db.learning_weights.find({}, {"_id": 0}).to_list(100)
    return {
        "last_run": last_run,
        "weights": weights,
        "next_run": "Sunday 02:00 ET",
    }


@api.get("/learning/combos")
async def learning_combos():
    db = get_db()
    rows = await db.combo_stats.find(
        {"trade_count": {"$gte": 2}}, {"_id": 0},
    ).sort("avg_return_30d", -1).to_list(100)
    return rows


@api.get("/learning/runs")
async def learning_runs(limit: int = 10):
    db = get_db()
    return await db.learning_runs.find({}, {"_id": 0}).sort("run_at", -1).to_list(limit)


@api.post("/learning/run")
async def learning_run():
    from services import learning_engine
    return await learning_engine.run_learning_cycle()


@api.post("/learning/reset")
async def learning_reset():
    from services import learning_engine
    n = await learning_engine.reset_weights()
    return {"reset": True, "weights_reset": n}


@api.get("/learning/preview")
async def learning_preview():
    """Dry-run preview of what the next cycle would do."""
    from services import learning_engine
    return await learning_engine.preview_learning_cycle()


@api.get("/learning/weight_history")
async def learning_weight_history(weight_key: str | None = None, limit: int = 500):
    from services import learning_engine
    return await learning_engine.weight_history(weight_key=weight_key, limit=limit)


@api.get("/learning/signal_stats")
async def learning_signal_stats():
    """Lifetime per-signal win-rate + return league table."""
    from services import learning_engine
    return await learning_engine.signal_lifetime_stats()


# ─────────── AXIOM v3.2 endpoints ───────────
@api.get("/v32/earnings_week")
async def v32_earnings_week(week_offset: int = 0):
    from services import earnings_engine
    db = get_db()
    last_scan = await db.scan_results.find_one({}, {"_id": 0, "results": 1},
                                                  sort=[("finished_at", -1)])
    scan_set = {r["ticker"] for r in (last_scan or {}).get("results", []) or []}
    return await earnings_engine.current_week_cached(scan_tickers=scan_set, week_offset=week_offset)


@api.post("/v32/earnings_divergences/dispatch")
async def v32_earnings_divergences_dispatch(week_offset: int = 0):
    from services import earnings_engine
    db = get_db()
    last_scan = await db.scan_results.find_one({}, {"_id": 0, "results": 1},
                                                  sort=[("finished_at", -1)])
    scan_set = {r["ticker"] for r in (last_scan or {}).get("results", []) or []}
    snapshot = await earnings_engine.current_week_cached(scan_tickers=scan_set, week_offset=week_offset)
    result = await telegram_service.dispatch_earnings_divergences(snapshot)
    return {"ok": result.get("messages_sent", 0) > 0, **result}


@api.get("/v32/lottery")
async def v32_lottery(days: int = 14, tier: str | None = None):
    from services import lottery
    picks = await lottery.recent_picks(days=days, tier=tier)
    track = await lottery.track_record()
    return {"picks": picks, "track_record": track}


@api.post("/v32/lottery/refresh")
async def v32_lottery_refresh():
    """Manually re-price every open lottery position + settle expired ones."""
    from services import lottery
    return await lottery.refresh_settlements()


@api.get("/v32/lottery/current")
async def v32_lottery_current():
    """Returns lottery picks attached to the latest scan."""
    db = get_db()
    last = await db.scan_results.find_one({}, {"_id": 0, "lottery_picks": 1, "finished_at": 1},
                                              sort=[("finished_at", -1)])
    if not last:
        return {"picks": [], "scan_at": None}
    return {"picks": last.get("lottery_picks") or [], "scan_at": last.get("finished_at")}


@api.get("/v32/dark_horse")
async def v32_dark_horse(days: int = 7):
    from services import dark_horse
    return await dark_horse.recent_alerts(days=days)


@api.get("/v32/x_factor")
async def v32_x_factor(days: int = 7):
    from services import x_factor
    return await x_factor.recent_alerts(days=days)


@api.get("/v32/x_factor/discoveries")
async def v32_x_factor_discoveries(days: int = 7):
    """Tickers OUTSIDE the scan universe that hit Yahoo trending or Barchart
    unusual options — candidates to add to the universe."""
    from services import x_factor
    return await x_factor.recent_discoveries(days=days)


@api.get("/v32/sentiment/{ticker}")
async def v32_sentiment(ticker: str):
    from services import x_factor
    twits, trends = await asyncio.gather(
        x_factor.fetch_stocktwits(ticker),
        x_factor.fetch_google_trends(ticker),
        return_exceptions=True,
    )
    return {
        "ticker": ticker.upper(),
        "stocktwits": None if isinstance(twits, Exception) else twits,
        "google_trends": None if isinstance(trends, Exception) else trends,
    }


@api.get("/v32/macro")
async def v32_macro(days_ahead: int = 14):
    from services import macro_pulse
    events = await macro_pulse.upcoming_events(days_ahead=days_ahead)
    return {
        "events": events,
        "imminent_warnings": [
            e for e in events if e.get("is_imminent") and e.get("warns_sectors")
        ],
        "fred_available": macro_pulse.has_fred(),
    }


@api.get("/v32/conviction")
async def v32_conviction():
    from services import conviction
    top3 = await conviction.latest_top3()
    locks = await conviction.recent_locks(days=14)
    return {"top3": top3, "narrative_locks_14d": locks}


@api.get("/ticker/{ticker}")
async def ticker_detail(ticker: str):
    """Full deep-dive for a single ticker — used by /ticker/:ticker frontend page."""
    db = get_db()
    t = ticker.upper()
    latest_scan = await db.scan_results.find_one(
        {"results.ticker": t}, {"_id": 0}, sort=[("finished_at", -1)],
    )
    pnl_record = await db.signal_performance.find_one(
        {"ticker": t}, {"_id": 0}, sort=[("ts", -1)],
    )
    first_seen = await db.signal_first_seen.find_one(
        {"ticker": t}, {"_id": 0},
    )
    fund = await risk_target.fetch_fundamentals(t)
    q = await fetch_quote(t)
    result: dict[str, Any] = {"ticker": t}
    if latest_scan:
        for r in latest_scan.get("results", []) or []:
            if r.get("ticker") == t:
                result.update(r)
                break
    if pnl_record:
        result["pnl_record"] = pnl_record
        result["first_found"] = pnl_record.get("date")
    times_found = await db.signal_performance.count_documents({"ticker": t})
    result["times_found"] = times_found
    result["fundamentals"] = fund or {}
    result["price"] = (q or {}).get("price")
    result["change_pct"] = None
    if q and q.get("previous_close") and q.get("price"):
        try:
            result["change_pct"] = round(
                (q["price"] - q["previous_close"]) / q["previous_close"] * 100, 2
            )
        except Exception:
            pass
    if first_seen:
        first_price = first_seen.get("first_seen_price")
        current_price = result.get("price")
        result["first_alert"] = {
            "date": first_seen.get("first_seen_date"),
            "price": first_price,
            "signals": first_seen.get("first_signals") or [],
            "signal_score": first_seen.get("first_signal_score"),
        }
        result["change_since_first_alert_pct"] = None
        if first_price and current_price and first_price > 0:
            try:
                result["change_since_first_alert_pct"] = round(
                    (current_price - first_price) / first_price * 100.0, 2
                )
            except Exception:
                pass
    return result


@api.get("/fy/status")
async def fy_status():
    from services.time_target import fiscal_year_multiplier_active, fy_days_remaining
    return {
        "fy_multiplier_active": fiscal_year_multiplier_active(),
        "days_to_fy_end": fy_days_remaining(),
        "multiplier": 1.5 if fiscal_year_multiplier_active() else 1.0,
    }


@api.get("/scan/latest")
async def scan_latest():
    s = await scanner.latest_scan()
    return s or {"results": [], "pre_filter_passed": 0, "raw_counts": {}}


@api.get("/kronos/forecast")
async def kronos_forecast(persist: bool = True):
    from services import kronos
    return await kronos.forecast(persist=persist)


@api.get("/kronos/market_forecast")
async def kronos_market_forecast():
    from services import kronos
    return await kronos.market_forecast()


@api.get("/kronos/disagreements")
async def kronos_disagreements(limit: int = 200):
    from services import kronos
    return await kronos.disagreement_performance(limit=limit)


@api.get("/kronos/calendar")
async def kronos_calendar(year: int, month: int):
    from services import kronos
    return await kronos.calendar_month(year=year, month=month)


@api.get("/kronos/battle_card/{ticker}")
async def kronos_battle_card(ticker: str):
    from services import kronos
    return await kronos.battle_card(ticker)


@api.get("/case_court/latest")
async def case_court_latest(limit: int = 30, session_id: str | None = None):
    from services import case_court
    return await case_court.latest(limit=limit, session_id=session_id)


@api.get("/case_court/sessions")
async def case_court_sessions(limit: int = 12):
    from services import case_court
    return await case_court.sessions(limit=limit)


@api.get("/case_court/record")
async def case_court_record(days: int = 30):
    from services import case_court
    return await case_court.record(days=days)


@api.get("/case_court/trial/{ticker}")
async def case_court_trial(ticker: str):
    from services import case_court
    return await case_court.trial(ticker)


@api.post("/case_court/refresh")
async def case_court_refresh(limit: int = 30):
    from services import case_court
    return await case_court.run_trials(limit=limit, persist=True)


@api.post("/kronos/telegram/morning")
async def kronos_telegram_morning(force: bool = False):
    from services import kronos
    return await kronos.dispatch_morning_forecast(force=force)


@api.get("/research/dashboard")
async def research_dashboard(limit_scans: int = 160):
    from services import research_lab
    return await research_lab.dashboard(limit_scans=limit_scans)


@api.post("/research/refresh")
async def research_refresh(limit_scans: int = 180):
    from services import research_lab
    return await research_lab.refresh_snapshot(limit_scans=limit_scans, triggered_by="manual")


@api.get("/scan/history")
async def scan_history(limit: int = 10):
    db = get_db()
    items = await db.scan_results.find({}, {"_id": 0, "results": 0}).sort(
        "finished_at", -1
    ).to_list(limit)
    return items


@api.get("/activity")
async def activity(limit: int = 50):
    db = get_db()
    items = await db.activity_log.find({}, {"_id": 0}).sort("ts", -1).to_list(limit)
    return items


@api.get("/audit_logs")
async def audit_logs(limit: int = 250, source: str | None = None, event_type: str | None = None, ticker: str | None = None):
    from services import audit_logs as svc
    return await svc.list_events(limit=limit, source=source, event_type=event_type, ticker=ticker)


@api.get("/watchlist")
async def watchlist_list():
    db = get_db()
    items = await db.watchlist.find({}, {"_id": 0}).sort("added_at", -1).to_list(500)
    # Attach quotes
    out = []
    for it in items:
        q = await fetch_quote(it["ticker"])
        out.append({**it, "price": (q or {}).get("price"), "name": (q or {}).get("name")})
    return out


@api.post("/watchlist")
async def watchlist_add(item: WatchlistItem):
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "admin")
    db = get_db()
    ticker = item.ticker.upper().lstrip("$")
    await db.watchlist.update_one(
        {"ticker": ticker, "chat_id": chat_id},
        {"$set": {"ticker": ticker, "chat_id": chat_id,
                   "added_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )
    await log_activity(f"Watchlist add ${ticker} (admin)", "info")
    return {"ok": True, "ticker": ticker}


@api.delete("/watchlist/{ticker}")
async def watchlist_delete(ticker: str):
    db = get_db()
    res = await db.watchlist.delete_many({"ticker": ticker.upper()})
    return {"ok": True, "deleted": res.deleted_count}


@api.get("/alerts")
async def alerts_list():
    db = get_db()
    items = await db.alerts.find({"triggered": False}, {"_id": 0}).sort("created_at", -1).to_list(500)
    return items


@api.post("/alerts")
async def alerts_add(item: AlertItem):
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "admin")
    db = get_db()
    ticker = item.ticker.upper().lstrip("$")
    doc = {
        "ticker": ticker,
        "target_price": float(item.target_price),
        "chat_id": chat_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "triggered": False,
    }
    await db.alerts.insert_one(dict(doc))
    await log_activity(f"Alert set ${ticker} @ ${item.target_price}", "info")
    return {"ok": True, **doc}


@api.delete("/alerts/{ticker}")
async def alerts_delete(ticker: str):
    db = get_db()
    res = await db.alerts.delete_many({"ticker": ticker.upper(), "triggered": False})
    return {"ok": True, "deleted": res.deleted_count}


@api.get("/quote/{ticker}")
async def quote(ticker: str):
    q = await fetch_quote(ticker)
    if not q:
        raise HTTPException(404, "quote not available")
    return q


@api.post("/analyze/{ticker}")
async def analyze(ticker: str):
    q = await fetch_quote(ticker)
    a = await claude_service.analyze_single(ticker, context={"quote": q})
    if not a:
        raise HTTPException(500, "analysis failed (check ANTHROPIC_API_KEY)")
    return {"analysis": a, "quote": q}


# ---------- Telegram webhook ----------
@api.post("/telegram/webhook")
async def telegram_webhook(req: Request, bg: BackgroundTasks):
    webhook_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
    if webhook_secret:
        provided = req.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(provided, webhook_secret):
            raise HTTPException(status_code=403, detail="invalid telegram webhook secret")
    try:
        update = await req.json()
    except Exception:
        return {"ok": False}
    bg.add_task(telegram_service.handle_update, update)
    return {"ok": True}


@api.post("/telegram/setup")
async def telegram_setup():
    base = os.environ.get("PUBLIC_BASE_URL")
    if not base:
        raise HTTPException(400, "PUBLIC_BASE_URL not set")
    res = await telegram_service.register_webhook(base)
    return res


@api.get("/telegram/info")
async def telegram_info():
    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        return {"configured": False}
    import httpx
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    async with httpx.AsyncClient(timeout=15.0) as client:
        me = await client.get(f"https://api.telegram.org/bot{token}/getMe")
        wh = await client.get(f"https://api.telegram.org/bot{token}/getWebhookInfo")
    return {"configured": True, "me": me.json(), "webhook": wh.json()}


# ─────── Pipeline Criteria (Settings) ───────
@api.get("/admin/pipeline_criteria")
async def pipeline_criteria():
    """Two-box descriptor for the Settings page.
    LEFT: static pre-filter rules.
    RIGHT: live final-screener weights from the learning engine."""
    from services import learning_engine
    weights = await learning_engine.get_weights()
    # Static description of the pre-filter rules from scanner._aggregate +
    # _finalize_signals_and_filter. Kept hard-coded because they ARE the
    # source-of-truth in code and there is no DB row to read.
    pre_filter = [
        {"rule": "2+ signals required", "detail": "Ticker must hit ≥2 distinct signal types before it's enriched"},
        {"rule": "Insider cluster buy", "detail": "≥2 insiders bought in last 90d (OpenInsider)"},
        {"rule": "High short interest", "detail": "Short float ≥ 10% (Finviz)"},
        {"rule": "Upcoming earnings", "detail": "Earnings within next 14 days (Finviz)"},
        {"rule": "Gov contract surge", "detail": "30d award total ≥ 1.4× prior 90d avg (USASpending)"},
        {"rule": "Congressional buy", "detail": "Recent Congress purchase (curated + Quiver scrape)"},
        {"rule": "Unusual options flow", "detail": "Promoted to signal AFTER pre-filter passes; adds to score"},
        {"rule": "Concentration win", "detail": "Single award > $20M to mkt-cap < $2B issuer"},
        {"rule": "Momentum stack", "detail": "≥3 distinct agencies in 30d, cumulative > $20M"},
        {"rule": "Budget surge", "detail": "Agency monthly obligations ≥ 1.5× 3-mo avg"},
    ]
    final_screener = [
        {"key": k, "weight": v, "description": _WEIGHT_DESCRIPTIONS.get(k, "")}
        for k, v in sorted(weights.items(), key=lambda x: -x[1])
    ]
    return {
        "pre_filter": pre_filter,
        "final_screener": final_screener,
        "axiom_score_formula": "Case Score = Σ (signal × live_weight) + bonuses (UNUSUAL_FLOW +2, CALL_SWEEP +3, NARRATIVE_LOCK +20)",
    }


_WEIGHT_DESCRIPTIONS = {
    "insider_cluster_buy":   "Insiders buying in concert (3+ in 90d, > $500k)",
    "high_short_interest":   "Short float > 10% — squeeze fuel",
    "CONTRACT_SURGE":        "30d gov spend ≥ 1.4× prior 90d avg, > $10M",
    "CONGRESSIONAL_BUY":     "Congress disclosure purchase in last 30 days",
    "NEW_WINNER":            "First award from agency in 12 months, > $5M",
    "CONCENTRATION_WIN":     "Single award > $20M to mkt-cap < $2B issuer",
    "MOMENTUM_STACK":        "≥3 agencies in 30d, cumulative > $20M",
    "BUDGET_SURGE":          "Agency monthly obligations ≥ 1.5× 3-mo avg",
    "UNUSUAL_FLOW":          "Options volume ≥ 3× OI on any single strike",
    "CALL_SWEEP":            "Multi-exchange aggressive call sweep detected",
    "upcoming_earnings":     "Earnings within 14 days",
    "squeeze_bonus":         "Squeeze score ≥ 65 triggers additional weight",
    "committee_match_bonus": "Congressional buyer sits on directly relevant committee",
}


# ─────── PHARMA endpoints ───────
@api.post("/pharma/scan")
async def pharma_scan_endpoint():
    from services import pharma
    return await pharma.run_pharma_scan(triggered_by="api")


@api.get("/pharma/pdufa")
async def pharma_pdufa(days: int = 90):
    from services import pharma
    rows = await pharma.get_pdufa_within_days(days=days)
    return {"results": rows, "fetched_at": datetime.now(timezone.utc).isoformat()}


@api.get("/pharma/active")
async def pharma_active():
    from services import pharma
    return {"plays": await pharma.get_active_plays()}


@api.get("/pharma/track_record")
async def pharma_track_record():
    from services import pharma
    return await pharma.track_record()


class PharmaManualPlay(BaseModel):
    ticker: str
    drug: str | None = None
    pdufa_date: str | None = None
    entry_price: float | None = None
    notes: str | None = None


@api.post("/pharma/play")
async def pharma_add_manual_play(p: PharmaManualPlay):
    from services import pharma
    return await pharma.add_manual_play(p.ticker, p.drug, p.pdufa_date, p.entry_price, p.notes)


@api.post("/pharma/close")
async def pharma_close_play(ticker: str, pdufa_date: str, exit_price: float | None = None):
    from services import pharma
    ok = await pharma.close_play(ticker, pdufa_date, exit_price)
    return {"closed": ok}


# ─────── CONTRACTS endpoints ───────
@api.get("/contracts")
async def contracts_list(days: int = 90, min_amount: float = 1_000_000,
                          agency: str | None = None):
    rows = await usaspending.list_prime_contracts(days=days, min_amount=min_amount,
                                                    agency=agency)
    return {"contracts": rows, "fetched_at": datetime.now(timezone.utc).isoformat(),
             "filters": {"days": days, "min_amount": min_amount, "agency": agency}}


@api.get("/contracts/sub_awards")
async def contracts_sub_awards(award_id: str):
    """Returns subcontractors under a prime award. Cached 24h per prime."""
    db = get_db()
    cached = await db.subaward_cache.find_one({"_id": award_id})
    if cached:
        try:
            age_hours = (datetime.now(timezone.utc) -
                          datetime.fromisoformat(cached["fetched_at"])).total_seconds() / 3600
        except Exception:
            age_hours = 9999
        if age_hours < 24:
            return {"sub_awards": cached["sub_awards"], "cached": True}
    rows = await usaspending.fetch_sub_awards(award_id)
    await db.subaward_cache.update_one(
        {"_id": award_id},
        {"$set": {"sub_awards": rows,
                   "fetched_at": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )
    return {"sub_awards": rows, "cached": False}


@api.get("/gov/intel")
async def gov_intel_layer(ticker: str | None = None, recipient: str | None = None,
                          agency: str | None = None, description: str | None = None):
    from services import gov_intel
    return await gov_intel.contract_layer(
        ticker=ticker,
        recipient=recipient,
        agency=agency,
        description=description,
    )



# ---------- App wiring ----------
# (Router include moved to end of file so v5.0 endpoints register)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup():
    from services import learning_engine, pnl_tracker
    db_ready = True
    try:
        await learning_engine.ensure_weights_exist()
    except Exception as e:
        db_ready = False
        logger.warning("learning weights init skipped; MongoDB unavailable: %s", e)
    try:
        if db_ready:
            await pnl_tracker.ensure_first_seen_backfill()
    except Exception as e:
        logger.warning("first_seen backfill failed: %s", e)
    scheduler_enabled = os.environ.get("ENABLE_SCHEDULER", "true").strip().lower() not in {"0", "false", "no", "off"}
    if scheduler_enabled:
        scheduler.start_scheduler()
    else:
        logger.warning("Scheduler disabled by ENABLE_SCHEDULER")
    base = os.environ.get("PUBLIC_BASE_URL")
    if os.environ.get("TELEGRAM_BOT_TOKEN") and base:
        try:
            res = await telegram_service.register_webhook(base)
            logger.info("Telegram webhook setup: %s", res)
        except Exception as e:
            logger.warning("Webhook setup failed: %s", e)
    try:
        if db_ready:
            await log_activity("Server started", "info")
    except Exception as e:
        logger.warning("startup activity log skipped: %s", e)
    # Trade Floor Engine — one-time fork from Signal Engine
    try:
        if db_ready:
            from services import trade_floor_learning
            await trade_floor_learning.initialize_from_signal_engine()
    except Exception as e:
        logger.warning("Trade Floor Engine init: %s", e)


# ─────── SEC FILINGS ───────
@api.post("/sec/poll")
async def sec_poll():
    from services import sec_filings
    return await sec_filings.poll_edgar_filings()


@api.get("/sec/filings")
async def sec_filings_list(days: int = 7, form: str | None = None):
    from services import sec_filings
    rows = await sec_filings.recent_filings(days=days, form=form)
    return {"filings": rows, "count": len(rows)}


@api.get("/sec/battle_card/{ticker}")
async def sec_battle_card(ticker: str, limit: int = 25):
    from services import sec_filings
    return await sec_filings.battle_card(ticker=ticker, limit=limit)


@api.get("/sec/edgartools/{ticker}")
async def sec_edgartools_snapshot(ticker: str):
    from services import edgartools_bridge
    return await edgartools_bridge.company_snapshot(ticker=ticker)


# ─────── TRADE FLOOR ───────
@api.get("/trade_floor/account")
async def tf_account():
    from services import trade_floor
    return {"account": await trade_floor.get_account(),
             "alpaca_configured": bool(
                os.environ.get("APCA_API_KEY_ID") and
                os.environ.get("APCA_API_SECRET_KEY"))}


@api.get("/trade_floor/positions")
async def tf_positions():
    from services import trade_floor
    db_pos = await trade_floor.open_positions_view()
    live = await trade_floor.list_positions()
    last_log = await trade_floor.latest_scan_log()
    return {"db_positions": db_pos, "live_alpaca": live, "last_scan_log": last_log}


@api.get("/trade_floor/regime")
async def tf_regime():
    from services import trade_floor
    return await trade_floor.regime_status()


@api.get("/trade_floor/orders")
async def tf_orders(status: str = "all", limit: int = 50):
    from services import trade_floor
    return {"orders": await trade_floor.list_orders(status=status, limit=limit)}


@api.post("/trade_floor/close")
async def tf_close(ticker: str):
    from services import trade_floor
    res = await trade_floor.close_position(ticker)
    return {"closed": res is not None, "result": res}


@api.post("/trade_floor/sync")
async def tf_sync():
    from services import trade_floor
    return await trade_floor.sync_positions_and_close_settled()


@api.post("/trade_floor/execute_pm_ticker")
async def tf_execute_pm_ticker(ticker: str):
    from services import trade_floor
    from services.db import get_db
    t = ticker.upper()
    scan = await get_db().scan_results.find_one({}, {"_id": 0}, sort=[("finished_at", -1)])
    rows = [r for r in ((scan or {}).get("results") or []) if str(r.get("ticker") or "").upper() == t]
    if not rows:
        return {"ok": False, "reason": "ticker_not_in_latest_scan", "ticker": t}
    result = await trade_floor.evaluate_and_execute(rows, only_tickers={t})
    return {"ok": bool(result.get("executed")), "ticker": t, "scan_finished_at": (scan or {}).get("finished_at"), **result}


@api.post("/trade_floor/execution_probe")
async def tf_execution_probe(ticker: str = "AAPL", notional: float = 1.0, place_order: bool = False):
    from services import trade_floor
    return await trade_floor.execution_probe(ticker=ticker, notional=notional, place_order=place_order)


@api.get("/trade_floor/history")
async def tf_history():
    from services import trade_floor
    return {"trades": await trade_floor.trade_history()}


@api.get("/trade_floor/journal")
async def tf_journal(date: str | None = None):
    from services import trade_floor
    return {"journal": await trade_floor.daily_journal(date)}


@api.get("/trade_journal/overview")
async def trade_journal_overview(limit_scans: int = 120, limit_trades: int = 200):
    from services import trade_journal
    return await trade_journal.overview(limit_scans=limit_scans, limit_trades=limit_trades)


@api.post("/trade_floor/manual_send")
async def tf_manual_send(ticker: str, risk_dollars: float, source: str = "manual"):
    """Send a ticker straight to the Trade Floor with EXACT risk amount —
    no scan gates, no recalc. Still enforces: (a) dedup vs Alpaca open
    positions AND open orders; (b) limit DAY order at current ask;
    (c) absolute risk hard cap by score tier; (d) analytical stop engine."""
    from services import trade_floor, stop_engine, trade_floor_learning as tfle
    if not trade_floor._alpaca_ready():
        return {"ok": False, "reason": "alpaca_not_configured"}
    ticker = ticker.upper()
    # Dedup
    held = {p.get("symbol", "").upper() for p in await trade_floor.list_positions()}
    pending = {o.get("symbol", "").upper() for o in await trade_floor.list_orders(status="open")}
    if ticker in held:
        return {"ok": False, "reason": "ticker_already_open_in_alpaca"}
    if ticker in pending:
        return {"ok": False, "reason": "ticker_has_pending_open_order"}
    # Entry price = Alpaca ask
    ask = await trade_floor.get_latest_ask(ticker)
    if not ask:
        from services import pricer
        ask = await pricer.get_latest_close(ticker)
    if not ask or ask <= 0:
        return {"ok": False, "reason": "no_ask_quote"}
    # Stop via analytical engine (assume score 30 if not provided so manual
    # plays land in the middle tier of stop adjustment)
    stop_calc = await stop_engine.compute_stop(
        ticker=ticker, entry_price=ask,
        signal_combo=[source.upper()], score=30, hold_window_days=30,
        sector=None, instrument="fractional",
    )
    # Hard cap by score (manual = treated as 30-49 tier unless explicitly higher)
    hard_cap = trade_floor.hard_cap_for(30, "fractional")
    notional = round(min(float(risk_dollars), hard_cap), 2)
    if notional < 1.0:
        return {"ok": False, "reason": f"notional_too_small (cap=${hard_cap})"}
    cli = f"tf-manual-{ticker}-{int(datetime.now(timezone.utc).timestamp())}"
    order = await trade_floor.submit_fractional_limit_buy(
        ticker, notional, limit_price=round(ask, 4), client_order_id=cli,
    )
    if order:
        from services.db import get_db, stamped
        trade_doc = stamped({
            "client_order_id": cli,
            "order_id": order.get("id"),
            "ticker": ticker,
            "entry_score": None,
            "trade_score": 30,
            "signal_combo": [source.upper()],
            "instrument": "fractional",
            "notional": notional,
            "hard_cap_applied": hard_cap,
            "limit_price": round(ask, 4),
            "entry_price_ref": round(ask, 4),
            "stop_price": stop_calc["stop_price"],
            "stop_pct": stop_calc["stop_pct"],
            "stop_breakdown": stop_calc["breakdown"],
            "hold_window_days": 30,
            "status": "OPEN",
            "fill_status": "PENDING",
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "source": source,
        })
        await get_db().tf_trades.insert_one(trade_doc)
        try:
            await tfle.log_trade_initiation(trade_doc)
        except Exception:
            pass
    return {"ok": order is not None, "notional": notional,
             "stop": stop_calc["stop_price"], "limit_price": round(ask, 4),
             "hard_cap": hard_cap, "order": order}


@api.post("/trade_floor/sweep_stale_orders")
async def tf_sweep_stale():
    """Manually trigger the 24h stale-order cancel sweep."""
    from services import trade_floor
    return await trade_floor.cancel_stale_orders(max_age_hours=24)


@api.post("/admin/reset_learning_engines")
async def reset_learning_engines(confirm: str = ""):
    """Wipe all learned state on BOTH engines so they restart from the
    rebalanced DEFAULT_WEIGHTS. Requires ?confirm=RESET to protect against
    accidental firing."""
    if confirm != "RESET":
        return {"ok": False, "hint": "Pass ?confirm=RESET to actually wipe."}
    from services.db import get_db
    db = get_db()
    wiped: dict[str, int] = {}
    collections = [
        "combo_stats",           # main learning engine per-combo stats
        "tf_weights", "tf_combo_stats", "tf_risk_tiers",
        "tf_stop_engine", "tf_phase_engine", "tf_entry_engine",
        "tf_trade_decisions", "tf_recalibration_log",
    ]
    for c in collections:
        r = await db[c].delete_many({})
        wiped[c] = r.deleted_count
    # Re-seed by calling the proper reset function on the main engine
    from services import trade_floor_learning as tfle
    from services import learning_engine as sle
    reset_n = await sle.reset_weights()   # overwrites learning_weights to defaults
    wiped["learning_weights_reset"] = reset_n
    await tfle.initialize_from_signal_engine()
    return {"ok": True, "wiped": wiped,
             "note": "Both learning engines reset. Main engine reseeded from new "
                        "DEFAULT_WEIGHTS; TF engine inherited from main baseline."}


@api.post("/trade_floor/process_phases")
async def tf_process_phases():
    """Manually trigger the three-phase exit processor (normally runs every
    15 min via position_monitor)."""
    from services import trade_floor_phases
    return await trade_floor_phases.process_phase_exits()


@api.get("/trade_floor/phase_outcomes")
async def tf_phase_outcomes(limit: int = 60):
    """Return the closed-trade phase records used by the learning engine."""
    from services.db import get_db
    docs = await get_db().tf_phase_outcomes.find({}, {"_id": 0}) \
                  .sort("closed_at", -1).to_list(limit)
    return {"outcomes": docs, "count": len(docs)}


# ─────── Trade Floor Learning Engine ───────
@api.get("/trade_floor/engine/status")
async def tf_engine_status():
    from services import trade_floor_learning
    return await trade_floor_learning.status()


@api.get("/trade_floor/engine/combos")
async def tf_engine_combos():
    from services import trade_floor_learning
    return {"combos": await trade_floor_learning.combo_stats()}


@api.post("/trade_floor/engine/recalibrate")
async def tf_engine_recal():
    from services import trade_floor_learning
    return await trade_floor_learning.recalibrate()


@api.get("/portfolio_manager/latest")
async def portfolio_manager_latest(equity: float | None = None, mode: str = "AUTO", ruleset_id: str | None = None):
    from services import portfolio_manager
    return await portfolio_manager.latest_portfolio_plan(equity=equity, mode=mode, ruleset_id=ruleset_id)


@api.get("/portfolio_manager/learning/status")
async def portfolio_manager_learning_status(limit_scans: int = 120):
    from services import pm_learning
    return await pm_learning.status(limit_scans=limit_scans)


@api.get("/portfolio_manager/backtest")
async def portfolio_manager_backtest(
    limit_scans: int = 120,
    equity: float = 1000.0,
    mode: str = "BALANCED",
    max_position_pct: float | None = None,
    max_single_name_risk_pct: float | None = None,
    max_gross_deployment_pct: float | None = None,
    accumulate_score: float | None = None,
    accumulate_rr: float | None = None,
    starter_score: float | None = None,
    starter_rr: float | None = None,
    watch_score: float | None = None,
    ruleset_id: str | None = None,
):
    from services import pm_backtest
    overrides = {
        "max_position_pct": max_position_pct,
        "max_single_name_risk_pct": max_single_name_risk_pct,
        "max_gross_deployment_pct": max_gross_deployment_pct,
        "accumulate_score": accumulate_score,
        "accumulate_rr": accumulate_rr,
        "starter_score": starter_score,
        "starter_rr": starter_rr,
        "watch_score": watch_score,
    }
    return await pm_backtest.run(
        limit_scans=limit_scans,
        equity=equity,
        mode=mode,
        profile_override=overrides,
        ruleset_id=ruleset_id,
    )


@api.get("/portfolio_manager/options/latest")
async def portfolio_manager_options_latest():
    from services import options_desk
    return await options_desk.candidates()


@api.get("/portfolio_manager/options/learning/status")
async def portfolio_manager_options_learning_status(limit: int = 200):
    from services import options_desk
    return await options_desk.learning_status(limit=limit)


@api.get("/portfolio_manager/options/backtest")
async def portfolio_manager_options_backtest(limit_scans: int = 120):
    from services import options_desk
    return await options_desk.backtest(limit_scans=limit_scans)


@api.get("/options_desk/account")
async def options_desk_account():
    from services import options_desk
    return await options_desk.account()


@api.get("/options_desk/positions")
async def options_desk_positions():
    from services import options_desk
    return await options_desk.positions()


@api.get("/options_desk/orders")
async def options_desk_orders(status: str = "all", limit: int = 100):
    from services import options_desk
    return await options_desk.orders(status=status, limit=limit)


@api.get("/options_desk/candidates")
async def options_desk_candidates():
    from services import options_desk
    return await options_desk.candidates()


@api.post("/options_desk/candidates/refresh")
async def options_desk_refresh():
    from services import options_desk
    return await options_desk.build_candidates(persist=True)


class OptionsDeskExecutePayload(BaseModel):
    candidate_id: str
    qty: int | None = None
    limit_price: float | None = None


@api.post("/options_desk/execute")
async def options_desk_execute(payload: OptionsDeskExecutePayload):
    from services import options_desk
    return await options_desk.execute(
        candidate_id=payload.candidate_id,
        qty=payload.qty,
        limit_price=payload.limit_price,
    )


@api.post("/options_desk/auto_execute_latest")
async def options_desk_auto_execute_latest(limit: int | None = None):
    from services import options_desk
    return await options_desk.auto_execute_latest(limit=limit)


class OptionsDeskClosePayload(BaseModel):
    symbol: str
    qty: int | None = None


@api.post("/options_desk/close")
async def options_desk_close(payload: OptionsDeskClosePayload):
    from services import options_desk
    return await options_desk.close(symbol=payload.symbol, qty=payload.qty)


@api.post("/options_desk/sync")
async def options_desk_sync():
    from services import options_desk
    return await options_desk.sync()


@api.get("/options_desk/trades")
async def options_desk_trades(limit: int = 100, sync_live: bool = True):
    from services import options_desk
    return await options_desk.trades(limit=limit, sync_live=sync_live)


@api.get("/options_desk/leaps")
async def options_desk_leaps(limit_candidates: int = 12):
    from services import options_desk
    return await options_desk.leaps_sleeve(limit_candidates=limit_candidates)


@api.post("/options_desk/leaps/refresh")
async def options_desk_leaps_refresh(limit_candidates: int = 12):
    from services import options_desk
    return await options_desk.leaps_sleeve(limit_candidates=limit_candidates)


@api.post("/options_desk/fills/sync")
async def options_desk_fills_sync():
    from services import options_desk
    return await options_desk.sync_fills()


@api.post("/options_desk/reports/daily/dispatch")
async def options_desk_daily_report_dispatch(force: bool = False):
    from services import options_desk
    return await options_desk.dispatch_options_daily_report(force=force)


@api.post("/options_desk/reports/weekly/dispatch")
async def options_desk_weekly_report_dispatch(force: bool = False):
    from services import options_desk
    return await options_desk.dispatch_options_weekly_report(force=force)


@api.get("/options_desk/risk")
async def options_desk_risk():
    from services import options_desk
    return await options_desk.latest_risk_check()


@api.post("/options_desk/risk/check")
async def options_desk_risk_check():
    from services import options_desk
    return await options_desk.monitor_open_positions(enforce_hard_stop=True)


@api.get("/options_desk/marks/audit")
async def options_desk_marks_audit_latest():
    from services import options_desk
    return await options_desk.latest_mark_audit()


@api.post("/options_desk/marks/audit")
async def options_desk_marks_audit():
    from services import options_desk
    return await options_desk.mark_accuracy_audit(persist=True)


@api.get("/portfolio_manager/rulesets")
async def portfolio_manager_rulesets():
    from services import pm_rules
    return await pm_rules.list_rulesets()


@api.post("/portfolio_manager/rulesets")
async def portfolio_manager_create_ruleset(payload: dict):
    from services import pm_rules
    return await pm_rules.create_ruleset(
        name=payload.get("name") or "Custom PM Rules",
        description=payload.get("description") or "",
        mode_overrides=payload.get("mode_overrides") or {},
        activate=bool(payload.get("activate")),
    )


@api.post("/portfolio_manager/rulesets/{ruleset_id}/activate")
async def portfolio_manager_activate_ruleset(ruleset_id: str):
    from services import pm_rules
    return await pm_rules.activate_ruleset(ruleset_id)


@api.post("/portfolio_manager/ratchet/process")
async def portfolio_manager_ratchet_process():
    from services import pm_ratchet
    return await pm_ratchet.process_open_ratchets()


@api.get("/portfolio_manager/ratchet/events")
async def portfolio_manager_ratchet_events(limit: int = 50):
    from services import pm_ratchet
    return await pm_ratchet.recent_events(limit=limit)


@app.on_event("shutdown")
async def on_shutdown():
    scheduler.shutdown_scheduler()

# v5.0 — include router at end so all endpoints register
# ─────── v5.1 — Lottery dedicated scan + manual entry + settle ───────
@api.post("/lottery/scan")
async def lottery_dedicated_scan():
    from services import lottery
    return await lottery.run_dedicated_lottery_scan()


@api.get("/lottery/board")
async def lottery_league_board():
    from services import lottery
    return await lottery.board()


@api.get("/lottery/candidates")
async def lottery_league_candidates():
    from services import lottery
    return await lottery.league_candidates()


@api.get("/lottery/tickets")
async def lottery_league_tickets(active_only: bool = False):
    from services import lottery
    return await lottery.league_tickets(active_only=active_only)


class LotteryTicketEntry(BaseModel):
    ticker: str
    entry_price: float
    variant: str = "V1_DAY2_CONTINUATION"
    score: float | None = None
    reason: str = "operator"


@api.post("/lottery/ticket")
async def lottery_league_issue_ticket(payload: LotteryTicketEntry):
    from services import lottery
    return await lottery.issue_ticket(
        payload.ticker,
        payload.entry_price,
        variant=payload.variant,
        score=payload.score,
        reason=payload.reason,
    )


@api.post("/lottery/ticket/settle")
async def lottery_league_settle_ticket(ticket_id: str, exit_price: float, reason: str = "operator_settle"):
    from services import lottery
    return await lottery.settle_ticket(ticket_id, exit_price, reason=reason)


@api.get("/lottery/screener")
async def lottery_screener():
    from services import lottery
    return {"candidates": await lottery.latest_dedicated_lottery()}


class LotteryManualEntry(BaseModel):
    ticker: str
    entry_price: float
    lottery_score: int | None = None
    risk_amount: float | None = None


@api.post("/lottery/manual")
async def lottery_manual_add(p: LotteryManualEntry):
    from services import lottery
    return await lottery.add_manual_play(p.ticker, p.entry_price, p.lottery_score, p.risk_amount)


@api.post("/lottery/settle")
async def lottery_settle(ticker: str, exit_price: float, play_date: str):
    from services import lottery
    return await lottery.settle_manual_play(ticker, exit_price, play_date)


@api.get("/lottery/manual_plays")
async def lottery_manual_plays(active_only: bool = False):
    from services import lottery
    await lottery.update_manual_peak_marks(refresh=True)
    return {"plays": await lottery.list_manual_plays(active_only=active_only)}


@api.get("/lottery/manual_track_record")
async def lottery_manual_tracker():
    from services import lottery
    return await lottery.lottery_manual_track_record()


@api.post("/lottery/track/settle")
async def lottery_track_settle(ticker: str, exit_ask: float, play_date: str):
    """Manually lock the realized P&L on an auto-tracked lottery pick using
    the user's actual exit ask price."""
    from services import lottery
    return await lottery.manual_settle_track_pick(ticker, exit_ask, play_date)


@api.post("/lottery/track/delete")
async def lottery_track_delete(ticker: str, play_date: str):
    """Delete an auto-tracked lottery pick from the track record."""
    from services import lottery
    return await lottery.delete_track_pick(ticker, play_date)


# ─────── v5.1 — Settings: integrations + jobs + commands ───────
@api.get("/admin/integration_status")
async def admin_integration_status():
    from services import integration_status as svc
    return {"integrations": await svc.integration_status(),
             "jobs": svc.scheduled_jobs(),
             "commands": svc.telegram_commands()}


@api.get("/georisk/live")
async def georisk_live():
    from services import georisk
    return await georisk.live_georisk()


app.include_router(api)
