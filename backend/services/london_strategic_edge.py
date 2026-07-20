"""London Strategic Edge provider wrapper.

This service is intentionally narrow: it normalizes the lse-data SDK behind
async helpers so scanners, backtests, options desk, and ticker pages can share
one provider contract.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any


def configured() -> bool:
    return bool(os.environ.get("LSE_API_KEY", "").strip())


def applicability_map() -> list[dict[str, str]]:
    return [
        {"area": "Backtests", "use": "Primary historical candles, option candles, options flow, macro and bond-yield regime context."},
        {"area": "Options Desk", "use": "Options chains, greeks, IV, contract candles, and historical options flow before Alpaca execution."},
        {"area": "Portfolio Manager", "use": "Regime scoring from macro series, bond yields, multi-asset correlations, and realized volatility."},
        {"area": "Ticker Profiles", "use": "Candles, company profiles, fundamentals, financial reports, dividends, splits, and insider trades."},
        {"area": "Scanner", "use": "Cleaner OHLCV history and cross-asset confirmation before a ticker reaches PM."},
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
    return await asyncio.to_thread(fn, *args, **kwargs)


async def health_probe() -> dict[str, Any]:
    if not configured():
        return {"ok": False, "configured": False, "reason": "missing LSE_API_KEY"}
    try:
        client = _client(timeout=20)
        rows = await _to_thread(client.candles, "AAPL", "1d", limit=1, order="desc")
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
    rows = await _to_thread(
        client.candles,
        symbol.upper(),
        timeframe,
        start=start,
        end=end,
        limit=min(max(int(limit), 1), 5000),
        order=order,
        dataset=dataset,
    )
    return {"provider": "london_strategic_edge", "symbol": symbol.upper(), "timeframe": timeframe, "rows": rows}


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
    rows = await _to_thread(
        client.options,
        underlying.upper(),
        type=option_type,
        min_dte=min_dte,
        max_dte=max_dte,
        limit=min(max(int(limit), 1), 5000),
    )
    return {"provider": "london_strategic_edge", "underlying": underlying.upper(), "rows": rows}


async def options_flow(
    underlying: str | None = None,
    option_type: str | None = None,
    min_premium: float | None = None,
    max_dte: int | None = None,
    limit: int = 5000,
) -> dict[str, Any]:
    client = _client()
    rows = await _to_thread(
        client.options_flow,
        underlying=underlying.upper() if underlying else None,
        type=option_type,
        min_premium=min_premium,
        max_dte=max_dte,
        limit=min(max(int(limit), 1), 5000),
    )
    return {"provider": "london_strategic_edge", "underlying": underlying.upper() if underlying else None, "rows": rows}


async def ticker_context(symbol: str) -> dict[str, Any]:
    client = _client()
    t = symbol.upper()
    profile, fundamentals, reports = await asyncio.gather(
        _to_thread(client.company_profiles, t, limit=5),
        _to_thread(client.fundamentals, t, limit=5),
        _to_thread(client.financial_reports, t, limit=12),
    )
    return {
        "provider": "london_strategic_edge",
        "symbol": t,
        "company_profiles": profile,
        "fundamentals": fundamentals,
        "financial_reports": reports,
    }


async def macro_context(limit: int = 100) -> dict[str, Any]:
    client = _client(timeout=90)
    calendar, yields = await asyncio.gather(
        _to_thread(client.economic_calendar, limit=min(max(int(limit), 1), 5000)),
        _to_thread(client.bond_yields, limit=min(max(int(limit), 1), 5000)),
    )
    return {
        "provider": "london_strategic_edge",
        "economic_calendar": calendar,
        "bond_yields": yields,
    }
