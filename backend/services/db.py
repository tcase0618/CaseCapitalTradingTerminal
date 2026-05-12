"""Shared MongoDB client + collection accessors."""
import os
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient

_client: AsyncIOMotorClient | None = None

FEATURE_VERSION = "3.0"


def get_db():
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return _client[os.environ["DB_NAME"]]


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
    await db.activity_log.insert_one({
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "message": message,
        "meta": meta or {},
        "feature_version": FEATURE_VERSION,
    })
