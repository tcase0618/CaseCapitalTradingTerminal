from __future__ import annotations

import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

from services import readiness  # noqa: E402


async def main() -> int:
    report = await readiness.run(force_refresh=False, persist=True)
    print(json.dumps(report, indent=2, default=str))
    return 2 if report.get("decision") == "BLOCK" else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
