"""One-shot terminal readiness checks for pre-deploy and Sunday boot."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

from .db import get_db, stamped


READINESS_TIMEOUT_SECONDS = 14.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _status(ok: bool, warning: bool = False) -> str:
    if not ok:
        return "BLOCK"
    return "WATCH" if warning else "PASS"


def _age_minutes(value: Any) -> float | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None
    return round(max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 60.0), 1)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


async def _safe_check(name: str, coro, *, blocks: bool = True) -> dict[str, Any]:
    try:
        data = await asyncio.wait_for(coro, timeout=READINESS_TIMEOUT_SECONDS)
        ok = bool(data.get("ok", True)) if isinstance(data, dict) else bool(data)
        return {"name": name, "status": _status(ok) if blocks else _status(True, warning=not ok), "blocks": blocks, "detail": data}
    except Exception as exc:
        return {
            "name": name,
            "status": "BLOCK" if blocks else "WATCH",
            "blocks": blocks,
            "detail": {"ok": False, "reason": exc.__class__.__name__, "message": str(exc)[:220]},
        }


async def _mongo_check() -> dict[str, Any]:
    db = get_db()
    await db.command("ping")
    latest_scan = await db.scan_results.find_one({}, {"_id": 0, "finished_at": 1, "results": 1}, sort=[("finished_at", -1)])
    latest_snapshot = await db.live_position_snapshot_latest.find_one({}, {"_id": 0}, sort=[("snapshot_at", -1)])
    latest_risk = await db.options_desk_risk_checks.find_one(
        {}, {"_id": 0, "checked_at": 1, "positions_checked": 1, "errors": 1}, sort=[("checked_at", -1)]
    )
    return {
        "ok": True,
        "latest_scan_at": (latest_scan or {}).get("finished_at"),
        "latest_scan_age_minutes": _age_minutes((latest_scan or {}).get("finished_at")),
        "latest_scan_rows": len((latest_scan or {}).get("results") or []),
        "position_snapshot_at": (latest_snapshot or {}).get("snapshot_at"),
        "position_snapshot_age_minutes": _age_minutes((latest_snapshot or {}).get("snapshot_at")),
        "options_risk_checked_at": (latest_risk or {}).get("checked_at"),
        "options_risk_age_minutes": _age_minutes((latest_risk or {}).get("checked_at")),
        "options_risk_errors": len((latest_risk or {}).get("errors") or []),
    }


async def _equity_account_check() -> dict[str, Any]:
    from . import trade_floor

    account = await trade_floor.get_account()
    if not account:
        return {"ok": False, "reason": "equity_alpaca_account_unavailable"}
    return {
        "ok": True,
        "status": account.get("status"),
        "trading_blocked": account.get("trading_blocked"),
        "account_blocked": account.get("account_blocked"),
        "equity": account.get("equity"),
        "buying_power": account.get("buying_power"),
    }


async def _options_account_check() -> dict[str, Any]:
    from . import options_desk

    return await options_desk.account()


async def _latest_options_mark_audit() -> dict[str, Any]:
    from . import options_desk

    return await options_desk.latest_mark_audit()


async def run(force_refresh: bool = False, persist: bool = True) -> dict[str, Any]:
    from . import data_quality, data_truth, execution_gate

    truth = await data_truth.overview(force_refresh=force_refresh, persist=False)
    checks = [
        await _safe_check("mongo", _mongo_check()),
        await _safe_check("data_quality", data_quality.overview(force_refresh=force_refresh, record_event=False), blocks=False),
        await _safe_check("data_truth", asyncio.sleep(0, result=truth)),
        await _safe_check("system_gate", execution_gate.check(scope="system", truth=truth, record=False)),
        await _safe_check("equity_gate", execution_gate.check(scope="equity", truth=truth, record=False), blocks=False),
        await _safe_check("options_gate", execution_gate.check(scope="options", truth=truth, record=False), blocks=False),
        await _safe_check("equity_account", _equity_account_check(), blocks=False),
        await _safe_check("options_account", _options_account_check(), blocks=False),
        await _safe_check("options_mark_audit", _latest_options_mark_audit(), blocks=False),
    ]
    try:
        from . import public_api

        checks.append(await _safe_check("public_api_research", public_api.status(), blocks=False))
    except Exception as exc:
        checks.append({
            "name": "public_api_research",
            "status": "WATCH",
            "blocks": False,
            "detail": {"ok": False, "reason": str(exc)[:220]},
        })
    checks.append({
        "name": "scheduler_env",
        "status": "PASS" if _env_bool("ENABLE_SCHEDULER") else "WATCH",
        "blocks": False,
        "detail": {"ok": _env_bool("ENABLE_SCHEDULER"), "enabled": _env_bool("ENABLE_SCHEDULER")},
    })

    blockers = [c for c in checks if c.get("blocks") and c.get("status") == "BLOCK"]
    warnings = [c for c in checks if c.get("status") == "WATCH" or (not c.get("blocks") and c.get("status") == "BLOCK")]
    score = round(max(0.0, 100.0 - len(blockers) * 22.0 - len(warnings) * 6.0), 1)
    decision = "BLOCK" if blockers else "WATCH" if warnings else "READY"
    payload = {
        # WATCH means reportable, not ready for trading.
        "ok": decision == "READY",
        "decision": decision,
        "score": score,
        "generated_at": _now_iso(),
        "mode": os.environ.get("APP_ENV", "local"),
        "execution": truth.get("execution") or {},
        "checks": checks,
        "blockers": [{"name": c["name"], "detail": c.get("detail")} for c in blockers],
        "warnings": [{"name": c["name"], "detail": c.get("detail")} for c in warnings],
        "next_steps": [
            "Fix BLOCK rows before enabling execution.",
            "WATCH rows can deploy if they are expected, then verify them live on the VPS after restart.",
            "Run python backend/readiness_check.py before and after the deploy pull.",
        ],
    }
    if persist:
        db = get_db()
        await db.readiness_checks.insert_one(stamped(payload))
        await db.bot_state.update_one({"_id": "readiness_latest"}, {"$set": payload}, upsert=True)
    return payload
