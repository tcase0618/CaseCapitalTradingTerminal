"""One DB-independent alert attempt per systemd backup-failure activation.

No delivery retry: an ambiguous timeout may already have reached Telegram.
The systemd unit owns invocation; this does not provide global deduplication.
"""
from __future__ import annotations

import asyncio
import json
import os

import httpx


async def notify() -> dict:
    from . import telegram_service

    enabled = os.environ.get("TELEGRAM_EVENTS_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled or not telegram_service._standalone_delivery_allowed("ops_alert"):
        return {"attempted": False, "sent": False, "reason": "disabled"}
    if not telegram_service._has_token() or not telegram_service._default_chat_id():
        return {"attempted": False, "sent": False, "reason": "not_configured"}
    # Only this fixed operational notice bypasses DB-backed delivery guards.
    # Do not include journal output, DSNs, credentials or arbitrary arguments.
    payload = {
        "chat_id": telegram_service._default_chat_id(),
        "text": ("CASE CAPITAL | POSTGRESQL BACKUP FAILED — CRITICAL\n"
                 "The scheduled backup service failed. A new recovery point is NOT VERIFIED.\n"
                 "Check the backup unit journal, disk headroom and last verified dump. "
                 "Do not delete backups without verified off-host recovery copies."),
        "disable_web_page_preview": True,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(telegram_service._api_url("sendMessage"), json=payload)
        sent = response.status_code == 200 and response.json().get("ok") is True
        return {"attempted": True, "sent": sent, "reason": "sent" if sent else "delivery_unverified"}
    except Exception:
        # Token-bearing URLs and raw exceptions must never reach the journal.
        return {"attempted": True, "sent": False, "reason": "delivery_unverified"}


def main() -> int:
    result = asyncio.run(notify())
    print(json.dumps(result))
    return 0 if result["sent"] or result["reason"] == "disabled" else 1


if __name__ == "__main__":
    raise SystemExit(main())
