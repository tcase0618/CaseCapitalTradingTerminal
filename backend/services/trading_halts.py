"""Trading halt monitor.

Uses Nasdaq Trader's free trade-halt RSS feed as the public authority for
exchange halt/pause events. The monitor is stateful: first run bootstraps the
feed without alert spam, later runs alert only newly observed halt keys.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

import httpx
import pytz

from .db import get_db, log_activity, stamped

HALT_RSS_URL = "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts"
NDAQ_NS = "{http://www.nasdaqtrader.com/}"
ET_TZ = pytz.timezone("America/New_York")


REASON_CODES = {
    "T1": "News pending",
    "T2": "News released",
    "T5": "Single-stock trading pause",
    "T6": "Regulatory concern",
    "T8": "Exchange requested info",
    "T12": "Additional information requested",
    "H4": "Noncompliance",
    "H9": "Not current in SEC filings",
    "H10": "SEC trading suspension",
    "H11": "Regulatory halt",
    "LUDP": "Limit up/down pause",
    "MWC1": "Market-wide circuit breaker level 1",
    "MWC2": "Market-wide circuit breaker level 2",
    "MWC3": "Market-wide circuit breaker level 3",
}


def _text(item: ET.Element, tag: str) -> str:
    node = item.find(f"{NDAQ_NS}{tag}")
    return (node.text or "").strip() if node is not None else ""


def _parse_halt_dt(date_s: str, time_s: str) -> str | None:
    if not date_s or not time_s:
        return None
    clean_time = time_s.split(".", 1)[0]
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M"):
        try:
            naive = datetime.strptime(f"{date_s} {clean_time}", fmt)
            return ET_TZ.localize(naive).astimezone(timezone.utc).isoformat()
        except Exception:
            continue
    return None


def _event_key(row: dict[str, Any]) -> str:
    return "|".join([
        str(row.get("halt_date") or ""),
        str(row.get("halt_time") or ""),
        str(row.get("symbol") or ""),
        str(row.get("reason_code") or ""),
    ])


def _underlying_from_option_symbol(symbol: str) -> str | None:
    match = re.match(r"^([A-Z]{1,6})\d{6}[CP]\d{8}$", symbol or "")
    return match.group(1) if match else None


async def fetch_halts() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
        response = await client.get(HALT_RSS_URL)
    if response.status_code >= 400:
        return {"ok": False, "source": HALT_RSS_URL, "reason": f"http_{response.status_code}", "halts": []}

    text = response.text.lstrip("\ufeff")
    root = ET.fromstring(text)
    channel = root.find("channel")
    pub_date = channel.findtext("pubDate") if channel is not None else None
    items = channel.findall("item") if channel is not None else []
    halts: list[dict[str, Any]] = []
    for item in items:
        symbol = _text(item, "IssueSymbol") or (item.findtext("title") or "").strip().upper()
        reason_code = _text(item, "ReasonCode")
        halt_date = _text(item, "HaltDate")
        halt_time = _text(item, "HaltTime")
        resume_date = _text(item, "ResumptionDate")
        resume_quote = _text(item, "ResumptionQuoteTime")
        resume_trade = _text(item, "ResumptionTradeTime")
        row = {
            "symbol": symbol.upper(),
            "issue_name": _text(item, "IssueName"),
            "market": _text(item, "Market"),
            "halt_date": halt_date,
            "halt_time": halt_time,
            "halted_at": _parse_halt_dt(halt_date, halt_time),
            "reason_code": reason_code,
            "reason": REASON_CODES.get(reason_code, reason_code or "Unknown"),
            "pause_threshold_price": _text(item, "PauseThresholdPrice"),
            "resumption_date": resume_date,
            "resumption_quote_time": resume_quote,
            "resumption_trade_time": resume_trade,
            "active": not bool(resume_trade or resume_quote or resume_date),
            "source": HALT_RSS_URL,
        }
        row["event_key"] = _event_key(row)
        halts.append(row)
    return {"ok": True, "source": HALT_RSS_URL, "pub_date": pub_date, "halts": halts}


async def _held_underlyings() -> set[str]:
    symbols: set[str] = set()
    try:
        from . import trade_floor

        for pos in await trade_floor.list_positions():
            sym = str(pos.get("symbol") or "").upper()
            if sym:
                symbols.add(sym)
    except Exception:
        pass
    try:
        from . import options_desk

        opt_pos = await options_desk.positions()
        for pos in opt_pos.get("positions") or []:
            sym = str(pos.get("symbol") or "").upper()
            root = _underlying_from_option_symbol(sym)
            if root:
                symbols.add(root)
            elif sym and len(sym) <= 6:
                symbols.add(sym)
    except Exception:
        pass
    return symbols


def build_halt_message(events: list[dict[str, Any]], *, held: set[str] | None = None) -> str:
    held = held or set()
    impacted = [e for e in events if e.get("symbol") in held]
    title = "CASE CAPITAL TRADING HALT"
    if impacted:
        title += " - HELD POSITION IMPACT"
    lines = [
        f"<b>{title}</b>",
        f"<code>{datetime.now(ET_TZ).strftime('%b %d %H:%M ET')}</code>",
        "--------------------",
    ]
    for event in events[:8]:
        symbol = event.get("symbol") or "?"
        flag = "HELD" if symbol in held else "WATCH"
        lines.extend([
            f"<b>${symbol}</b> - <b>{event.get('reason_code') or '?'}</b> - {event.get('reason') or 'Unknown'}",
            f"{event.get('issue_name') or ''} / {event.get('market') or ''} / {flag}",
            f"Halt: {event.get('halt_date') or '?'} {event.get('halt_time') or '?'} ET",
        ])
        if event.get("resumption_trade_time") or event.get("resumption_quote_time"):
            lines.append(f"Resume: quote {event.get('resumption_quote_time') or '-'} / trade {event.get('resumption_trade_time') or '-'}")
        lines.append("")
    if len(events) > 8:
        lines.append(f"+ {len(events) - 8} more halt event(s)")
    lines.append("Source: Nasdaq Trader Trade Halt RSS")
    return "\n".join(lines).strip()


async def check_and_alert(force_alert: bool = False) -> dict[str, Any]:
    db = get_db()
    state = await db.bot_state.find_one({"_id": "trading_halt_monitor"}, {"_id": 0}) or {}
    bootstrapped = bool(state.get("bootstrapped"))
    payload = await fetch_halts()
    if not payload.get("ok"):
        await db.bot_state.update_one(
            {"_id": "trading_halt_monitor"},
            {"$set": stamped({"last_checked_at": datetime.now(timezone.utc).isoformat(), "last_error": payload.get("reason")})},
            upsert=True,
        )
        return {**payload, "alerts_sent": 0, "new_events": 0}

    active_halts = [h for h in payload.get("halts") or [] if h.get("active")]
    new_events: list[dict[str, Any]] = []
    for event in active_halts:
        exists = await db.trading_halt_events.find_one({"event_key": event["event_key"]}, {"_id": 1})
        if exists:
            continue
        doc = stamped({**event, "first_seen_at": datetime.now(timezone.utc).isoformat(), "notified": False})
        await db.trading_halt_events.insert_one(doc)
        new_events.append(doc)

    alerts_sent = 0
    should_alert = force_alert or bootstrapped
    if should_alert and new_events:
        from . import telegram_service

        held = await _held_underlyings()
        message = build_halt_message(new_events, held=held)
        if await telegram_service.send_message(message):
            alerts_sent = 1
            keys = [e["event_key"] for e in new_events]
            await db.trading_halt_events.update_many(
                {"event_key": {"$in": keys}},
                {"$set": {"notified": True, "notified_at": datetime.now(timezone.utc).isoformat()}},
            )

    await db.bot_state.update_one(
        {"_id": "trading_halt_monitor"},
        {"$set": stamped({
            "bootstrapped": True,
            "last_checked_at": datetime.now(timezone.utc).isoformat(),
            "last_pub_date": payload.get("pub_date"),
            "active_count": len(active_halts),
            "last_new_events": len(new_events),
            "last_alerts_sent": alerts_sent,
            "source": HALT_RSS_URL,
        })},
        upsert=True,
    )
    await log_activity("Trading halt monitor checked", meta={
        "active_count": len(active_halts),
        "new_events": len(new_events),
        "alerts_sent": alerts_sent,
        "source": HALT_RSS_URL,
    })
    return {
        "ok": True,
        "source": HALT_RSS_URL,
        "pub_date": payload.get("pub_date"),
        "bootstrapped": bootstrapped,
        "active_count": len(active_halts),
        "new_events": len(new_events),
        "alerts_sent": alerts_sent,
        "events": new_events,
    }


async def latest(limit: int = 25) -> dict[str, Any]:
    db = get_db()
    limit = max(1, min(int(limit or 25), 100))
    rows = await db.trading_halt_events.find({}, {"_id": 0}).sort("created_at", -1).to_list(limit)
    state = await db.bot_state.find_one({"_id": "trading_halt_monitor"}, {"_id": 0}) or {}
    return {"ok": True, "source": HALT_RSS_URL, "state": state, "count": len(rows), "events": rows}
