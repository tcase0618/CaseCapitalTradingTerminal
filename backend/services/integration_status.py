"""Aggregated integration, data-quality, and job status for the terminal."""
from __future__ import annotations

import os
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from .db import get_db

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _refresh_env() -> None:
    """Let Settings reflect backend/.env edits without requiring a restart."""
    load_dotenv(BACKEND_DIR / ".env", override=True)


def _row(
    key: str,
    name: str,
    ok: bool,
    *,
    last: str | None = None,
    detail: Any = None,
    quality: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    row = {
        "key": key,
        "name": name,
        "ok": ok,
        "quality": quality or ("live" if ok else "down"),
        "last": last,
    }
    if detail is not None:
        row["detail"] = detail
    if reason:
        row["reason"] = reason
    return row


async def _last_activity(matchers: list[str]) -> str | None:
    db = get_db()
    q = {"$or": [{"message": {"$regex": m, "$options": "i"}} for m in matchers]}
    row = await db.activity_log.find_one(q, {"_id": 0}, sort=[("ts", -1)])
    return row.get("ts") if row else None


async def _latest(collection: str, fields: list[str]) -> str | None:
    db = get_db()
    row = await db[collection].find_one({}, {"_id": 0}, sort=[("created_at", -1)])
    if not row:
        return None
    for field in fields:
        if row.get(field):
            return row.get(field)
    return row.get("created_at")


async def _ping(url: str, headers: dict | None = None, timeout: float = 6.0) -> bool:
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            headers=headers or {},
            follow_redirects=True,
        ) as client:
            r = await client.get(url)
            return r.status_code < 400
    except Exception:
        return False


async def _yfinance_news_probe() -> bool:
    def _sync() -> bool:
        try:
            import yfinance as yf

            return bool(yf.Ticker("AAPL").news)
        except Exception:
            return False
    import asyncio

    return await asyncio.get_event_loop().run_in_executor(None, _sync)


async def integration_status() -> list[dict[str, Any]]:
    """All integrations with explicit quality labels.

    quality meanings:
    - live: active probe or configured live path succeeded
    - fallback: usable but not a fully live authoritative source
    - unchecked: only checked inside scan runtime, not by this status endpoint
    - down: missing config or failed probe
    """
    _refresh_env()
    out: list[dict[str, Any]] = []

    alpaca_ok = False
    alpaca_acct = None
    if os.environ.get("APCA_API_KEY_ID") and os.environ.get("APCA_API_SECRET_KEY"):
        from . import trade_floor

        a = await trade_floor.get_account()
        if a:
            alpaca_ok = True
            alpaca_acct = {"equity": a.get("equity"), "cash": a.get("cash")}
    out.append(_row(
        "alpaca",
        "Alpaca Paper Trading",
        alpaca_ok,
        last=_now_iso() if alpaca_ok else None,
        detail=alpaca_acct,
        reason=None if alpaca_ok else "account probe failed",
    ))

    try:
        from . import ibkr_research, ibkr_terminal

        ibkr_probe = await asyncio.wait_for(asyncio.to_thread(ibkr_research.status), timeout=12.0)
        ibkr_cfg = ibkr_probe.get("config") or ibkr_research.safety_state()
        ibkr_apps = await asyncio.wait_for(ibkr_terminal.applications(), timeout=12.0)
        app_summary = ibkr_apps.get("summary") or {}
        ibkr_enabled = bool(ibkr_cfg.get("enabled"))
        ibkr_ok = bool(ibkr_probe.get("ok") and ibkr_probe.get("connected"))
        quality = "live" if ibkr_ok else "optional" if not ibkr_enabled else "down"
        out.append(_row(
            "ibkr_readonly",
            "IBKR Gateway Read-Only Data",
            ibkr_ok,
            last=_now_iso() if ibkr_ok else None,
            detail={
                "mode": ibkr_cfg.get("mode"),
                "data_only": ibkr_cfg.get("data_only"),
                "allow_trading": ibkr_cfg.get("allow_trading"),
                "host": ibkr_cfg.get("host"),
                "port": ibkr_cfg.get("port"),
                "applications": app_summary,
                "coverage": [
                    "options validation",
                    "equity quotes",
                    "historical bars",
                    "option chains",
                    "option greeks when available",
                    "scanner top-candidate validation",
                ],
            },
            quality=quality,
            reason=None if ibkr_ok else ibkr_probe.get("reason") or "IBKR read-only data not connected",
        ))
    except Exception as exc:
        out.append(_row(
            "ibkr_readonly",
            "IBKR Gateway Read-Only Data",
            False,
            quality="down",
            reason=str(exc)[:160],
        ))

    fh_key = os.environ.get("FINNHUB_API_KEY", "").strip()
    fh_ok = False
    fh_reason = "missing FINNHUB_API_KEY"
    fh_quality = "optional"
    if fh_key:
        fh_ok = await _ping(f"https://finnhub.io/api/v1/quote?symbol=AAPL&token={fh_key}")
        fh_reason = None if fh_ok else "quote probe failed"
        fh_quality = "live" if fh_ok else "down"
    out.append(_row(
        "finnhub",
        "Finnhub (Optional Price Source)",
        fh_ok,
        last=_now_iso() if fh_ok else None,
        quality=fh_quality,
        reason=fh_reason,
    ))

    try:
        from . import finance_toolkit_source

        fmp_status = finance_toolkit_source.status()
        fmp_ok = bool(fmp_status.get("configured"))
        out.append(_row(
            "finance_toolkit_fmp",
            "FinanceToolkit / FMP Research Data",
            fmp_ok,
            last=_now_iso() if fmp_ok else None,
            detail={
                "provider": fmp_status.get("provider"),
                "adapter": fmp_status.get("adapter"),
                "env_key": fmp_status.get("env_key"),
                "key_state": fmp_status.get("key_state"),
                "data_role": fmp_status.get("data_role"),
                "wired_to_pm": fmp_status.get("wired_to_pm"),
                "wired_to_execution": fmp_status.get("wired_to_execution"),
                "coverage": fmp_status.get("coverage"),
            },
            quality="configured" if fmp_ok else "optional",
            reason=None if fmp_ok else "missing FMP/FinanceToolkit API key",
        ))
    except Exception as exc:
        out.append(_row(
            "finance_toolkit_fmp",
            "FinanceToolkit / FMP Research Data",
            False,
            quality="down",
            reason=str(exc)[:160],
        ))

    try:
        from . import pricer

        label = pricer.source_label()
        out.append(_row(
            "price_path",
            "Configured Price Path",
            True,
            last=_now_iso(),
            detail=label,
            quality="live" if label.startswith("alpaca") else "fallback",
        ))
    except Exception as exc:
        out.append(_row(
            "price_path",
            "Configured Price Path",
            False,
            quality="down",
            reason=str(exc)[:120],
        ))

    from . import london_strategic_edge as lse_svc

    lse_probe = await lse_svc.health_probe()
    out.append(_row(
        "london_strategic_edge",
        "London Strategic Edge",
        bool(lse_probe.get("ok")),
        last=_now_iso() if lse_probe.get("ok") else None,
        detail="candles/options/flow/fundamentals/macro provider",
        quality="live" if lse_probe.get("ok") else "down",
        reason=None if lse_probe.get("ok") else lse_probe.get("reason"),
    ))

    sec_last = await _latest("sec_filings", ["accepted_at", "updated", "created_at"])
    sec_last = sec_last or await _last_activity(["EDGAR poll"])
    sec_ok = await _ping(
        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&output=atom",
        headers={"User-Agent": "Case Capital Terminal research@casecapital.local"},
    )
    out.append(_row(
        "edgar",
        "SEC EDGAR RSS",
        sec_ok,
        last=sec_last,
        reason=None if sec_ok else "SEC Atom probe failed",
    ))

    ct_ok = await _ping("https://clinicaltrials.gov/api/v2/studies?query.term=aspirin&pageSize=1")
    out.append(_row(
        "clinicaltrials",
        "ClinicalTrials.gov",
        ct_ok,
        last=await _latest("pharma_pdufa", ["evaluated_at", "created_at"]),
        reason=None if ct_ok else "API probe failed",
    ))

    openfda_ok = await _ping("https://api.fda.gov/drug/event.json?limit=1")
    out.append(_row(
        "openfda",
        "openFDA",
        openfda_ok,
        last=_now_iso() if openfda_ok else None,
        reason=None if openfda_ok else "API probe failed",
    ))

    out.append(_row(
        "fda_pdufa",
        "PDUFA Calendar",
        True,
        last=await _latest("pharma_pdufa", ["evaluated_at", "created_at"])
             or await _last_activity(["Pharma scan"]),
        detail="curated seed currently labels rows as fallback_calendar",
        quality="fallback",
    ))

    usaspending_ok = await _ping("https://api.usaspending.gov/api/v2/references/toptier_agencies/")
    out.append(_row(
        "usaspending",
        "USAspending.gov",
        usaspending_ok,
        last=await _last_activity(["contract", "USAspending"]),
        reason=None if usaspending_ok else "API probe failed",
    ))

    barchart_ok = await _ping("https://www.barchart.com/options/unusual-activity/stocks")
    out.append(_row(
        "barchart",
        "Barchart (Unusual Options)",
        barchart_ok,
        last=await _last_activity(["X-Factor", "barchart"]),
        reason=None if barchart_ok else "public page probe failed",
    ))

    reddit_oauth = bool(os.environ.get("REDDIT_CLIENT_ID") and os.environ.get("REDDIT_CLIENT_SECRET"))
    out.append(_row(
        "reddit",
        "Reddit Signals",
        True,
        last=await _last_activity(["X-Factor"]),
        detail="OAuth" if reddit_oauth else "Public RSS (rate-limited)",
        quality="live" if reddit_oauth else "fallback",
    ))

    stocktwits_ok = await _ping("https://api.stocktwits.com/api/2/streams/symbol/AAPL.json")
    out.append(_row(
        "stocktwits",
        "StockTwits API",
        stocktwits_ok,
        last=await _last_activity(["X-Factor"]),
        reason=None if stocktwits_ok else "public API probe failed",
    ))

    out.append(_row(
        "google_trends",
        "Google Trends",
        True,
        last=await _last_activity(["X-Factor"]),
        detail="checked inside X-Factor scans",
        quality="unchecked",
    ))

    yahoo_rss_ok = await _ping(
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=AAPL&region=US&lang=en-US"
    )
    yahoo_trending_ok = await _ping(
        "https://query1.finance.yahoo.com/v1/finance/trending/US?count=5"
    )
    yahoo_yf_ok = await _yfinance_news_probe()
    yahoo_ok = yahoo_rss_ok or yahoo_trending_ok or yahoo_yf_ok
    yahoo_quality = "live" if (yahoo_rss_ok or yahoo_trending_ok) else "fallback" if yahoo_yf_ok else "down"
    out.append(_row(
        "yahoo_news",
        "Yahoo Finance RSS / Trending",
        yahoo_ok,
        last=await _last_activity(["X-Factor"]),
        quality=yahoo_quality,
        detail=f"rss={yahoo_rss_ok}, trending={yahoo_trending_ok}, yfinance_news={yahoo_yf_ok}",
        reason=None if yahoo_ok else "RSS, trending, and yfinance news probes failed",
    ))

    out.append(_row(
        "nih_cdc",
        "NIH / CDC Prevalence",
        True,
        last=_now_iso(),
        detail="static curated dataset",
        quality="fallback",
    ))

    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    tg_ok = bool(tg_token) and await _ping(f"https://api.telegram.org/bot{tg_token}/getMe")
    out.append(_row(
        "telegram",
        "Telegram Bot",
        tg_ok,
        last=_now_iso() if tg_ok else None,
        reason=None if tg_ok else "getMe probe failed",
    ))

    return out


def scheduled_jobs() -> list[dict[str, str]]:
    return [
        {"id": "main_scans", "name": "Main Scan",
         "cron": "00:00, 08:00, 13:00, 18:00 ET daily"},
        {"id": "regime_gate", "name": "Regime Gate",
         "cron": "Every 30 min (Mon-Fri, 09:00-16:30 ET)"},
        {"id": "position_monitor", "name": "Position Monitor",
         "cron": "Every 15 min (Mon-Fri, 09:00-16:30 ET)"},
        {"id": "kronos_morning_forecast_930", "name": "Kronos Morning Forecast",
         "cron": "09:30 ET Mon-Fri"},
        {"id": "stale_order_sweep", "name": "Stale Order Sweep - 24h Day-Order Cancel",
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


def telegram_commands() -> list[dict[str, str]]:
    return [
        {"cmd": "/status", "desc": "System status + last scan summary"},
        {"cmd": "/scan", "desc": "Trigger an on-demand scan"},
        {"cmd": "/positions", "desc": "All current open Trade Floor positions"},
        {"cmd": "/account", "desc": "Account value, total return, win rate"},
        {"cmd": "/regime", "desc": "Current regime status + VIX level"},
        {"cmd": "/risk", "desc": "Circuit breaker + active risk tier"},
        {"cmd": "/journal", "desc": "Last 3 closed trade journal entries"},
        {"cmd": "/sec", "desc": "Last 5 SEC filings (meet filter criteria)"},
        {"cmd": "/pharma", "desc": "Top 3 pharma plays by Binary Event Score"},
        {"cmd": "/contracts", "desc": "Most recent 5 gov contracts detected"},
        {"cmd": "/checkup", "desc": "Buys/sells + unrealized P/L since last check-up"},
    ]
