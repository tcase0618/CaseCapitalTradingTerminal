"""Single-batch Claude analysis with 24h Mongo cache. Token-efficient."""
from __future__ import annotations
import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from emergentintegrations.llm.chat import LlmChat, UserMessage

from .db import get_db

logger = logging.getLogger(__name__)

CLAUDE_MODEL = "claude-sonnet-4-5-20250929"
CACHE_TTL_HOURS = 24


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


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
    """Build the structured input for Claude. Compact JSON to save tokens."""
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


def _parse_json_array(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    # Strip markdown fences if Claude added them
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    # Find first [ ... ]
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        text = m.group(0)
    return json.loads(text)


async def analyze_batch(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Token-efficient: pulls cached analyses; calls Claude only for uncached
    tickers in a SINGLE batch request. Returns merged list."""
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
        api_key = os.environ.get("EMERGENT_LLM_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            logger.error("No EMERGENT_LLM_KEY / ANTHROPIC_API_KEY set")
        else:
            session_id = f"scan-{uuid.uuid4()}"
            chat = LlmChat(
                api_key=api_key,
                session_id=session_id,
                system_message=(
                    "You are a senior small/mid-cap equity analyst. You output "
                    "compact JSON only. No prose."
                ),
            ).with_model("anthropic", CLAUDE_MODEL)
            prompt = _build_prompt(to_analyze)
            try:
                resp = await chat.send_message(UserMessage(text=prompt))
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
                logger.exception("Claude batch analyze failed: %s", e)

    # Preserve original input order, merge with signals
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
    """For /analyze [ticker] command. Uses cache; otherwise single Claude call."""
    cached = await get_cached(ticker)
    if cached:
        return {**cached["analysis"], "cached": True}

    api_key = os.environ.get("EMERGENT_LLM_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None

    chat = LlmChat(
        api_key=api_key,
        session_id=f"analyze-{uuid.uuid4()}",
        system_message="You output compact JSON only. No prose.",
    ).with_model("anthropic", CLAUDE_MODEL)

    payload = {"ticker": ticker.upper(), "context": context or {}}
    prompt = (
        f"Analyze this single stock and respond with ONLY a JSON object: "
        f"{json.dumps(payload, separators=(',', ':'))}\n"
        "Schema: {\"ticker\":\"XYZ\",\"signal_score\":1-10,"
        "\"thesis\":\"one sentence\",\"entry_zone\":\"$X-$Y\","
        "\"catalyst_date\":\"YYYY-MM-DD or text\"}"
    )
    try:
        resp = await chat.send_message(UserMessage(text=prompt))
        text = resp.strip()
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
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
        logger.exception("analyze_single failed for %s: %s", ticker, e)
        return None
