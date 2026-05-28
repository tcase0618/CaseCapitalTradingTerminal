"""FastAPI entrypoint: Stock Intelligence Telegram Bot backend."""
from __future__ import annotations
import asyncio
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
        await telegram_service.dispatch_consolidated(scan)
    return scan


@api.post("/scan/dispatch")
async def scan_dispatch():
    scan = await scanner.latest_scan()
    if not scan:
        raise HTTPException(404, "no scan available")
    return await telegram_service.dispatch_consolidated(scan)


@api.get("/scan/preview")
async def scan_preview():
    scan = await scanner.latest_scan()
    if not scan:
        return {"messages": [], "char_counts": [], "total_chars": 0}
    msgs = telegram_service.build_consolidated_messages(scan)
    return {"messages": msgs, "char_counts": [len(m) for m in msgs],
             "total_chars": sum(len(m) for m in msgs)}


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
    from services import pnl_tracker
    sig = await pnl_tracker.performance_by_signals()
    opt = await pnl_tracker.options_performance_summary()
    return {"signals": sig, "options": opt}


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
async def v32_earnings_week():
    from services import earnings_engine
    db = get_db()
    last_scan = await db.scan_results.find_one({}, {"_id": 0, "results": 1},
                                                  sort=[("finished_at", -1)])
    scan_set = {r["ticker"] for r in (last_scan or {}).get("results", []) or []}
    return await earnings_engine.current_week_with_probability(scan_tickers=scan_set)


@api.get("/v32/lottery")
async def v32_lottery(days: int = 14, tier: str | None = None):
    from services import lottery
    picks = await lottery.recent_picks(days=days, tier=tier)
    track = await lottery.track_record()
    return {"picks": picks, "track_record": track}


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
        "imminent_warnings": [e for e in events if e.get("is_imminent")],
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
    from services import learning_engine, pnl_tracker
    await learning_engine.ensure_weights_exist()
    try:
        await pnl_tracker.ensure_first_seen_backfill()
    except Exception as e:
        logger.warning("first_seen backfill failed: %s", e)
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
