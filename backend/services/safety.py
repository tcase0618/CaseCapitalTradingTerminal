"""Execution safety controls shared by equity and options desks.

This module is deliberately small and dependency-light. It owns operator halt
state and the daily-loss breaker so every execution path can fail safe before
touching Alpaca.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from .db import get_db, log_activity, stamped


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _today_key() -> str:
    return _now().date().isoformat()


async def trading_status() -> dict[str, Any]:
    """Return the durable execution state.

    DB errors are treated as disabled by callers. A missing state doc defaults
    to enabled so existing paper systems keep running after deployment, but the
    response marks that state as not explicitly initialized.
    """
    try:
        db = get_db()
        state = await db.bot_state.find_one({"_id": "state"}, {"_id": 0}) or {}
    except Exception as exc:
        return {
            "ok": False,
            "trading_enabled": False,
            "reason": "safety_state_unavailable",
            "detail": str(exc)[:180],
            "fail_closed": True,
        }

    trading_enabled = state.get("trading_enabled")
    initialized = trading_enabled is not None
    if trading_enabled is None:
        trading_enabled = _env_bool("TRADING_ENABLED_DEFAULT", True)

    daily = state.get("daily_loss_breaker") or {}
    return {
        "ok": True,
        "trading_enabled": bool(trading_enabled),
        "initialized": initialized,
        "halt_reason": state.get("trading_halt_reason"),
        "halted_at": state.get("trading_halted_at"),
        "resumed_at": state.get("trading_resumed_at"),
        "daily_loss_breaker": daily,
        "generated_at": _now().isoformat(),
    }


async def trading_enabled(scope: str = "system") -> tuple[bool, dict[str, Any]]:
    status = await trading_status()
    if not status.get("ok"):
        return False, status
    if not status.get("trading_enabled"):
        return False, {**status, "reason": status.get("halt_reason") or "operator_halt"}
    return True, status


async def set_trading(enabled: bool, reason: str = "operator") -> dict[str, Any]:
    db = get_db()
    now = _now().isoformat()
    update: dict[str, Any] = {
        "trading_enabled": bool(enabled),
        "trading_state_updated_at": now,
        "trading_state_reason": reason,
    }
    if enabled:
        update["trading_resumed_at"] = now
        update["trading_halt_reason"] = None
    else:
        update["trading_halted_at"] = now
        update["trading_halt_reason"] = reason
    await db.bot_state.update_one({"_id": "state"}, {"$set": update}, upsert=True)
    await db.safety_events.insert_one(stamped({
        "type": "trading_resumed" if enabled else "trading_halted",
        "enabled": bool(enabled),
        "reason": reason,
        "created_at": now,
    }))
    await log_activity(
        f"Trading {'resumed' if enabled else 'halted'}: {reason}",
        "success" if enabled else "warn",
        {"reason": reason},
    )
    return await trading_status()


async def snapshot_day_start_equity(equity: float | None = None, source: str = "manual") -> dict[str, Any]:
    """Persist the equity baseline used by the daily-loss breaker."""
    if equity is None:
        try:
            from . import trade_floor
            account = await trade_floor.get_account() or {}
            equity = _safe_float(account.get("equity"))
        except Exception:
            equity = 0.0
    if not equity or equity <= 0:
        return {"ok": False, "reason": "equity_unavailable"}
    payload = {
        "date": _today_key(),
        "day_start_equity": round(float(equity), 2),
        "snapshot_at": _now().isoformat(),
        "source": source,
    }
    await get_db().bot_state.update_one(
        {"_id": "state"},
        {"$set": {"daily_loss_breaker": payload}},
        upsert=True,
    )
    return {"ok": True, **payload}


async def check_daily_loss(account: dict[str, Any] | None = None, *, source: str = "execution") -> dict[str, Any]:
    """Trip the global halt when current equity breaches the daily loss limit."""
    threshold_pct = _safe_float(os.environ.get("DAILY_LOSS_HALT_PCT"), 3.0)
    if threshold_pct <= 0:
        return {"ok": True, "enabled": False, "reason": "daily_loss_breaker_disabled"}
    if account is None:
        try:
            from . import trade_floor
            account = await trade_floor.get_account()
        except Exception:
            account = None
    current_equity = _safe_float((account or {}).get("equity"))
    if current_equity <= 0:
        return {"ok": False, "reason": "current_equity_unavailable"}

    status = await trading_status()
    daily = status.get("daily_loss_breaker") or {}
    if daily.get("date") != _today_key() or _safe_float(daily.get("day_start_equity")) <= 0:
        daily = await snapshot_day_start_equity(current_equity, source=f"{source}_auto_snapshot")
        if not daily.get("ok"):
            return daily

    start = _safe_float(daily.get("day_start_equity"))
    drawdown_pct = ((start - current_equity) / start * 100.0) if start > 0 else 0.0
    payload = {
        "ok": True,
        "enabled": True,
        "threshold_pct": threshold_pct,
        "day_start_equity": round(start, 2),
        "current_equity": round(current_equity, 2),
        "drawdown_pct": round(drawdown_pct, 2),
        "tripped": drawdown_pct >= threshold_pct,
        "source": source,
        "checked_at": _now().isoformat(),
    }
    await get_db().bot_state.update_one(
        {"_id": "state"},
        {"$set": {"daily_loss_breaker.last_check": payload}},
        upsert=True,
    )
    if payload["tripped"]:
        await set_trading(False, f"daily_loss_breaker_{payload['drawdown_pct']}pct")
        try:
            from . import telegram_events
            await telegram_events.emit_event(
                "daily_loss_breaker_tripped",
                severity="critical",
                scope="risk",
                title="Daily loss breaker tripped",
                summary=f"Drawdown {payload['drawdown_pct']}% exceeded {threshold_pct}%. Trading halted.",
                details=payload,
                priority="critical",
            )
        except Exception:
            pass
    return payload


def quote_age_seconds(ts: Any) -> int | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None
    return max(0, int((_now() - parsed.astimezone(timezone.utc)).total_seconds()))


def quote_is_fresh(meta: dict[str, Any], max_age_s: int | None = None) -> tuple[bool, int | None]:
    max_age = int(max_age_s or os.environ.get("QUOTE_MAX_AGE_S", "90") or 90)
    age = meta.get("age_s")
    if age is None:
        age = quote_age_seconds(meta.get("ts") or meta.get("fetched_at"))
    if age is None:
        return False, None
    return age <= max_age, int(age)
