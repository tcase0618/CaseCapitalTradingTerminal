"""APScheduler: 8AM ET daily scan + every-5min alert check."""
from __future__ import annotations
import logging

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from . import scanner, telegram_service

logger = logging.getLogger(__name__)
_scheduler: AsyncIOScheduler | None = None
ET = pytz.timezone("America/New_York")


async def _daily_scan_job():
    try:
        scan = await scanner.run_scan(triggered_by="scheduler")
        await telegram_service.send_message(telegram_service.format_scan_results(scan))
    except Exception as e:
        logger.exception("daily scan job failed: %s", e)


async def _alerts_job():
    try:
        await telegram_service.check_alerts()
    except Exception as e:
        logger.exception("alerts job failed: %s", e)


def start_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        return
    _scheduler = AsyncIOScheduler(timezone=ET)
    _scheduler.add_job(
        _daily_scan_job,
        CronTrigger(hour=8, minute=0, timezone=ET),
        id="daily_scan",
        replace_existing=True,
    )
    _scheduler.add_job(
        _alerts_job,
        IntervalTrigger(minutes=5),
        id="alerts_check",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Scheduler started: daily 8AM ET scan + 5min alert check")


def shutdown_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        _scheduler = None
