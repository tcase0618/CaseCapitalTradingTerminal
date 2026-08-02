"""APScheduler: market-hours scans + every-5min alert check + 15-min
options-flow refresh + nightly P&L returns refresh."""
from __future__ import annotations
import logging
import os
from datetime import datetime

import httpx
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


async def _stock_scan_market_day_now() -> tuple[bool, str]:
    now_et = datetime.now(ET)
    if now_et.weekday() >= 5:
        return False, f"weekend ({now_et.strftime('%A')})"

    key = os.environ.get("APCA_API_KEY_ID", "").strip()
    secret = os.environ.get("APCA_API_SECRET_KEY", "").strip()
    base = os.environ.get("APCA_API_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")
    if base.endswith("/v2"):
        base = base[:-3]
    if not key or not secret:
        return True, "weekday fallback; Alpaca calendar unavailable"

    date_str = now_et.date().isoformat()
    try:
        async with httpx.AsyncClient(timeout=4.0, headers={
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
        }) as client:
            resp = await client.get(
                f"{base}/v2/calendar",
                params={"start": date_str, "end": date_str},
            )
            resp.raise_for_status()
            sessions = resp.json() or []
        if sessions:
            return True, f"market session day ({date_str})"
        return False, f"market closed ({date_str})"
    except Exception as exc:
        logger.warning("Alpaca market calendar check failed; weekday fallback: %s", exc)
        return True, f"weekday fallback after calendar check failed ({date_str})"


async def _daily_scan_job():
    try:
        market_day, reason = await _stock_scan_market_day_now()
        if not market_day:
            await log_activity(f"Scheduled stock scan skipped: {reason}", "info")
            try:
                from . import telegram_events
                await telegram_events.emit_event(
                    "scan_skipped_market_closed",
                    severity="info",
                    scope="scanner",
                    title="Scheduled scan skipped",
                    summary=reason,
                    details={"reason": reason, "trading_impact": "no stock scan"},
                    priority="summary",
                )
            except Exception:
                pass
            return
        scan = await scanner.run_scan(triggered_by="scheduler")
        from . import telegram_events
        await telegram_events.dispatch_scan_report(scan)
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


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _compact_position(row: dict) -> dict:
    return {
        "symbol": row.get("symbol"),
        "asset_class": row.get("asset_class"),
        "side": row.get("side"),
        "qty": row.get("qty"),
        "avg_entry_price": row.get("avg_entry_price"),
        "current_price": row.get("current_price"),
        "market_value": row.get("market_value"),
        "cost_basis": row.get("cost_basis"),
        "unrealized_pl": row.get("unrealized_pl"),
        "unrealized_plpc": row.get("unrealized_plpc"),
        "change_today": row.get("change_today"),
    }


async def persist_live_position_snapshot(triggered_by: str = "scheduler_15m", management: dict | None = None) -> dict:
    """Persist the PM's live view across both funds.

    This is intentionally separate from scan results. It records what Alpaca
    says is currently held, what is working, and what the monitor did.
    """
    from . import options_desk, trade_floor

    db = get_db()
    equity_positions = await trade_floor.list_positions()
    equity_orders = await trade_floor.list_orders(status="open", limit=100)
    equity_account = await trade_floor.get_account()
    options_positions_payload = await options_desk.positions()
    options_orders_payload = await options_desk.orders(status="open", limit=100)
    options_account_payload = await options_desk.account()

    option_positions = options_positions_payload.get("positions") or []
    option_orders = options_orders_payload.get("orders") or []
    options_account = options_account_payload.get("account") if options_account_payload.get("ok") else {}

    equity_unrealized = sum(_num(p.get("unrealized_pl")) for p in equity_positions)
    options_unrealized = sum(_num(p.get("unrealized_pl")) for p in option_positions)
    equity_market_value = sum(_num(p.get("market_value")) for p in equity_positions)
    options_market_value = sum(_num(p.get("market_value")) for p in option_positions)

    snapshot = stamped({
        "snapshot_at": _now_iso(),
        "triggered_by": triggered_by,
        "cadence_minutes": 15,
        "management": management or {},
        "totals": {
            "positions": len(equity_positions) + len(option_positions),
            "open_orders": len(equity_orders) + len(option_orders),
            "market_value": round(equity_market_value + options_market_value, 2),
            "unrealized_pl": round(equity_unrealized + options_unrealized, 2),
        },
        "equities": {
            "account": {
                "status": (equity_account or {}).get("status"),
                "equity": (equity_account or {}).get("equity"),
                "cash": (equity_account or {}).get("cash"),
                "buying_power": (equity_account or {}).get("buying_power"),
                "trading_blocked": (equity_account or {}).get("trading_blocked"),
            },
            "position_count": len(equity_positions),
            "open_order_count": len(equity_orders),
            "market_value": round(equity_market_value, 2),
            "unrealized_pl": round(equity_unrealized, 2),
            "positions": [_compact_position(p) for p in equity_positions],
            "open_orders": [
                {
                    "id": o.get("id"),
                    "symbol": o.get("symbol"),
                    "side": o.get("side"),
                    "type": o.get("type"),
                    "qty": o.get("qty"),
                    "notional": o.get("notional"),
                    "limit_price": o.get("limit_price"),
                    "status": o.get("status"),
                    "submitted_at": o.get("submitted_at"),
                }
                for o in equity_orders
            ],
        },
        "options": {
            "account": options_account,
            "position_count": len(option_positions),
            "open_order_count": len(option_orders),
            "market_value": round(options_market_value, 2),
            "unrealized_pl": round(options_unrealized, 2),
            "positions": [_compact_position(p) for p in option_positions],
            "open_orders": [
                {
                    "id": o.get("id"),
                    "symbol": o.get("symbol"),
                    "side": o.get("side"),
                    "type": o.get("type"),
                    "qty": o.get("qty"),
                    "limit_price": o.get("limit_price"),
                    "status": o.get("status"),
                    "submitted_at": o.get("submitted_at"),
                }
                for o in option_orders
            ],
        },
    })
    await db.live_position_snapshots.insert_one(snapshot)
    latest_snapshot = {k: v for k, v in snapshot.items() if k != "_id"}
    await db.bot_state.update_one(
        {"_id": "live_position_snapshot_latest"},
        {"$set": latest_snapshot},
        upsert=True,
    )
    return {
        "ok": True,
        "snapshot_at": snapshot["snapshot_at"],
        "cadence_minutes": 15,
        "totals": snapshot["totals"],
        "equities": {
            "position_count": snapshot["equities"]["position_count"],
            "open_order_count": snapshot["equities"]["open_order_count"],
            "unrealized_pl": snapshot["equities"]["unrealized_pl"],
        },
        "options": {
            "position_count": snapshot["options"]["position_count"],
            "open_order_count": snapshot["options"]["open_order_count"],
            "unrealized_pl": snapshot["options"]["unrealized_pl"],
        },
    }


def start_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        return
    _scheduler = AsyncIOScheduler(timezone=ET)
    # Original fixed stock-scan cadence, restricted to market-session days by
    # the runtime guard in _daily_scan_job. Other scheduled data pulls keep
    # their own cadence and are not blocked by this stock-scan guard.
    for tag, hr in [("midnight_scan", 0), ("morning_scan", 8),
                      ("midday_scan", 13), ("evening_scan", 18)]:
        _scheduler.add_job(
            _daily_scan_job,
            CronTrigger(day_of_week="mon-fri", hour=hr, minute=0, timezone=ET),
            id=tag, replace_existing=True,
        )
    # v5.1 — auto-digest goes out 5 min after each scheduled scan
    # Grouped Telegram scan reports are dispatched by _daily_scan_job. There
    # is intentionally no separate 5-minute digest job so scans do not double-text.
    _scheduler.add_job(
        _alerts_job,
        IntervalTrigger(minutes=5),
        id="alerts_check",
        replace_existing=True,
    )
    async def _kronos_morning_forecast_job():
        try:
            from . import kronos
            await kronos.dispatch_morning_forecast()
        except Exception as e:
            logger.warning("kronos morning forecast: %s", e)
    _scheduler.add_job(
        _kronos_morning_forecast_job,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=30, timezone=ET),
        id="kronos_morning_forecast_930",
        replace_existing=True,
    )
    # 15-min options flow refresh — Mon-Fri 9:30 to 16:00 ET
    _scheduler.add_job(
        _flow_refresh_job,
        CronTrigger(day_of_week="mon-fri", hour="9-16", minute="*/15", timezone=ET),
        id="flow_refresh",
        replace_existing=True,
    )
    # Options open sweep: close/refresh risk first, then submit PM-approved buys.
    async def _options_open_auto_execute_job():
        try:
            from . import options_desk, telegram_events

            result = await options_desk.refresh_and_auto_execute_latest()
            await log_activity(
                f"Options open auto-execute 9:35: {len(result.get('submitted', []))} submitted, "
                f"{len(result.get('skipped', []))} skipped",
                "success" if result.get("submitted") else "info",
                result,
            )
            if not result.get("submitted"):
                await telegram_events.emit_event(
                    "options_open_no_orders",
                    severity="info",
                    scope="options",
                    title="Options open sweep complete",
                    summary=f"No option buys submitted; {len(result.get('skipped', []))} skipped by gate/preflight.",
                    details={
                        "ready": result.get("ready"),
                        "skipped": (result.get("skipped") or [])[:8],
                        "pre_execution_risk_check": result.get("pre_execution_risk_check"),
                    },
                    priority="summary",
                )
        except Exception as e:
            logger.warning("options open auto-execute: %s", e)
    _scheduler.add_job(
        _options_open_auto_execute_job,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=35, timezone=ET),
        id="options_open_auto_execute_935",
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
    async def _options_daily_report_job():
        try:
            from . import options_desk
            await options_desk.dispatch_options_daily_report()
        except Exception as e:
            logger.warning("options daily report: %s", e)
    _scheduler.add_job(
        _options_daily_report_job,
        CronTrigger(day_of_week="mon-thu", hour=17, minute=0, timezone=ET),
        id="options_daily_report_5pm",
        replace_existing=True,
    )
    async def _terminal_daily_report_job():
        try:
            from . import telegram_events
            await telegram_events.dispatch_daily_report()
        except Exception as e:
            logger.warning("terminal daily report: %s", e)
    _scheduler.add_job(
        _terminal_daily_report_job,
        CronTrigger(day_of_week="mon-thu", hour=17, minute=2, timezone=ET),
        id="terminal_daily_report_502pm",
        replace_existing=True,
    )
    async def _options_weekly_report_job():
        try:
            from . import options_desk
            await options_desk.dispatch_options_weekly_report()
        except Exception as e:
            logger.warning("options weekly report: %s", e)
    _scheduler.add_job(
        _options_weekly_report_job,
        CronTrigger(day_of_week="fri", hour=21, minute=0, timezone=ET),
        id="options_weekly_report_friday_9pm",
        replace_existing=True,
    )
    async def _terminal_weekly_report_job():
        try:
            from . import telegram_events
            await telegram_events.dispatch_weekly_report()
        except Exception as e:
            logger.warning("terminal weekly report: %s", e)
    _scheduler.add_job(
        _terminal_weekly_report_job,
        CronTrigger(day_of_week="fri", hour=21, minute=2, timezone=ET),
        id="terminal_weekly_report_friday_902pm",
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
    async def _day_start_equity_snapshot_job():
        try:
            from . import safety
            result = await safety.snapshot_day_start_equity(source="scheduler_0928")
            await log_activity("Daily loss baseline refreshed", "info", result)
        except Exception as e:
            logger.warning("daily loss baseline: %s", e)
    _scheduler.add_job(
        _day_start_equity_snapshot_job,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=28, timezone=ET),
        id="daily_loss_baseline_0928",
        replace_existing=True,
    )

    async def _trading_halt_job():
        try:
            from . import trading_halts

            await trading_halts.check_and_alert()
        except Exception as e:
            logger.warning("trading halt monitor: %s", e)
    _scheduler.add_job(
        _trading_halt_job,
        CronTrigger(day_of_week="mon-fri", hour="4-20", minute="*", timezone=ET),
        id="trading_halt_monitor",
        replace_existing=True,
    )

    async def _position_monitor():
        try:
            from . import options_desk, pm_ratchet, trade_floor, trade_floor_phases
            await trade_floor.sync_positions_and_close_settled()
            try:
                from . import safety
                await safety.check_daily_loss(source="position_monitor")
            except Exception as breaker_exc:
                logger.warning("daily loss breaker: %s", breaker_exc)
            await pm_ratchet.process_open_ratchets()
            await options_desk.monitor_open_positions(enforce_hard_stop=True)
            # v5.3 — run the three-phase exit logic on every sync
            await trade_floor_phases.process_phase_exits()
        except Exception as e:
            logger.warning("position monitor: %s", e)
    async def _position_monitor_with_snapshot():
        management: dict[str, dict] = {}
        try:
            await _position_monitor()
            management["legacy_position_monitor"] = {"ok": True}
        except Exception as e:
            logger.warning("position monitor wrapper: %s", e)
            management["legacy_position_monitor"] = {"ok": False, "reason": e.__class__.__name__}
        try:
            snapshot = await persist_live_position_snapshot(
                triggered_by="scheduler_position_monitor_15m",
                management=management,
            )
            await log_activity(
                f"Live position snapshot: {snapshot['totals']['positions']} positions, "
                f"{snapshot['totals']['open_orders']} open orders, "
                f"unrealized={snapshot['totals']['unrealized_pl']}",
                "info",
                snapshot,
            )
        except Exception as e:
            logger.warning("position monitor snapshot: %s", e)

    _scheduler.add_job(
        _position_monitor_with_snapshot,
        CronTrigger(day_of_week="mon-fri", hour="9-16", minute="*/5", timezone=ET),
        id="position_monitor", replace_existing=True,
    )

    async def _live_position_snapshot_job():
        try:
            await persist_live_position_snapshot(triggered_by="scheduler_live_snapshot_15m")
        except Exception as e:
            logger.warning("live position snapshot: %s", e)
    _scheduler.add_job(
        _live_position_snapshot_job,
        IntervalTrigger(minutes=15),
        id="live_position_snapshot_15m",
        replace_existing=True,
    )

    async def _research_lab_job():
        try:
            from . import research_lab
            res = await research_lab.refresh_snapshot(triggered_by="scheduler")
            stats = res.get("stats", {})
            await log_activity(
                f"R&D refresh: decisions={stats.get('reconstructed_decisions', 0)} "
                f"matured={stats.get('matured_outcomes', 0)} experiments={stats.get('active_experiments', 0)}",
                "info",
            )
        except Exception as e:
            logger.warning("research lab refresh: %s", e)
    _scheduler.add_job(
        _research_lab_job,
        IntervalTrigger(hours=1),
        id="research_lab_hourly_refresh",
        replace_existing=True,
    )

    # v5.2 — stale-order sweep every hour: cancel any TF buy still unfilled > 24h
    async def _stale_order_sweep():
        try:
            from . import trade_floor
            await trade_floor.cancel_stale_orders(max_age_hours=24)
        except Exception as e:
            logger.warning("stale order sweep: %s", e)
    _scheduler.add_job(
        _stale_order_sweep,
        CronTrigger(minute="5", timezone=ET),  # top of every hour + 5 min
        id="stale_order_sweep", replace_existing=True,
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
