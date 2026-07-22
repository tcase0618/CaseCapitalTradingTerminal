"""Terminal readiness and execution diagnostics."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import httpx

from .db import get_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mask(value: str | None) -> str:
    if not value:
        return "missing"
    return f"set:{len(value)}"


async def _alpaca_probe() -> dict[str, Any]:
    key = os.environ.get("APCA_API_KEY_ID", "").strip()
    secret = os.environ.get("APCA_API_SECRET_KEY", "").strip()
    base = os.environ.get("APCA_API_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")
    account_base = base[:-3] if base.endswith("/v2") else base
    out: dict[str, Any] = {
        "configured": bool(key and secret),
        "base_url": base,
        "normalized_trade_base_url": account_base,
        "key_state": _mask(key),
        "secret_state": _mask(secret),
        "ok": False,
        "status_code": None,
        "reason": None,
        "account": None,
    }
    if not key or not secret:
        out["reason"] = "missing_key_or_secret"
        return out
    try:
        async with httpx.AsyncClient(timeout=float(os.environ.get("ALPACA_HEALTH_TIMEOUT_SEC", "3.0")), headers={
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
        }) as client:
            r = await client.get(f"{account_base}/v2/account")
            out["status_code"] = r.status_code
            if r.status_code != 200:
                out["reason"] = (r.text or "")[:220]
                return out
            data = r.json()
            out["ok"] = True
            out["reason"] = "account_ok"
            out["account"] = {
                "status": data.get("status"),
                "trading_blocked": data.get("trading_blocked"),
                "account_blocked": data.get("account_blocked"),
                "equity": data.get("equity"),
                "cash": data.get("cash"),
                "buying_power": data.get("buying_power"),
                "pattern_day_trader": data.get("pattern_day_trader"),
            }
            return out
    except Exception as exc:
        out["reason"] = str(exc)[:220]
        return out


async def _db_counts() -> dict[str, Any]:
    db = get_db()
    counts = {}
    for name in [
        "scan_results",
        "signal_performance",
        "tf_trades",
        "tf_phase_outcomes",
        "pm_ratchet_events",
        "pm_rulesets",
        "tf_journal",
        "tf_execution_tests",
    ]:
        counts[name] = await db[name].count_documents({})
    latest_scan = await db.scan_results.find_one({}, {"_id": 0, "finished_at": 1}, sort=[("finished_at", -1)])
    return {"ok": True, "counts": counts, "latest_scan_at": (latest_scan or {}).get("finished_at")}


async def overview() -> dict[str, Any]:
    db = {"ok": False, "counts": {}, "latest_scan_at": None, "reason": None}
    try:
        db = await _db_counts()
    except Exception as exc:
        db["reason"] = str(exc)[:220]

    alpaca = await _alpaca_probe()
    env = {
        "mongodb": _mask(os.environ.get("MONGO_URL")),
        "db_name": os.environ.get("DB_NAME", ""),
        "anthropic": _mask(os.environ.get("ANTHROPIC_API_KEY")),
        "claude_disabled": os.environ.get("DISABLE_CLAUDE_ANALYSIS", "").lower() == "true",
        "fred": _mask(os.environ.get("FRED_API_KEY")),
        "alpha_vantage": _mask(os.environ.get("ALPHA_VANTAGE_API_KEY")),
        "fmp": _mask(os.environ.get("FMP_API_KEY")),
        "telegram": _mask(os.environ.get("TELEGRAM_BOT_TOKEN")),
    }

    blockers = []
    if not db.get("ok"):
        blockers.append("MongoDB unavailable")
    if not alpaca.get("ok"):
        blockers.append(f"Alpaca execution unavailable: {alpaca.get('reason')}")
    if not db.get("counts", {}).get("scan_results"):
        blockers.append("No saved scans for PM/backtest/journal")
    if not db.get("counts", {}).get("tf_trades"):
        blockers.append("No Trade Floor execution records yet")

    return {
        "generated_at": _now(),
        "ready_for_scanning": bool(db.get("ok")),
        "ready_for_pm": bool(db.get("ok") and db.get("counts", {}).get("scan_results")),
        "ready_for_trade_floor": bool(db.get("ok") and alpaca.get("ok")),
        "ready_for_journal_learning": bool(db.get("ok") and db.get("counts", {}).get("tf_trades")),
        "blockers": blockers,
        "database": db,
        "alpaca": alpaca,
        "env": env,
    }
