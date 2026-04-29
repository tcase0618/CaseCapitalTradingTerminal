"""Single-batch Claude analysis with 24h Mongo cache. Token-efficient.

Uses the official `anthropic` SDK when ANTHROPIC_API_KEY is set; otherwise
falls back to emergentintegrations LlmChat with EMERGENT_LLM_KEY.
"""
from __future__ import annotations
import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import get_db

logger = logging.getLogger(__name__)

CLAUDE_MODEL = "claude-sonnet-4-5-20250929"
CACHE_TTL_HOURS = 24


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _use_native() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


async def _call_claude(system: str, user: str) -> str | None:
    """Call Claude via official SDK (if ANTHROPIC_API_KEY) or emergentintegrations."""
    if _use_native():
        try:
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
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

    api_key = os.environ.get("EMERGENT_LLM_KEY")
    if not api_key:
        logger.error("No ANTHROPIC_API_KEY or EMERGENT_LLM_KEY configured")
        return None
    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
        chat = LlmChat(
            api_key=api_key,
            session_id=f"claude-{uuid.uuid4()}",
            system_message=system,
        ).with_model("anthropic", CLAUDE_MODEL)
        return await chat.send_message(UserMessage(text=user))
    except Exception as e:
        logger.exception("Emergent LlmChat call failed: %s", e)
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
    compact = []
    for c in candidates:
        compact.append({
            "ticker": c["ticker"],
            "signals": c["signals"],
            "insider": c.get("insider_summary"),
            "short": c.get("short_summary"),
            "earnings": c.get("earnings_summary"),
        })
    return (
        "You are a sharp stock-trading analyst. Below is a JSON array of "
        "pre-filtered tickers, each having 2+ confirmed signals (insider cluster "
        "buys, high short interest, upcoming earnings catalyst). Analyze ALL "
        "tickers in this single call.\n\n"
        f"INPUT:\n{json.dumps(compact, separators=(',', ':'))}\n\n"
        "Return ONLY a valid JSON array (no prose, no markdown fences) with one "
        "object per ticker exactly in this shape:\n"
        "[{\"ticker\":\"XYZ\",\"signal_score\":1-10,\"thesis\":\"one sentence\","
        "\"entry_zone\":\"$X.XX-$Y.YY\",\"catalyst_date\":\"YYYY-MM-DD or text\"}]\n"
        "signal_score: 1-10 integer reflecting setup quality (10=highest conviction). "
        "thesis: ONE punchy sentence tying signals together. "
        "entry_zone: a price band you'd buy. "
        "catalyst_date: nearest meaningful catalyst (earnings or filing). "
        "Be concise. Output JSON only."
    )


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    return text


def _parse_json_array(text: str) -> list[dict[str, Any]]:
    text = _strip_fences(text)
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        text = m.group(0)
    return json.loads(text)


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
        system = (
            "You are a senior small/mid-cap equity analyst. You output compact "
            "JSON only. No prose."
        )
        prompt = _build_prompt(to_analyze)
        resp = await _call_claude(system, prompt)
        if resp:
            try:
                parsed = _parse_json_array(resp)
                for item in parsed:
                    t = str(item.get("ticker", "")).upper()
                    if not t:
                        continue
                    analysis = {
                        "ticker": t,
                        "signal_score": int(item.get("signal_score", 0) or 0),
                        "thesis": str(item.get("thesis", "")).strip(),
                        "entry_zone": str(item.get("entry_zone", "")).strip(),
                        "catalyst_date": str(item.get("catalyst_date", "")).strip(),
                    }
                    await set_cached(t, analysis)
                    results[t] = {**analysis, "cached": False}
            except Exception as e:
                logger.exception("Failed to parse Claude batch JSON: %s", e)

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

    payload = {"ticker": ticker.upper(), "context": context or {}}
    prompt = (
        f"Analyze this single stock and respond with ONLY a JSON object: "
        f"{json.dumps(payload, separators=(',', ':'))}\n"
        "Schema: {\"ticker\":\"XYZ\",\"signal_score\":1-10,"
        "\"thesis\":\"one sentence\",\"entry_zone\":\"$X-$Y\","
        "\"catalyst_date\":\"YYYY-MM-DD or text\"}"
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
            "entry_zone": str(item.get("entry_zone", "")).strip(),
            "catalyst_date": str(item.get("catalyst_date", "")).strip(),
        }
        await set_cached(ticker, analysis)
        return {**analysis, "cached": False}
    except Exception as e:
        logger.exception("analyze_single parse failed for %s: %s", ticker, e)
        return None
