"""London Strategic Edge provider wrapper.

This service is intentionally narrow: it normalizes the lse-data SDK behind
async helpers so scanners, backtests, options desk, and ticker pages can share
one provider contract.
"""
from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any


_LSE_SEMAPHORE = asyncio.Semaphore(1)
_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_TTL_SEC = 90.0


def configured() -> bool:
    return bool(os.environ.get("LSE_API_KEY", "").strip())


def applicability_map() -> list[dict[str, str]]:
    return [
        {"area": "Backtests", "use": "Primary historical candles, option candles, options flow, macro and bond-yield regime context."},
        {"area": "Options Desk", "use": "Options chains, greeks, IV, contract candles, and historical options flow before Alpaca execution."},
        {"area": "Portfolio Manager", "use": "Regime scoring from macro series, bond yields, multi-asset correlations, and realized volatility."},
        {"area": "Ticker Profiles", "use": "Candles, company profiles, fundamentals, financial reports, dividends, splits, and insider trades."},
        {"area": "Scanner", "use": "Reserved for later; current scanner routing is unchanged."},
        {"area": "Earnings", "use": "Pre/post earnings price reaction history, fundamentals, reports, and macro backdrop."},
        {"area": "Intel Feed", "use": "Insider trades, economic calendar, dividends, splits, and market-wide unusual options flow."},
        {"area": "Performance", "use": "Reprice historical signals from an independent source and audit Alpaca/yfinance drift."},
        {"area": "GeoRisk", "use": "FX, commodities, indices, bond yields, and crypto reaction data around mapped geopolitical events."},
        {"area": "Audit/Data Quality", "use": "Provider freshness, coverage, and fallback reason logging for every market-data pull."},
    ]


def _client(timeout: float = 60):
    from lse import LSE

    return LSE(api_key=os.environ.get("LSE_API_KEY"), timeout=timeout)


async def _to_thread(fn, *args, **kwargs):
    async with _LSE_SEMAPHORE:
        return await asyncio.to_thread(fn, *args, **kwargs)


async def _cached(key: str, ttl: float, fn, *args, **kwargs):
    now = time.monotonic()
    cached = _CACHE.get(key)
    if cached and now - cached[0] <= ttl:
        return cached[1]
    value = await _to_thread(fn, *args, **kwargs)
    _CACHE[key] = (now, value)
    return value


def _error_payload(scope: str, exc: Exception, **extra) -> dict[str, Any]:
    reason = str(exc)[:240]
    return {
        "ok": False,
        "provider": "london_strategic_edge",
        "scope": scope,
        "rows": [],
        "error": reason,
        "degraded": True,
        **extra,
    }


async def health_probe() -> dict[str, Any]:
    if not configured():
        return {"ok": False, "configured": False, "reason": "missing LSE_API_KEY"}
    try:
        client = _client(timeout=20)
        rows = await _cached("health:AAPL:1d", 30.0, client.candles, "AAPL", "1d", limit=1, order="desc")
        return {
            "ok": bool(rows),
            "configured": True,
            "reason": "candles_ok" if rows else "empty candles response",
            "sample_rows": len(rows or []),
        }
    except Exception as exc:
        return {"ok": False, "configured": True, "reason": str(exc)[:240]}


async def candles(
    symbol: str,
    timeframe: str = "1d",
    start: str | None = None,
    end: str | None = None,
    limit: int = 5000,
    order: str = "asc",
    dataset: str | None = None,
) -> dict[str, Any]:
    client = _client()
    t = symbol.upper()
    safe_limit = min(max(int(limit), 1), 5000)
    key = f"candles:{t}:{timeframe}:{start}:{end}:{safe_limit}:{order}:{dataset}"
    try:
        rows = await _cached(
            key,
            _CACHE_TTL_SEC,
            client.candles,
            t,
            timeframe,
            start=start,
            end=end,
            limit=safe_limit,
            order=order,
            dataset=dataset,
        )
        return {"ok": True, "provider": "london_strategic_edge", "symbol": t, "timeframe": timeframe, "rows": rows}
    except Exception as exc:
        return _error_payload("candles", exc, symbol=t, timeframe=timeframe)


async def latest_candles(symbol: str, days: int = 365, timeframe: str = "1d") -> dict[str, Any]:
    start = (datetime.now(timezone.utc) - timedelta(days=max(days, 1))).date().isoformat()
    return await candles(symbol, timeframe=timeframe, start=start, order="asc")


async def options_chain(
    underlying: str,
    option_type: str | None = None,
    min_dte: int | None = None,
    max_dte: int | None = None,
    limit: int = 5000,
) -> dict[str, Any]:
    client = _client()
    t = underlying.upper()
    safe_limit = min(max(int(limit), 1), 5000)
    key = f"options:{t}:{option_type}:{min_dte}:{max_dte}:{safe_limit}"
    try:
        rows = await _cached(
            key,
            _CACHE_TTL_SEC,
            client.options,
            t,
            type=option_type,
            min_dte=min_dte,
            max_dte=max_dte,
            limit=safe_limit,
        )
        return {"ok": True, "provider": "london_strategic_edge", "underlying": t, "rows": rows}
    except Exception as exc:
        return _error_payload("options_chain", exc, underlying=t)


async def options_flow(
    underlying: str | None = None,
    option_type: str | None = None,
    min_premium: float | None = None,
    max_dte: int | None = None,
    limit: int = 5000,
) -> dict[str, Any]:
    client = _client()
    t = underlying.upper() if underlying else None
    safe_limit = min(max(int(limit), 1), 5000)
    key = f"options_flow:{t}:{option_type}:{min_premium}:{max_dte}:{safe_limit}"
    try:
        rows = await _cached(
            key,
            _CACHE_TTL_SEC,
            client.options_flow,
            underlying=t,
            type=option_type,
            min_premium=min_premium,
            max_dte=max_dte,
            limit=safe_limit,
            order="desc",
        )
        return {"ok": True, "provider": "london_strategic_edge", "underlying": t, "rows": rows}
    except Exception as exc:
        return _error_payload("options_flow", exc, underlying=t)


async def ticker_context(symbol: str) -> dict[str, Any]:
    client = _client()
    t = symbol.upper()
    errors: dict[str, str] = {}
    try:
        profile = await _cached(f"profile:{t}", _CACHE_TTL_SEC, client.company_profiles, t, limit=5)
    except Exception as exc:
        profile = []
        errors["company_profiles"] = str(exc)[:180]
    try:
        fundamentals = await _cached(f"fundamentals:{t}", _CACHE_TTL_SEC, client.fundamentals, t, limit=5)
    except Exception as exc:
        fundamentals = []
        errors["fundamentals"] = str(exc)[:180]
    try:
        reports = await _cached(f"reports:{t}", _CACHE_TTL_SEC, client.financial_reports, t, limit=12, order="desc")
    except Exception as exc:
        reports = []
        errors["financial_reports"] = str(exc)[:180]
    return {
        "ok": not errors,
        "degraded": bool(errors),
        "provider": "london_strategic_edge",
        "symbol": t,
        "company_profiles": profile,
        "fundamentals": fundamentals,
        "financial_reports": reports,
        "errors": errors,
    }


async def macro_context(limit: int = 100) -> dict[str, Any]:
    client = _client(timeout=45)
    safe_limit = min(max(int(limit), 1), 5000)
    errors: dict[str, str] = {}
    try:
        calendar = await _cached(
            f"macro:calendar:{safe_limit}",
            _CACHE_TTL_SEC,
            client.economic_calendar,
            order="desc",
            limit=safe_limit,
        )
    except Exception as exc:
        calendar = []
        errors["economic_calendar"] = str(exc)[:180]
    try:
        yields = await _cached(
            f"macro:yields:{safe_limit}",
            _CACHE_TTL_SEC,
            client.bond_yields,
            order="desc",
            limit=safe_limit,
        )
    except Exception as exc:
        yields = []
        errors["bond_yields"] = str(exc)[:180]
    return {
        "ok": not errors,
        "degraded": bool(errors),
        "provider": "london_strategic_edge",
        "economic_calendar": calendar,
        "bond_yields": yields,
        "errors": errors,
    }
