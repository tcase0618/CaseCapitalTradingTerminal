"""Telegram bot: send messages, register webhook, parse commands."""
from __future__ import annotations
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx
import pytz

from . import claude_service, risk_target, scanner, usaspending
from .db import get_db, log_activity
from .scrapers import fetch_quote

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")


def _api_url(method: str) -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    return f"https://api.telegram.org/bot{token}/{method}"


def _has_token() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN"))


def _default_chat_id() -> str:
    return os.environ.get("TELEGRAM_CHAT_ID", "")


async def send_message(text: str, chat_id: str | None = None, parse_mode: str = "HTML") -> bool:
    if not _has_token():
        return False
    chat_id = chat_id or _default_chat_id()
    if not chat_id:
        return False
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode,
                "disable_web_page_preview": True}
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
            r = await client.post(_api_url("setWebhook"),
                                    json={"url": url, "drop_pending_updates": True})
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


# ============== Formatters ==============
SIG_BADGE = {
    "insider_cluster_buy": "👥 INSIDER",
    "high_short_interest": "📉 SHORT",
    "upcoming_earnings": "📅 EARNINGS",
    "CONTRACT_SURGE": "🚀 CONTRACT_SURGE",
    "NEW_WINNER": "🏆 NEW_WINNER",
    "CONCENTRATION_WIN": "💎 CONCENTRATION_WIN",
    "MOMENTUM_STACK": "🌊 MOMENTUM_STACK",
    "BUDGET_SURGE": "💰 BUDGET_SURGE",
}


def _fmt_price(v):
    if v is None:
        return "—"
    try:
        return f"${float(v):,.2f}"
    except Exception:
        return str(v)


def _fmt_pct(v):
    if v is None:
        return "—"
    try:
        f = float(v)
        sign = "+" if f >= 0 else ""
        return f"{sign}{f:.1f}%"
    except Exception:
        return str(v)


def _now_et() -> str:
    return datetime.now(ET).strftime("%b %d %H:%M ET")


def format_stock_alert(r: dict[str, Any]) -> str:
    badges = " ".join(SIG_BADGE.get(s, s) for s in r.get("signals", []))
    risk = r.get("risk") or {}
    targets = r.get("targets") or {}
    risk_factors = risk.get("factors") or []
    contract_line = ""
    contracts = r.get("contracts") or []
    if contracts:
        c0 = contracts[0]
        contract_line = (f"\n🏛 Gov contract: {c0.get('agency')} — "
                         f"${(c0.get('amount') or 0)/1e6:.1f}M ({c0.get('award_id') or 'n/a'})")

    line_factors = "\n".join(f"   └ {f}" for f in risk_factors[:3]) or "   └ (none)"

    head = (
        f"📡 <b>STOCK INTEL</b> — {_now_et()}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎯 <b>${r['ticker']}</b> — <code>{r.get('signal_score',0)}/10</code> — "
        f"{risk.get('emoji','⚪')} <b>{risk.get('level','?')}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Signals: {badges}\n"
        f"💡 <i>{r.get('thesis','')}</i>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Entry zone: {_fmt_price(r.get('entry_low'))} — {_fmt_price(r.get('entry_high'))}\n\n"
        "🎯 Price targets:\n"
        f"   └ Bear case:  {_fmt_price(targets.get('target_low'))} ({_fmt_pct(targets.get('upside_low'))})\n"
        f"   └ Blended:    {_fmt_price(targets.get('target_blended'))} ({_fmt_pct(targets.get('upside_blended'))})\n"
        f"   └ Bull case:  {_fmt_price(targets.get('target_high'))} ({_fmt_pct(targets.get('upside_high'))})\n\n"
        f"🛑 Stop loss: {_fmt_price(r.get('stop_loss'))}\n"
        f"⏱ Horizon: {r.get('time_horizon','?')}\n"
        f"📅 Catalyst: {r.get('catalyst_date','—')}\n"
        f"🏆 Conviction: {r.get('conviction','?')}\n\n"
        "⚠️ Risk factors:\n"
        f"{line_factors}"
        f"{contract_line}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
    return head


def format_scan_summary(scan: dict[str, Any]) -> str:
    results = scan.get("results", [])
    n = len(results)
    raw = scan.get("raw_counts", {})
    return (
        f"📡 <b>STOCK INTEL — DAILY SCAN</b> {_now_et()}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>{n} stocks passed 2+ signal filter • "
        f"{scan.get('claude_calls_made', 0)} Claude call • "
        f"{scan.get('claude_cache_hits', 0)} cached</i>\n"
        f"<i>Sources: {raw.get('insider_clusters',0)} insider · "
        f"{raw.get('high_short_interest',0)} short · "
        f"{raw.get('upcoming_earnings',0)} earnings · "
        f"{raw.get('gov_public_tickers',0)} gov-public</i>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━"
    )


def format_brief(r: dict[str, Any]) -> str:
    risk = r.get("risk") or {}
    targets = r.get("targets") or {}
    badges = " ".join(SIG_BADGE.get(s, s) for s in r.get("signals", []))
    return (
        f"<b>${r['ticker']}</b> {risk.get('emoji','')} "
        f"<code>{r.get('signal_score',0)}/10</code> "
        f"{badges}\n"
        f"  💰 {_fmt_price(r.get('price'))} → {_fmt_price(targets.get('target_blended'))} "
        f"({_fmt_pct(targets.get('upside_blended'))})\n"
        f"  💡 {r.get('thesis','')}"
    )


def format_contracts_list(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No recent gov contracts to public companies."
    lines = ["🏛 <b>TOP RECENT GOV CONTRACTS</b>"]
    for r in rows:
        amount = r.get('amount') or 0
        lines.append(
            f"• <b>${r['ticker']}</b> — {r.get('agency','?')[:35]}\n"
            f"  ${amount/1e6:.1f}M · {r.get('recipient','')[:40]}"
        )
    return "\n".join(lines)


# ============== Command handler ==============
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
            "<b>Market signals:</b>\n"
            "/scan — full scan now\n"
            "/analyze TICKER — deep dive\n\n"
            "<b>Watchlist & alerts:</b>\n"
            "/watchlist · /watch TICKER · /unwatch TICKER\n"
            "/alert TICKER PRICE · /alerts\n\n"
            "<b>Government contracts:</b>\n"
            "/contracts — top recent gov awards\n"
            "/scan_gov — gov contracts scan only\n"
            "/agency NAME — awards from agency\n"
            "/watchlist_contracts — recent wins for watchlist\n\n"
            "<b>Analysis:</b>\n"
            "/risk TICKER — risk breakdown\n"
            "/target TICKER — price targets (3 methods)\n"
            "/compare TICKER1 TICKER2 — side-by-side",
            chat_id=chat_id,
        )
        return

    if cmd == "/scan":
        await send_message("⏳ Running full scan...", chat_id=chat_id)
        scan = await scanner.run_scan(triggered_by=f"telegram:{chat_id}")
        await send_message(format_scan_summary(scan), chat_id=chat_id)
        for r in scan.get("results", [])[:10]:
            await send_message(format_stock_alert(r), chat_id=chat_id)
        if not scan.get("results"):
            await send_message("No tickers passed 2+ signal filter.", chat_id=chat_id)
        return

    if cmd == "/scan_gov":
        await send_message("⏳ Running government contracts scan...", chat_id=chat_id)
        scan = await scanner.run_gov_scan_only(triggered_by=f"telegram:{chat_id}")
        await send_message(
            f"🏛 <b>GOV CONTRACTS SCAN</b> — {len(scan['results'])} public-company hits",
            chat_id=chat_id,
        )
        for r in scan["results"][:10]:
            await send_message(format_brief(r), chat_id=chat_id)
        # Budget surges
        bs = scan.get("budget_surges", [])
        if bs:
            lines = ["💰 <b>AGENCY BUDGET SURGES</b>"]
            for b in bs[:5]:
                lines.append(f"• {b['agency']} — +{b['pct_increase']}% vs 3-mo avg "
                              f"(exposed: {', '.join(b.get('exposed_tickers') or []) or '—'})")
            await send_message("\n".join(lines), chat_id=chat_id)
        return

    if cmd == "/contracts":
        rows = await usaspending.list_recent_contracts_for_tickers(limit=5)
        await send_message(format_contracts_list(rows), chat_id=chat_id)
        return

    if cmd == "/agency":
        if not args:
            await send_message("Usage: <code>/agency Department of Defense</code>", chat_id=chat_id)
            return
        name = " ".join(args)
        rows = await usaspending.awards_for_agency(name, days=30, limit=15)
        if not rows:
            await send_message(f"No public-company awards from <b>{name}</b> in last 30d.", chat_id=chat_id)
            return
        # Group by ticker
        by_t: dict[str, list[dict]] = {}
        for r in rows:
            by_t.setdefault(r["ticker"], []).append(r)
        lines = [f"🏛 <b>{name}</b> — last 30d public-company winners:"]
        for ticker, items in by_t.items():
            total = sum(i["amount"] for i in items)
            fund = await risk_target.fetch_fundamentals(ticker)
            risk = risk_target.compute_risk(fund or {}, [], None, None, 0)
            targets = risk_target.compute_targets(fund or {}, [], None)
            lines.append(
                f"• <b>${ticker}</b> {risk['emoji']} — ${total/1e6:.1f}M total "
                f"({len(items)} awards) → target {_fmt_price(targets.get('target_blended'))} "
                f"({_fmt_pct(targets.get('upside_blended'))})"
            )
        await send_message("\n".join(lines), chat_id=chat_id)
        return

    if cmd == "/watchlist_contracts":
        items = await db.watchlist.find({"chat_id": chat_id}, {"_id": 0}).to_list(200)
        if not items:
            await send_message("Watchlist empty.", chat_id=chat_id)
            return
        lines = ["📋 <b>Watchlist — recent gov wins (7d)</b>"]
        any_hits = False
        for it in items:
            wins = await usaspending.recent_wins_for_ticker(it["ticker"], days=7)
            if not wins:
                continue
            any_hits = True
            total = sum(w["amount"] for w in wins)
            lines.append(f"• <b>${it['ticker']}</b>: {len(wins)} wins, ${total/1e6:.1f}M total")
            for w in wins[:2]:
                lines.append(f"   └ {w['agency'][:40]} ${w['amount']/1e6:.1f}M")
        if not any_hits:
            lines.append("No gov contract wins in last 7d.")
        await send_message("\n".join(lines), chat_id=chat_id)
        return

    if cmd == "/risk":
        if not args:
            await send_message("Usage: <code>/risk TICKER</code>", chat_id=chat_id)
            return
        ticker = args[0].upper().lstrip("$")
        fund = await risk_target.fetch_fundamentals(ticker)
        if not fund:
            await send_message(f"Could not fetch data for ${ticker}.", chat_id=chat_id)
            return
        risk = risk_target.compute_risk(fund, [], None, None, 0)
        lines = [
            f"⚠️ <b>RISK — ${ticker}</b>",
            f"{risk['emoji']} <b>{risk['level']}</b> (score {risk['score']})",
            "━━━━━━━━━━━━━━━━━━",
            f"Market cap: ${(fund.get('market_cap') or 0)/1e6:,.0f}M",
            f"Beta: {fund.get('beta') or '—'}",
            f"Trailing EPS: {fund.get('trailing_eps') or '—'}",
            "",
            "<b>Factors:</b>",
        ]
        for f in risk["factors"]:
            lines.append(f"   └ {f}")
        if not risk["factors"]:
            lines.append("   └ (none)")
        await send_message("\n".join(lines), chat_id=chat_id)
        return

    if cmd == "/target":
        if not args:
            await send_message("Usage: <code>/target TICKER</code>", chat_id=chat_id)
            return
        ticker = args[0].upper().lstrip("$")
        fund = await risk_target.fetch_fundamentals(ticker)
        if not fund or not fund.get("price"):
            await send_message(f"Could not fetch data for ${ticker}.", chat_id=chat_id)
            return
        targets = risk_target.compute_targets(fund, [], None)
        m = targets["methods"]
        lines = [
            f"🎯 <b>TARGETS — ${ticker}</b>",
            f"Current price: {_fmt_price(targets.get('current_price'))}",
            "━━━━━━━━━━━━━━━━━━",
            "<b>Method 1 — Contract Revenue Multiple:</b>",
            (f"   └ {_fmt_price(m['contract_revenue_multiple']['value'])}"
             f" (P/S {m['contract_revenue_multiple']['ps_used']:.1f} for {m['contract_revenue_multiple']['sector']})"
             if m["contract_revenue_multiple"] else "   └ (no contract data)"),
            "<b>Method 2 — Analyst Consensus:</b>",
            (f"   └ {_fmt_price(m['analyst_consensus']['value'])}"
             f" (range {_fmt_price(m['analyst_consensus'].get('low'))}–{_fmt_price(m['analyst_consensus'].get('high'))})"
             if m["analyst_consensus"] else "   └ (no analyst targets)"),
            "<b>Method 3 — Signal-Adjusted:</b>",
            (f"   └ {_fmt_price(m['signal_adjusted']['value'])}"
             f" ({_fmt_pct(m['signal_adjusted']['uplift_pct'])} avg from {m['signal_adjusted']['match']})"
             if m["signal_adjusted"] else "   └ (no signals)"),
            "━━━━━━━━━━━━━━━━━━",
            f"<b>Blended: {_fmt_price(targets.get('target_blended'))} "
            f"({_fmt_pct(targets.get('upside_blended'))})</b>",
        ]
        await send_message("\n".join(lines), chat_id=chat_id)
        return

    if cmd == "/compare":
        if len(args) < 2:
            await send_message("Usage: <code>/compare TICKER1 TICKER2</code>", chat_id=chat_id)
            return
        t1, t2 = args[0].upper().lstrip("$"), args[1].upper().lstrip("$")
        f1, f2 = await asyncio.gather(
            risk_target.fetch_fundamentals(t1), risk_target.fetch_fundamentals(t2)
        )
        r1 = risk_target.compute_risk(f1 or {}, [], None, None, 0)
        r2 = risk_target.compute_risk(f2 or {}, [], None, None, 0)
        tg1 = risk_target.compute_targets(f1 or {}, [], None)
        tg2 = risk_target.compute_targets(f2 or {}, [], None)
        lines = [
            f"⚖️ <b>${t1}</b>  vs  <b>${t2}</b>",
            "━━━━━━━━━━━━━━━━━━",
            f"Price:   {_fmt_price((f1 or {}).get('price'))}   |   {_fmt_price((f2 or {}).get('price'))}",
            f"Risk:    {r1['emoji']} {r1['level']}   |   {r2['emoji']} {r2['level']}",
            f"Target:  {_fmt_price(tg1.get('target_blended'))} ({_fmt_pct(tg1.get('upside_blended'))})"
            f"   |   {_fmt_price(tg2.get('target_blended'))} ({_fmt_pct(tg2.get('upside_blended'))})",
            f"Mkt cap: ${((f1 or {}).get('market_cap') or 0)/1e9:.1f}B"
            f"   |   ${((f2 or {}).get('market_cap') or 0)/1e9:.1f}B",
            f"Beta:    {(f1 or {}).get('beta') or '—'}   |   {(f2 or {}).get('beta') or '—'}",
        ]
        await send_message("\n".join(lines), chat_id=chat_id)
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
        fund = await risk_target.fetch_fundamentals(ticker)
        risk = risk_target.compute_risk(fund or {}, [], None, None, 0)
        targets = risk_target.compute_targets(fund or {}, [], None)
        a = await claude_service.analyze_single(ticker)
        if not a:
            await send_message(f"Could not analyze ${ticker}.", chat_id=chat_id)
            return
        # Build alert format
        merged = {
            "ticker": ticker,
            "signals": [],
            "signal_score": a.get("signal_score", 0),
            "thesis": a.get("thesis", ""),
            "entry_low": a.get("entry_low"),
            "entry_high": a.get("entry_high"),
            "catalyst_date": a.get("catalyst_date", ""),
            "conviction": a.get("conviction", "medium"),
            "time_horizon": a.get("time_horizon", "medium"),
            "stop_loss": a.get("stop_loss") or risk_target.compute_stop_loss(fund or {}, risk),
            "price": (fund or {}).get("price"),
            "risk": risk,
            "targets": targets,
            "contracts": [],
        }
        await send_message(format_stock_alert(merged), chat_id=chat_id)
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
        await send_message(f"✅ Added <b>${ticker}</b>.", chat_id=chat_id)
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
            await send_message("Watchlist empty.", chat_id=chat_id)
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
            "ticker": ticker, "target_price": price, "chat_id": chat_id,
            "created_at": datetime.now(timezone.utc).isoformat(), "triggered": False,
        })
        await send_message(f"🔔 Alert: <b>${ticker}</b> @ <code>${price}</code>", chat_id=chat_id)
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
        prev = float(alert.get("baseline_price") or price)
        if "baseline_price" not in alert:
            await db.alerts.update_one({"_id": alert["_id"]},
                                         {"$set": {"baseline_price": price}})
            continue
        crossed = (prev < target <= price) or (prev > target >= price) or price == target
        if crossed:
            await send_message(
                f"🚨 <b>${ticker}</b> hit <code>${target}</code> "
                f"(now <code>${price}</code>)",
                chat_id=alert.get("chat_id"),
            )
            await db.alerts.update_one(
                {"_id": alert["_id"]},
                {"$set": {"triggered": True,
                           "triggered_at": datetime.now(timezone.utc).isoformat(),
                           "triggered_price": price}},
            )
            fired += 1
        else:
            await db.alerts.update_one({"_id": alert["_id"]},
                                         {"$set": {"baseline_price": price}})
    return fired


# Need import here to avoid circular at top
import asyncio  # noqa: E402
