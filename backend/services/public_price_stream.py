"""Bounded Public quote subscription for held-equity ratchets.

Public's SDK calls this a price stream, but it is SDK-managed quote polling.
The worker intentionally subscribes only to broker-reconciled Public holdings,
validates every callback with the same executable-quote policy as order entry,
and delegates all stop updates to ``pm_ratchet``.  It never submits orders.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
from datetime import datetime, timezone
from typing import Any

from .db import get_db

logger = logging.getLogger(__name__)
_task: asyncio.Task | None = None
_task_lock = asyncio.Lock()
_state: dict[str, Any] = {"status": "not_started", "symbols": [], "updates": 0}


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


def _poll_seconds() -> float:
    try:
        return max(0.5, min(60.0, float(os.environ.get("PUBLIC_PRICE_STREAM_POLL_SECONDS", "1"))))
    except ValueError:
        return 1.0


def _refresh_seconds() -> float:
    try:
        return max(15.0, min(300.0, float(os.environ.get("PUBLIC_PRICE_STREAM_SYMBOL_REFRESH_SECONDS", "60"))))
    except ValueError:
        return 60.0


def enabled() -> bool:
    from . import public_api

    cfg = public_api.config()
    return bool(
        _bool("PUBLIC_PRICE_STREAM_ENABLED", True)
        and cfg.enabled
        and cfg.sdk_enabled
        and cfg.secret
        and cfg.account_id
    )


async def _held_symbols() -> list[str]:
    rows = await get_db().tf_trades.find(
        {
            "broker_base": "public",
            "status": "OPEN",
            "fill_status": {"$in": ["FILLED", "PARTIALLY_FILLED"]},
            "qty_remaining": {"$gt": 0},
        },
        {"_id": 0, "ticker": 1},
    ).to_list(500)
    return sorted({str(row.get("ticker") or "").upper().strip() for row in rows if row.get("ticker")})


async def _persist_state(**updates: Any) -> None:
    _state.update(updates)
    _state["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        await get_db().bot_state.update_one(
            {"_id": "public_price_stream"},
            {"$set": {"_id": "public_price_stream", **_state}},
            upsert=True,
        )
    except Exception:
        logger.debug("Could not persist Public price-stream status", exc_info=True)


async def _unsubscribe(stream: Any, subscription_id: Any) -> None:
    if not subscription_id:
        return
    result = stream.unsubscribe(subscription_id)
    if inspect.isawaitable(result):
        await result


async def _run_subscription(symbols: list[str]) -> None:
    from public_api_sdk import (
        ApiKeyAuthConfig,
        AsyncPublicApiClient,
        AsyncPublicApiClientConfiguration,
        InstrumentType,
        OrderInstrument,
        SubscriptionConfig,
    )
    from . import pm_ratchet, public_api

    cfg = public_api.config()
    instruments = [OrderInstrument(symbol=symbol, type=InstrumentType.EQUITY) for symbol in symbols]
    subscription_id = None

    async def on_price_change(change: Any) -> None:
        try:
            symbol = str(getattr(getattr(change, "instrument", None), "symbol", "")).upper().strip()
            if not symbol:
                return
            row = public_api._sdk_quote_payload(getattr(change, "new_quote"), None)
            row["ticker"] = symbol
            row["symbol"] = symbol
            mark = pm_ratchet._fresh_public_execution_mark(row)
            if not mark:
                _state["invalid_updates"] = int(_state.get("invalid_updates") or 0) + 1
                return
            result = await pm_ratchet.process_public_ratchet_marks({symbol: mark}, source="public_sdk_price_stream")
            _state["updates"] = int(_state.get("updates") or 0) + 1
            _state["last_quote_at"] = datetime.now(timezone.utc).isoformat()
            _state["last_symbol"] = symbol
            _state["last_ratchet_result"] = {"ratcheted": result.get("ratcheted", 0), "checked": result.get("checked", 0)}
        except Exception as exc:
            _state["callback_errors"] = int(_state.get("callback_errors") or 0) + 1
            _state["last_error"] = f"callback:{exc.__class__.__name__}"
            logger.exception("Public price-stream callback failed")

    async with AsyncPublicApiClient(
        auth_config=ApiKeyAuthConfig(api_secret_key=cfg.secret, validity_minutes=cfg.sdk_token_validity_minutes),
        config=AsyncPublicApiClientConfiguration(default_account_number=cfg.account_id, base_url=cfg.api_base),
    ) as client:
        subscription_id = await client.price_stream.subscribe(
            instruments=instruments,
            callback=on_price_change,
            config=SubscriptionConfig(
                polling_frequency_seconds=_poll_seconds(),
                retry_on_error=True,
                max_retries=5,
                exponential_backoff=True,
            ),
        )
        await _persist_state(
            status="running",
            symbols=symbols,
            subscription_id=str(subscription_id),
            poll_seconds=_poll_seconds(),
            last_error=None,
        )
        try:
            await asyncio.sleep(_refresh_seconds())
        finally:
            await _unsubscribe(client.price_stream, subscription_id)


async def _run() -> None:
    backoff = 2.0
    while True:
        try:
            if not enabled():
                await _persist_state(status="disabled", symbols=[])
                await asyncio.sleep(30)
                continue
            symbols = await _held_symbols()
            if not symbols:
                await _persist_state(status="idle_no_public_positions", symbols=[])
                await asyncio.sleep(_refresh_seconds())
                continue
            await _run_subscription(symbols)
            backoff = 2.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await _persist_state(status="degraded", last_error=f"{exc.__class__.__name__}")
            logger.exception("Public price stream degraded; REST monitor remains active")
            await asyncio.sleep(backoff)
            backoff = min(60.0, backoff * 2)


async def start() -> dict[str, Any]:
    global _task
    async with _task_lock:
        if _task and not _task.done():
            return {"started": False, "reason": "already_running", **_state}
        if not enabled():
            await _persist_state(status="disabled")
            return {"started": False, "reason": "not_configured", **_state}
        _task = asyncio.create_task(_run(), name="public-price-stream")
        await _persist_state(status="starting")
        return {"started": True, **_state}


async def stop() -> None:
    global _task
    async with _task_lock:
        task, _task = _task, None
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await _persist_state(status="stopped", symbols=[])


async def status() -> dict[str, Any]:
    persisted = await get_db().bot_state.find_one({"_id": "public_price_stream"}, {"_id": 0}) or {}
    runtime_running = bool(_task and not _task.done())
    # A CLI health check imports a new module instance with the default local
    # state. Prefer the persisted service state in that process; the running
    # API process still exposes its newer in-memory callback counters.
    state = _state if runtime_running else persisted
    return {
        **persisted,
        **state,
        "enabled": enabled(),
        "task_running": runtime_running,
    }
