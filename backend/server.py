"""FastAPI entrypoint: Stock Intelligence Telegram Bot backend."""
from __future__ import annotations
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import APIRouter, BackgroundTasks, FastAPI, HTTPException, Request
from pydantic import BaseModel
from starlette.middleware.cors import CORSMiddleware

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

from services import claude_service, risk_target, scanner, scheduler, telegram_service, usaspending  # noqa: E402
from services.db import get_db, log_activity  # noqa: E402
from services.scrapers import fetch_quote  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("server")

app = FastAPI(title="Stock Intel Bot")
api = APIRouter(prefix="/api")


# ---------- Schemas ----------
class WatchlistItem(BaseModel):
    ticker: str


class AlertItem(BaseModel):
    ticker: str
    target_price: float


# ---------- Routes ----------
@api.get("/")
async def root():
    return {"name": "Stock Intel Bot API", "status": "ok"}


@api.get("/status")
async def status():
    db = get_db()
    state = await db.bot_state.find_one({"_id": "state"}, {"_id": 0}) or {}
    today_iso_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cache_today = await db.claude_cache.count_documents({"date_key": today_iso_prefix})
    last_scan = await db.scan_results.find_one({}, {"_id": 0}, sort=[("finished_at", -1)])
    watchlist_count = await db.watchlist.count_documents({})
    alerts_count = await db.alerts.count_documents({"triggered": False})

    return {
        "bot": {
            "online": True,
            "telegram_configured": bool(os.environ.get("TELEGRAM_BOT_TOKEN")),
            "claude_configured": bool(os.environ.get("ANTHROPIC_API_KEY")
                                       or os.environ.get("EMERGENT_LLM_KEY")),
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


@api.post("/scan/run")
async def run_scan_now():
    scan = await scanner.run_scan(triggered_by="admin_dashboard")
    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        await telegram_service.send_message(telegram_service.format_scan_summary(scan))
        for r in scan.get("results", [])[:10]:
            await telegram_service.send_message(telegram_service.format_stock_alert(r))
    return scan


@api.post("/scan/gov")
async def scan_gov():
    return await scanner.run_gov_scan_only(triggered_by="admin_dashboard")


@api.get("/contracts")
async def contracts(limit: int = 5):
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
    db = get_db()
    rows = await db.signal_performance.find({}, {"_id": 0}).sort("ts", -1).to_list(500)
    return {"count": len(rows), "rows": rows[:50]}


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
        raise HTTPException(500, "analysis failed (check EMERGENT_LLM_KEY)")
    return {"analysis": a, "quote": q}


# ---------- Telegram webhook ----------
@api.post("/telegram/webhook")
async def telegram_webhook(req: Request, bg: BackgroundTasks):
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


# ---------- App wiring ----------
app.include_router(api)
app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup():
    scheduler.start_scheduler()
    base = os.environ.get("PUBLIC_BASE_URL")
    if os.environ.get("TELEGRAM_BOT_TOKEN") and base:
        try:
            res = await telegram_service.register_webhook(base)
            logger.info("Telegram webhook setup: %s", res)
        except Exception as e:
            logger.warning("Webhook setup failed: %s", e)
    await log_activity("Server started", "info")


@app.on_event("shutdown")
async def on_shutdown():
    scheduler.shutdown_scheduler()
