"""Database access for the terminal's PostgreSQL runtime."""
import os
from datetime import datetime, timezone
from typing import Any

_client: Any | None = None

FEATURE_VERSION = "3.0"


def database_failure_reason(exc: Exception) -> str:
    """Public-safe categories only; exception messages can contain credentials."""
    if isinstance(exc, TimeoutError):
        return "database_timeout"
    sqlstate = str(getattr(exc, "sqlstate", "") or "")
    if sqlstate.startswith("28"):
        return "database_authentication_failed"
    if sqlstate.startswith("08") or isinstance(exc, OSError):
        return "database_connection_failed"
    if sqlstate.startswith("53"):
        return "database_capacity_unavailable"
    if sqlstate.startswith("42"):
        return "database_schema_unavailable"
    return "database_read_failed"


def get_db():
    # PostgreSQL is the only supported runtime backend. Keep the function
    # boundary so services retain their existing collection-style interface.
    from . import postgres_store
    return postgres_store.get_database()


def stamped(doc: dict | None = None) -> dict:
    """Inject created_at + feature_version=3.0 into every write so we can
    track which engine version produced each row."""
    return {
        **(doc or {}),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "feature_version": FEATURE_VERSION,
    }


async def log_activity(message: str, level: str = "info", meta: dict | None = None):
    db = get_db()
    doc = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "message": message,
        "meta": meta or {},
        "feature_version": FEATURE_VERSION,
    }
    await db.activity_log.insert_one(doc)
