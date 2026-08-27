"""Telegram bot: send messages, register webhook, parse commands."""
from __future__ import annotations
import asyncio  # noqa: F401
import html
import hashlib
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytz
from pymongo.errors import DuplicateKeyError

from . import claude_service, risk_target, scanner, usaspending
from .db import get_db, log_activity
from .scrapers import fetch_quote

logger = logging.getLogger(__name__)
ET = pytz.timezone("America/New_York")


OUTBOUND_TEXT_REPLACEMENTS = {
    "AXIOM INTELLIGENCE": "CASE CAPITAL COMMANDS",
    "AXIOM TRACKER": "CASE CAPITAL SIGNAL TRACKER",
    "AXIOM CURVE": "CASE CAPITAL PERFORMANCE CURVE",
    "AXIOM INTEL": "CASE CAPITAL INTEL",
    "AXIOM v3.2": "Case Capital v3.2",
    "STOCK INTEL": "CASE CAPITAL | STOCK INTEL",
    "OPTIONS FILLS": "CASE CAPITAL | OPTIONS FILLS",
    "OPTIONS RATCHET UPDATE": "CASE CAPITAL | OPTIONS RISK UPDATE",
    "OPTIONS HARD STOP": "CASE CAPITAL | OPTIONS EXIT",
    "REGIME HALT": "CASE CAPITAL | REGIME GATE",
    "CASE CAPITAL KRONOS MORNING FORECAST": "CASE CAPITAL | KRONOS MORNING BRIEF",
    "CASE CAPITAL OPTIONS FUND REPORT": "CASE CAPITAL | OPTIONS DAILY REPORT",
    "CASE CAPITAL OPTIONS WEEKLY REPORT": "CASE CAPITAL | OPTIONS WEEKLY REPORT",
    "CASE CAPITAL DAILY OPS REPORT": "CASE CAPITAL | DAILY OPS",
    "CASE CAPITAL WEEKLY OPS REPORT": "CASE CAPITAL | WEEKLY TRUTH REPORT",
    "CASE CAPITAL QUALITY CONTROL": "CASE CAPITAL | QC GATE",
    "CASE CAPITAL QUALITY": "CASE CAPITAL | QUALITY REPORT",
    "CASE CAPITAL SCAN REPORT": "CASE CAPITAL | SCAN REPORT",
    "GOV CONTRACT SCAN": "CASE CAPITAL | GOV CONTRACT SCAN",
    "Running full scan...": "CASE CAPITAL | SCAN STARTED\nFull signal scan is running.",
    "Running fresh scan...": "CASE CAPITAL | SCAN STARTED\nFresh signal scan is running.",
    "Running gov contracts scan...": "CASE CAPITAL | GOV CONTRACT SCAN STARTED\nGovernment contract scan is running.",
    "AXIOM answers": "Case Capital answers",
}


async def ensure_telegram_outbound_indexes() -> None:
    db = get_db()
    await db.telegram_outbound_guard.create_index("expires_at", expireAfterSeconds=0)
    await db.telegram_outbound_guard.create_index([("kind", 1), ("sent", 1), ("created_at", -1)])


def _normalize_outbound_text(text: str) -> str:
    cleaned = text
    for old, new in OUTBOUND_TEXT_REPLACEMENTS.items():
        cleaned = cleaned.replace(old, new)
    while "CASE CAPITAL | CASE CAPITAL | " in cleaned:
        cleaned = cleaned.replace("CASE CAPITAL | CASE CAPITAL | ", "CASE CAPITAL | ")
    cleaned = re.sub(r"\n{4,}", "\n\n\n", cleaned)
    return cleaned.strip()


def _outbound_kind(text: str) -> str:
    upper = str(text or "").upper()
    if "CASE CAPITAL | SCAN REPORT" in upper:
        return "scan_report"
    if "CASE SCORE" in upper and ("PHARMA" in upper or "PDUFA" in upper or "BINARY FDA" in upper):
        return "pharma_alert"
    return "generic"


def _single_consolidated_scan_only() -> bool:
    """Return whether production Telegram is restricted to the scan digest."""
    return os.environ.get("TELEGRAM_SINGLE_CONSOLIDATED_SCAN_ONLY", "false").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _outbound_cooldown(kind: str) -> timedelta:
    if kind == "scan_report":
        return timedelta(minutes=int(os.environ.get("TELEGRAM_SCAN_REPORT_THROTTLE_MINUTES", "10") or 10))
    if kind == "pharma_alert":
        return timedelta(minutes=int(os.environ.get("TELEGRAM_PHARMA_ALERT_COOLDOWN_MINUTES", "360") or 360))
    return timedelta(seconds=int(os.environ.get("TELEGRAM_EXACT_DUPLICATE_SECONDS", "45") or 45))


async def _should_skip_outbound(text: str) -> tuple[bool, str]:
    kind = _outbound_kind(text)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]
    now = datetime.now(timezone.utc)
    cooldown = _outbound_cooldown(kind)
    expires_at = now + cooldown
    db = get_db()

    # This is an atomic cooldown lock, not a post-send audit check. The old
    # implementation checked for a sent row, then inserted a pending row. Two
    # scan paths starting seconds apart could both pass the check before either
    # one marked sent=true, causing duplicate Telegram pushes. The lock is owned
    # before Telegram is called, so concurrent senders fail closed.
    lock_id = f"telegram_outbound:{kind}:global" if kind == "scan_report" else f"telegram_outbound:{kind}:{digest}"
    try:
        result = await db.telegram_outbound_guard.update_one(
            {
                "_id": lock_id,
                "$or": [
                    {"expires_at": {"$lte": now}},
                    {"expires_at": {"$lte": now.isoformat()}},
                    {"expires_at": {"$exists": False}},
                ],
            },
            {
                "$set": {
                    "kind": kind,
                    "digest": digest,
                    "sent": False,
                    "created_at": now,
                    "expires_at": expires_at,
                    "cooldown_seconds": int(cooldown.total_seconds()),
                    "preview": text[:220],
                }
            },
            upsert=True,
        )
    except DuplicateKeyError:
        return True, f"{kind}_cooldown"
    except Exception as exc:
        logger.error("Telegram outbound guard unavailable; suppressing %s: %s", kind, exc)
        return True, "telegram_guard_unavailable"

    if not (result.upserted_id or result.modified_count):
        return True, f"{kind}_cooldown"
    return False, lock_id


async def _mark_outbound_sent(lock_id: str | None, text: str, sent: bool) -> None:
    if not lock_id or lock_id.endswith("_cooldown"):
        return
    try:
        await get_db().telegram_outbound_guard.update_one(
            {"_id": lock_id},
            {"$set": {"sent": bool(sent), "sent_at": datetime.now(timezone.utc).isoformat()}},
        )
    except Exception as exc:
        logger.warning("Telegram outbound guard update failed: %s", exc)


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
    text = _normalize_outbound_text(str(text or ""))
    outbound_kind = _outbound_kind(text)
    if _single_consolidated_scan_only() and outbound_kind != "scan_report":
        await log_activity(
            f"Telegram outbound suppressed by consolidated-scan policy: {outbound_kind}",
            "info",
        )
        return False
    chat_id = chat_id or _default_chat_id()
    if not chat_id:
        return False
    skip, guard_token = await _should_skip_outbound(text)
    if skip:
        await log_activity(f"Telegram outbound deduped: {guard_token}", "info")
        return False
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode,
                "disable_web_page_preview": True}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(_api_url("sendMessage"), json=payload)
        if r.status_code != 200:
            logger.warning("Telegram send failed (%s): %s", r.status_code, r.text[:200])
            # Plain-text fallback: if HTML parsing fails, retry without parse_mode
            # so the user always receives content (even if styling is lost).
            if parse_mode and r.status_code == 400 and "parse" in r.text.lower():
                stripped = re.sub(r"<[^>]+>", "", text)
                payload2 = {"chat_id": chat_id, "text": stripped,
                             "disable_web_page_preview": True}
                try:
                    async with httpx.AsyncClient(timeout=20.0) as client:
                        r2 = await client.post(_api_url("sendMessage"), json=payload2)
                    if r2.status_code == 200:
                        logger.info("Telegram plain-text fallback succeeded")
                        await _mark_outbound_sent(guard_token, text, True)
                        return True
                    logger.warning("Telegram plain-text fallback failed (%s): %s",
                                    r2.status_code, r2.text[:200])
                except Exception as e:
                    logger.warning("Telegram plain-text fallback exception: %s", e)
            return False
        await _mark_outbound_sent(guard_token, text, True)
        return True
    except Exception as e:
        logger.warning("Telegram send exception: %s", e)
        return False


async def register_webhook(public_base_url: str) -> dict[str, Any]:
    if not _has_token():
        return {"ok": False, "reason": "no token"}
    url = f"{public_base_url.rstrip('/')}/api/telegram/webhook"
    try:
        payload = {"url": url, "drop_pending_updates": True}
        webhook_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
        if webhook_secret:
            payload["secret_token"] = webhook_secret
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(_api_url("setWebhook"), json=payload)
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
    "CONGRESSIONAL_BUY": "🏛️ CONGRESS_BUY",
    "PRE_AWARD": "📋 PRE_AWARD",
    "SUB_BENEFICIARY": "🔗 SUB_BENEFICIARY",
    "UNUSUAL_FLOW": "⚡ UNUSUAL_FLOW",
    "CALL_SWEEP": "🔥 CALL_SWEEP",
}


def _esc(v: Any) -> str:
    """HTML-escape any dynamic content. Stray '<' chars in thesis/agency/recipient
    names break Telegram HTML parse mode (e.g. '<5%' -> 'Unsupported start tag')."""
    if v is None:
        return ""
    return html.escape(str(v), quote=False)


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
    badges = " ".join(SIG_BADGE.get(s, _esc(s)) for s in r.get("signals", []))
    risk = r.get("risk") or {}
    targets = r.get("targets") or {}
    sq = r.get("squeeze") or {}
    tt = r.get("time_target") or {}
    risk_factors = risk.get("factors") or []
    contract_line = ""
    contracts = r.get("contracts") or []
    if contracts:
        c0 = contracts[0]
        contract_line = (f"\n🏛 Gov contract: {_esc(c0.get('agency'))} — "
                         f"${(c0.get('amount') or 0)/1e6:.1f}M ({_esc(c0.get('award_id') or 'n/a')})")

    line_factors = "\n".join(f"   └ {_esc(f)}" for f in risk_factors[:3]) or "   └ (none)"

    sq_line = ""
    if sq.get("score") is not None:
        sq_line = f"\n🔀 Squeeze: <b>{sq['score']}/100</b> {sq.get('emoji','')}"

    tt_line = ""
    if tt.get("target_date"):
        tt_line = (f"\n📅 Time target: <b>{_esc(tt['target_date'])}</b> "
                   f"({tt.get('days_remaining', 0)}d)\n"
                   f"⏱ Hold: {tt.get('hold_period_low', 0)}–{tt.get('hold_period_high', 0)} days")

    fy_line = "\n🏛 <i>FY-end multiplier active (1.5x gov signals)</i>" if r.get("fy_multiplier_applied") else ""

    head = (
        f"📡 <b>STOCK INTEL</b> — {_now_et()}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🎯 <b>${_esc(r['ticker'])}</b> — <code>{r.get('signal_score',0)}/10</code> — "
        f"{risk.get('emoji','⚪')} <b>{_esc(risk.get('level','?'))}</b>{fy_line}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Signals: {badges}\n"
        f"💡 <i>{_esc(r.get('thesis',''))}</i>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Entry zone: {_fmt_price(r.get('entry_low'))} — {_fmt_price(r.get('entry_high'))}\n\n"
        "🎯 Price targets:\n"
        f"   └ Bear:    {_fmt_price(targets.get('target_low'))} ({_fmt_pct(targets.get('upside_low'))})\n"
        f"   └ Blended: {_fmt_price(targets.get('target_blended'))} ({_fmt_pct(targets.get('upside_blended'))})\n"
        f"   └ Bull:    {_fmt_price(targets.get('target_high'))} ({_fmt_pct(targets.get('upside_high'))})"
        f"{tt_line}{sq_line}\n\n"
        f"🛑 Stop loss: {_fmt_price(r.get('stop_loss'))}\n"
        f"🏆 Conviction: {_esc(r.get('conviction','?'))}\n\n"
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
    badges = " ".join(SIG_BADGE.get(s, _esc(s)) for s in r.get("signals", []))
    return (
        f"<b>${_esc(r['ticker'])}</b> {risk.get('emoji','')} "
        f"<code>{r.get('signal_score',0)}/10</code> "
        f"{badges}\n"
        f"  💰 {_fmt_price(r.get('price'))} → {_fmt_price(targets.get('target_blended'))} "
        f"({_fmt_pct(targets.get('upside_blended'))})\n"
        f"  💡 {_esc(r.get('thesis',''))}"
    )


def _format_options_block(opts: dict[str, Any] | None) -> str:
    """Telegram options block (Part 12). Returns '' when no options data."""
    if not opts:
        return ""
    strat = opts.get("strategy") or "LONG_CALL"
    name = _esc(opts.get("strategy_name") or strat.replace("_", " ").title())
    one = _esc(opts.get("one_liner") or opts.get("strategy_reason") or "")
    crush = opts.get("crush_risk") or "MODERATE"
    iv_rank = opts.get("iv_rank")
    iv_label = opts.get("iv_label") or ""
    flow = opts.get("flow") or {}
    bias = flow.get("flow_bias") or "NEUTRAL"
    cp = flow.get("call_put_ratio") or 0

    # AVOID block
    if strat == "AVOID_OPTIONS":
        return (
            f"\n⚠️ <b>OPTIONS — AVOID</b> · IV CRUSH RISK {_esc(crush)}\n"
            f"   └ Buy stock directly or wait until after catalyst"
        )

    contract = opts.get("contract") or {}
    spread = opts.get("spread") or {}

    if strat in ("BULL_CALL_SPREAD", "BEAR_PUT_SPREAD") and spread:
        return (
            f"\n🎯 <b>SPREAD — {name}</b>\n"
            f"   └ <i>{one}</i>\n"
            f"   └ Buy ${spread['buy_strike']}{contract.get('type','C')} / "
            f"Sell ${spread['sell_strike']}{contract.get('type','C')} exp {_esc(spread['expiration'])}\n"
            f"   └ Net debit: ${spread['net_debit']} · Max profit: ${spread['max_profit']}\n"
            f"   └ Max loss: ${spread['max_loss']} · BE: ${spread['break_even']} · R/R: {spread['risk_reward']}:1\n"
            f"   └ IV rank: {iv_rank}% {_esc(iv_label)}"
            f"{_crush_line(crush)}"
            f"\n⚡ FLOW: {_esc(bias)} · P/C ratio {cp}"
        )

    # Single-leg
    if not contract:
        return ""
    return (
        f"\n🎯 <b>OPTIONS PLAY — {name}</b>\n"
        f"   └ <i>{one}</i>\n"
        f"   └ Play: ${contract['strike']}{contract.get('type','C')} exp {_esc(contract['expiration'])}\n"
        f"   └ Premium: ~${contract['premium']} · Max loss: ${contract['max_loss']}\n"
        f"   └ R/R per contract · Liquidity: {_esc(contract.get('liquidity','?'))}\n"
        f"   └ IV rank: {iv_rank}% {_esc(iv_label)}"
        f"{_crush_line(crush)}"
        f"\n⚡ FLOW: {_esc(bias)} · P/C ratio {cp}"
    )


def _crush_line(crush: str) -> str:
    if crush in ("HIGH", "SEVERE"):
        return f"\n   └ ⚠️ IV CRUSH RISK: <b>{_esc(crush)}</b>"
    return ""


def _format_one_card(rank: int, r: dict[str, Any]) -> str:
    """v3.2 ticker card — adds Dark Horse, X Factor, Narrative Lock, Lottery."""
    risk = r.get("risk") or {}
    targets = r.get("targets") or {}
    tt = r.get("time_target") or {}
    badges = " ".join(SIG_BADGE.get(s, _esc(s)) for s in r.get("signals", [])[:6])
    rf = (risk.get("factors") or [])[:2]
    rf_line = " · ".join(_esc(f) for f in rf) if rf else "—"
    target_date = _esc(tt.get("target_date", "—"))
    hold = f"{tt.get('hold_period_low', 0)}–{tt.get('hold_period_high', 0)}d"
    beta = r.get("beta") or (r.get("fundamentals") or {}).get("beta")

    learning = r.get("learning_score") or r.get("signal_score", 0)

    # v3.2 alert lines
    extras: list[str] = []
    dh = r.get("dark_horse")
    if dh:
        extras.append(
            f"🐴 <b>DARK HORSE</b> · {dh.get('off_exchange_pct',0)}% off-exchange · "
            f"{int(dh.get('block_volume',0)/1000)}k shares · +{dh.get('premium_pct',0)}% premium"
        )
    xf = r.get("x_factor")
    if xf:
        prim = xf.get("primary_trigger") or {}
        plat = prim.get("platform") or "?"
        spike = prim.get("spike_x") or prim.get("ratio") or "?"
        bull = prim.get("bullish_pct") or 0
        extras.append(f"⚡ <b>X FACTOR</b> · {_esc(plat)} {spike}x spike · {bull}% bullish")
    if r.get("narrative_lock"):
        extras.append("🔒 <b>NARRATIVE LOCK — ALL SYSTEMS GO</b>")

    opts = r.get("options") or {}
    opts_line = ""
    if opts.get("contract"):
        c = opts["contract"]
        opts_line = (
            f"🎯 <b>{_esc(opts.get('strategy_name') or opts.get('strategy','OPTS'))}</b> · "
            f"${c.get('strike')}{c.get('type','')[:1]} {_esc(c.get('expiration',''))} "
            f"@${c.get('premium')} · IV {opts.get('iv_rank','?')}%"
        )

    lot_line = ""
    if r.get("lottery_tier"):
        ls = r.get("lottery_score") or 0
        tier = r.get("lottery_tier")
        # Find EV from lottery picks
        ev = r.get("ev") or {}
        from .lottery import TIER_LIMITS
        max_bet = TIER_LIMITS.get(tier, 0)
        p2x = ev.get("p_double", 0) * 100
        p10x = ev.get("p_10x", 0) * 100
        lot_line = (
            f"🎰 <b>{_esc(tier)}</b> {int(ls)}/100 · "
            f"P(2x): {p2x:.0f}% · P(10x): {p10x:.1f}% · Max: ${max_bet}"
        )

    risk_emoji = risk.get("emoji", "⚪")
    risk_letter = (risk.get("level", "?") or "?")[:1]

    lines = [
        f"<b>{rank}. ${_esc(r['ticker'])}</b> · <code>{r.get('signal_score',0)}/10</code> · "
        f"{risk_emoji}{risk_letter} · CASE SCORE <b>{learning}</b> "
        + ("🟢" if r.get("max_conviction") else ""),
        badges,
    ]
    lines.extend(extras)
    lines.append(f"<i>{_esc(r.get('thesis',''))}</i>")
    lines.append(
        f"💰 {_fmt_price(r.get('price'))} → <b>{_fmt_price(targets.get('target_blended'))}</b> "
        f"<code>{_fmt_pct(targets.get('upside_blended'))}</code> · {target_date} · {hold}"
    )
    stop_str = f"🛑 Stop {_fmt_price(r.get('stop_loss'))} · ⚠️ {rf_line}"
    if beta:
        stop_str += f" · Beta {beta:.1f}"
    lines.append(stop_str)
    if opts_line:
        lines.append(opts_line)
    if lot_line:
        lines.append(lot_line)
    return "\n".join(lines)


def _format_footer(results: list[dict[str, Any]], scan: dict[str, Any] | None = None) -> str:
    """v3.2 footer — lottery picks, earnings, dark horses, narrative locks."""
    if not results and not scan:
        return ""
    scan = scan or {}
    lines = ["━━━━━━━━━━━━━━━━━━━━━━━━━"]

    # Lottery — top 3 by score (only non-COLD)
    lot = sorted(
        [r for r in results if r.get("lottery_tier") and r["lottery_tier"] != "COLD"],
        key=lambda x: -(x.get("lottery_score") or 0),
    )[:3]
    if lot:
        lines.append("🎰 LOTTERY · " + " · ".join(
            f"${_esc(r['ticker'])} {int(r.get('lottery_score') or 0)}" for r in lot
        ))

    # Earnings this week — top 3 by beat probability
    earnings_summary = (scan.get("v32") or {}).get("earnings_summary") or {}
    # earnings rows are nested under v32.earnings — but we only stored counts.
    # Surface tickers from results that have earnings_this_week flag, then
    # if we have full earnings_week from v32, prefer probability-sorted top 3.
    e_tickers = [r for r in results if r.get("earnings_this_week")]
    if e_tickers:
        lines.append("📅 EARNINGS · " + " · ".join(
            f"${_esc(r['ticker'])}" for r in e_tickers[:3]
        ))

    # Dark Horse
    dh = [r for r in results if r.get("dark_horse")][:3]
    if dh:
        lines.append("🐴 DARK HORSE · " + " · ".join(f"${_esc(r['ticker'])}" for r in dh))

    # Narrative Lock
    nl = [r for r in results if r.get("narrative_lock")][:3]
    if nl:
        lines.append("🔒 NARRATIVE LOCK · " + " · ".join(f"${_esc(r['ticker'])}" for r in nl))

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
    duration = scan.get("duration_sec", 0)
    sig_count = sum(len(r.get("signals", [])) for r in results)
    lines.append(f"⚡ AXIOM v3.2 · {duration}s · {sig_count} signals fired")
    return "\n".join(lines)


def _format_header(scan: dict[str, Any], title: str = "CASE CAPITAL INTEL") -> str:
    """v3.2 header — adds Macro line."""
    rc = scan.get("raw_counts") or {}
    universe = scan.get("universe_size")
    macro = (scan.get("v32") or {}).get("macro") or {}
    imminent = macro.get("imminent") or []
    if imminent:
        m = imminent[0]
        macro_line = f"🌐 MACRO: <b>WARNING</b> · 📅 {_esc(m.get('tag'))} IN {m.get('days_until')} DAYS"
    else:
        macro_line = "🌐 MACRO: <b>CLEAR</b>"

    return (
        f"⚡ <b>{_esc(title)}</b> · {_now_et()}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📡 UNIVERSE: <b>{universe or '?'}</b>"
        f"  ·  ✅ ACQUIRED: <b>{scan.get('pre_filter_passed', 0)}</b>"
        f"  ·  🤖 BATCH: <b>{scan.get('claude_calls_made', 0)}</b>\n"
        f"📊 {rc.get('insider_clusters', 0)} INSIDER · "
        f"{rc.get('high_short_interest', 0)} SHORT · "
        f"{rc.get('upcoming_earnings', 0)} ERN · "
        f"{rc.get('gov_public_tickers', 0)} GOV\n"
        f"{macro_line}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━"
    )


TG_LIMIT = 4000  # leave buffer below 4096


def build_consolidated_messages(scan: dict[str, Any], title: str = "CASE CAPITAL INTEL") -> list[str]:
    """Greedy chunking: fits as many cards as possible per message, opens a new
    message when the next card would overflow. Header on first, footer on last.
    Never truncates mid-card (which would break HTML parsing)."""
    results = list(scan.get("results", []))
    results.sort(key=lambda r: r.get("signal_score", 0), reverse=True)

    header = _format_header(scan, title)
    footer = _format_footer(results, scan)
    sep = "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"

    if not results:
        return [header + "\n\n<i>No tickers passed 2+ signal filter.</i>\n" + footer]

    cards = [_format_one_card(i + 1, r) for i, r in enumerate(results)]

    # Greedy pack cards into messages
    chunks: list[list[str]] = [[]]
    # Budget for chunk 0 must include header; last chunk must include footer.
    # Start by reserving header for chunk 0.
    current_len = len(header) + len(sep)
    for card in cards:
        # If adding this card would exceed limit (with safety for footer/sep),
        # start a new chunk.
        # Reserve footer length only when on what could be the final chunk.
        add_len = len(card) + len(sep)
        if current_len + add_len > TG_LIMIT and chunks[-1]:
            chunks.append([])
            current_len = 0
        chunks[-1].append(card)
        current_len += add_len

    # Build message strings
    msgs: list[str] = []
    for i, chunk in enumerate(chunks):
        body = sep.join(chunk)
        if i == 0:
            body = header + "\n\n" + body
        if i == len(chunks) - 1:
            body = body + "\n" + footer
        msgs.append(body)

    # Safety net: if any single message is still over limit (shouldn't happen
    # unless one card itself is huge), truncate by removing trailing tags safely.
    safe = []
    for m in msgs:
        if len(m) <= TG_LIMIT:
            safe.append(m)
            continue
        # Trim at last newline before limit so we don't break mid-tag
        cut = m[: TG_LIMIT - 50]
        last_nl = cut.rfind("\n")
        if last_nl > 0:
            cut = cut[:last_nl]
        safe.append(cut + "\n\n<i>(truncated)</i>")
    return safe


def build_quality_message(scan: dict[str, Any], quality: dict[str, Any]) -> str:
    overview = quality.get("overview") or quality
    summary = overview.get("summary") or {}
    gate = overview.get("trading_gate") or {}
    remediation = overview.get("remediation") or quality.get("remediation") or {}
    attempts = remediation.get("attempts") or quality.get("attempts") or []
    checks = overview.get("checks") or []
    degraded = [
        c for c in checks
        if c.get("blocks_trading") or c.get("status") in {"WARN", "FALLBACK", "STALE", "MISSING", "DOWN"} or c.get("warnings")
    ][:6]
    lines = [
        f"<b>CASE CAPITAL QUALITY</b> - {_now_et()}",
        "--------------------",
        f"Gate: <b>{_esc(gate.get('decision') or 'UNKNOWN')}</b> / QC <b>{overview.get('score', '-')}</b> / Critical <b>{overview.get('critical_score', '-')}</b>",
        f"Scan: <b>{len(scan.get('results') or [])}</b> rows / {scan.get('duration_sec', '-')}s / Claude {scan.get('claude_calls_made', 0)}",
        f"Checks: {summary.get('live', 0)} live / {summary.get('warnings', 0)} warnings / {summary.get('fallbacks', 0)} fallbacks / {summary.get('blockers', 0)} blockers",
    ]
    if attempts:
        fixed = sum(1 for a in attempts if a.get("outcome") in {"live", "refreshed", "repulled", "rechecked_clean"})
        lines.append(f"Auto-fix: {fixed}/{len(attempts)} repaired or refreshed")
    if degraded:
        lines.append("")
        lines.append("<b>Degraded sources:</b>")
        for c in degraded:
            warnings = "; ".join(c.get("warnings") or [])
            detail = warnings or c.get("detail") or c.get("auto_fix") or ""
            lines.append(f"- {_esc(c.get('label'))}: <b>{_esc(c.get('status'))}</b> / {_esc(detail)[:120]}")
    blockers = gate.get("blockers") or []
    if blockers:
        lines.append("")
        lines.append("<b>Trading blockers:</b>")
        for b in blockers[:4]:
            lines.append(f"- {_esc(b.get('label'))}: {_esc(b.get('detail') or b.get('status'))}")
    lines.append("")
    lines.append("<i>Order path uses fresh critical cache first; display-only fallbacks do not slow execution.</i>")
    return "\n".join(lines)


async def dispatch_quality_after_scan(scan: dict[str, Any], chat_id: str | None = None) -> bool:
    try:
        from . import data_quality

        quality = await data_quality.remediate(limit=12)
        message = build_quality_message(scan, quality)
        ok = await send_message(message, chat_id=chat_id)
        await log_activity("Telegram quality report dispatched" if ok else "Telegram quality report failed", meta={
            "ok": ok,
            "score": ((quality.get("overview") or {}).get("score")),
            "summary": quality.get("summary"),
        })
        return ok
    except Exception as exc:
        logger.warning("quality telegram dispatch failed: %s", exc)
        fallback = (
            f"<b>CASE CAPITAL QUALITY</b> - {_now_et()}\n"
            "--------------------\n"
            f"Quality check failed: {_esc(str(exc)[:180])}"
        )
        return await send_message(fallback, chat_id=chat_id)


async def dispatch_consolidated(scan: dict[str, Any], chat_id: str | None = None,
                                title: str = "CASE CAPITAL INTEL") -> dict[str, Any]:
    """Build + send consolidated msgs. Returns delivery summary."""
    msgs = build_consolidated_messages(scan, title=title)
    sent = 0
    for m in msgs:
        await log_activity(f"Telegram dispatch: {len(m)} chars", "info")
        ok = await send_message(m, chat_id=chat_id)
        if ok:
            sent += 1
    quality_sent = await dispatch_quality_after_scan(scan, chat_id=chat_id)
    return {"messages_built": len(msgs), "messages_sent": sent,
            "quality_sent": quality_sent, "char_counts": [len(m) for m in msgs]}


def build_earnings_divergence_message(snapshot: dict[str, Any]) -> str:
    divergences = list(snapshot.get("earnings_divergences") or [])
    week = snapshot.get("week_of") or "?"
    week_end = snapshot.get("week_end") or "?"
    lines = [
        f"<b>CASE CAPITAL EARNINGS DIVERGENCES</b> - {_now_et()}",
        f"<code>{_esc(week)}</code> to <code>{_esc(week_end)}</code>",
        "--------------------",
    ]
    if not divergences:
        lines.append("<i>No active call-tone / price-reaction divergences found for the selected week.</i>")
        return "\n".join(lines)

    for i, row in enumerate(divergences[:8], start=1):
        div = row.get("earnings_divergence") or {}
        tone = (row.get("earnings_call_tone") or {}).get("tone") or "?"
        reaction = row.get("post_earnings_reaction") or {}
        lines.extend([
            f"<b>{i}. ${_esc(row.get('ticker'))}</b> - <b>{_esc(div.get('severity', ''))}</b>",
            f"Tone: <b>{_esc(tone)}</b> - Reaction: <b>{_fmt_pct(reaction.get('reaction_pct'))}</b> - {_esc(reaction.get('reaction_label'))}",
            f"{_esc(div.get('label'))}",
            f"<i>{_esc(div.get('read'))}</i>",
            f"Action: {_esc(div.get('action'))}",
            "",
        ])
    lines.append("Source note: tone is inferred from free headline/news context and available growth fields until transcript data is connected.")
    return "\n".join(lines).strip()


async def dispatch_earnings_divergences(snapshot: dict[str, Any],
                                        chat_id: str | None = None) -> dict[str, Any]:
    msg = build_earnings_divergence_message(snapshot)
    ok = await send_message(msg, chat_id=chat_id)
    await log_activity(f"Earnings divergence Telegram dispatch: {len(msg)} chars", "info")
    return {
        "messages_built": 1,
        "messages_sent": 1 if ok else 0,
        "char_counts": [len(msg)],
        "divergence_count": snapshot.get("earnings_divergence_count", 0),
    }


def format_contracts_condensed(rows: list[dict[str, Any]]) -> str:
    """For /contracts: one line per award."""
    if not rows:
        return "No recent gov contracts to public companies."
    lines = ["🏛 <b>GOV CONTRACTS — RECENT</b>"]
    for r in rows:
        amt = (r.get("amount") or 0) / 1e6
        agency = _esc((r.get("agency") or "?")[:25])
        lines.append(
            f"🏛 <b>${_esc(r['ticker'])}</b> — ${amt:.1f}M — {agency}"
        )
    return "\n".join(lines)


def format_contracts_list(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No recent gov contracts to public companies."
    lines = ["🏛 <b>TOP RECENT GOV CONTRACTS</b>"]
    for r in rows:
        amount = r.get('amount') or 0
        lines.append(
            f"• <b>${_esc(r['ticker'])}</b> — {_esc((r.get('agency') or '?')[:35])}\n"
            f"  ${amount/1e6:.1f}M · {_esc((r.get('recipient') or '')[:40])}"
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
    allowed_chat = str(os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if allowed_chat and chat_id != allowed_chat:
        await log_activity(
            f"Telegram cmd ignored from unauthorized chat {chat_id}: {text[:80]}",
            "warn",
            {"chat_id": chat_id, "allowed_chat": allowed_chat},
        )
        return
    await log_activity(f"Telegram cmd from {chat_id}: {text[:80]}", "info")

    parts = text.split()
    cmd = parts[0].lower().split("@")[0]
    args = parts[1:]
    db = get_db()

    if cmd == "/positions":
        from . import trade_floor as _tf
        live = await _tf.list_positions()
        if not live:
            await send_message("📊 No open Trade Floor positions.", chat_id=chat_id)
            return
        lines = ["🎯 <b>OPEN POSITIONS</b>"]
        for p in live[:20]:
            sym = p.get("symbol")
            pct = float(p.get("unrealized_plpc") or 0) * 100
            mark = float(p.get("market_value") or 0)
            color = "🟢" if pct >= 0 else "🔴"
            lines.append(f"{color} <b>${sym}</b> · ${mark:.0f} · {pct:+.2f}%")
        await send_message("\n".join(lines), chat_id=chat_id); return

    if cmd == "/account":
        from . import trade_floor as _tf
        a = await _tf.get_account() or {}
        equity = float(a.get("equity") or 0)
        cash = float(a.get("cash") or 0)
        bp = float(a.get("buying_power") or 0)
        history = await _tf.trade_history()
        wins = sum(1 for h in history if (h.get("realized_pct") or 0) > 0)
        wr = (wins / len(history) * 100) if history else 0
        await send_message(
            f"💼 <b>ACCOUNT</b>\n"
            f"Equity ${equity:.2f} · Cash ${cash:.2f} · BP ${bp:.2f}\n"
            f"Closed trades {len(history)} · win rate {wr:.0f}%",
            chat_id=chat_id); return

    if cmd == "/regime":
        from . import trade_floor as _tf
        r = await _tf.regime_status()
        e = "🟢" if r["status"] == "green" else "🟡" if r["status"] == "yellow" else "🔴"
        await send_message(
            f"{e} <b>REGIME · {r['status'].upper()}</b>\n"
            f"VIX {r['vix']} · SPY {r['spy_last']} · EMA200 {r['spy_ema200']}\n"
            f"Halt: {'YES' if r['halt_new_entries'] else 'no'}",
            chat_id=chat_id); return

    if cmd == "/risk":
        from . import trade_floor as _tf, trade_floor_learning as _tfl
        regime = await _tf.regime_status()
        engine = await _tfl.status()
        positions = await _tf.list_positions()
        await send_message(
            f"🛡 <b>RISK · {regime['status'].upper()}</b>\n"
            f"VIX {regime['vix']} · Circuit: "
            f"{'HALTED' if regime['halt_new_entries'] else 'OK'}\n"
            f"Positions {len(positions)}/10 · Engine phase: {engine['phase']}",
            chat_id=chat_id); return

    if cmd == "/journal":
        from . import trade_floor as _tf
        history = await _tf.trade_history()
        if not history:
            await send_message("📓 No closed trades yet.", chat_id=chat_id); return
        lines = ["📓 <b>LAST 3 JOURNAL ENTRIES</b>"]
        for h in history[:3]:
            ret = h.get("realized_pct") or 0
            color = "🟢" if ret >= 0 else "🔴"
            sigs = " · ".join(h.get("signal_combo") or [])[:60]
            j = h.get("journal_summary") or ""
            lines.append(
                f"\n{color} <b>${h.get('ticker')}</b> · {ret:+.2f}%\n"
                f"<i>{sigs}</i>\n{_esc(j[:300]) if j else ''}"
            )
        await send_message("\n".join(lines), chat_id=chat_id); return

    if cmd == "/sec":
        from . import sec_filings as _s
        rows = await _s.recent_filings(days=7)
        if not rows:
            await send_message("§ No SEC filings to report.", chat_id=chat_id); return
        lines = ["§ <b>SEC FILINGS · TOP 5</b>"]
        for r in rows[:5]:
            badge = "🔒" if r.get("narrative_lock_badge") else " "
            lines.append(
                f"{badge} <b>${_esc(r.get('ticker',''))}</b> "
                f"{_esc(r.get('form',''))} · sig {r.get('significance')} · "
                f"nls {r.get('narrative_lock_score')}"
            )
        await send_message("\n".join(lines), chat_id=chat_id); return

    if cmd == "/pharma":
        from . import pharma as _p
        rows = await _p.get_pdufa_within_days(days=90)
        rows = sorted(rows, key=lambda r: -(r.get('binary_event_score') or 0))[:3]
        if not rows:
            await send_message("🧬 No PDUFA in next 90d.", chat_id=chat_id); return
        lines = ["🧬 <b>TOP PHARMA · BINARY EVENT SCORE</b>"]
        for r in rows:
            lines.append(
                f"<b>${_esc(r.get('ticker',''))}</b> · "
                f"{r.get('binary_event_score'):.0f}/100 · "
                f"{_esc(r.get('drug',''))[:30]} · "
                f"PDUFA {r.get('pdufa_date')} ({r.get('days_until')}d)"
            )
        await send_message("\n".join(lines), chat_id=chat_id); return

    if cmd == "/contracts":
        from . import usaspending as _u
        rows = await _u.list_recent_contracts_for_tickers(limit=5)
        if not rows:
            await send_message("🏛 No recent contracts.", chat_id=chat_id); return
        lines = ["🏛 <b>RECENT CONTRACTS</b>"]
        for r in rows:
            amt = r.get("amount") or 0
            lines.append(
                f"<b>${_esc(r.get('ticker',''))}</b> · ${amt/1e6:.1f}M · "
                f"{_esc(r.get('agency',''))[:30]}"
            )
        await send_message("\n".join(lines), chat_id=chat_id); return

    if cmd == "/checkup":
        # Portfolio check-up: buys+sells + unrealized/realized since last checkup
        from . import trade_floor as _tf
        last_check = await db.bot_state.find_one({"_id": "last_checkup"}) or {}
        last_ts = last_check.get("timestamp", "")
        since_filter = {"submitted_at": {"$gte": last_ts}} if last_ts else {}
        new_trades = await db.tf_trades.find(since_filter, {"_id": 0})\
            .sort("submitted_at", -1).to_list(100)
        live = await _tf.list_positions()
        a = await _tf.get_account() or {}
        unrealized = sum(float(p.get("unrealized_pl") or 0) for p in live)
        realized_today = sum(
            (t.get("realized_pct") or 0) for t in new_trades
            if t.get("status") == "CLOSED"
        )
        buys = [t for t in new_trades if t.get("status") == "OPEN"]
        sells = [t for t in new_trades if t.get("status") == "CLOSED"]
        lines = [f"📋 <b>CHECK-UP · since {last_ts[:10] if last_ts else 'launch'}</b>"]
        lines.append(f"Buys: {len(buys)} · Sells: {len(sells)}")
        lines.append(f"Unrealized P/L: ${unrealized:+.2f}")
        lines.append(f"Realized P/L (avg): {realized_today:+.2f}%")
        lines.append(f"Account equity: ${a.get('equity', '—')}")
        await db.bot_state.update_one(
            {"_id": "last_checkup"},
            {"$set": {"timestamp": datetime.now(timezone.utc).isoformat()}},
            upsert=True,
        )
        await send_message("\n".join(lines), chat_id=chat_id); return

    if cmd in ("/start", "/help"):
        await send_message(
            "⚡ <b>AXIOM INTELLIGENCE</b> · v3.0\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "<b>SCANS</b>\n"
            "/scan · /scan_gov · /analyze TICKER\n\n"
            "<b>PERFORMANCE</b>\n"
            "/tracker — live P&L all signals\n"
            "/curve [days] — daily curve sparkline\n"
            "/performance — combo win rates\n"
            "/backtest · /backtest_seed\n\n"
            "<b>OPTIONS</b>\n"
            "/options · /flow · /iv · /spread\n"
            "/calls · /puts · /noiv\n\n"
            "<b>ANALYSIS</b>\n"
            "/risk · /target · /squeeze · /compare T1 T2\n\n"
            "<b>GOVERNMENT</b>\n"
            "/contracts · /agency NAME · /watchlist_contracts\n\n"
            "<b>TRACKING</b>\n"
            "/watch · /unwatch · /watchlist\n"
            "/alert TICKER PRICE · /alerts\n\n"
            "<b>LIVE</b>\n"
            "/congress · /geo · /premarket\n\n"
            "<i>or type any question — AXIOM answers in plain English.</i>",
            chat_id=chat_id,
        )
        return

    if cmd == "/tracker":
        # Daily P&L of every ticker we've ever signaled
        from . import pnl_tracker as _p
        rows = await _p.signals_tracker_summary(limit=200)
        if not rows:
            await send_message("📊 <b>AXIOM TRACKER</b>\nNo signals tracked yet.", chat_id=chat_id)
            return
        with_gain = [r for r in rows if r.get("gain_pct") is not None]
        winners = [r for r in with_gain if r["gain_pct"] > 0]
        losers = [r for r in with_gain if r["gain_pct"] < 0]
        avg = round(sum(r["gain_pct"] for r in with_gain) / len(with_gain), 2) if with_gain else 0
        best = max(with_gain, key=lambda r: r["gain_pct"]) if with_gain else None
        worst = min(with_gain, key=lambda r: r["gain_pct"]) if with_gain else None

        # Sort by gain desc, take top 15
        rows_sorted = sorted(with_gain, key=lambda r: r["gain_pct"], reverse=True)
        top = rows_sorted[:10]
        bot = rows_sorted[-5:] if len(rows_sorted) > 10 else []

        lines = [
            f"⚡ <b>AXIOM TRACKER</b> · {_now_et()}",
            "━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📊 <b>{len(with_gain)}</b> TRACKED · "
            f"<b>{len(winners)}W</b>/<b>{len(losers)}L</b> · "
            f"AVG <b>{avg:+.2f}%</b>",
        ]
        if best:
            lines.append(f"🏆 BEST: <b>${_esc(best['ticker'])}</b> {best['gain_pct']:+.1f}%")
        if worst:
            lines.append(f"📉 WORST: <b>${_esc(worst['ticker'])}</b> {worst['gain_pct']:+.1f}%")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("<b>TOP MOVERS:</b>")
        for r in top:
            arrow = "📈" if r["gain_pct"] > 0 else "📉"
            lines.append(
                f"{arrow} <b>${_esc(r['ticker'])}</b> "
                f"<code>{r['gain_pct']:+.1f}%</code> "
                f"${r['first_seen_price']:.2f}→${r['current_price']:.2f} "
                f"({_esc(r['first_seen_date'])})"
            )
        if bot:
            lines.append("")
            lines.append("<b>WORST:</b>")
            for r in bot:
                lines.append(
                    f"📉 <b>${_esc(r['ticker'])}</b> "
                    f"<code>{r['gain_pct']:+.1f}%</code> "
                    f"${r['first_seen_price']:.2f}→${r['current_price']:.2f}"
                )
        await send_message("\n".join(lines), chat_id=chat_id)
        return

    if cmd == "/curve":
        from . import pnl_tracker as _p
        days = int(args[0]) if args and args[0].isdigit() else 30
        curve = await _p.daily_pnl_curve(days=days)
        if not curve:
            await send_message("📈 <b>AXIOM CURVE</b>\nNot enough history yet.", chat_id=chat_id)
            return
        latest = curve[-1]
        peak = max(curve, key=lambda c: c["avg_gain_pct"])
        trough = min(curve, key=lambda c: c["avg_gain_pct"])
        # ASCII sparkline
        vals = [c["avg_gain_pct"] for c in curve]
        lo, hi = min(vals), max(vals)
        spark_chars = "▁▂▃▄▅▆▇█"
        if hi > lo:
            spark = "".join(spark_chars[min(7, int((v - lo) / (hi - lo) * 7))] for v in vals)
        else:
            spark = "▄" * len(vals)
        await send_message(
            f"📈 <b>AXIOM CURVE — {days}D</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"<code>{spark}</code>\n"
            f"📊 NOW: <b>{latest['avg_gain_pct']:+.2f}%</b> · "
            f"{latest['positions']} positions ({latest['winners']}W/{latest['losers']}L)\n"
            f"🏆 PEAK: <b>{peak['avg_gain_pct']:+.2f}%</b> ({_esc(peak['date'])})\n"
            f"📉 TROUGH: <b>{trough['avg_gain_pct']:+.2f}%</b> ({_esc(trough['date'])})\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━",
            chat_id=chat_id,
        )
        return

    if cmd == "/squeeze":
        if not args:
            await send_message("Usage: <code>/squeeze TICKER</code>", chat_id=chat_id)
            return
        ticker = args[0].upper().lstrip("$")
        from .scrapers import fetch_quote as _fq
        from .squeeze import compute_squeeze
        fund = await risk_target.fetch_fundamentals(ticker)
        sq = await compute_squeeze(ticker, None, fund or {})
        comp = sq.get("components", {})
        await send_message(
            f"🔀 <b>SQUEEZE — ${ticker}</b>\n"
            f"{sq.get('emoji','⚪')} <b>{sq['score']}/100</b> ({sq.get('band','?')})\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"Short %: {comp.get('short_pct','—')}%\n"
            f"Days-to-cover: {comp.get('days_to_cover','—')}\n"
            f"30d ROC: {comp.get('rate_of_change_30d','—')}%\n"
            f"Borrow score: {comp.get('borrow_score','—')}",
            chat_id=chat_id,
        )
        return

    # ---------------- Options commands (Part 13) ----------------
    if cmd == "/options":
        if not args:
            await send_message("Usage: <code>/options TICKER</code>", chat_id=chat_id)
            return
        ticker = args[0].upper().lstrip("$")
        from . import options_engine
        fund = await risk_target.fetch_fundamentals(ticker)
        signals: list[str] = []
        stock = {"ticker": ticker, "signals": signals,
                 "risk": risk_target.compute_risk(fund or {}, signals, None, None, 0),
                 "squeeze": {}, "time_target": {"days_remaining": 30}}
        opts = await options_engine.analyze_ticker(stock)
        if not opts:
            await send_message(f"No options data for ${_esc(ticker)}.", chat_id=chat_id)
            return
        # Provide a default name so the formatter has something
        opts["strategy_name"] = opts.get("strategy", "LONG_CALL").replace("_", " ").title()
        opts["one_liner"] = opts.get("strategy_reason", "")
        block = _format_options_block(opts)
        await send_message(f"🎯 <b>OPTIONS — ${_esc(ticker)}</b>{block}", chat_id=chat_id)
        return

    if cmd == "/flow":
        if not args:
            await send_message("Usage: <code>/flow TICKER</code>", chat_id=chat_id)
            return
        ticker = args[0].upper().lstrip("$")
        from . import options_engine
        flow = await options_engine.detect_unusual_flow(ticker)
        await send_message(
            f"⚡ <b>FLOW — ${_esc(ticker)}</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"Call vol: <b>{flow['total_call_volume']:,}</b> · OI: {flow.get('call_oi', 0):,}\n"
            f"Put vol:  <b>{flow['total_put_volume']:,}</b> · OI: {flow.get('put_oi', 0):,}\n"
            f"P/C ratio: <b>{flow['call_put_ratio']}</b>\n"
            f"Bias: <b>{_esc(flow['flow_bias'])}</b>\n"
            f"{'🔥 CALL SWEEP DETECTED' if flow.get('call_sweep') else ''}",
            chat_id=chat_id,
        )
        return

    if cmd == "/iv":
        if not args:
            await send_message("Usage: <code>/iv TICKER</code>", chat_id=chat_id)
            return
        ticker = args[0].upper().lstrip("$")
        from . import options_engine
        iv = await options_engine.calculate_iv_rank(ticker)
        if iv.get("iv_rank") is None:
            await send_message(f"No IV data for ${_esc(ticker)}.", chat_id=chat_id)
            return
        # Build a fake stock for crush risk
        stock = {"ticker": ticker, "signals": [], "time_target": {"days_remaining": 30}}
        chain = {"iv_rank": iv["iv_rank"]}
        crush = options_engine.assess_iv_crush_risk(stock, chain)
        await send_message(
            f"📊 <b>IV — ${_esc(ticker)}</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"IV rank: <b>{iv['iv_rank']}/100</b> ({_esc(iv.get('iv_label','?'))})\n"
            f"ATM IV: {iv.get('atm_iv') or '—'}\n"
            f"30d HV: {iv.get('hv_30') or '—'}\n"
            f"Crush risk: <b>{_esc(crush['crush_risk'])}</b>\n"
            f"<i>{_esc(crush['recommendation'])}</i>",
            chat_id=chat_id,
        )
        return

    if cmd == "/spread":
        if not args:
            await send_message("Usage: <code>/spread TICKER</code>", chat_id=chat_id)
            return
        ticker = args[0].upper().lstrip("$")
        from . import options_engine
        chain = await options_engine.get_options_data(ticker)
        if not chain:
            await send_message(f"No options chain for ${_esc(ticker)}.", chat_id=chat_id)
            return
        bull = options_engine.build_spread(chain, "BULL")
        bear = options_engine.build_spread(chain, "BEAR")
        lines = [f"🎯 <b>SPREAD ANALYSIS — ${_esc(ticker)}</b>", "━━━━━━━━━━━━━━━━━━"]
        if bull:
            lines.append(
                f"<b>BULL CALL SPREAD</b>\n"
                f"Buy ${bull['buy_strike']}C / Sell ${bull['sell_strike']}C exp {_esc(bull['expiration'])}\n"
                f"Net debit: ${bull['net_debit']} · Max P: ${bull['max_profit']} · "
                f"Max L: ${bull['max_loss']} · R/R {bull['risk_reward']}:1"
            )
        if bear:
            lines.append(
                f"\n<b>BEAR PUT SPREAD</b>\n"
                f"Buy ${bear['buy_strike']}P / Sell ${bear['sell_strike']}P exp {_esc(bear['expiration'])}\n"
                f"Net debit: ${bear['net_debit']} · Max P: ${bear['max_profit']} · "
                f"Max L: ${bear['max_loss']} · R/R {bear['risk_reward']}:1"
            )
        await send_message("\n".join(lines), chat_id=chat_id)
        return

    if cmd in ("/calls", "/puts", "/noiv"):
        # Fetch latest scan; if missing today, run one
        today_iso = datetime.now(timezone.utc).date().isoformat()
        latest = await scanner.latest_scan()
        if not latest or latest.get("started_at", "")[:10] != today_iso:
            await send_message("⏳ Running fresh scan...", chat_id=chat_id)
            latest = await scanner.run_scan(triggered_by=f"telegram:{chat_id}")
        results = latest.get("results", [])
        if cmd == "/calls":
            picks = [r for r in results
                     if "UNUSUAL_FLOW" in r.get("signals", []) or "CALL_SWEEP" in r.get("signals", [])
                     or (r.get("options", {}) or {}).get("flow", {}).get("flow_bias") == "BULLISH"]
            picks.sort(key=lambda x: (x.get("options", {}) or {}).get("flow", {}).get("call_volume_ratio", 0), reverse=True)
            title = "📞 CALLS — UNUSUAL FLOW"
        elif cmd == "/puts":
            picks = [r for r in results
                     if (r.get("options", {}) or {}).get("flow", {}).get("flow_bias") == "BEARISH"]
            picks.sort(key=lambda x: (x.get("options", {}) or {}).get("flow", {}).get("put_volume_ratio", 0), reverse=True)
            title = "📉 PUTS — UNUSUAL FLOW"
        else:  # /noiv
            picks = [r for r in results
                     if (r.get("options", {}) or {}).get("iv_rank") is not None
                     and r["options"]["iv_rank"] < 35
                     and (r.get("options", {}) or {}).get("strategy") == "LONG_CALL"]
            picks.sort(key=lambda x: x["options"]["iv_rank"])
            title = "🟢 LOW IV ENTRIES (<35)"
        if not picks:
            await send_message(f"<b>{title}</b>\nNo matching tickers in today's scan.", chat_id=chat_id)
            return
        lines = [f"<b>{title}</b> · {len(picks)} matches"]
        for r in picks[:15]:
            opts = r.get("options") or {}
            ct = opts.get("contract") or {}
            f = opts.get("flow") or {}
            lines.append(
                f"\n<b>${_esc(r['ticker'])}</b> · IV {opts.get('iv_rank','?')}% · "
                f"P/C {f.get('call_put_ratio','?')} · "
                f"{_esc(opts.get('strategy_name') or opts.get('strategy','?'))}"
                + (f" · ${ct.get('strike')}{ct.get('type','')} exp {ct.get('expiration','')}" if ct else "")
            )
        await send_message("\n".join(lines), chat_id=chat_id)
        return

    if cmd == "/congress":
        from .congress import fetch_recent_buys
        buys = await fetch_recent_buys(days=30)
        if not buys:
            await send_message("No recent congressional purchases on file.", chat_id=chat_id)
            return
        lines = ["🏛 <b>CONGRESSIONAL PURCHASES (30d)</b>"]
        for b in buys[:10]:
            match = "✅" if b["committee_match"] else "  "
            amt = f"${b['amount_min']/1000:.0f}K–${b['amount_max']/1000:.0f}K"
            lines.append(f"{match} <b>${b['ticker']}</b> — {b['name']} ({b['chamber']}) "
                         f"· {amt} · {b['tx_date']}")
        lines.append("\n<i>✅ = directly relevant committee</i>")
        await send_message("\n".join(lines), chat_id=chat_id)
        return

    if cmd == "/performance":
        from . import pnl_tracker
        sig_perf = await pnl_tracker.performance_by_signals()
        opt_perf = await pnl_tracker.options_performance_summary()
        lines = ["📊 <b>SIGNAL PERFORMANCE</b>"]
        if not sig_perf:
            lines.append("<i>Waiting for 7+ days of scan data to compute returns.</i>")
        else:
            for r in sig_perf[:5]:
                lines.append(
                    f"• {_esc(r['combo'])}: 30d {r.get('avg_30d', '—')}% · "
                    f"WR {r.get('win_rate_30d', '—')}% · n={r['n']}"
                )
        lines.append("\n📊 <b>OPTIONS PERFORMANCE</b>")
        if not opt_perf.get("by_strategy"):
            lines.append("<i>Options P&L data accumulates 3 days post-pick.</i>")
        else:
            for r in opt_perf["by_strategy"][:5]:
                lines.append(
                    f"• {_esc(r['strategy'])}: actual {r.get('avg_return_actual','—')}% · "
                    f"proxy {r.get('avg_return_proxy','—')}% · IV@entry {r.get('avg_iv_at_entry','—')} · n={r['n']}"
                )
        if opt_perf.get("by_crush_risk"):
            lines.append("\n<b>By IV crush risk:</b>")
            for r in opt_perf["by_crush_risk"]:
                lines.append(
                    f"• {_esc(r['crush_risk'])}: avg {r['avg_return']}% · WR {r['win_rate']}% · n={r['n']}"
                )
        await send_message("\n".join(lines), chat_id=chat_id)
        return

    if cmd == "/backtest":
        from . import backtest as _bt
        summary = await _bt.backtest_summary()
        lines = ["📈 <b>BACKTEST SUMMARY</b>",
                 f"Forward rows: {summary['forward_count']} · Synthetic rows: {summary['synthetic_count']}"]
        if summary["synthetic"]:
            lines.append("\n<b>Synthetic (congress 30d):</b>")
            for r in summary["synthetic"][:5]:
                lines.append(
                    f"• {_esc(r['combo'])}: avg {r['avg_30d']}% · WR {r['win_rate_30d']}% · "
                    f"best {r['best']}% · worst {r['worst']}% · n={r['n']}"
                )
        if summary["forward"]:
            lines.append("\n<b>Forward (live scans 30d):</b>")
            for r in summary["forward"][:5]:
                lines.append(
                    f"• {_esc(r['combo'])}: avg {r['avg_30d']}% · WR {r['win_rate_30d']}% · n={r['n']}"
                )
        if not summary["synthetic"] and not summary["forward"]:
            lines.append("<i>Run /backtest_seed to seed synthetic data.</i>")
        await send_message("\n".join(lines), chat_id=chat_id)
        return

    if cmd == "/backtest_seed":
        from . import backtest as _bt
        await send_message("⏳ Seeding synthetic backtest from congressional dataset...", chat_id=chat_id)
        res = await _bt.synthetic_congress_backtest()
        await send_message(
            f"✅ Seeded <b>{res['written']}</b> congressional rows "
            f"({res['skipped']} already complete) from {_esc(res['source'])}",
            chat_id=chat_id,
        )
        return

    if cmd == "/geo":
        await send_message(
            "🌍 <b>GEOPOLITICAL MONITOR</b>\n"
            "<i>Active triggers: Google News RSS scraping for keywords (military "
            "conflict, sanctions, cyberattack, infrastructure, trade war, supply "
            "chain disruption, strait blockade, nuclear threat, terrorist attack, "
            "pandemic, energy crisis). Scheduled every 4h. No active triggers "
            "right now.</i>",
            chat_id=chat_id,
        )
        return

    if cmd == "/premarket":
        items = await db.watchlist.find({"chat_id": chat_id}, {"_id": 0}).to_list(50)
        if not items:
            await send_message("Watchlist empty.", chat_id=chat_id)
            return
        lines = ["☀️ <b>PRE-MARKET BRIEFING</b>"]
        for it in items[:10]:
            q = await fetch_quote(it["ticker"])
            price = f"${q['price']}" if q and q.get("price") is not None else "n/a"
            lines.append(f"• <b>${it['ticker']}</b>: {price}")
        await send_message("\n".join(lines), chat_id=chat_id)
        return

    if cmd in ("/add", "/watch", "/watchadd"):
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

    if cmd in ("/remove", "/unwatch"):
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

    if cmd == "/scan":
        await send_message("⏳ Running full scan...", chat_id=chat_id)
        scan = await scanner.run_scan(triggered_by=f"telegram:{chat_id}")
        await dispatch_consolidated(scan, chat_id=chat_id)
        return

    if cmd == "/scan_gov":
        await send_message("⏳ Running gov contracts scan...", chat_id=chat_id)
        scan = await scanner.run_gov_scan_only(triggered_by=f"telegram:{chat_id}")
        # Reshape gov scan into the consolidated schema
        gov_doc = {
            "raw_counts": {},
            "pre_filter_passed": len(scan.get("results", [])),
            "claude_calls_made": 0,
            "claude_cache_hits": 0,
            "results": scan.get("results", []),
        }
        await dispatch_consolidated(gov_doc, chat_id=chat_id, title="GOV CONTRACT SCAN")
        bs = scan.get("budget_surges", [])
        if bs:
            lines = ["💰 <b>AGENCY BUDGET SURGES</b>"]
            for b in bs[:5]:
                lines.append(f"• {b['agency']} — +{b['pct_increase']}% vs 3-mo avg "
                              f"(exposed: {', '.join(b.get('exposed_tickers') or []) or '—'})")
            await send_message("\n".join(lines), chat_id=chat_id)
        return

    if cmd == "/contracts":
        rows = await usaspending.list_recent_contracts_for_tickers(limit=10)
        await send_message(format_contracts_condensed(rows), chat_id=chat_id)
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

    # Natural language catch-all: any non-/-prefixed message goes to Claude
    if not text.startswith("/"):
        from .nlq import answer as _nlq_answer
        ans = await _nlq_answer(text, chat_id)
        await send_message(ans, chat_id=chat_id)
        return

    await send_message("Unknown command. Try /help — or just ask me a question in plain English.", chat_id=chat_id)


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
