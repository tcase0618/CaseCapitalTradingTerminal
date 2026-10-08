"""Observed-only, per-symbol ATM call IV history through the Postgres abstraction.

The series uses calls nearest 30 DTE within 21-45 DTE, not HV or an absolute
volatility bucket. Daily samples are first-observed UTC weekday snapshots,
not closing marks. Provider/feed series are never mixed or backfilled.
"""
from __future__ import annotations

import logging
import math
import statistics
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import get_db

logger = logging.getLogger(__name__)
METHOD = "observed_atm_call_30d_v1"
LOOKBACK_DAYS = 365
MIN_OBSERVATIONS = 200
MIN_SPAN_DAYS = 330
MAX_GAP_DAYS = 7


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: Any) -> datetime | None:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, TypeError):
        return None


def _positive(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def absolute_iv_label(value: Any) -> str:
    iv = _positive(value)
    if iv is None:
        return "UNKNOWN"
    return "LOW" if iv < .3 else "MODERATE" if iv < .6 else "HIGH" if iv < .9 else "VERY_HIGH"


def _result(reason: str, **details: Any) -> dict[str, Any]:
    return {
        "iv_rank": None, "iv_label": "UNKNOWN",
        "rv_20": None, "iv_rv": None, "hv_30": None, "rv_history": None,
        "iv_rank_history": {
            "method": METHOD, "status": reason, "lookback_days": LOOKBACK_DAYS,
            "minimum_observations": MIN_OBSERVATIONS, "minimum_span_days": MIN_SPAN_DAYS,
            "observation_count": 0, **details,
        },
    }


async def realized_iv_metrics(ticker: str, current_iv: Any) -> dict[str, Any]:
    """20 close-to-close log returns, sample stdev annualized by sqrt(252)."""
    result = {"rv_20": None, "iv_rv": None, "hv_30": None, "rv_history": {
        "status": "UNAVAILABLE", "source": "pricer.get_history",
        "return_sessions": 20, "annualization_sessions": 252,
        "return_type": "log_close_to_close", "standard_deviation_ddof": 1,
        "iv_basis": "observed_atm_call_30d_v1", "current_iv": _positive(current_iv),
    }}
    try:
        from . import pricer
        today = _now().date()
        prices = await pricer.get_history(ticker, days=90)
        closes = []
        for key, value in sorted(prices.items()):
            session = datetime.fromisoformat(str(key)).date()
            if session < today and session.weekday() < 5:
                closes.append((session, _positive(value)))
        if len(closes) < 21 or (today - closes[-1][0]).days > 7:
            result["rv_history"]["status"] = "INSUFFICIENT_OR_STALE_PRICES"
            return result
        window = closes[-21:]
        if (window[-1][0] - window[0][0]).days > 45:
            result["rv_history"]["status"] = "INSUFFICIENT_SESSION_DENSITY"
            return result
        if any(value is None for _, value in window):
            result["rv_history"]["status"] = "INVALID_PRICE_WINDOW"
            return result
        returns = [math.log(window[i][1] / window[i - 1][1]) for i in range(1, 21)]
        rv = statistics.stdev(returns) * math.sqrt(252)
        iv = _positive(current_iv)
        result.update(rv_20=round(rv, 6), iv_rv=round(iv - rv, 6) if iv is not None else None)
        result["rv_history"].update(status="AVAILABLE", first_session=window[0][0].isoformat(), last_session=window[-1][0].isoformat())
        if len(closes) >= 31 and all(value is not None for _, value in closes[-31:]):
            window30 = closes[-31:]
            returns30 = [math.log(window30[i][1] / window30[i - 1][1]) for i in range(1, 31)]
            result["hv_30"] = round(statistics.stdev(returns30) * math.sqrt(252), 6)
        return result
    except Exception as exc:
        logger.debug("Realized volatility unavailable for %s: %s", ticker, exc)
        return result


def rank_from_observations(
    current_iv: Any, observations: list[dict], *, ticker: str,
    provider: str, feed: str, as_of: datetime,
) -> dict[str, Any]:
    """Rank against prior daily extrema; exclude today's and future observations."""
    as_of = as_of.astimezone(timezone.utc)
    current = _positive(current_iv)
    if current is None:
        return _result("INVALID_CURRENT_IV")
    by_day: dict[str, tuple[datetime, float]] = {}
    for row in observations:
        if (row.get("ticker"), row.get("provider"), row.get("feed"), row.get("method")) != (
            ticker.upper(), provider, feed, METHOD,
        ):
            continue
        observed = _timestamp(row.get("observed_at"))
        value = _positive(row.get("atm_iv"))
        if observed is None or value is None:
            continue
        if not as_of - timedelta(days=LOOKBACK_DAYS) <= observed <= as_of:
            continue
        if observed.date() >= as_of.date() or observed.weekday() >= 5:
            continue
        day = observed.date().isoformat()
        if row.get("observation_date") != day:
            continue
        if day not in by_day or observed < by_day[day][0]:
            by_day[day] = observed, value
    samples = sorted(by_day.values())
    span = (samples[-1][0].date() - samples[0][0].date()).days if samples else 0
    details = {
        "as_of": as_of.isoformat(), "observation_count": len(samples), "span_days": span,
        "provider": provider, "feed": feed, "ticker": ticker.upper(),
        "first_observed_at": samples[0][0].isoformat() if samples else None,
        "last_observed_at": samples[-1][0].isoformat() if samples else None,
    }
    if len(samples) < MIN_OBSERVATIONS or span < MIN_SPAN_DAYS:
        return _result("INSUFFICIENT_HISTORY", **details)
    if (as_of - samples[-1][0]).total_seconds() > MAX_GAP_DAYS * 86400:
        return _result("STALE_HISTORY", **details)
    low, high = min(v for _, v in samples), max(v for _, v in samples)
    details.update(low_iv=low, high_iv=high)
    if high <= low:
        return _result("ZERO_HISTORY_RANGE", **details)
    rank = round(max(0.0, min(100.0, 100 * (current - low) / (high - low))), 2)
    result = _result("AVAILABLE", **details)
    result.update(iv_rank=rank, iv_label="CHEAP" if rank < 30 else "FAIR" if rank < 60 else "ELEVATED" if rank < 80 else "EXPENSIVE")
    return result


async def observe_atm_iv(
    ticker: str, atm_iv: Any, *, provider: str, feed: str,
    expiration: str, strike: Any, spot: Any, contract_symbol: str,
    quote_time: Any = None,
) -> dict[str, Any]:
    """Persist a compact real observation; storage failures leave the chain usable."""
    now = _now()
    ticker = ticker.strip().upper()
    iv, strike_value, spot_value = _positive(atm_iv), _positive(strike), _positive(spot)
    try:
        dte = (datetime.fromisoformat(expiration).date() - now.date()).days
    except (TypeError, ValueError):
        dte = -1
    if iv is None or not strike_value or not spot_value or not ticker or not provider or not feed or not contract_symbol:
        return _result("INVALID_CURRENT_IV")
    if not 21 <= dte <= 45 or abs(strike_value / spot_value - 1) > .05:
        return _result("NO_COMPARABLE_ATM_CONTRACT")
    # A quote timestamp is provenance, not a fabricated IV timestamp. Where
    # supplied, reject stale/future/invalid marks rather than accumulate them.
    quote_at = _timestamp(quote_time) if quote_time else None
    if quote_time and (quote_at is None or not 0 <= (now - quote_at).total_seconds() <= 86400):
        return _result("STALE_OR_INVALID_QUOTE")
    realized = await realized_iv_metrics(ticker, iv)
    try:
        collection = get_db().iv_history
        if now.weekday() < 5:
            day = now.date().isoformat()
            await collection.insert_one({
                "_id": f"{METHOD}:{ticker}:{provider}:{feed}:{day}",
                "ticker": ticker, "provider": provider, "feed": feed, "method": METHOD,
                "observation_date": day, "observed_at": now.isoformat(),
                "atm_iv": iv, "expiration": expiration, "dte": dte,
                "strike": strike_value, "spot": spot_value, "contract_symbol": contract_symbol,
                "quote_time": quote_at.isoformat() if quote_at else None,
                "timestamp_basis": "retrieved_at", "sampling": "first_observed_utc_weekday",
                **realized,
            })
        rows = await collection.find({
            "ticker": ticker, "provider": provider, "feed": feed, "method": METHOD,
            "observed_at": {"$gte": (now - timedelta(days=LOOKBACK_DAYS)).isoformat(), "$lte": now.isoformat()},
        }).sort("observed_at", -1).to_list(LOOKBACK_DAYS + 1)
        result = rank_from_observations(iv, rows, ticker=ticker, provider=provider, feed=feed, as_of=now)
        result["iv_rank_history"].update(current_atm_iv=iv, expiration=expiration, strike=strike_value)
        return {**result, **realized}
    except Exception as exc:
        logger.debug("IV history unavailable for %s: %s", ticker, exc)
        return {**_result("HISTORY_UNAVAILABLE"), **realized}
