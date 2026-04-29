"""Telegram bot: send messages, register webhook, parse commands."""
from __future__ import annotations
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from . import claude_service, scanner
from .db import get_db, log_activity
from .scrapers import fetch_quote

logger = logging.getLogger(__name__)


def _api_url(method: str) -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    return f"https://api.telegram.org/bot{token}/{method}"


def _has_token() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN"))


def _default_chat_id() -> str:
    return os.environ.get("TELEGRAM_CHAT_ID", "")


async def send_message(text: str, chat_id: str | None = None, parse_mode: str = "HTML") -> bool:
    if not _has_token():
        logger.info("Telegram token missing; skip send")
        return False
    chat_id = chat_id or _default_chat_id()
    if not chat_id:
        logger.info("Telegram chat_id missing; skip send")
        return False
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode, "disable_web_page_preview": True}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(_api_url("sendMessage"), json=payload)
        if r.status_code != 200:
            logger.warning("Telegram send failed (%s): %s", r.status_code, r.text[:200])
            return False
        return True
    except Exception as e:
        logger.warning("Telegram send exception: %s", e)
        return False


async def register_webhook(public_base_url: str) -> dict[str, Any]:
    if not _has_token():
        return {"ok": False, "reason": "no token"}
    url = f"{public_base_url.rstrip('/')}/api/telegram/webhook"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(_api_url("setWebhook"), json={"url": url, "drop_pending_updates": True})
        data = r.json()
        await get_db().bot_state.update_one(
            {"_id": "state"},
            {"$set": {"webhook_url": url, "webhook_set_at": datetime.now(timezone.utc).isoformat(),
                       "webhook_response": data}},
            upsert=True,
        )
        return data
    except Exception as e:
        logger.warning("setWebhook failed: %s", e)
        return {"ok": False, "error": str(e)}


# ---------- Formatters ----------
def _score_emoji(score: int) -> str:
    if score >= 9:
        return "🔥"
    if score >= 7:
        return "🟢"
    if score >= 5:
        return "🟡"
    return "⚪"


def _signals_emoji(signals: list[str]) -> str:
    m = {"insider_cluster_buy": "👥", "high_short_interest": "📉", "upcoming_earnings": "📅"}
    return "".join(m.get(s, "•") for s in signals)


def format_scan_results(scan: dict[str, Any]) -> str:
    results = scan.get("results", [])[:10]
    if not results:
        return (
            "🤖 <b>Stock Intel Scan</b>\n"
            f"No tickers passed the 2+ signal pre-filter.\n"
            f"<i>Insider {scan['raw_counts']['insider_clusters']} | "
            f"Short {scan['raw_counts']['high_short_interest']} | "
            f"Earnings {scan['raw_counts']['upcoming_earnings']}</i>"
        )
    lines = ["🤖 <b>STOCK INTEL — DAILY SCAN</b>"]
    lines.append(
        f"<i>{scan['pre_filter_passed']} passed pre-filter | "
        f"{scan['claude_calls_made']} fresh Claude calls | "
        f"{scan['claude_cache_hits']} cached</i>\n"
    )
    for r in results:
        lines.append(
            f"{_score_emoji(r.get('signal_score', 0))} <b>${r['ticker']}</b> "
            f"<code>[{r.get('signal_score', 0)}/10]</code> "
            f"{_signals_emoji(r.get('signals', []))}\n"
            f"<i>{r.get('thesis', '')}</i>\n"
            f"Entry: <code>{r.get('entry_zone', 'n/a')}</code> | "
            f"Catalyst: <code>{r.get('catalyst_date', 'n/a')}</code>\n"
        )
    return "\n".join(lines)


def format_analyze(ticker: str, a: dict[str, Any] | None, quote: dict[str, Any] | None) -> str:
    if not a:
        return f"⚠️ Could not analyze <b>${ticker.upper()}</b>."
    price_line = ""
    if quote and quote.get("price") is not None:
        price_line = f"Price: <code>${quote['price']}</code>\n"
    return (
        f"{_score_emoji(a.get('signal_score', 0))} <b>${a['ticker']}</b> "
        f"<code>[{a.get('signal_score', 0)}/10]</code>"
        f"{' (cached)' if a.get('cached') else ''}\n"
        f"{price_line}"
        f"<i>{a.get('thesis', '')}</i>\n"
        f"Entry: <code>{a.get('entry_zone', 'n/a')}</code>\n"
        f"Catalyst: <code>{a.get('catalyst_date', 'n/a')}</code>"
    )


# ---------- Command handler ----------
async def handle_update(update: dict[str, Any]) -> None:
    msg = update.get("message") or update.get("edited_message") or {}
    text = (msg.get("text") or "").strip()
    chat = msg.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    if not text or not chat_id:
        return
    await log_activity(f"Telegram cmd from {chat_id}: {text[:80]}", "info")

    parts = text.split()
    cmd = parts[0].lower().split("@")[0]
    args = parts[1:]
    db = get_db()

    if cmd in ("/start", "/help"):
        await send_message(
            "🤖 <b>Stock Intel Bot</b>\n"
            "Commands:\n"
            "/scan — run a fresh scan now\n"
            "/analyze TICKER — deep dive\n"
            "/watchlist — show tracked tickers\n"
            "/watch TICKER — add to watchlist\n"
            "/unwatch TICKER — remove\n"
            "/alert TICKER PRICE — set price alert\n"
            "/alerts — list price alerts",
            chat_id=chat_id,
        )
        return

    if cmd == "/scan":
        await send_message("⏳ Running scan...", chat_id=chat_id)
        scan = await scanner.run_scan(triggered_by=f"telegram:{chat_id}")
        await send_message(format_scan_results(scan), chat_id=chat_id)
        return

    if cmd == "/analyze":
        if not args:
            await send_message("Usage: <code>/analyze TICKER</code>", chat_id=chat_id)
            return
        ticker = args[0].upper().lstrip("$")
        if not re.match(r"^[A-Z\.\-]{1,6}$", ticker):
            await send_message("Invalid ticker.", chat_id=chat_id)
            return
        await send_message(f"🔎 Analyzing <b>${ticker}</b>...", chat_id=chat_id)
        quote = await fetch_quote(ticker)
        a = await claude_service.analyze_single(ticker, context={"quote": quote})
        await send_message(format_analyze(ticker, a, quote), chat_id=chat_id)
        return

    if cmd in ("/watch", "/watchadd"):
        if not args:
            await send_message("Usage: <code>/watch TICKER</code>", chat_id=chat_id)
            return
        ticker = args[0].upper().lstrip("$")
        await db.watchlist.update_one(
            {"ticker": ticker, "chat_id": chat_id},
            {"$set": {"ticker": ticker, "chat_id": chat_id,
                       "added_at": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
        await send_message(f"✅ Added <b>${ticker}</b> to watchlist.", chat_id=chat_id)
        return

    if cmd == "/unwatch":
        if not args:
            await send_message("Usage: <code>/unwatch TICKER</code>", chat_id=chat_id)
            return
        ticker = args[0].upper().lstrip("$")
        res = await db.watchlist.delete_one({"ticker": ticker, "chat_id": chat_id})
        await send_message(
            f"🗑️ Removed <b>${ticker}</b>." if res.deleted_count else "Not in watchlist.",
            chat_id=chat_id,
        )
        return

    if cmd == "/watchlist":
        items = await db.watchlist.find({"chat_id": chat_id}, {"_id": 0}).to_list(200)
        if not items:
            await send_message("Watchlist is empty.", chat_id=chat_id)
            return
        lines = ["📋 <b>Watchlist</b>"]
        for it in items:
            q = await fetch_quote(it["ticker"])
            price = f"${q['price']}" if q and q.get("price") is not None else "n/a"
            lines.append(f"• <b>${it['ticker']}</b> — <code>{price}</code>")
        await send_message("\n".join(lines), chat_id=chat_id)
        return

    if cmd == "/alert":
        if len(args) < 2:
            await send_message("Usage: <code>/alert TICKER PRICE</code>", chat_id=chat_id)
            return
        ticker = args[0].upper().lstrip("$")
        try:
            price = float(args[1].replace("$", ""))
        except ValueError:
            await send_message("Invalid price.", chat_id=chat_id)
            return
        await db.alerts.insert_one({
            "ticker": ticker,
            "target_price": price,
            "chat_id": chat_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "triggered": False,
        })
        await send_message(f"🔔 Alert set: <b>${ticker}</b> @ <code>${price}</code>", chat_id=chat_id)
        return

    if cmd == "/alerts":
        items = await db.alerts.find(
            {"chat_id": chat_id, "triggered": False}, {"_id": 0}
        ).to_list(200)
        if not items:
            await send_message("No active alerts.", chat_id=chat_id)
            return
        lines = ["🔔 <b>Active Alerts</b>"]
        for it in items:
            lines.append(f"• <b>${it['ticker']}</b> @ <code>${it['target_price']}</code>")
        await send_message("\n".join(lines), chat_id=chat_id)
        return

    await send_message("Unknown command. Try /help.", chat_id=chat_id)


async def check_alerts() -> int:
    """Check all active alerts; trigger Telegram on price hit. Returns # fired."""
    db = get_db()
    fired = 0
    cursor = db.alerts.find({"triggered": False})
    async for alert in cursor:
        ticker = alert["ticker"]
        target = float(alert["target_price"])
        q = await fetch_quote(ticker)
        if not q or q.get("price") is None:
            continue
        price = float(q["price"])
        # Direction-agnostic: trigger if reached or crossed
        prev = float(alert.get("baseline_price") or price)
        # Initialize baseline on first check
        if "baseline_price" not in alert:
            await db.alerts.update_one({"_id": alert["_id"]}, {"$set": {"baseline_price": price}})
            continue
        crossed = (prev < target <= price) or (prev > target >= price) or price == target
        if crossed:
            await send_message(
                f"🚨 <b>${ticker}</b> hit target <code>${target}</code> "
                f"(now <code>${price}</code>)",
                chat_id=alert.get("chat_id"),
            )
            await db.alerts.update_one(
                {"_id": alert["_id"]},
                {"$set": {"triggered": True, "triggered_at": datetime.now(timezone.utc).isoformat(),
                           "triggered_price": price}},
            )
            fired += 1
        else:
            await db.alerts.update_one({"_id": alert["_id"]}, {"$set": {"baseline_price": price}})
    return fired
