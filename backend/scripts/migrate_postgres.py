"""Run pending Case Capital PostgreSQL migrations explicitly during deploy."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

load_dotenv()

from services import postgres_migrations, postgres_store  # noqa: E402


async def main() -> int:
    if not await postgres_store.init_schema():
        print(json.dumps({"ok": False, "error": postgres_store.last_error()}))
        return 1
    pool = postgres_store.pool()
    if pool is None:
        print(json.dumps({"ok": False, "error": "Postgres pool unavailable"}))
        return 1
    result = await postgres_migrations.apply_pending_migrations(pool)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
