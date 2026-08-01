"""Ticker hygiene for scanner, PM, and execution boundaries."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .db import get_db, stamped

VALID_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,5}$")
SINGLE_LETTER_ALLOWLIST = {"A", "C", "F", "K", "O", "T", "V", "X"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_ticker(value: Any) -> str:
    return str(value or "").strip().upper().replace("$", "")


def validate_ticker(value: Any) -> dict[str, Any]:
    ticker = normalize_ticker(value)
    if not ticker:
        return {"ok": False, "ticker": ticker, "reason": "missing_ticker", "severity": "BLOCK"}
    if not VALID_TICKER_RE.match(ticker):
        return {"ok": False, "ticker": ticker, "reason": "invalid_ticker_format", "severity": "BLOCK"}
    if len(ticker) == 1 and ticker not in SINGLE_LETTER_ALLOWLIST:
        return {"ok": False, "ticker": ticker, "reason": "single_letter_not_allowlisted", "severity": "BLOCK"}
    return {"ok": True, "ticker": ticker, "reason": None, "severity": "PASS"}


def filter_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows or []:
        check = validate_ticker(row.get("ticker"))
        ticker = check["ticker"]
        if not check["ok"]:
            rejected.append({**check, "source": row.get("source"), "signals": row.get("signals")})
            continue
        if ticker in seen:
            rejected.append({"ok": False, "ticker": ticker, "reason": "duplicate_ticker_row", "severity": "WATCH"})
            continue
        seen.add(ticker)
        kept.append({**row, "ticker": ticker})
    return {"rows": kept, "rejected": rejected, "rejected_count": len(rejected), "checked_at": _now()}


async def record_rejections(source: str, rejected: list[dict[str, Any]]) -> None:
    if not rejected:
        return
    db = get_db()
    await db.ticker_hygiene_rejections.insert_one(stamped({
        "source": source,
        "rejected_count": len(rejected),
        "rejected": rejected[:100],
    }))
