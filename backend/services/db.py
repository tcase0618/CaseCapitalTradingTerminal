"""Shared MongoDB client + collection accessors."""
import os
from motor.motor_asyncio import AsyncIOMotorClient

_client: AsyncIOMotorClient | None = None


def get_db():
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return _client[os.environ["DB_NAME"]]


async def log_activity(message: str, level: str = "info", meta: dict | None = None):
    from datetime import datetime, timezone
    db = get_db()
    await db.activity_log.insert_one({
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "message": message,
        "meta": meta or {},
    })
