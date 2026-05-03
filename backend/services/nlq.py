"""Natural language Telegram queries. Routes any non-/ message to Claude with
filtered context. 6h cache. Capped at ~300 tokens."""
from __future__ import annotations
import hashlib
import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from .claude_service import _call_claude
from .db import get_db

logger = logging.getLogger(__name__)


def _question_hash(text: str) -> str:
    return hashlib.sha1(text.lower().strip().encode()).hexdigest()[:16]


async def _get_cached(qhash: str) -> str | None:
    db = get_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()
    doc = await db.nlq_cache.find_one(
        {"qhash": qhash, "ts": {"$gte": cutoff}}, {"_id": 0}, sort=[("ts", -1)],
    )
    return doc["answer"] if doc else None


async def _set_cached(qhash: str, question: str, answer: str):
    db = get_db()
    await db.nlq_cache.update_one(
        {"qhash": qhash},
        {"$set": {"qhash": qhash, "question": question, "answer": answer,
                   "ts": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )


async def _gather_context(question: str, chat_id: str) -> dict[str, Any]:
    """Pick only the relevant context slice based on lightweight keyword match."""
    db = get_db()
    q = question.lower()
    ctx: dict[str, Any] = {}

    # Latest scan results — always include but trim to top 10
    last = await db.scan_results.find_one({}, {"_id": 0}, sort=[("finished_at", -1)])
    if last:
        ctx["latest_scan"] = {
            "finished_at": last.get("finished_at"),
            "pre_filter_passed": last.get("pre_filter_passed"),
            "results": [
                {k: r.get(k) for k in ("ticker", "signal_score", "signals", "thesis",
                                         "price", "risk", "targets", "catalyst_date",
                                         "squeeze")}
                for r in (last.get("results") or [])[:10]
            ],
        }

    # Watchlist if mentioned
    if "watchlist" in q or "my list" in q or "tracking" in q:
        items = await db.watchlist.find({"chat_id": chat_id}, {"_id": 0}).to_list(50)
        ctx["watchlist"] = [it["ticker"] for it in items]

    # Specific ticker mention
    tickers = re.findall(r"\$?\b([A-Z]{2,5})\b", question)
    if tickers:
        ctx["mentioned_tickers"] = list(dict.fromkeys(tickers))[:5]

    # Sector keyword pruning to cut tokens
    if "defense" in q:
        ctx["sector_focus"] = "defense"
    elif "healthcare" in q or "biotech" in q:
        ctx["sector_focus"] = "healthcare"
    elif "cyber" in q:
        ctx["sector_focus"] = "cybersecurity"
    elif "energy" in q:
        ctx["sector_focus"] = "energy"

    # Squeeze
    if "squeeze" in q:
        from .squeeze import squeeze_leaderboard
        ctx["squeeze_leaderboard"] = (await squeeze_leaderboard(5))

    return ctx


async def answer(question: str, chat_id: str) -> str:
    qhash = _question_hash(question)
    cached = await _get_cached(qhash)
    if cached:
        return cached + "\n\n<i>(cached)</i>"

    ctx = await _gather_context(question, chat_id)
    prompt = (
        f"User question (Telegram): {question}\n\n"
        f"Context (filtered): {json.dumps(ctx, separators=(',', ':'))[:6000]}\n\n"
        "Answer in plain English, directly and concisely (<= 300 tokens). "
        "Use HTML tags for Telegram (<b> <i> <code>). When ranking stocks, use "
        "the data in context. Don't give blanket disclaimers; assume the user "
        "knows it's not financial advice."
    )
    resp = await _call_claude(
        "You are a sharp stock analyst. Answer the user's question using the "
        "provided context. Output Telegram-friendly HTML, plain English, no "
        "JSON, no markdown fences.",
        prompt,
    )
    if not resp:
        return "Sorry — I couldn't process that right now. Try /scan or /help."
    answer_text = resp.strip()
    await _set_cached(qhash, question, answer_text)
    return answer_text
