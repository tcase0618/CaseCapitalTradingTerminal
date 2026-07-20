"""Single-batch Claude analysis with 24h Mongo cache. Token-efficient."""
from __future__ import annotations
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import get_db

logger = logging.getLogger(__name__)

CLAUDE_MODEL = "claude-haiku-4-5"
CACHE_TTL_HOURS = 24


def claude_analysis_disabled() -> bool:
    return os.environ.get("DISABLE_CLAUDE_ANALYSIS", "").strip().lower() in {"1", "true", "yes", "on"}


def _num(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


async def _call_claude(system: str, user: str) -> str | None:
    """Call Claude via the official Anthropic SDK."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY is not configured")
        return None
    try:
        import anthropic
        client = anthropic.AsyncAnthropic(api_key=api_key)
        msg = await client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        parts = []
        for block in msg.content:
            if getattr(block, "type", None) == "text":
                parts.append(block.text)
        return "".join(parts)
    except Exception as e:
        logger.exception("Anthropic SDK call failed: %s", e)
        return None


async def get_cached(ticker: str) -> dict[str, Any] | None:
    db = get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=CACHE_TTL_HOURS)).isoformat()
    doc = await db.claude_cache.find_one(
        {"ticker": ticker.upper(), "created_at": {"$gte": cutoff}},
        {"_id": 0},
        sort=[("created_at", -1)],
    )
    return doc


async def set_cached(ticker: str, analysis: dict[str, Any]):
    db = get_db()
    doc = {
        "ticker": ticker.upper(),
        "analysis": analysis,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "date_key": _today_key(),
    }
    await db.claude_cache.update_one(
        {"ticker": ticker.upper(), "date_key": doc["date_key"]},
        {"$set": doc},
        upsert=True,
    )


def _build_prompt(candidates: list[dict[str, Any]]) -> str:
    """New schema: pre-computed values are passed in. Claude only generates
    thesis, conviction, time_horizon, stop_loss, options_strategy_name (3 words),
    options_one_liner (1 sentence). Entry zone too (price band)."""
    stocks = []
    for c in candidates:
        opts = c.get("options") or {}
        stocks.append({
            "ticker": c["ticker"],
            "price": c.get("price"),
            "mktcap": c.get("market_cap"),
            "signals": c["signals"],
            "contracts": c.get("contracts_brief", []),
            "short_pct": c.get("short_pct"),
            "squeeze": c.get("squeeze_score"),
            "insider_buys": c.get("insider_buys", 0),
            "sector": c.get("sector"),
            "beta": c.get("beta"),
            "rev_ttm": c.get("rev_ttm"),
            "risk_score": c.get("risk_score"),
            "target_low": c.get("target_low"),
            "target_high": c.get("target_high"),
            "target_blended": c.get("target_blended"),
            # Pre-computed options context — stays under 200 tokens/stock
            "opt_strategy": opts.get("strategy"),
            "opt_iv_rank": opts.get("iv_rank"),
            "opt_crush": opts.get("crush_risk"),
            "opt_debit": (opts.get("spread") or {}).get("net_debit"),
            "opt_max_profit": (opts.get("spread") or {}).get("max_profit"),
            "opt_max_loss": (opts.get("spread") or {}).get("max_loss") or (opts.get("contract") or {}).get("max_loss"),
        })
    payload = {"stocks": stocks}
    return (
        "Pre-filtered stocks below already have signals confirmed and risk/targets/"
        "squeeze/options computed. For each ticker, produce ONLY: signal_score(1-10), "
        "thesis(1 sentence), entry_low, entry_high (suggested buy band), "
        "catalyst_date, conviction(low/medium/high), time_horizon(swing/short/"
        "medium/long), stop_loss(price), options_strategy_name(3 words max), "
        "options_one_liner(1 sentence why this strategy fits), "
        "hold_stock_instead(true if opt_strategy=AVOID_OPTIONS).\n"
        f"INPUT:{json.dumps(payload, separators=(',', ':'))}\n"
        "Return ONLY: {\"results\":[{\"ticker\":\"\",\"signal_score\":0,\"thesis\":\"\","
        "\"entry_low\":0,\"entry_high\":0,\"catalyst_date\":\"\",\"conviction\":\"\","
        "\"time_horizon\":\"\",\"stop_loss\":0,\"options_strategy_name\":\"\","
        "\"options_one_liner\":\"\",\"hold_stock_instead\":false}]} — no prose, no fences."
    )


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    return text


def _parse_response_results(text: str) -> list[dict[str, Any]]:
    """Parse new schema: {\"results\":[{...}]} or fallback to bare array."""
    text = _strip_fences(text)
    # Try object envelope first
    obj_match = re.search(r"\{.*\}", text, re.DOTALL)
    if obj_match:
        try:
            obj = json.loads(obj_match.group(0))
            if isinstance(obj, dict) and "results" in obj:
                return obj["results"]
        except Exception:
            pass
    # Fallback to bare array
    arr_match = re.search(r"\[.*\]", text, re.DOTALL)
    if arr_match:
        return json.loads(arr_match.group(0))
    return []


def _parse_json_array(text: str) -> list[dict[str, Any]]:
    text = _strip_fences(text)
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        text = m.group(0)
    return json.loads(text)


def _fallback_analysis(candidate: dict[str, Any]) -> dict[str, Any]:
    """Deterministic analysis used when Claude returns no usable row."""
    ticker = str(candidate.get("ticker", "")).upper()
    signals = candidate.get("signals") or []
    signal_count = len(set(signals))
    risk_score = _num(candidate.get("risk_score")) or 0
    squeeze_score = _num(candidate.get("squeeze_score")) or 0
    price = _num(candidate.get("price"))
    score = min(10, max(1, round(signal_count * 1.5 + risk_score / 25 + squeeze_score / 30)))
    thesis_bits = ", ".join(str(s).replace("_", " ").lower() for s in signals[:3])
    thesis = f"{ticker} passed the confirmed signal filter"
    if thesis_bits:
        thesis = f"{ticker} passed the confirmed signal filter with {thesis_bits}."
    entry_low = price
    entry_high = price
    if price:
        entry_low = round(price * 0.98, 2)
        entry_high = round(price * 1.02, 2)
    stop_loss = None
    if price:
        stop_loss = round(price * 0.88, 2)
    return {
        "ticker": ticker,
        "signal_score": int(score),
        "thesis": thesis,
        "entry_low": entry_low,
        "entry_high": entry_high,
        "catalyst_date": "",
        "conviction": "medium" if score >= 5 else "low",
        "time_horizon": "short",
        "stop_loss": stop_loss,
        "options_strategy_name": "Signal Watch",
        "options_one_liner": "Use the computed risk, target, and options data while Claude analysis is unavailable.",
        "hold_stock_instead": False,
    }


async def analyze_batch(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not candidates:
        return []

    results: dict[str, dict[str, Any]] = {}
    to_analyze: list[dict[str, Any]] = []

    for c in candidates:
        cached = await get_cached(c["ticker"])
        if cached:
            results[c["ticker"]] = {**cached["analysis"], "cached": True}
        else:
            to_analyze.append(c)

    if to_analyze:
        if claude_analysis_disabled():
            logger.info("Claude analysis disabled; using deterministic fallback analysis")
        else:
            system = (
                "You are a senior small/mid-cap equity analyst. You output compact "
                "JSON only. No prose."
            )
            prompt = _build_prompt(to_analyze)
            resp = await _call_claude(system, prompt)
            if resp:
                try:
                    parsed = _parse_response_results(resp)
                    if not parsed:
                        logger.warning("Claude batch returned no parseable analysis rows")
                    for item in parsed:
                        t = str(item.get("ticker", "")).upper()
                        if not t:
                            continue
                        analysis = {
                            "ticker": t,
                            "signal_score": int(item.get("signal_score", 0) or 0),
                            "thesis": str(item.get("thesis", "")).strip(),
                            "entry_low": _num(item.get("entry_low")),
                            "entry_high": _num(item.get("entry_high")),
                            "catalyst_date": str(item.get("catalyst_date", "")).strip(),
                            "conviction": str(item.get("conviction", "")).strip().lower() or "medium",
                            "time_horizon": str(item.get("time_horizon", "")).strip().lower() or "medium",
                            "stop_loss": _num(item.get("stop_loss")),
                            "options_strategy_name": str(item.get("options_strategy_name", "")).strip(),
                            "options_one_liner": str(item.get("options_one_liner", "")).strip(),
                            "hold_stock_instead": bool(item.get("hold_stock_instead", False)),
                        }
                        await set_cached(t, analysis)
                        results[t] = {**analysis, "cached": False}
                except Exception as e:
                    logger.exception("Failed to parse Claude batch JSON: %s", e)
            else:
                logger.warning("Claude batch returned no response")

        missing = [c for c in to_analyze if c["ticker"] not in results]
        if missing:
            logger.warning("Using fallback analysis for %s candidates", len(missing))
            for c in missing:
                analysis = _fallback_analysis(c)
                results[analysis["ticker"]] = {**analysis, "cached": False}

    merged: list[dict[str, Any]] = []
    for c in candidates:
        a = results.get(c["ticker"])
        if not a:
            continue
        merged.append({
            **a,
            "signals": c["signals"],
            "insider_summary": c.get("insider_summary"),
            "short_summary": c.get("short_summary"),
            "earnings_summary": c.get("earnings_summary"),
        })
    merged.sort(key=lambda x: x.get("signal_score", 0), reverse=True)
    return merged


async def analyze_single(ticker: str, context: dict[str, Any] | None = None) -> dict[str, Any] | None:
    cached = await get_cached(ticker)
    if cached:
        return {**cached["analysis"], "cached": True}
    if claude_analysis_disabled():
        return {**_fallback_analysis({"ticker": ticker.upper(), **(context or {})}), "cached": False}

    payload = {"ticker": ticker.upper(), "context": context or {}}
    prompt = (
        f"Analyze single stock and respond ONLY with JSON: "
        f"{json.dumps(payload, separators=(',', ':'))}\n"
        "Schema: {\"ticker\":\"\",\"signal_score\":0,\"thesis\":\"\","
        "\"entry_low\":0,\"entry_high\":0,\"catalyst_date\":\"\","
        "\"conviction\":\"\",\"time_horizon\":\"\",\"stop_loss\":0}"
    )
    resp = await _call_claude("You output compact JSON only. No prose.", prompt)
    if not resp:
        return None
    try:
        text = _strip_fences(resp)
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            text = m.group(0)
        item = json.loads(text)
        analysis = {
            "ticker": ticker.upper(),
            "signal_score": int(item.get("signal_score", 0) or 0),
            "thesis": str(item.get("thesis", "")).strip(),
            "entry_low": _num(item.get("entry_low")),
            "entry_high": _num(item.get("entry_high")),
            "catalyst_date": str(item.get("catalyst_date", "")).strip(),
            "conviction": str(item.get("conviction", "medium")).strip().lower(),
            "time_horizon": str(item.get("time_horizon", "medium")).strip().lower(),
            "stop_loss": _num(item.get("stop_loss")),
        }
        await set_cached(ticker, analysis)
        return {**analysis, "cached": False}
    except Exception as e:
        logger.exception("analyze_single parse failed for %s: %s", ticker, e)
        return None
