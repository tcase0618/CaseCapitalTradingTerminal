"""APScheduler: 8AM ET daily scan + every-5min alert check + 15-min market-hours
options-flow refresh + nightly P&L returns refresh."""
from __future__ import annotations
import logging
import os
from datetime import datetime

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from . import learning_engine, lottery, options_engine, pnl_tracker, scanner, telegram_service
from .db import get_db, log_activity, stamped


def _now_iso():
    from datetime import timezone
    return datetime.now(timezone.utc).isoformat()

logger = logging.getLogger(__name__)
_scheduler: AsyncIOScheduler | None = None
ET = pytz.timezone("America/New_York")


async def _daily_scan_job():
    try:
        scan = await scanner.run_scan(triggered_by="scheduler")
        await telegram_service.dispatch_consolidated(scan)
    except Exception as e:
        logger.exception("daily scan job failed: %s", e)


async def _alerts_job():
    try:
        await telegram_service.check_alerts()
    except Exception as e:
        logger.exception("alerts job failed: %s", e)


async def _flow_refresh_job():
    """Every 15min during US market hours — refresh unusual flow on tickers
    from today's scan. Stores into flow_snapshots collection for the dashboard."""
    try:
        now_et = datetime.now(ET)
        # Skip weekends
        if now_et.weekday() >= 5:
            return
        # Market hours 9:30-16:00 ET (CronTrigger handles day/hour but we
        # double-guard the minute-level boundary)
        h, m = now_et.hour, now_et.minute
        if h < 9 or h > 16 or (h == 9 and m < 30):
            return
        latest = await scanner.latest_scan()
        if not latest:
            return
        tickers = [r["ticker"] for r in latest.get("results", [])][:30]
        if not tickers:
            return
        db = get_db()
        for tk in tickers:
            try:
                flow = await options_engine.detect_unusual_flow(tk)
                await db.flow_snapshots.insert_one(stamped({
                    "ticker": tk, "ts": now_et.isoformat(), **flow,
                }))
            except Exception:
                continue
        await log_activity(f"Flow refresh complete for {len(tickers)} tickers", "info")
    except Exception as e:
        logger.exception("flow refresh job failed: %s", e)


async def _pnl_refresh_job():
    """Nightly: fill 7/30/90d returns, refresh options proxy + actual,
    refresh lottery track-record settlements (live ask + expired settle)."""
    try:
        sig = await pnl_tracker.refresh_due_returns()
        opt = await pnl_tracker.refresh_due_options_returns()
        lot = await lottery.refresh_settlements()
        await log_activity(
            f"P&L refresh: signals={sig} options_rows={opt} lottery={lot}", "info",
        )
    except Exception as e:
        logger.exception("P&L refresh job failed: %s", e)


def start_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        return
    _scheduler = AsyncIOScheduler(timezone=ET)
    # v5.0 — four fixed daily scans: midnight, 8am, 1pm, 6pm ET
    for tag, hr in [("midnight_scan", 0), ("morning_scan", 8),
                      ("midday_scan", 13), ("evening_scan", 18)]:
        _scheduler.add_job(
            _daily_scan_job,
            CronTrigger(hour=hr, minute=0, timezone=ET),
            id=tag, replace_existing=True,
        )
    _scheduler.add_job(
        _alerts_job,
        IntervalTrigger(minutes=5),
        id="alerts_check",
        replace_existing=True,
    )
    # 15-min options flow refresh — Mon-Fri 9:30 to 16:00 ET
    _scheduler.add_job(
        _flow_refresh_job,
        CronTrigger(day_of_week="mon-fri", hour="9-16", minute="*/15", timezone=ET),
        id="flow_refresh",
        replace_existing=True,
    )
    # Nightly P&L refresh — 23:00 ET (also runs at 02:00 ET below)
    _scheduler.add_job(
        _pnl_refresh_job,
        CronTrigger(hour=23, minute=0, timezone=ET),
        id="pnl_refresh_nightly",
        replace_existing=True,
    )
    # 02:00 ET P&L refresh — second pass after market settle
    _scheduler.add_job(
        _pnl_refresh_job,
        CronTrigger(hour=2, minute=0, timezone=ET),
        id="pnl_refresh",
        replace_existing=True,
    )
    # Mid-day scan — 12:01 ET Mon-Fri
    _scheduler.add_job(
        _daily_scan_job,
        CronTrigger(day_of_week="mon-fri", hour=12, minute=1, timezone=ET),
        id="midday_scan_legacy",
        replace_existing=True,
    )
    # Pre-close scan — 15:30 ET Mon-Fri
    _scheduler.add_job(
        _daily_scan_job,
        CronTrigger(day_of_week="mon-fri", hour=15, minute=30, timezone=ET),
        id="preclose_scan",
        replace_existing=True,
    )

    # v5.0 — regime gate every 30 min during market hours
    async def _regime_job():
        try:
            from . import trade_floor
            r = await trade_floor.regime_status()
            if r.get("halt_new_entries") and os.environ.get("TELEGRAM_CHAT_ID"):
                await telegram_service.send_message(
                    f"⛔ <b>REGIME HALT</b>\nVIX={r['vix']} · SPY={r['spy_last']} "
                    f"vs EMA200={r['spy_ema200']}", chat_id=os.environ.get("TELEGRAM_CHAT_ID"),
                )
        except Exception as e:
            logger.warning("regime job: %s", e)
    _scheduler.add_job(
        _regime_job,
        CronTrigger(day_of_week="mon-fri", hour="9-16", minute="*/30", timezone=ET),
        id="regime_gate", replace_existing=True,
    )

    # v5.0 — position monitor every 15 min during market hours
    async def _position_monitor():
        try:
            from . import trade_floor
            await trade_floor.sync_positions_and_close_settled()
        except Exception as e:
            logger.warning("position monitor: %s", e)
    _scheduler.add_job(
        _position_monitor,
        CronTrigger(day_of_week="mon-fri", hour="9-16", minute="*/15", timezone=ET),
        id="position_monitor", replace_existing=True,
    )

    # v5.0 — daily database backup at 2am ET
    async def _db_backup():
        try:
            from .db import get_db
            db = get_db()
            stats = await db.command("dbStats")
            await db.bot_state.update_one(
                {"_id": "last_backup"},
                {"$set": {"timestamp": _now_iso(),
                            "collections": stats.get("collections"),
                            "data_size": stats.get("dataSize")}},
                upsert=True,
            )
            await log_activity("Daily DB stats checkpoint stored", "info")
        except Exception as e:
            logger.warning("db backup: %s", e)
    _scheduler.add_job(
        _db_backup,
        CronTrigger(hour=2, minute=0, timezone=ET),
        id="db_backup", replace_existing=True,
    )

    # v5.0 — Trade Floor weekly recalibration — Sunday 03:00 ET
    async def _tf_recal():
        try:
            from . import trade_floor_learning
            await trade_floor_learning.recalibrate()
        except Exception as e:
            logger.warning("tf recal: %s", e)
    _scheduler.add_job(
        _tf_recal,
        CronTrigger(day_of_week="sun", hour=3, minute=0, timezone=ET),
        id="tf_engine_recal", replace_existing=True,
    )
    # Learning cycle — Sunday 02:00 ET weekly
    async def _learning_job():
        try:
            res = await learning_engine.run_learning_cycle()
            if res.get("insights") and os.environ.get("TELEGRAM_CHAT_ID"):
                msg = (
                    "🧠 <b>AXIOM Learning Cycle Complete</b>\n\n"
                    f"Trades analyzed: {res.get('trades', 0)}\n"
                    f"Overall win rate: {res.get('win_rate', 0):.1%}\n"
                    f"Weights adjusted: {res.get('changes', 0)}\n\n"
                    "<b>Insights:</b>\n" + "\n".join(f"• {i}" for i in res["insights"][:5])
                )
                await telegram_service.send_message(msg)
        except Exception as e:
            logger.exception("learning cycle failed: %s", e)

    _scheduler.add_job(
        _learning_job,
        CronTrigger(day_of_week="sun", hour=2, minute=0, timezone=ET),
        id="learning_cycle",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Scheduler: 8AM/12:01/15:30 scans + 5m alerts + 15m flow + P&L + Sunday 2AM learning")


def shutdown_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None
