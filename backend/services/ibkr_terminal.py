"""Terminal-wide IBKR read-only data orchestration.

IBKR is a reliability layer for market data, option metadata, and historical
bars. Alpaca remains the account, position, order, fill, and execution source.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from .db import get_db, stamped

APPLICATIONS: list[dict[str, Any]] = [
    {
        "key": "options_desk_validation",
        "name": "Options Desk Validation",
        "role": "primary_validation",
        "uses": ["chain", "contract", "quote", "greeks", "spread", "size"],
        "endpoint": "/api/options_desk/alpaca_workflow",
        "impact": 98,
    },
    {
        "key": "options_pm_learning",
        "name": "Options PM / Learning",
        "role": "training_truth",
        "uses": ["tradeability", "theta", "iv", "delta", "spread_quality"],
        "endpoint": "/api/ibkr/scanner_validation",
        "impact": 94,
    },
    {
        "key": "kronos_candles",
        "name": "Kronos Candle Engine",
        "role": "historical_ohlcv",
        "uses": ["5m_bars", "daily_bars", "atr", "realized_volatility"],
        "endpoint": "/api/ibkr/enrichment/SPY",
        "impact": 92,
    },
    {
        "key": "quality_gate",
        "name": "Quality / Data Truth",
        "role": "source_health",
        "uses": ["gateway_status", "farm_status", "live_vs_delayed", "permission_errors"],
        "endpoint": "/api/ibkr/status",
        "impact": 96,
    },
    {
        "key": "ticker_profiles",
        "name": "Ticker Profiles / Battle Cards",
        "role": "ticker_enrichment",
        "uses": ["contract_metadata", "quote", "history", "optionability"],
        "endpoint": "/api/ibkr/enrichment/{symbol}",
        "impact": 90,
    },
    {
        "key": "scanner_validation",
        "name": "Scanner Top-Candidate Validation",
        "role": "high_ranked_only",
        "uses": ["quote", "history", "optionability", "chain_health"],
        "endpoint": "/api/ibkr/scanner_validation",
        "impact": 91,
    },
    {
        "key": "backtests",
        "name": "Backtests / Outcome Research",
        "role": "cached_bars",
        "uses": ["historical_ohlcv", "post_signal_paths", "volatility_regimes"],
        "endpoint": "/api/ibkr/enrichment/{symbol}",
        "impact": 89,
    },
    {
        "key": "execution_gate_witness",
        "name": "Execution Gate Witness",
        "role": "pre_execution_data_witness",
        "uses": ["quote_conflict", "contract_exists", "stale_tick_detection"],
        "endpoint": "/api/ibkr/enrichment/{symbol}",
        "impact": 95,
    },
    {
        "key": "pharma_lottery",
        "name": "Pharma / Lottery Volatility Truth",
        "role": "danger_name_validation",
        "uses": ["spread", "last", "volume", "no_tick", "historical_bars"],
        "endpoint": "/api/ibkr/enrichment/{symbol}",
        "impact": 88,
    },
    {
        "key": "macro_futures_later",
        "name": "Macro / Futures Later",
        "role": "future_expansion",
        "uses": ["futures", "fx", "indexes", "rates_if_subscribed"],
        "endpoint": "/api/ibkr/applications",
        "impact": 78,
        "status": "planned",
    },
]

DEFAULT_CACHE_TTL_SECONDS = 180
SCANNER_VALIDATION_LIMIT = 8


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _age_seconds(value: Any) -> float | None:
    dt = _parse_dt(value)
    if not dt:
        return None
    return max(0.0, (_now() - dt).total_seconds())


def _market_quality(payload: dict[str, Any] | None) -> str:
    if not payload:
        return "missing"
    if payload.get("ok") is False:
        return str(payload.get("data_quality") or "down").lower()
    return str(payload.get("data_quality") or "live").lower()


def _numeric_quote_value(quote: dict[str, Any] | None) -> float | None:
    q = quote or {}
    for key in ("last", "delayed_last", "close", "delayed_close", "bid", "delayed_bid", "ask", "delayed_ask"):
        try:
            value = q.get(key)
            if value is not None:
                return float(value)
        except Exception:
            continue
    return None


async def applications() -> dict[str, Any]:
    from . import ibkr_research

    status = await asyncio.to_thread(ibkr_research.status)
    cfg = status.get("config") or ibkr_research.safety_state()
    connected = bool(status.get("ok") and status.get("connected"))
    enabled = bool(cfg.get("enabled"))
    apps = []
    for app in APPLICATIONS:
        state = app.get("status") or ("live" if connected else "configured" if enabled else "disabled")
        apps.append({**app, "status": state})
    return {
        "ok": connected,
        "generated_at": _now_iso(),
        "gateway": status,
        "policy": {
            "data_only": cfg.get("data_only"),
            "allow_trading": cfg.get("allow_trading"),
            "account_source": "alpaca_only",
            "execution_source": "alpaca_only",
            "ibkr_order_policy": cfg.get("order_mutation_policy"),
        },
        "applications": apps,
        "summary": {
            "enabled": enabled,
            "connected": connected,
            "live_apps": sum(1 for app in apps if app.get("status") == "live"),
            "configured_apps": sum(1 for app in apps if app.get("status") == "configured"),
            "planned_apps": sum(1 for app in apps if app.get("status") == "planned"),
            "avg_impact": round(sum(float(app.get("impact") or 0) for app in apps) / max(1, len(apps)), 1),
        },
    }


async def _cached_doc(cache_key: str, ttl_seconds: int) -> dict[str, Any] | None:
    db = get_db()
    row = await db.bot_state.find_one({"_id": cache_key}, {"_id": 0})
    if not row:
        return None
    age = _age_seconds(row.get("generated_at"))
    if age is not None and age <= ttl_seconds:
        return row.get("payload")
    return None


async def _store_cache(cache_key: str, payload: dict[str, Any]) -> None:
    db = get_db()
    await db.bot_state.update_one(
        {"_id": cache_key},
        {"$set": stamped({"generated_at": _now_iso(), "payload": payload})},
        upsert=True,
    )


async def ticker_enrichment(symbol: str, *, force: bool = False, ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS) -> dict[str, Any]:
    """Pull read-only IBKR market data for one symbol with short cache."""
    clean = str(symbol or "").upper().strip().lstrip("$")
    if not clean:
        return {"ok": False, "reason": "missing_symbol", "generated_at": _now_iso()}
    cache_key = f"ibkr_terminal_enrichment:{clean}"
    if not force:
        cached = await _cached_doc(cache_key, ttl_seconds)
        if cached:
            return {**cached, "cache": "hit"}

    from . import ibkr_research

    status = await asyncio.to_thread(ibkr_research.status)
    if not status.get("ok"):
        payload = {
            "ok": False,
            "symbol": clean,
            "generated_at": _now_iso(),
            "cache": "miss",
            "gateway": status,
            "reason": status.get("reason") or "ibkr_gateway_not_ready",
        }
        await _store_cache(cache_key, payload)
        return payload

    quote, history, contract, chain = await asyncio.gather(
        asyncio.to_thread(ibkr_research.quote, clean, delayed_allowed=True),
        asyncio.to_thread(ibkr_research.historical_data, clean, duration="1 D", bar_size="5 mins", use_rth=True),
        asyncio.to_thread(ibkr_research.contract_info, clean),
        asyncio.to_thread(ibkr_research.option_chain, clean, max_expirations=4, max_strikes=40),
        return_exceptions=True,
    )

    def _safe(value: Any) -> dict[str, Any]:
        if isinstance(value, Exception):
            return {"ok": False, "reason": str(value)[:240]}
        return value if isinstance(value, dict) else {"ok": False, "reason": "unexpected_payload"}

    quote = _safe(quote)
    history = _safe(history)
    contract = _safe(contract)
    chain = _safe(chain)
    bars = history.get("bars") or []
    contracts = contract.get("contracts") or []
    option_chains = chain.get("chains") or []
    quote_price = _numeric_quote_value(quote.get("quote"))
    payload = {
        "ok": bool(quote.get("ok") or bars or contracts or option_chains),
        "symbol": clean,
        "generated_at": _now_iso(),
        "cache": "miss",
        "policy": {
            "data_only": True,
            "execution_source": "alpaca_only",
            "account_source": "alpaca_only",
        },
        "quote": {
            "ok": quote.get("ok"),
            "data_quality": quote.get("data_quality"),
            "price": quote_price,
            "reason": quote.get("reason"),
            "permission_errors": quote.get("permission_errors") or quote.get("errors"),
        },
        "history": {
            "ok": bool(bars),
            "bar_count": len(bars),
            "first_bar": bars[0] if bars else None,
            "last_bar": bars[-1] if bars else None,
            "data_quality": history.get("data_quality") or ("HISTORICAL_BARS" if bars else "NO_HISTORICAL_BARS"),
        },
        "contract": {
            "ok": bool(contracts),
            "contract_count": len(contracts),
            "primary": ((contracts[0] or {}).get("contract") if contracts else None),
            "data_quality": contract.get("data_quality") or ("CONTRACT_DETAILS" if contracts else "NO_CONTRACT_DETAILS"),
        },
        "options": {
            "ok": bool(option_chains),
            "optionable": bool(option_chains),
            "data_quality": chain.get("data_quality"),
            "summary": chain.get("summary"),
            "sample_chain": option_chains[0] if option_chains else None,
            "reason": chain.get("reason"),
        },
        "terminal_applications": [
            "ticker_profiles",
            "scanner_validation",
            "execution_gate_witness",
            "kronos_candles",
            "backtests",
            "pharma_lottery",
        ],
        "quality": {
            "quote": _market_quality(quote),
            "history": "live" if bars else "missing",
            "contract": "live" if contracts else "missing",
            "options": "live" if option_chains else "missing",
        },
    }
    await _store_cache(cache_key, payload)
    return payload


async def scanner_validation(*, limit: int = SCANNER_VALIDATION_LIMIT, force: bool = False) -> dict[str, Any]:
    db = get_db()
    latest = await db.scan_results.find_one({}, {"_id": 0}, sort=[("finished_at", -1)])
    rows = (latest or {}).get("results") or []
    ranked = sorted(
        rows,
        key=lambda r: float(r.get("trade_score") or r.get("score") or r.get("case_score") or 0),
        reverse=True,
    )
    symbols = []
    for row in ranked:
        ticker = str(row.get("ticker") or row.get("symbol") or "").upper().strip().lstrip("$")
        if ticker and ticker not in symbols:
            symbols.append(ticker)
        if len(symbols) >= max(1, min(limit, 25)):
            break
    enriched = []
    for ticker in symbols:
        enriched.append(await ticker_enrichment(ticker, force=force, ttl_seconds=DEFAULT_CACHE_TTL_SECONDS))
    return {
        "ok": True,
        "generated_at": _now_iso(),
        "latest_scan_at": (latest or {}).get("finished_at"),
        "symbols": symbols,
        "validated": enriched,
        "summary": {
            "requested": len(symbols),
            "ibkr_ok": sum(1 for row in enriched if row.get("ok")),
            "quotes_ok": sum(1 for row in enriched if (row.get("quote") or {}).get("ok")),
            "histories_ok": sum(1 for row in enriched if (row.get("history") or {}).get("ok")),
            "optionable": sum(1 for row in enriched if (row.get("options") or {}).get("optionable")),
        },
    }
