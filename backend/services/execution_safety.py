"""Execution-path guardrails shared by equity and options order routes."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo.errors import DuplicateKeyError

from .db import get_db, stamped


def _now() -> datetime:
    return datetime.now(timezone.utc)


def stable_client_order_id(*parts: Any, prefix: str = "cc", max_len: int = 48) -> str:
    """Build a deterministic broker-safe idempotency key."""
    normalized = "|".join(str(part or "").strip().upper() for part in parts)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"[:max_len]


async def ensure_execution_indexes() -> None:
    db = get_db()
    await db.execution_intents.create_index("expires_at", expireAfterSeconds=0)
    await db.execution_intents.create_index([("scope", 1), ("symbol", 1), ("status", 1)])


async def claim_execution_intent(
    *,
    scope: str,
    client_order_id: str,
    symbol: str,
    side: str,
    ttl_seconds: int = 24 * 60 * 60,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically claim one logical order intent before broker submission."""
    db = get_db()
    now = _now()
    doc = stamped({
        "_id": f"intent:{client_order_id}",
        "scope": scope,
        "client_order_id": client_order_id,
        "symbol": str(symbol or "").upper(),
        "side": str(side or "").lower(),
        "status": "claimed",
        "claimed_at": now.isoformat(),
        "expires_at": now + timedelta(seconds=ttl_seconds),
        "metadata": metadata or {},
    })
    try:
        await db.execution_intents.insert_one(doc)
        return {"ok": True, "claimed": True, "intent": {k: v for k, v in doc.items() if k != "_id"}}
    except DuplicateKeyError:
        existing = await db.execution_intents.find_one({"_id": doc["_id"]}, {"_id": 0})
        return {
            "ok": False,
            "claimed": False,
            "reason": "duplicate_execution_intent",
            "client_order_id": client_order_id,
            "existing": existing,
        }


async def mark_execution_intent(client_order_id: str | None, status: str, details: dict[str, Any] | None = None) -> None:
    if not client_order_id:
        return
    await get_db().execution_intents.update_one(
        {"_id": f"intent:{client_order_id}"},
        {"$set": {"status": status, "updated_at": _now().isoformat(), "details": details or {}}},
    )


async def add_risk_allowed(scope: str) -> tuple[bool, dict[str, Any]]:
    from . import safety

    enabled, status = await safety.trading_enabled(scope=scope)
    if not enabled:
        return False, {"reason": status.get("reason") or "safety_halt", "safety": status}
    return True, status
