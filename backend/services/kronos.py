"""Kronos advisory forecast layer.

Kronos is intentionally read-only. It builds forecast intelligence from
existing scanner, PM, trade-floor, options-desk, and market-data state, then
stores snapshots so disagreement performance can be audited later.
"""
from __future__ import annotations

import asyncio
import calendar
import hashlib
import json
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any

from .db import get_db, stamped
from . import kronos_contract

logger = logging.getLogger(__name__)
_refresh_lock = asyncio.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ticker(v: Any) -> str:
    return str(v or "").replace("$", "").strip().upper()


def _num(v: Any, default: float | None = None) -> float | None:
    try:
        if v is None or v == "":
            return default
        n = float(v)
        if math.isfinite(n):
            return n
    except Exception:
        pass
    return default




def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("rows", "results", "data", "events", "positions", "trades"):
        val = payload.get(key)
        if isinstance(val, list):
            return [r for r in val if isinstance(r, dict)]
    return []


async def _latest_context() -> dict[str, Any]:
    from . import options_desk, portfolio_manager, scanner

    scan_task = _bounded("scan", scanner.latest_scan(), timeout=4.0)
    from . import public_execution
    pm_task = _bounded("pm", portfolio_manager.latest_persisted_portfolio_plan(), timeout=5.0)
    eq_task = _bounded("equity_db", _public_ledger(), timeout=3.0)
    live_eq_task = _bounded("equity_live", public_execution.portfolio_state(), timeout=10.0)
    opt_task = _bounded("options", options_desk.positions(), timeout=5.0)
    risk_task = _bounded("option_risk", options_desk.latest_risk_check(), timeout=3.0)
    trades_task = _bounded("option_trades", options_desk.trades(limit=100, sync_live=False), timeout=3.0)
    results = await asyncio.gather(
        scan_task, pm_task, eq_task, live_eq_task, opt_task, risk_task, trades_task,
        return_exceptions=True,
    )
    keys = ("scan", "pm", "equity_db", "equity_live", "options", "option_risk", "option_trades")
    ctx = {}
    for key, value in zip(keys, results):
        ctx[key] = {"error": str(value)} if isinstance(value, Exception) else value
    live = ctx.get("equity_live") or {}
    ctx["equity_live"] = live.get("positions", []) if isinstance(live, dict) and live.get("ok") else []
    ctx["public_portfolio_health"] = {"ok": bool(live.get("ok")), "reason": live.get("reason") or live.get("error")} if isinstance(live, dict) else {"ok": False}
    return ctx


async def _public_ledger():
    return await get_db().tf_trades.find({"broker_base": "public", "qty_remaining": {"$gt": 0}}, {"_id": 0}).to_list(1000)


async def _bounded(label: str, awaitable, timeout: float = 4.0) -> Any:
    try:
        return await asyncio.wait_for(awaitable, timeout=timeout)
    except asyncio.TimeoutError:
        return {"error": f"{label}_timeout"}
    except Exception as exc:
        return {"error": str(exc)}


async def _scan_pm_context() -> dict[str, Any]:
    from . import portfolio_manager, scanner

    scan = await _bounded("scan", scanner.latest_scan(), timeout=3.0)
    pm = await _bounded("pm", portfolio_manager.latest_persisted_portfolio_plan(), timeout=3.0)
    return {"scan": scan, "pm": pm}




def _norm_candle(row: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    close = _num(row.get("close") or row.get("c") or row.get("last") or row.get("price"))
    open_ = _num(row.get("open", row.get("o")))
    high = _num(row.get("high", row.get("h")))
    low = _num(row.get("low", row.get("l")))
    if close is None or open_ is None or high is None or low is None:
        return None
    if min(close, open_, high, low) <= 0 or high < max(open_, close) or low > min(open_, close) or low > high:
        return None
    ts = row.get("timestamp") or row.get("datetime") or row.get("date") or row.get("time") or row.get("t")
    return {
        "timestamp": str(ts or ""),
        "open": float(open_),
        "high": float(high),
        "low": float(low),
        "close": float(close),
        "volume": _num(row.get("volume") or row.get("v"), 0.0) or 0.0,
    }


def _ema(vals: list[float], period: int) -> float | None:
    if not vals:
        return None
    k = 2 / (period + 1)
    ema = vals[0]
    for v in vals[1:]:
        ema = v * k + ema * (1 - k)
    return ema


def _rsi(vals: list[float], period: int = 14) -> float | None:
    if len(vals) < period + 1:
        return None
    gains, losses = [], []
    for i in range(-period, 0):
        delta = vals[i] - vals[i - 1]
        gains.append(max(delta, 0))
        losses.append(abs(min(delta, 0)))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _atr_pct(candles: list[dict[str, Any]], period: int = 14) -> float:
    if len(candles) < 2:
        return 0.5
    trs = []
    for i in range(max(1, len(candles) - period), len(candles)):
        cur, prev = candles[i], candles[i - 1]
        tr = max(
            cur["high"] - cur["low"],
            abs(cur["high"] - prev["close"]),
            abs(cur["low"] - prev["close"]),
        )
        if cur["close"]:
            trs.append(tr / cur["close"] * 100)
    return max(0.03, sum(trs) / len(trs)) if trs else 0.5


def _volume_z(candles: list[dict[str, Any]], lookback: int = 30) -> float | None:
    vols = [float(c.get("volume") or 0) for c in candles[-lookback:] if _num(c.get("volume")) is not None]
    if len(vols) < 6:
        return None
    last = vols[-1]
    base = vols[:-1]
    mean = sum(base) / len(base)
    var = sum((x - mean) ** 2 for x in base) / max(1, len(base) - 1)
    sd = math.sqrt(var)
    return round((last - mean) / sd, 2) if sd else 0.0


def _vwap_distance(candles: list[dict[str, Any]], lookback: int = 30) -> float | None:
    rows = candles[-lookback:]
    total_vol = sum(max(0.0, float(c.get("volume") or 0)) for c in rows)
    if total_vol <= 0:
        return None
    vwap = sum(((c["high"] + c["low"] + c["close"]) / 3) * max(0.0, float(c.get("volume") or 0)) for c in rows) / total_vol
    last = rows[-1]["close"]
    return ((last - vwap) / vwap) * 100 if vwap else None


def _candle_features(candles: list[dict[str, Any]]) -> dict[str, Any]:
    last = candles[-1]
    prev = candles[-2] if len(candles) > 1 else last
    closes = [c["close"] for c in candles]
    rng = max(0.000001, last["high"] - last["low"])
    body = last["close"] - last["open"]
    body_pct = (body / last["open"]) * 100 if last["open"] else 0.0
    range_pct = (rng / last["open"]) * 100 if last["open"] else 0.0
    upper_wick_pct = ((last["high"] - max(last["open"], last["close"])) / rng) * 100
    lower_wick_pct = ((min(last["open"], last["close"]) - last["low"]) / rng) * 100
    gap_pct = ((last["open"] - prev["close"]) / prev["close"]) * 100 if prev["close"] else 0.0
    atr = _atr_pct(candles)
    ema9 = _ema(closes[-60:], 9)
    ema21 = _ema(closes[-80:], 21)
    ema50 = _ema(closes[-120:], 50)
    rsi = _rsi(closes)
    vol_z = _volume_z(candles)
    vwap_dist = _vwap_distance(candles)
    returns = [
        ((closes[i] - closes[i - 1]) / closes[i - 1]) * 100
        for i in range(max(1, len(closes) - 10), len(closes))
        if closes[i - 1]
    ]
    momentum_3 = sum(returns[-3:]) if len(returns) >= 3 else sum(returns)
    momentum_5 = sum(returns[-5:]) if len(returns) >= 5 else sum(returns)
    higher_highs = sum(1 for i in range(max(1, len(candles) - 5), len(candles)) if candles[i]["high"] > candles[i - 1]["high"])
    lower_lows = sum(1 for i in range(max(1, len(candles) - 5), len(candles)) if candles[i]["low"] < candles[i - 1]["low"])
    structure = "HIGHER_HIGH" if higher_highs > lower_lows else "LOWER_LOW" if lower_lows > higher_highs else "MIXED"
    pattern = "BULLISH_BODY" if body_pct > atr * 0.25 else "BEARISH_BODY" if body_pct < -atr * 0.25 else "DOJI"
    if lower_wick_pct > 55 and body_pct >= 0:
        pattern = "LOWER_WICK_REJECTION"
    elif upper_wick_pct > 55 and body_pct <= 0:
        pattern = "UPPER_WICK_REJECTION"
    return {
        "last_close": round(last["close"], 4),
        "last_open": round(last["open"], 4),
        "last_high": round(last["high"], 4),
        "last_low": round(last["low"], 4),
        "body_pct": round(body_pct, 3),
        "range_pct": round(range_pct, 3),
        "upper_wick_pct": round(upper_wick_pct, 1),
        "lower_wick_pct": round(lower_wick_pct, 1),
        "gap_pct": round(gap_pct, 3),
        "atr_pct": round(atr, 3),
        "ema9": round(ema9, 4) if ema9 else None,
        "ema21": round(ema21, 4) if ema21 else None,
        "ema50": round(ema50, 4) if ema50 else None,
        "rsi14": round(rsi, 1) if rsi is not None else None,
        "vwap_distance_pct": round(vwap_dist, 3) if vwap_dist is not None else None,
        "volume_z": vol_z,
        "momentum_3_pct": round(momentum_3, 3),
        "momentum_5_pct": round(momentum_5, 3),
        "structure": structure,
        "last_candle_pattern": pattern,
        "latest_timestamp": last.get("timestamp"),
    }


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-max(-10, min(10, x))))


def _score_candle_features(features: dict[str, Any]) -> dict[str, Any]:
    score = 0.0
    reasons: list[str] = []
    ema9, ema21, ema50 = features.get("ema9"), features.get("ema21"), features.get("ema50")
    close = features.get("last_close") or 0
    atr = max(0.03, float(features.get("atr_pct") or 0.5))
    if ema9 and ema21:
        if ema9 > ema21:
            score += 13
            reasons.append("EMA9 above EMA21")
        else:
            score -= 13
            reasons.append("EMA9 below EMA21")
    if ema21 and ema50:
        if ema21 > ema50:
            score += 7
            reasons.append("EMA21 above EMA50")
        else:
            score -= 7
            reasons.append("EMA21 below EMA50")
    if ema21 and close:
        score += max(-10, min(10, ((close - ema21) / ema21) * 100 / max(atr, 0.1) * 4))
    mom3 = float(features.get("momentum_3_pct") or 0)
    mom5 = float(features.get("momentum_5_pct") or 0)
    score += max(-16, min(16, (mom3 + mom5 * 0.55) / max(atr, 0.1) * 5))
    body = float(features.get("body_pct") or 0)
    score += max(-9, min(9, body / max(atr, 0.1) * 4))
    if features.get("last_candle_pattern") == "LOWER_WICK_REJECTION":
        score += 7
        reasons.append("lower wick rejection")
    elif features.get("last_candle_pattern") == "UPPER_WICK_REJECTION":
        score -= 7
        reasons.append("upper wick rejection")
    rsi = features.get("rsi14")
    if rsi is not None:
        if 48 <= rsi <= 68:
            score += 5
        elif rsi > 78:
            score -= 5
        elif rsi < 35:
            score -= 3
    vwap_dist = features.get("vwap_distance_pct")
    if vwap_dist is not None:
        score += max(-7, min(7, float(vwap_dist) / max(atr, 0.1) * 2))
    if features.get("structure") == "HIGHER_HIGH":
        score += 5
    elif features.get("structure") == "LOWER_LOW":
        score -= 5
    vol_z = features.get("volume_z")
    if vol_z is not None and abs(float(vol_z)) >= 1.0:
        score += 3 if score >= 0 else -3
    return {"score": round(score, 2), "reasons": reasons[:5]}


def _regime_from_features(features: dict[str, Any]) -> str:
    structure = str((features or {}).get("structure") or "UNKNOWN").upper()
    if structure == "HIGHER_HIGH":
        return "TREND_UP"
    if structure == "LOWER_LOW":
        return "TREND_DOWN"
    return "CHOP"


def _accuracy_lookup(snapshot: dict[str, Any], symbol: str, timeframe: str, regime: str) -> dict[str, Any]:
    def _match(rows: Any, key: str) -> dict[str, Any] | None:
        for row in rows or []:
            if str(row.get("key") or "").upper() == key.upper():
                return row
        return None

    return {
        "symbol": _match(snapshot.get("by_symbol"), symbol),
        "timeframe": _match(snapshot.get("by_timeframe"), timeframe),
        "regime": _match(snapshot.get("by_regime"), regime),
        "overall": snapshot.get("overall") or {},
    }


async def _candle_learning_adjustment(symbol: str, timeframe: str, regime: str) -> dict[str, Any]:
    """Return conservative calibration from the latest persisted accuracy proof."""
    try:
        db = get_db()
        snap = await db.kronos_accuracy_snapshots.find_one({"model_version": kronos_contract.MODEL_VERSION, "promotion_status": "VALIDATED_ADVISORY"}, {"_id": 0}, sort=[("generated_at", -1)])
    except Exception as exc:
        logger.debug("kronos learning snapshot unavailable: %s", exc)
        snap = None
    if not snap:
        return {
            "active": False,
            "status": "NO_PROOF",
            "forecast_multiplier": 1.0,
            "confidence_multiplier": 1.0,
            "cone_multiplier": 1.0,
            "sample": 0,
            "reason": "No mature Kronos accuracy snapshot exists yet.",
        }
    buckets = _accuracy_lookup(snap, symbol, timeframe, regime)
    ordered = [buckets.get("symbol"), buckets.get("timeframe"), buckets.get("regime"), buckets.get("overall")]
    bucket = next((b for b in ordered if b and int(b.get("sample") or 0) >= 12), None)
    if not bucket:
        return {
            "active": False,
            "status": "WARMING_UP",
            "forecast_multiplier": 1.0,
            "confidence_multiplier": 0.92,
            "cone_multiplier": 1.15,
            "sample": int((buckets.get("overall") or {}).get("sample") or 0),
            "reason": "Not enough mature samples for dependable learning calibration.",
        }

    sample = int(bucket.get("sample") or 0)
    win_rate = _num(bucket.get("direction_win_rate_pct"))
    cone_rate = _num(bucket.get("cone_coverage_pct"))
    mae = _num(bucket.get("mae_pct"))
    forecast_mult = 1.0
    confidence_mult = 1.0
    cone_mult = 1.0
    notes = []

    if win_rate is not None:
        if sample >= 20 and win_rate < 45:
            forecast_mult *= 0.55
            confidence_mult *= 0.68
            notes.append("direction history poor; dampened forecast")
        elif win_rate < 52:
            forecast_mult *= 0.74
            confidence_mult *= 0.82
            notes.append("direction history below edge; reduced conviction")
        elif win_rate >= 58:
            forecast_mult *= 1.06
            confidence_mult *= 1.05
            notes.append("direction history positive; modestly reinforced")

    if cone_rate is not None:
        if cone_rate < 58:
            cone_mult *= 1.65
            notes.append("cone coverage weak; widened range")
        elif cone_rate < 68:
            cone_mult *= 1.28
            notes.append("cone coverage below target; widened range")
        elif cone_rate > 86:
            cone_mult *= 0.92
            notes.append("cone coverage loose; tightened range")

    if mae is not None and mae > 0.45:
        forecast_mult *= 0.88
        cone_mult *= 1.18
        notes.append("forecast error elevated; widened protection")

    return {
        "active": True,
        "status": "CALIBRATED",
        "bucket": bucket.get("key"),
        "sample": sample,
        "direction_win_rate_pct": win_rate,
        "cone_coverage_pct": cone_rate,
        "mae_pct": mae,
        "forecast_multiplier": round(forecast_mult, 3),
        "confidence_multiplier": round(confidence_mult, 3),
        "cone_multiplier": round(cone_mult, 3),
        "snapshot_at": snap.get("generated_at"),
        "reason": "; ".join(notes) if notes else "Accuracy proof is acceptable; no major dampening applied.",
    }


def _apply_candle_learning(result: dict[str, Any], adjustment: dict[str, Any]) -> dict[str, Any]:
    if not adjustment:
        return result
    result["learning_adjustment"] = adjustment
    if not adjustment.get("active") and adjustment.get("status") != "WARMING_UP":
        return result
    forecast_mult = _num(adjustment.get("forecast_multiplier"), 1.0) or 1.0
    confidence_mult = _num(adjustment.get("confidence_multiplier"), 1.0) or 1.0
    cone_mult = _num(adjustment.get("cone_multiplier"), 1.0) or 1.0
    old_forecast = _num(result.get("forecast_pct"), 0.0) or 0.0
    old_low = _num(result.get("cone_low_pct"), old_forecast - 0.1) or old_forecast - 0.1
    old_high = _num(result.get("cone_high_pct"), old_forecast + 0.1) or old_forecast + 0.1
    width = max(abs(old_forecast - old_low), abs(old_high - old_forecast), 0.03) * cone_mult
    new_forecast = old_forecast * forecast_mult
    result["forecast_pct_raw"] = round(old_forecast, 3)
    result["forecast_pct"] = round(new_forecast, 3)
    result["cone_low_pct"] = round(new_forecast - width, 3)
    result["cone_high_pct"] = round(new_forecast + width, 3)
    result["confidence_raw"] = result.get("confidence")
    result["confidence"] = int(max(18, min(92, (_num(result.get("confidence"), 50) or 50) * confidence_mult)))
    noise = max(0.03, _num(result.get("noise_band_pct"), 0.06) or 0.06)
    if abs(new_forecast) <= noise:
        result["direction"] = "FLAT"
    elif new_forecast > 0:
        result["direction"] = "UP"
    else:
        result["direction"] = "DOWN"

    close = _num((result.get("features") or {}).get("last_close") or (result.get("predicted_next_candle") or {}).get("open"))
    if close and close > 0:
        result["predicted_next_candle"] = {
            "open": round(close, 4),
            "high": round(close * (1 + max(new_forecast, 0) / 100 + width / 100), 4),
            "low": round(close * (1 + min(new_forecast, 0) / 100 - width / 100), 4),
            "close": round(close * (1 + new_forecast / 100), 4),
        }

    for h in result.get("horizons") or []:
        center = _num(h.get("forecast_pct"), 0.0) or 0.0
        low = _num(h.get("cone_low_pct"), center - width) or center - width
        high = _num(h.get("cone_high_pct"), center + width) or center + width
        h_width = max(abs(center - low), abs(high - center), 0.03) * cone_mult
        h_center = center * forecast_mult
        h["forecast_pct_raw"] = round(center, 3)
        h["forecast_pct"] = round(h_center, 3)
        h["cone_low_pct"] = round(h_center - h_width, 3)
        h["cone_high_pct"] = round(h_center + h_width, 3)
    return result


async def candle_forecast(symbol: str = "SPY", timeframe: str = "5m", limit: int = 220, persist: bool = False) -> dict[str, Any]:
    """Candle-aware read-only forecast from raw OHLCV.

    This is intentionally separate from the portfolio forecast engine. It uses
    numerical OHLCV, not TradingView pixels.
    """
    ticker = _ticker(symbol) or "SPY"
    tf = str(timeframe or "5m").lower()
    try:
        from . import london_strategic_edge as lse
        payload = await asyncio.wait_for(lse.candles(ticker, timeframe=tf, limit=max(60, min(int(limit or 220), 500)), order="desc", request_timeout=8), timeout=15)
        candles = [_norm_candle(r) for r in _rows(payload)]
        candles = [c for c in candles if c]
        provider = payload.get("provider") or "london_strategic_edge"
        degraded = not bool(payload.get("ok"))
    except Exception as exc:
        logger.debug("kronos candle forecast LSE failed %s %s: %s", ticker, tf, exc)
        candles = []
        provider = "london_strategic_edge"
        degraded = True
    candles, contract = kronos_contract.input_contract(candles, tf, _now())
    if len(candles) < 20 or not contract.get("ok") or degraded:
        return {
            "ok": False,
            "symbol": ticker,
            "timeframe": tf,
            "direction": "UNKNOWN",
            "reason": contract.get("reason") or "insufficient_ohlcv",
            "provider": provider,
            "degraded": True,
            "candles": len(candles),
            "input_contract": contract,
        }
    features = _candle_features(candles)
    scored = _score_candle_features(features)
    atr = max(0.03, float(features.get("atr_pct") or 0.5))
    expected_pct = max(-3.5, min(3.5, scored["score"] / 55 * atr))
    noise_band = max(0.06, min(0.32, atr * 0.22))
    up_prob = _sigmoid(scored["score"] / 18)
    down_prob = 1 - up_prob
    flat_prob = max(0.06, min(0.38, 0.34 - min(0.28, abs(expected_pct) / max(noise_band, 0.01) * 0.11)))
    directional_mass = 1 - flat_prob
    up = round(up_prob * directional_mass * 100, 1)
    down = round(down_prob * directional_mass * 100, 1)
    flat = round(flat_prob * 100, 1)
    direction = "FLAT"
    if expected_pct > noise_band and up >= 52:
        direction = "UP"
    elif expected_pct < -noise_band and down >= 52:
        direction = "DOWN"
    close = features["last_close"]
    predicted_close = close * (1 + expected_pct / 100)
    half_range = max(atr * 0.55, noise_band)
    pred = {
        "open": round(close, 4),
        "high": round(close * (1 + max(expected_pct, 0) / 100 + half_range / 100), 4),
        "low": round(close * (1 + min(expected_pct, 0) / 100 - half_range / 100), 4),
        "close": round(predicted_close, 4),
    }
    horizons = []
    for bars, mult in ((1, 1.0), (3, 1.65), (5, 2.15), ("EOD", 3.2)):
        horizon_pct = expected_pct * (mult if isinstance(bars, str) else math.sqrt(mult))
        cone = half_range * (mult if isinstance(bars, str) else math.sqrt(mult))
        horizons.append({
            "horizon": f"{bars} BAR" if isinstance(bars, int) else str(bars),
            "forecast_pct": round(horizon_pct, 3),
            "cone_low_pct": round(horizon_pct - cone, 3),
            "cone_high_pct": round(horizon_pct + cone, 3),
            "up_probability": up,
            "down_probability": down,
            "flat_probability": flat,
        })
    result = {
        "ok": True,
        "symbol": ticker,
        "timeframe": tf,
        "direction": direction,
        "forecast_pct": round(expected_pct, 3),
        "confidence": int(max(25, min(88, max(up, down) + abs(scored["score"]) * 0.25))),
        "probabilities": {"up": up, "down": down, "flat": flat},
        "noise_band_pct": round(noise_band, 3),
        "cone_low_pct": round(expected_pct - half_range, 3),
        "cone_high_pct": round(expected_pct + half_range, 3),
        "predicted_next_candle": pred,
        "horizons": horizons,
        "features": features,
        "score": scored["score"],
        "drivers": scored["reasons"],
        "provider": provider,
        "degraded": degraded,
        "source": "raw_ohlcv_candle_engine",
        "generated_at": _now().isoformat(),
        "input_contract": contract,
        "input_asof": contract["input_asof"],
        "target_at": contract["target_at"],
        "model_version": kronos_contract.MODEL_VERSION,
        "research_only": True,
        "calibrated": False,
        "confidence_kind": "heuristic_score_not_probability",
    }
    adjustment = await _candle_learning_adjustment(ticker, tf, _regime_from_features(features))
    result = _apply_candle_learning(result, adjustment)
    result["horizons"] = [{"horizon": "NEXT FULL RTH BAR", "target_at": contract["target_at"], "forecast_pct": result["forecast_pct"], "cone_low_pct": result["cone_low_pct"], "cone_high_pct": result["cone_high_pct"]}]
    identity = json.dumps({"symbol": ticker, "timeframe": tf, "model": result["model_version"], "input": candles, "target": result["target_at"]}, sort_keys=True)
    result["input_hash"] = hashlib.sha256(identity.encode()).hexdigest()
    # Provider revisions must not turn one issued bar horizon into extra samples.
    issue_key = "|".join([ticker, tf, result["model_version"], result["input_asof"], result["target_at"]])
    result["prediction_id"] = hashlib.sha256(issue_key.encode()).hexdigest()
    if persist:
        try:
            db = get_db()
            await db.kronos_candle_predictions.update_one({"prediction_id": result["prediction_id"]}, {"$setOnInsert": stamped(result)}, upsert=True)
            saved = await db.kronos_candle_predictions.find_one({"prediction_id": result["prediction_id"]}, {"_id": 0})
            if saved:
                result = saved
        except Exception as exc:
            logger.exception("kronos candle prediction persistence failed")
            result["persistence_error"] = str(exc)
    return result


async def candle_forecast_suite(symbol: str = "SPY", persist: bool = False) -> dict[str, Any]:
    timeframes = ["5m", "15m", "1h", "1d"]
    results = await asyncio.gather(
        *(candle_forecast(symbol=symbol, timeframe=tf, persist=persist) for tf in timeframes),
        return_exceptions=True,
    )
    rows = []
    for tf, row in zip(timeframes, results):
        if isinstance(row, Exception):
            rows.append({"ok": False, "symbol": _ticker(symbol), "timeframe": tf, "error": str(row), "direction": "UNKNOWN"})
        else:
            rows.append(row)
    primary = next((r for r in rows if r.get("ok") and r.get("timeframe") == "5m"), None) or next((r for r in rows if r.get("ok")), None)
    return {
        "ok": bool(primary),
        "symbol": _ticker(symbol) or "SPY",
        "primary": primary,
        "timeframes": rows,
        "generated_at": _now().isoformat(),
    }






async def market_forecast(persist: bool = False) -> dict[str, Any]:
    suite = await candle_forecast_suite("SPY", persist=persist)
    primary = suite.get("primary") or {}
    if not primary.get("ok"):
        return {"ok": False, "symbol": "SPY", "direction": "UNKNOWN", "forecast_pct": None,
                "confidence": None, "source": "unavailable", "reason": "no_fresh_completed_ohlcv", "candle_engine": suite}
    return {**primary, "last_price": (primary.get("features") or {}).get("last_close"),
            "source": "ohlcv_baseline_v2", "reason": "Uncalibrated research baseline, not the Kronos foundation model",
            "candle_engine": suite}


def _score(row: dict[str, Any], pm_row: dict[str, Any]) -> float | None:
    score = next((_num(source.get(key)) for source, key in ((pm_row, "pm_score"), (pm_row, "score"), (row, "trade_score"), (row, "signal_score"), (row, "case_score"), (row, "learning_score")) if _num(source.get(key)) is not None), None)
    if score is not None and score > 10:
        return round(score / 10.0, 2)
    return score


def _pm_rows(pm: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("recommendations", "decisions", "plan", "rows", "candidates"):
        if isinstance(pm.get(key), list):
            return pm[key]
    summary = pm.get("summary") or {}
    return summary.get("decisions") or []


def _snapshot_key(ts: str) -> str:
    day = (_parse_dt(ts) or _now()).astimezone(ZoneInfo("America/New_York")).date().isoformat()
    return f"kronos-latest-{day}"


def _age_minutes(ts: Any) -> float | None:
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return max(0.0, (_now() - d).total_seconds() / 60.0)
    except Exception:
        return None


def _freshness_status(age_minutes: float | None) -> str:
    if age_minutes is None:
        return "MISSING"
    if age_minutes <= 20:
        return "LIVE"
    if age_minutes <= 180:
        return "AGING"
    return "STALE"


def _parse_dt(ts: Any) -> datetime | None:
    raw = str(ts or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y %H:%M", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw[:len(fmt)], fmt).replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None






def _aligned(pm_action: str, bias: str) -> bool:
    if pm_action == "UNMAPPED":
        return False
    if pm_action == "PASS":
        return bias in ("BEARISH", "CHOP")
    if pm_action in ("EQUITY", "OPTION", "BOTH"):
        return bias in ("BULLISH", "HEDGE")
    return False


def _attribution(score: float | None, pm_action: str, signal: dict[str, Any], instrument: str, risk: dict[str, Any]) -> list[dict[str, Any]]:
    pieces = [
        {"factor": "PM route", "weight": 28, "state": pm_action},
        {"factor": "Case Score", "weight": 24, "state": "-" if score is None else round(score, 1)},
        {"factor": "Scanner stack", "weight": 18, "state": len(signal.get("signals") or [])},
        {"factor": "Instrument risk", "weight": 12, "state": instrument},
        {"factor": "Options decay", "weight": 10, "state": risk.get("theta_status") or "N/A"},
        {"factor": "Liquidity/flow", "weight": 8, "state": "linked"},
    ]
    return pieces










def _instrument_rows(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    live_eq = ctx.get("equity_live") if isinstance(ctx.get("equity_live"), list) else []
    db_eq = ctx.get("equity_db") if isinstance(ctx.get("equity_db"), list) else []
    eq_source = live_eq or db_eq
    rows = []
    for p in eq_source:
        t = _ticker(p.get("ticker") or p.get("symbol"))
        if not t:
            continue
        rows.append({
            "ticker": t,
            "instrument": "EQUITY",
            "quantity": p.get("qty_remaining", p.get("qty", p.get("quantity", p.get("shares")))),
            "market_value": _num(p.get("market_value") or p.get("marketValue") or p.get("notional"), 0.0),
            "unrealized_pct": _num(p.get("unrealized_pct")) if p.get("unrealized_pct") is not None else (_num(p.get("unrealized_plpc"), 0.0) * 100),
            "broker": "public",
            "position_verified": bool(live_eq),
            "contract": None,
            "risk": {},
        })

    opt_positions = _rows(ctx.get("options"))
    risk_by_symbol = {str(r.get("symbol") or "").upper(): r for r in _rows(ctx.get("option_risk")) or _rows((ctx.get("option_risk") or {}).get("checks"))}
    trade_by_symbol = {str(r.get("symbol") or "").upper(): r for r in _rows(ctx.get("option_trades"))}
    for p in opt_positions:
        sym = str(p.get("symbol") or "").upper()
        risk = risk_by_symbol.get(sym, {})
        trade = trade_by_symbol.get(sym, {})
        root = _ticker(p.get("underlying_symbol") or p.get("underlying") or trade.get("ticker") or _option_root(sym))
        if not root:
            continue
        rows.append({
            "ticker": root,
            "instrument": "OPTION",
            "quantity": p.get("qty") or p.get("quantity"),
            "market_value": _num(p.get("market_value") or p.get("cost_basis"), 0.0),
            "unrealized_pct": _num(risk.get("pnl_pct")) if risk.get("pnl_pct") is not None else (_num(p.get("unrealized_pct")) if p.get("unrealized_pct") is not None else _num(p.get("unrealized_plpc"), 0.0) * 100),
            "contract": sym,
            "risk": risk,
            "trade": trade,
        })
    return rows


def _option_root(symbol: str) -> str:
    import re
    m = re.match(r"^([A-Z]{1,6})\d{6}[CP]\d+", symbol or "")
    return m.group(1) if m else ""


async def forecast(persist: bool = True) -> dict[str, Any]:
    ctx = await _latest_context()
    market = await market_forecast(persist=persist)
    scan_rows = _rows(ctx.get("scan"))
    scan_by_ticker = {_ticker(r.get("ticker") or r.get("symbol")): r for r in scan_rows}
    pm_rows = _pm_rows(ctx.get("pm") if isinstance(ctx.get("pm"), dict) else {})
    pm_by_ticker = {_ticker(r.get("ticker") or r.get("symbol")): r for r in pm_rows}
    forecasts = []
    cumulative_low = 0.0
    cumulative_base = 0.0
    cumulative_high = 0.0
    positions = _instrument_rows(ctx)
    tickers = sorted({p["ticker"] for p in positions})
    predictions = await asyncio.gather(*(candle_forecast(t, "1d", persist=persist) for t in tickers), return_exceptions=True)
    by_ticker = {t: (p if isinstance(p, dict) else {"ok": False, "reason": str(p)}) for t, p in zip(tickers, predictions)}
    for pos in positions:
        signal = scan_by_ticker.get(pos["ticker"], {})
        pm_row = pm_by_ticker.get(pos["ticker"], {})
        pm_action = str(pm_row.get("action") or pm_row.get("route") or pm_row.get("decision") or signal.get("pm_action") or signal.get("pm_route") or "UNMAPPED").upper()
        if not pm_row and not signal and (pos.get("market_value") or pos.get("quantity")):
            pm_action = "HELD_NOT_IN_LATEST_PM"
        score = _score(signal, pm_row)
        prediction = by_ticker[pos["ticker"]]
        valid = bool(prediction.get("ok"))
        bias = {"label": {"UP": "BULLISH", "DOWN": "BEARISH", "FLAT": "CHOP"}.get(prediction.get("direction"), "UNAVAILABLE"),
                "base": prediction.get("forecast_pct") if valid else None,
                "bear": prediction.get("cone_low_pct") if valid else None,
                "bull": prediction.get("cone_high_pct") if valid else None}
        confidence = prediction.get("confidence") if valid else None
        aligned = bool(valid and _aligned(pm_action, bias["label"]))
        probs = prediction.get("probabilities") or {}
        tripwires = _tripwires(pos, score, pm_action)
        kscore = confidence if valid else 0
        mv = pos.get("market_value") or 0.0
        if valid and pos["instrument"] == "EQUITY":
            cumulative_low += mv * (bias["bear"] / 100)
            cumulative_base += mv * (bias["base"] / 100)
            cumulative_high += mv * (bias["bull"] / 100)
        row = {
            **pos,
            "pm_action": pm_action,
            "case_score": score,
            "forecast_bias": bias["label"],
            "forecast_pct": round(bias["base"], 2) if valid else None,
            "bear_pct": round(bias["bear"], 2) if valid else None,
            "bull_pct": round(bias["bull"], 2) if valid else None,
            "input_asof": prediction.get("input_asof"),
            "target_at": prediction.get("target_at"),
            "prediction_id": prediction.get("prediction_id"),
            "forecast_status": "AVAILABLE" if valid else "UNAVAILABLE",
            "reason": prediction.get("reason"),
            "forecast_basis": "UNDERLYING_RETURN" if pos["instrument"] == "OPTION" else "EQUITY_RETURN",
            "anchor_price": (prediction.get("features") or {}).get("last_close"),
            "confidence": confidence,
            "kronos_score": kscore,
            "direction_score": prediction.get("score") if valid else None,
            "aligned_with_pm": aligned,
            "attribution": _attribution(score, pm_action, signal, pos["instrument"], pos.get("risk") or {}),
            "horizons": prediction.get("horizons", []),
            "probabilities": probs,
            "exit_forecast": {"research_only": True, "reason": "Exits remain owned by the portfolio ratchet, not this forecast"},
            "tripwires": tripwires,
            "catalysts": _catalysts(signal),
        }
        forecasts.append(row)

    forecasts.sort(key=lambda r: r.get("kronos_score") or 0, reverse=True)
    disagreements = [
        r for r in forecasts
        if r.get("forecast_status") == "AVAILABLE" and not r.get("aligned_with_pm") and r.get("pm_action") not in {"UNMAPPED", "HELD_NOT_IN_LATEST_PM"}
    ]
    payload = {
        "ok": bool(market.get("ok") and (ctx.get("public_portfolio_health") or {}).get("ok")),
        "generated_at": _now().isoformat(),
        "snapshot_key": None,
        "model_mode": "ohlcv_baseline_v2_research_only",
        "model_version": kronos_contract.MODEL_VERSION,
        "calibrated": False,
        "public_portfolio_health": ctx.get("public_portfolio_health"),
        "read_only": True,
        "market_forecast": market,
        "portfolio_day_cone": {
            "basis": "Equity-only, next full RTH session; excludes option premium returns",
            "covered_positions": sum(r.get("forecast_status") == "AVAILABLE" and r["instrument"] == "EQUITY" for r in forecasts),
            "low_usd": round(cumulative_low, 2),
            "base_usd": round(cumulative_base, 2),
            "high_usd": round(cumulative_high, 2),
        },
        "forecasts": forecasts,
        "disagreements": disagreements,
        "summary": {
            "positions": len(forecasts),
            "underlyings": len({r["ticker"] for r in forecasts}),
            "bullish": sum(1 for r in forecasts if r["forecast_bias"] == "BULLISH"),
            "bearish": sum(1 for r in forecasts if r["forecast_bias"] == "BEARISH"),
            "chop": sum(1 for r in forecasts if r["forecast_bias"] == "CHOP"),
            "pm_disagreements": len(disagreements),
            "avg_kronos_score": round(sum(r["kronos_score"] for r in forecasts) / len(forecasts), 1) if forecasts else 0,
            "mapped_pm": sum(1 for r in forecasts if r.get("pm_action") not in {"UNMAPPED", "HELD_NOT_IN_LATEST_PM"}),
            "unmapped_pm": sum(1 for r in forecasts if r.get("pm_action") in {"UNMAPPED", "HELD_NOT_IN_LATEST_PM"}),
            "stale_position_context": sum(1 for r in forecasts if r.get("instrument") == "EQUITY" and not r.get("position_verified")),
            "pm_context_asof": (ctx.get("pm") or {}).get("generated_at"),
            "risk_flags": sum(len(r.get("tripwires") or []) for r in forecasts),
        },
    }
    payload["snapshot_key"] = _snapshot_key(payload["generated_at"])
    if persist:
        try:
            db = get_db()
            await db.kronos_forecast_runs.insert_one(stamped(payload))
            await db.kronos_forecast_snapshots.update_one(
                {"snapshot_key": payload["snapshot_key"]},
                {"$set": stamped(payload)},
                upsert=True,
            )
            for r in disagreements:
                audit_id = "|".join([
                    str(payload["snapshot_key"]),
                    str(r.get("ticker")),
                    str(r.get("instrument")),
                    str(r.get("contract") or ""),
                    str(r.get("pm_action")),
                    str(r.get("forecast_bias")),
                ])
                await db.kronos_pm_disagreements.update_one(
                    {"audit_id": audit_id},
                    {"$setOnInsert": stamped({
                    "audit_id": audit_id,
                    "model_version": kronos_contract.MODEL_VERSION,
                    "ticker": r["ticker"],
                    "instrument": r["instrument"],
                    "contract": r.get("contract"),
                    "pm_action": r["pm_action"],
                    "forecast_bias": r["forecast_bias"],
                    "kronos_score": r["kronos_score"],
                    "forecast_pct": r["forecast_pct"],
                    "prediction_id": r.get("prediction_id"),
                    "target_at": r.get("target_at"),
                    "generated_at": payload["generated_at"],
                    "status": "OPEN_AUDIT",
                    })},
                    upsert=True,
                )
        except Exception as exc:
            logger.exception("kronos persistence failed")
            payload["persistence_error"] = str(exc)
    return payload


def _tripwires(pos: dict[str, Any], score: float | None, pm_action: str) -> list[str]:
    flags = []
    risk = pos.get("risk") or {}
    if pos["instrument"] == "OPTION" and risk.get("hard_stop_triggered"):
        flags.append("HARD_STOP")
    if pos["instrument"] == "OPTION" and str(risk.get("theta_status") or "").upper() == "WATCH":
        flags.append("THETA_WATCH")
    if pos.get("unrealized_pct") is not None and pos["unrealized_pct"] <= -8:
        flags.append("DRAWDOWN")
    if score is None:
        flags.append("NO_CASE_SCORE")
    if pm_action == "HELD_NOT_IN_LATEST_PM":
        flags.append("OUTSIDE_LATEST_PM")
    if pm_action == "UNMAPPED":
        flags.append("NO_PM_MAP")
    return flags


def _catalysts(signal: dict[str, Any]) -> list[str]:
    tags = []
    blob = str(signal).lower()
    if "earn" in blob:
        tags.append("EARNINGS")
    if "contract" in blob or "sam" in blob:
        tags.append("CONTRACT")
    if "fda" in blob or "pdufa" in blob or "clinical" in blob:
        tags.append("PHARMA")
    if "flow" in blob:
        tags.append("OPTIONS_FLOW")
    if "x_factor" in blob or "stocktwits" in blob or "trend" in blob:
        tags.append("RETAIL")
    return tags[:5]


def _side_from_forecast(row: dict[str, Any]) -> str:
    pct = _num(row.get("forecast_pct"))
    bias = str(row.get("forecast_bias") or "").upper()
    if pct is not None:
        if pct >= 0.15:
            return "BULL"
        if pct <= -0.15:
            return "BEAR"
    if "BULL" in bias:
        return "BULL"
    if "BEAR" in bias:
        return "BEAR"
    return "NEUTRAL"


def _side_from_pm(row: dict[str, Any]) -> str:
    action = str(row.get("pm_action") or "").upper()
    if action in {"ACCUMULATE", "STARTER", "BUY", "EQUITY", "OPTION", "BOTH", "ADD"}:
        return "BULL"
    if action in {"TRIM", "SELL", "REJECT", "PASS"}:
        return "BEAR" if action in {"TRIM", "SELL"} else "NEUTRAL"
    return "NEUTRAL"


def _side_from_return(actual_pct: float | None, threshold: float = 0.25) -> str:
    if actual_pct is None:
        return "UNKNOWN"
    if actual_pct >= threshold:
        return "BULL"
    if actual_pct <= -threshold:
        return "BEAR"
    return "NEUTRAL"


def _winner_for_disagreement(row: dict[str, Any], actual_pct: float | None) -> str:
    actual = _side_from_return(actual_pct)
    if actual == "UNKNOWN":
        return "PENDING"
    if actual == "NEUTRAL":
        return "NO_EDGE"
    pm_side = _side_from_pm(row)
    kronos_side = _side_from_forecast(row)
    pm_right = pm_side == actual or (pm_side == "NEUTRAL" and actual == "BEAR")
    kronos_right = kronos_side == actual
    if pm_right and kronos_right:
        return "BOTH_RIGHT"
    if kronos_right and not pm_right:
        return "KRONOS_WON"
    if pm_right and not kronos_right:
        return "PM_WON"
    return "BOTH_WRONG"




async def reconcile_disagreements(limit: int = 500, min_age_hours: float = 6.0) -> dict[str, Any]:
    db = get_db()
    rows = await db.kronos_pm_disagreements.find(
        {"status": {"$in": ["OPEN_AUDIT", "OUT_FOR_AUDIT", None]}},
    ).sort("generated_at", 1).to_list(max(25, min(int(limit or 500), 1500)))
    checked = 0
    resolved = 0
    archived = 0
    pending = 0
    errors = []
    now = _now()
    for row in rows:
        checked += 1
        generated = _parse_dt(row.get("generated_at"))
        if generated is None or (now - generated).total_seconds() < float(min_age_hours) * 3600:
            pending += 1
            continue
        update_filter = {"audit_id": row.get("audit_id")} if row.get("audit_id") else {"_id": row.get("_id")}
        if str(row.get("pm_action") or "").upper() in {"UNMAPPED", "HELD_NOT_IN_LATEST_PM"}:
            result = await db.kronos_pm_disagreements.update_one(update_filter, {"$set": {
                "status": "ARCHIVED_UNMAPPED",
                "resolved_at": now.isoformat(),
                "winner": "NO_PM_MAP",
                "reconciliation_source": "pm_context_missing",
                "reconciliation_note": "Archived because the row did not have a real PM route to compare against.",
            }})
            if result.modified_count:
                archived += 1
            else:
                pending += 1
            continue
        if not row.get("prediction_id"):
            await db.kronos_pm_disagreements.update_one(update_filter, {"$set": {"status": "ARCHIVED_UNVERIFIABLE", "reason": "legacy_prediction_has_no_fixed_horizon"}})
            archived += 1
            continue
        resolved_prediction = await db.kronos_candle_outcomes.find_one({"prediction_id": row["prediction_id"]}, {"_id": 0})
        outcome = {"ok": bool(resolved_prediction), "actual_return_pct": (resolved_prediction or {}).get("actual_pct"), "target_at": row.get("target_at"), "reason": "pending_target_bar"}
        if not outcome.get("ok"):
            pending += 1
            if outcome.get("reason") not in {"not_enough_mature_history"}:
                errors.append({"ticker": row.get("ticker"), "reason": outcome.get("reason")})
            continue
        winner = _winner_for_disagreement(row, _num(outcome.get("actual_return_pct")))
        update = {
            "status": "RESOLVED",
            "resolved_at": now.isoformat(),
            "winner": winner,
            "pm_side": _side_from_pm(row),
            "kronos_side": _side_from_forecast(row),
            "actual_side": _side_from_return(_num(outcome.get("actual_return_pct"))),
            "outcome": outcome,
            "actual_return_pct": outcome.get("actual_return_pct"),
            "reconciliation_source": "exact_prediction_target_bar",
        }
        result = await db.kronos_pm_disagreements.update_one(update_filter, {"$set": update})
        if result.modified_count:
            resolved += 1
        else:
            pending += 1
    return {
        "ok": True,
        "checked": checked,
        "resolved": resolved,
        "archived_unmapped": archived,
        "pending": pending,
        "errors": errors[:20],
        "generated_at": now.isoformat(),
    }


async def learning_state(limit: int = 1200, persist: bool = False) -> dict[str, Any]:
    accuracy = await accuracy_snapshot()
    overall = accuracy.get("overall") if accuracy.get("ok") else {}
    recommendations = []
    for row in (accuracy.get("by_timeframe") or []):
        sample = int(row.get("sample") or 0)
        if sample < 12:
            recommendations.append({"scope": row.get("key"), "action": "COLLECT_MORE_SAMPLES", "reason": f"Only {sample} mature samples."})
            continue
        wr = _num(row.get("direction_win_rate_pct"))
        cone = _num(row.get("cone_coverage_pct"))
        if wr is not None and wr < 52:
            recommendations.append({"scope": row.get("key"), "action": "DAMPEN_DIRECTION", "reason": f"Direction win rate {wr}% is below edge threshold."})
        if cone is not None and cone < 68:
            recommendations.append({"scope": row.get("key"), "action": "WIDEN_CONE", "reason": f"Cone coverage {cone}% is below 68% target."})
    health = _learning_health_from_overall(overall)
    return {
        "ok": accuracy.get("ok", False),
        "health": health,
        "overall": overall,
        "recommendations": recommendations[:18],
        "calibration_note": "Research baseline only. Heuristic confidence and probability weights are not calibrated probabilities or demonstrated alpha.",
        "accuracy": accuracy,
        "generated_at": _now().isoformat(),
    }


def _learning_health_from_overall(overall: dict[str, Any] | None) -> str:
    overall = overall or {}
    sample = int(overall.get("sample") or 0)
    wr = _num(overall.get("direction_win_rate_pct"))
    cone = _num(overall.get("cone_coverage_pct"))
    if sample < 12:
        return "WARMING_UP"
    if sample >= 40 and wr is not None and wr >= 55 and (cone is None or cone >= 62):
        return "IMPROVING"
    if sample >= 40 and wr is not None and wr < 48:
        return "DEFENSIVE"
    return "LEARNING"


async def disagreement_performance(limit: int = 200, auto_reconcile: bool = False) -> dict[str, Any]:
    db = get_db()
    reconciliation = await reconcile_disagreements(limit=max(limit, 250)) if auto_reconcile else None
    rows = await db.kronos_pm_disagreements.find({"model_version": kronos_contract.MODEL_VERSION}, {"_id": 0}).sort("generated_at", -1).to_list(limit)
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = f"{row.get('forecast_bias')} vs {row.get('pm_action')}"
        g = grouped.setdefault(key, {"setup": key, "count": 0, "open_audits": 0, "resolved": 0, "archived": 0, "pm_wins": 0, "kronos_wins": 0, "no_edge": 0})
        g["count"] += 1
        if row.get("status") in {"OPEN_AUDIT", "OUT_FOR_AUDIT"}:
            g["open_audits"] += 1
        elif row.get("status") == "RESOLVED":
            g["resolved"] += 1
            winner = str(row.get("winner") or "")
            if winner == "PM_WON":
                g["pm_wins"] += 1
            elif winner == "KRONOS_WON":
                g["kronos_wins"] += 1
            elif winner == "NO_EDGE":
                g["no_edge"] += 1
        elif row.get("status") == "ARCHIVED_UNMAPPED":
            g["archived"] += 1
    return {
        "rows": rows,
        "summary": list(grouped.values()),
        "reconciliation": reconciliation,
        "note": "Disagreements auto-reconcile once enough post-signal price history exists; unresolved rows remain open audits.",
    }


def _empty_accuracy_bucket(key: str = "ALL") -> dict[str, Any]:
    return {
        "key": key,
        "sample": 0,
        "pending": 0,
        "direction_wins": 0,
        "direction_losses": 0,
        "direction_win_rate_pct": None,
        "cone_wins": 0,
        "cone_losses": 0,
        "cone_coverage_pct": None,
        "mae_pct": None,
        "rmse_pct": None,
        "avg_error_pct": None,
    }


def _finalize_accuracy_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
    sample = int(bucket.get("sample") or 0)
    direction_total = int(bucket.get("direction_wins") or 0) + int(bucket.get("direction_losses") or 0)
    cone_total = int(bucket.get("cone_wins") or 0) + int(bucket.get("cone_losses") or 0)
    abs_errors = bucket.pop("_abs_errors", [])
    sq_errors = bucket.pop("_sq_errors", [])
    errors = bucket.pop("_errors", [])
    bucket["direction_win_rate_pct"] = round(bucket["direction_wins"] / direction_total * 100, 1) if direction_total else None
    bucket["cone_coverage_pct"] = round(bucket["cone_wins"] / cone_total * 100, 1) if cone_total else None
    bucket["mae_pct"] = round(sum(abs_errors) / len(abs_errors), 3) if abs_errors else None
    bucket["rmse_pct"] = round(math.sqrt(sum(sq_errors) / len(sq_errors)), 3) if sq_errors else None
    bucket["avg_error_pct"] = round(sum(errors) / len(errors), 3) if errors else None
    bucket["sample"] = sample
    null_errors = bucket.pop("_null_errors", [])
    widths = bucket.pop("_cone_widths", [])
    bucket["no_change_mae_pct"] = round(sum(null_errors) / len(null_errors), 3) if null_errors else None
    bucket["mean_cone_width_pct"] = round(sum(widths) / len(widths), 3) if widths else None
    bucket["mae_skill_vs_no_change"] = round(1 - sum(abs_errors) / sum(null_errors), 4) if null_errors and sum(null_errors) else None
    return bucket


def _add_accuracy_sample(bucket: dict[str, Any], row: dict[str, Any]) -> None:
    actual = _num(row.get("actual_pct"))
    forecast = _num(row.get("forecast_pct"))
    if actual is None or forecast is None:
        bucket["pending"] = int(bucket.get("pending") or 0) + 1
        return
    bucket["sample"] = int(bucket.get("sample") or 0) + 1
    noise = max(0.03, _num(row.get("noise_band_pct"), 0.06) or 0.06)
    forecast_dir = "FLAT" if abs(forecast) <= noise else ("UP" if forecast > 0 else "DOWN")
    actual_dir = "FLAT" if abs(actual) <= noise else ("UP" if actual > 0 else "DOWN")
    if forecast_dir == actual_dir:
        bucket["direction_wins"] = int(bucket.get("direction_wins") or 0) + 1
    else:
        bucket["direction_losses"] = int(bucket.get("direction_losses") or 0) + 1
    cone_low = _num(row.get("cone_low_pct"))
    cone_high = _num(row.get("cone_high_pct"))
    if cone_low is not None and cone_high is not None:
        lo, hi = sorted([cone_low, cone_high])
        if lo <= actual <= hi:
            bucket["cone_wins"] = int(bucket.get("cone_wins") or 0) + 1
        else:
            bucket["cone_losses"] = int(bucket.get("cone_losses") or 0) + 1
    err = actual - forecast
    bucket.setdefault("_errors", []).append(err)
    bucket.setdefault("_abs_errors", []).append(abs(err))
    bucket.setdefault("_sq_errors", []).append(err * err)
    bucket.setdefault("_null_errors", []).append(abs(actual))
    if cone_low is not None and cone_high is not None:
        bucket.setdefault("_cone_widths", []).append(abs(cone_high - cone_low))


async def candle_accuracy(limit: int = 800, persist: bool = False) -> dict[str, Any]:
    """Score persisted candle forecasts against the next available OHLCV candle.

    Pending rows stay pending until a future candle exists. This makes Kronos'
    accuracy display falsifiable without mutating the original forecast.
    """
    db = get_db()
    rows = await db.kronos_candle_predictions.find(
        {"ok": True, "model_version": kronos_contract.MODEL_VERSION},
        {"_id": 0},
    ).sort("generated_at", -1).to_list(max(50, min(int(limit or 800), 2500)))
    existing = await db.kronos_candle_outcomes.find({"prediction_id": {"$in": [r["prediction_id"] for r in rows if r.get("prediction_id")]}}, {"_id": 0}).to_list(2500)
    resolved_ids = {r["prediction_id"] for r in existing}
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("prediction_id") in resolved_ids:
            continue
        key = (_ticker(row.get("symbol")) or "SPY", str(row.get("timeframe") or "5m").lower())
        grouped.setdefault(key, []).append(row)

    scored: list[dict[str, Any]] = list(existing)
    pending = 0
    try:
        from . import london_strategic_edge as lse
    except Exception as exc:
        return {"ok": False, "error": f"lse_unavailable:{exc}", "sample": 0, "pending": len(rows)}

    for (symbol, timeframe), preds in grouped.items():
        try:
            targets = [_parse_dt(p.get("target_at")) for p in preds]
            targets = [t for t in targets if t]
            start = (min(targets) - timedelta(days=1)).date().isoformat() if targets else None
            end = (min(max(targets), _now()) + timedelta(days=1)).date().isoformat() if targets else None
            payload = await asyncio.wait_for(lse.candles(symbol, timeframe=timeframe, start=start, end=end, limit=5000, order="desc", request_timeout=8), timeout=15)
            candles = [_norm_candle(r) for r in _rows(payload)]
            candles = [c for c in candles if c and _parse_dt(c.get("timestamp"))]
        except Exception as exc:
            logger.debug("kronos accuracy candles failed %s %s: %s", symbol, timeframe, exc)
            candles = []
        if not candles:
            pending += len(preds)
            continue
        candle_pairs = [(_parse_dt(c.get("timestamp")), c) for c in candles]
        candle_pairs = [(dt, c) for dt, c in candle_pairs if dt]
        for pred in preds:
            resolved = await db.kronos_candle_outcomes.find_one({"prediction_id": pred.get("prediction_id")}, {"_id": 0})
            if resolved:
                scored.append(resolved)
                continue
            generated = _parse_dt(pred.get("generated_at") or pred.get("created_at"))
            base = _num((pred.get("features") or {}).get("last_close") or (pred.get("predicted_next_candle") or {}).get("open"))
            if generated is None or base is None or base <= 0:
                pending += 1
                continue
            target = _parse_dt(pred.get("target_at"))
            actual_candle = next((c for dt, c in candle_pairs if target and target <= _now() and kronos_contract.bar_close(c.get("timestamp"), timeframe, _now()) == target), None)
            if not actual_candle:
                pending += 1
                continue
            actual_close = _num(actual_candle.get("close"))
            if actual_close is None:
                pending += 1
                continue
            actual_pct = (actual_close - base) / base * 100.0
            structure = str((pred.get("features") or {}).get("structure") or "UNKNOWN").upper()
            regime = (
                "TREND_UP" if structure == "HIGHER_HIGH"
                else "TREND_DOWN" if structure == "LOWER_LOW"
                else "CHOP"
            )
            scored.append({
                "prediction_id": pred.get("prediction_id"),
                "model_version": kronos_contract.MODEL_VERSION,
                "target_at": pred.get("target_at"),
                "symbol": symbol,
                "timeframe": timeframe,
                "regime": regime,
                "generated_at": pred.get("generated_at"),
                "actual_at": actual_candle.get("timestamp"),
                "direction": pred.get("direction"),
                "forecast_pct": _num(pred.get("forecast_pct")),
                "actual_pct": round(actual_pct, 3),
                "error_pct": round(actual_pct - (_num(pred.get("forecast_pct"), 0.0) or 0.0), 3),
                "noise_band_pct": _num(pred.get("noise_band_pct"), 0.06),
                "cone_low_pct": _num(pred.get("cone_low_pct")),
                "cone_high_pct": _num(pred.get("cone_high_pct")),
                "confidence": pred.get("confidence"),
                "provider": pred.get("provider"),
            })
            if persist:
                await db.kronos_candle_outcomes.update_one({"prediction_id": pred["prediction_id"]}, {"$setOnInsert": scored[-1]}, upsert=True)

    overall = _empty_accuracy_bucket("ALL")
    by_timeframe: dict[str, dict[str, Any]] = {}
    by_regime: dict[str, dict[str, Any]] = {}
    by_symbol: dict[str, dict[str, Any]] = {}
    for row in scored:
        _add_accuracy_sample(overall, row)
        _add_accuracy_sample(by_timeframe.setdefault(row["timeframe"], _empty_accuracy_bucket(row["timeframe"])), row)
        _add_accuracy_sample(by_regime.setdefault(row["regime"], _empty_accuracy_bucket(row["regime"])), row)
        _add_accuracy_sample(by_symbol.setdefault(row["symbol"], _empty_accuracy_bucket(row["symbol"])), row)
    overall["pending"] = pending
    result = {
        "ok": True,
        "overall": _finalize_accuracy_bucket(overall),
        "by_timeframe": [_finalize_accuracy_bucket(v) for v in by_timeframe.values()],
        "by_regime": [_finalize_accuracy_bucket(v) for v in by_regime.values()],
        "by_symbol": sorted((_finalize_accuracy_bucket(v) for v in by_symbol.values()), key=lambda r: r.get("sample") or 0, reverse=True)[:20],
        "recent": scored[:80],
        "pending": pending,
        "stored_predictions": len(rows),
        "scored_predictions": len(scored),
        "source": "immutable predictions + exact completed target bar",
        "model_version": kronos_contract.MODEL_VERSION,
        "generated_at": _now().isoformat(),
    }
    if persist:
        try:
            await db.kronos_accuracy_snapshots.insert_one(stamped(result))
        except Exception as exc:
            logger.debug("kronos accuracy persistence skipped: %s", exc)
    return result


async def status() -> dict[str, Any]:
    db = get_db()
    latest = await latest_forecast()
    latest_key = (latest or {}).get("snapshot_key") or _snapshot_key((latest or {}).get("generated_at") or _now().isoformat())
    latest_disagreements = await db.kronos_pm_disagreements.count_documents({
        "model_version": kronos_contract.MODEL_VERSION,
        "status": {"$in": ["OPEN_AUDIT", "OUT_FOR_AUDIT"]},
        "audit_id": {"$regex": f"^{re.escape(str(latest_key))}"},
    })
    total_open_disagreements = await db.kronos_pm_disagreements.count_documents({
        "model_version": kronos_contract.MODEL_VERSION,
        "status": {"$in": ["OPEN_AUDIT", "OUT_FOR_AUDIT", None]},
    })
    resolved_disagreements = await db.kronos_pm_disagreements.count_documents({"status": "RESOLVED", "model_version": kronos_contract.MODEL_VERSION})
    archived_disagreements = await db.kronos_pm_disagreements.count_documents({"status": "ARCHIVED_UNMAPPED", "model_version": kronos_contract.MODEL_VERSION})
    age = _age_minutes((latest or {}).get("generated_at"))
    summary = (latest or {}).get("summary") or {}
    now = _now()
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    end_day = calendar.monthrange(now.year, now.month)[1]
    end = datetime(now.year, now.month, end_day, 23, 59, 59, tzinfo=timezone.utc)
    month_days = await calendar_month(now.astimezone(ZoneInfo("America/New_York")).year, now.astimezone(ZoneInfo("America/New_York")).month)
    scored_days = (month_days.get("summary") or {}).get("scored_days", 0)
    health = _freshness_status(age)
    if not latest.get("ok") or not (latest.get("market_forecast") or {}).get("input_asof"):
        health = "DEGRADED"
    market = latest.get("market_forecast") or {}
    if market.get("ok"):
        _, current_contract = kronos_contract.input_contract([{"timestamp": (market.get("features") or {}).get("latest_timestamp")}], market.get("timeframe", "5m"), now)
        if not current_contract.get("ok"):
            health = "STALE_INPUT"
        elif health == "LIVE" and not any(op <= now < cl for _, op, cl in kronos_contract.sessions(now)):
            health = "MARKET_CLOSED"
    pm_coverage = "FULL" if not summary.get("unmapped_pm", 0) else "PARTIAL"
    try:
        accuracy = await accuracy_snapshot()
        proof = accuracy.get("overall") if accuracy.get("ok") else {}
        learning_health = _learning_health_from_overall(proof)
    except Exception as exc:
        logger.debug("kronos status accuracy degraded: %s", exc)
        proof = {}
        learning_health = "UNKNOWN"
    return {
        "ok": True,
        "health": health,
        "pm_context_health": pm_coverage,
        "latest_snapshot_at": (latest or {}).get("generated_at"),
        "snapshot_age_minutes": round(age, 1) if age is not None else None,
        "positions": summary.get("positions", 0),
        "mapped_pm": summary.get("mapped_pm", 0),
        "unmapped_pm": summary.get("unmapped_pm", 0),
        "stale_position_context": summary.get("stale_position_context", 0),
        "risk_flags": summary.get("risk_flags", 0),
        "open_disagreement_audits": total_open_disagreements,
        "latest_snapshot_open_disagreement_audits": latest_disagreements,
        "resolved_disagreement_audits": resolved_disagreements,
        "archived_unmapped_disagreement_audits": archived_disagreements,
        "learning_health": learning_health,
        "calendar": {
            "direction_win_rate_pct": None,
            "cone_win_rate_pct": None,
            "scored_days": scored_days,
        },
        "proof": proof,
        "input_asof": market.get("input_asof"),
        "model_version": kronos_contract.MODEL_VERSION,
        "research_only": True,
    }


async def latest_forecast() -> dict[str, Any]:
    latest = await get_db().kronos_forecast_snapshots.find_one({"model_version": kronos_contract.MODEL_VERSION}, {"_id": 0}, sort=[("generated_at", -1)])
    return latest or {"ok": False, "forecasts": [], "reason": "awaiting_scheduled_refresh", "research_only": True}


async def accuracy_snapshot() -> dict[str, Any]:
    latest = await get_db().kronos_accuracy_snapshots.find_one({"model_version": kronos_contract.MODEL_VERSION}, {"_id": 0}, sort=[("generated_at", -1)])
    return latest or {"ok": True, "overall": {"sample": 0}, "pending": 0, "reason": "awaiting_resolved_predictions"}


async def refresh_snapshot() -> dict[str, Any]:
    if _refresh_lock.locked():
        return {"ok": False, "reason": "refresh_already_running", "forecast": await latest_forecast()}
    async with _refresh_lock:
        payload = await forecast(persist=True)
        accuracy = await candle_accuracy(limit=1200, persist=True)
        reconciliation = await reconcile_disagreements(limit=750)
        stat = await status()
        return {"ok": bool(payload.get("ok") and accuracy.get("ok")), "forecast": payload, "accuracy": accuracy, "reconciliation": reconciliation, "status": stat}


async def calendar_month(year: int, month: int) -> dict[str, Any]:
    month = max(1, min(12, int(month)))
    zone = ZoneInfo("America/New_York")
    start = datetime(int(year), month, 1, tzinfo=zone)
    end = (start + timedelta(days=32)).replace(day=1)
    query = {"generated_at": {"$gte": start.astimezone(timezone.utc).isoformat(), "$lt": end.astimezone(timezone.utc).isoformat()}, "model_version": kronos_contract.MODEL_VERSION}
    db = get_db()
    fields = {key: 1 for key in ("prediction_id", "generated_at", "symbol", "timeframe", "forecast_pct", "cone_low_pct", "cone_high_pct", "target_at", "confidence")}
    fields["_id"] = 0
    preds = await db.kronos_candle_predictions.find(query, fields).sort("generated_at", 1).to_list(None)
    outcomes = await db.kronos_candle_outcomes.find({"prediction_id": {"$in": [p["prediction_id"] for p in preds]}}, {"_id": 0}).to_list(None) if preds else []
    by_id = {r["prediction_id"]: r for r in outcomes}
    out = []
    total = _empty_accuracy_bucket("MONTH")
    for number in range(1, calendar.monthrange(int(year), month)[1] + 1):
        day = f"{int(year):04d}-{month:02d}-{number:02d}"
        issued = [p for p in preds if (_parse_dt(p.get("generated_at")) or start).astimezone(zone).date().isoformat() == day]
        mature = [by_id[p["prediction_id"]] for p in issued if p["prediction_id"] in by_id]
        bucket = _empty_accuracy_bucket(day)
        for row in mature:
            _add_accuracy_sample(bucket, row)
            _add_accuracy_sample(total, row)
        proof = _finalize_accuracy_bucket(bucket)
        latest_spy = next((p for p in reversed(issued) if p.get("symbol") == "SPY" and p.get("timeframe") == "5m"), {})
        actual = by_id.get(latest_spy.get("prediction_id"), {})
        rate = proof.get("direction_win_rate_pct")
        verdict = ("GOOD" if rate > 50 else "BAD" if rate < 50 else "WATCH") if rate is not None else ("PENDING" if issued else "NO_FORECAST")
        out.append({"date": day, "has_prediction": bool(issued), "status": verdict,
                    "score": proof.get("direction_win_rate_pct"), "predictions": len(issued), "resolved_predictions": len(mature),
                    "pending_predictions": len(issued) - len(mature), "proof": proof,
                    "direction_win": _direction_win(latest_spy.get("forecast_pct"), actual.get("actual_pct")),
                    "cone_win": _cone_win(latest_spy.get("cone_low_pct"), latest_spy.get("cone_high_pct"), actual.get("actual_pct")),
                    "spy_prediction_pct": latest_spy.get("forecast_pct"), "spy_actual_pct": actual.get("actual_pct"),
                    "spy_cone_low_pct": latest_spy.get("cone_low_pct"), "spy_cone_high_pct": latest_spy.get("cone_high_pct"),
                    "snapshot_at": latest_spy.get("generated_at"), "target_at": latest_spy.get("target_at"),
                    "confidence": latest_spy.get("confidence"), "fund_actual_pct": None, "fund_prediction_usd": None})
    summary = _finalize_accuracy_bucket(total)
    summary.update({"direction_losses": summary["direction_losses"], "cone_win_rate_pct": summary.get("cone_coverage_pct"),
                    "predictions": len(preds), "resolved_predictions": len(outcomes), "pending_predictions": len(preds) - len(outcomes),
                    "predicted_days": sum(d["has_prediction"] for d in out), "scored_days": sum(d["resolved_predictions"] > 0 for d in out)})
    return {"ok": True, "year": int(year), "month": month, "month_label": start.strftime("%B %Y"), "days": out,
            "summary": summary, "available_years": await _calendar_years(db),
            "source": "ET issue dates; exact completed forecast horizons, not daily market returns"}






def _direction_win(forecast_pct: float | None, spy_actual_pct: float | None) -> bool | None:
    if forecast_pct is None or spy_actual_pct is None or abs(forecast_pct) < 0.06:
        return None
    return (forecast_pct >= 0 and spy_actual_pct >= 0) or (forecast_pct < 0 and spy_actual_pct < 0)


def _cone_win(cone_low: float | None, cone_high: float | None, spy_actual_pct: float | None) -> bool | None:
    if cone_low is None or cone_high is None or spy_actual_pct is None:
        return None
    lo, hi = sorted([cone_low, cone_high])
    return lo <= spy_actual_pct <= hi


async def _calendar_years(db) -> list[int]:
    rows = await db.kronos_forecast_snapshots.find({}, {"_id": 0, "generated_at": 1}).sort("generated_at", -1).to_list(1500)
    years = []
    for row in rows:
        try:
            y = int(str(row.get("generated_at") or "")[:4])
        except Exception:
            continue
        if y and y not in years:
            years.append(y)
    current = datetime.now(timezone.utc).year
    if current not in years:
        years.insert(0, current)
    return sorted(years, reverse=True)


async def battle_card(ticker: str) -> dict[str, Any]:
    t = _ticker(ticker)
    ctx = await _scan_pm_context()
    pm_row = next((r for r in _pm_rows(ctx.get("pm") or {}) if _ticker(r.get("ticker") or r.get("symbol")) == t), {})
    prediction = await candle_forecast(t, "1d", persist=False)
    match = {"ticker": t, "instrument": "EQUITY", "pm_action": pm_row.get("action") or pm_row.get("route") or "UNMAPPED",
             "forecast_bias": {"UP": "BULLISH", "DOWN": "BEARISH", "FLAT": "CHOP"}.get(prediction.get("direction"), "UNAVAILABLE"),
             "forecast_pct": prediction.get("forecast_pct"), "bear_pct": prediction.get("cone_low_pct"), "bull_pct": prediction.get("cone_high_pct"),
             "confidence": prediction.get("confidence"), "kronos_score": prediction.get("score"), "probabilities": prediction.get("probabilities") or {},
             "horizons": prediction.get("horizons") or [], "input_asof": prediction.get("input_asof"), "target_at": prediction.get("target_at"),
             "exit_forecast": {"research_only": True}, "tripwires": [], "catalysts": []}
    return {"ok": prediction.get("ok", False), "ticker": t, "match": match, "forecast": match, "prediction": prediction,
            "battle_card": match, "pm": pm_row, "research_only": True, "generated_at": _now().isoformat()}


def build_morning_message(payload: dict[str, Any]) -> str:
    market = payload.get("market_forecast") or {}
    return "\n".join([
        "<b>CASE CAPITAL | KRONOS RESEARCH BRIEF</b>",
        f"SPY: {market.get('direction', 'UNKNOWN')}",
        f"Input as-of: {market.get('input_asof', 'unavailable')}",
        f"Target: {market.get('target_at', 'unavailable')}",
        f"Baseline return estimate: {market.get('forecast_pct')}%",
        "<i>Uncalibrated research baseline. No trading authority.</i>",
    ])


async def dispatch_morning_forecast(force: bool = False) -> dict[str, Any]:
    from . import telegram_service
    payload = await forecast(persist=True)
    sent = await telegram_service.send_message(build_morning_message(payload))
    await get_db().telegram_reports.insert_one(stamped({"type": "kronos_morning_forecast", "sent": bool(sent), "force": force, "payload": {"market_forecast": payload.get("market_forecast"), "summary": payload.get("summary")}}))
    return {"ok": bool(sent), "sent": bool(sent), "forecast": payload}
