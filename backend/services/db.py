"""Shared MongoDB client + collection accessors."""
import os
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient

_client: AsyncIOMotorClient | None = None

FEATURE_VERSION = "3.0"


def get_db():
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(
            os.environ["MONGO_URL"],
            serverSelectionTimeoutMS=int(os.environ.get("MONGO_SERVER_SELECTION_TIMEOUT_MS", "5000")),
        )
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
    doc = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "message": message,
        "meta": meta or {},
        "feature_version": FEATURE_VERSION,
    }
    await db.activity_log.insert_one(doc)
    try:
        from . import postgres_store
        await postgres_store.mirror_document("activity_log", doc)
    except Exception:
        # Postgres is a parallel durability layer during migration; it must not
        # break the active Mongo-backed runtime.
        pass
