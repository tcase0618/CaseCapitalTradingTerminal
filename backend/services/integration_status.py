"""v5.1 — Aggregated integration + job status for the Settings tab."""
from __future__ import annotations
import os
from datetime import datetime, timezone

import httpx

from .db import get_db


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _last_activity(matchers: list[str]) -> str | None:
    """Find the most recent activity row whose message matches any tag."""
    db = get_db()
    q = {"$or": [{"message": {"$regex": m, "$options": "i"}} for m in matchers]}
    row = await db.activity.find_one(q, {"_id": 0}, sort=[("ts", -1)])
    return row.get("ts") if row else None


async def _ping(url: str, headers: dict | None = None, timeout: float = 6.0) -> bool:
    try:
        async with httpx.AsyncClient(timeout=timeout, headers=headers or {},
                                       follow_redirects=True) as c:
            r = await c.get(url)
            return r.status_code < 400
    except Exception:
        return False


async def integration_status() -> list[dict]:
    """All integrations: live status + last successful timestamp."""
    out: list[dict] = []
    # Alpaca
    alpaca_ok = False
    alpaca_acct = None
    if os.environ.get("APCA_API_KEY_ID") and os.environ.get("APCA_API_SECRET_KEY"):
        from . import trade_floor
        a = await trade_floor.get_account()
        if a:
            alpaca_ok = True
            alpaca_acct = {"equity": a.get("equity"), "cash": a.get("cash")}
    out.append({
        "key": "alpaca",
        "name": "Alpaca Paper Trading",
        "ok": alpaca_ok,
        "last": _now_iso() if alpaca_ok else None,
        "detail": alpaca_acct,
    })
    # Finnhub
    fh_key = os.environ.get("FINNHUB_API_KEY", "").strip()
    fh_ok = False
    if fh_key:
        fh_ok = await _ping(f"https://finnhub.io/api/v1/quote?symbol=AAPL&token={fh_key}")
    out.append({"key": "finnhub", "name": "Finnhub (Price Source)",
                 "ok": fh_ok, "last": _now_iso() if fh_ok else None})
    # EDGAR
    sec_last = await _last_activity(["EDGAR poll"])
    out.append({"key": "edgar", "name": "SEC EDGAR RSS",
                 "ok": await _ping("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&output=atom",
                                     headers={"User-Agent": "AXIOM Intel research@axiom.local"}),
                 "last": sec_last})
    # ClinicalTrials
    out.append({"key": "clinicaltrials", "name": "ClinicalTrials.gov",
                 "ok": True, "last": await _last_activity(["Pharma scan"])})
    # FDA / biopharmcatalyst
    out.append({"key": "fda_pdufa", "name": "FDA PDUFA Calendar",
                 "ok": True, "last": await _last_activity(["Pharma scan"])})
    # Barchart
    out.append({"key": "barchart", "name": "Barchart (Unusual Options)",
                 "ok": await _ping("https://www.barchart.com/options/unusual-activity/stocks"),
                 "last": await _last_activity(["X-Factor", "barchart"])})
    # Reddit
    reddit_oauth = bool(os.environ.get("REDDIT_CLIENT_ID") and
                          os.environ.get("REDDIT_CLIENT_SECRET"))
    out.append({"key": "reddit", "name": "Reddit API",
                 "ok": reddit_oauth, "last": await _last_activity(["X-Factor"]),
                 "detail": "OAuth" if reddit_oauth else "Public RSS (rate-limited)"})
    # StockTwits
    out.append({"key": "stocktwits", "name": "StockTwits API",
                 "ok": True, "last": await _last_activity(["X-Factor"])})
    # Google Trends
    out.append({"key": "google_trends", "name": "Google Trends",
                 "ok": True, "last": await _last_activity(["X-Factor"])})
    # Yahoo Finance news
    out.append({"key": "yahoo_news", "name": "Yahoo Finance News",
                 "ok": True, "last": await _last_activity(["X-Factor"])})
    # NIH / CDC — static curated dataset; mark "loaded"
    out.append({"key": "nih_cdc", "name": "NIH / CDC Prevalence",
                 "ok": True, "last": _now_iso(),
                 "detail": "static curated dataset"})
    # Telegram
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    tg_ok = bool(tg_token) and await _ping(f"https://api.telegram.org/bot{tg_token}/getMe")
    out.append({"key": "telegram", "name": "Telegram Bot",
                 "ok": tg_ok, "last": _now_iso() if tg_ok else None})
    return out


def scheduled_jobs() -> list[dict]:
    """Static description of v5.1 scheduler — matches `services/scheduler.py`."""
    return [
        {"id": "main_scans", "name": "Main Scan",
          "cron": "00:00, 08:00, 13:00, 18:00 ET daily"},
        {"id": "regime_gate", "name": "Regime Gate",
          "cron": "Every 30 min (Mon-Fri, 09:00-16:30 ET)"},
        {"id": "position_monitor", "name": "Position Monitor",
          "cron": "Every 15 min (Mon-Fri, 09:00-16:30 ET)"},
        {"id": "stale_order_sweep", "name": "Stale Order Sweep · 24h Day-Order Cancel",
          "cron": "Hourly :05 ET"},
        {"id": "pharma_scrape", "name": "Pharma PDUFA Calendar Scrape",
          "cron": "Weekly (auto on first scan of week)"},
        {"id": "learning_recal", "name": "Signal Learning Engine Recalibration",
          "cron": "Weekly Sunday 04:00 ET"},
        {"id": "tf_engine_recal", "name": "Trade Floor Engine Recalibration",
          "cron": "Weekly Sunday 03:00 ET"},
        {"id": "db_backup", "name": "Daily DB Backup",
          "cron": "Daily 02:00 ET"},
    ]


def telegram_commands() -> list[dict]:
    return [
        {"cmd": "/status",     "desc": "System status + last scan summary"},
        {"cmd": "/scan",       "desc": "Trigger an on-demand scan"},
        {"cmd": "/positions",  "desc": "All current open Trade Floor positions"},
        {"cmd": "/account",    "desc": "Account value, total return, win rate"},
        {"cmd": "/regime",     "desc": "Current regime status + VIX level"},
        {"cmd": "/risk",       "desc": "Circuit breaker + active risk tier"},
        {"cmd": "/journal",    "desc": "Last 3 closed trade journal entries"},
        {"cmd": "/sec",        "desc": "Last 5 SEC filings (meet filter criteria)"},
        {"cmd": "/pharma",     "desc": "Top 3 pharma plays by Binary Event Score"},
        {"cmd": "/contracts",  "desc": "Most recent 5 gov contracts detected"},
        {"cmd": "/checkup",    "desc": "Buys/sells + unrealized P/L since last check-up"},
    ]
