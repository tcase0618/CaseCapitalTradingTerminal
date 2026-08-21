"""Kronos advisory forecast layer.

Kronos is intentionally read-only. It builds forecast intelligence from
existing scanner, PM, trade-floor, options-desk, and market-data state, then
stores snapshots so disagreement performance can be audited later.
"""
from __future__ import annotations

import asyncio
import calendar
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any

from .db import get_db, stamped

logger = logging.getLogger(__name__)


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


def _pct(v: Any) -> float | None:
    n = _num(v)
    if n is None:
        return None
    return n * 100 if abs(n) <= 2 else n


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
    from . import options_desk, portfolio_manager, scanner, trade_floor

    scan_task = _bounded("scan", scanner.latest_scan(), timeout=4.0)
    pm_task = _bounded("pm", portfolio_manager.latest_portfolio_plan(), timeout=5.0)
    eq_task = _bounded("equity_db", trade_floor.open_positions_view(), timeout=3.0)
    live_eq_task = _bounded("equity_live", trade_floor.list_positions(), timeout=5.0)
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
    return ctx


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
    rows = _rows(scan)
    try:
        decisions = portfolio_manager.evaluate_rows(
            rows,
            equity=portfolio_manager.DEFAULT_EQUITY,
            mode="BALANCED",
        )
        pm = {"decisions": decisions, "source": "deterministic_scan_replay"}
    except Exception as exc:
        pm = {"error": str(exc)}
    return {"scan": scan, "pm": pm}


async def _spy_history() -> list[float]:
    try:
        from . import pricer
        data = await pricer.get_history("SPY", days=90)
        vals = [float(v) for _, v in sorted(data.items()) if v]
        if len(vals) >= 15:
            return vals[-90:]
    except Exception as exc:
        logger.debug("kronos pricer SPY history failed: %s", exc)

    def _yf() -> list[float]:
        try:
            import yfinance as yf
            hist = yf.Ticker("SPY").history(period="90d")["Close"]
            return [float(x) for x in hist.dropna().tolist()]
        except Exception:
            return []

    return await asyncio.to_thread(_yf)


def _norm_candle(row: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    close = _num(row.get("close") or row.get("c") or row.get("last") or row.get("price"))
    open_ = _num(row.get("open") or row.get("o") or close)
    high = _num(row.get("high") or row.get("h") or close)
    low = _num(row.get("low") or row.get("l") or close)
    if close is None or open_ is None or high is None or low is None:
        return None
    high = max(high, open_, close)
    low = min(low, open_, close)
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


async def candle_forecast(symbol: str = "SPY", timeframe: str = "5m", limit: int = 220, persist: bool = False) -> dict[str, Any]:
    """Candle-aware read-only forecast from raw OHLCV.

    This is intentionally separate from the portfolio forecast engine. It uses
    numerical OHLCV, not TradingView pixels.
    """
    ticker = _ticker(symbol) or "SPY"
    tf = str(timeframe or "5m").lower()
    try:
        from . import london_strategic_edge as lse
        payload = await lse.candles(ticker, timeframe=tf, limit=max(60, min(int(limit or 220), 500)), order="asc")
        candles = [_norm_candle(r) for r in _rows(payload)]
        candles = [c for c in candles if c]
        provider = payload.get("provider") or "london_strategic_edge"
        degraded = not bool(payload.get("ok"))
    except Exception as exc:
        logger.debug("kronos candle forecast LSE failed %s %s: %s", ticker, tf, exc)
        candles = []
        provider = "london_strategic_edge"
        degraded = True
    if len(candles) < 35:
        vals = await _spy_history() if ticker == "SPY" else []
        if len(vals) >= 35:
            candles = [{"timestamp": "", "open": v, "high": v, "low": v, "close": v, "volume": 0.0} for v in vals[-120:]]
            provider = "pricer_close_fallback"
            degraded = True
    if len(candles) < 20:
        return {
            "ok": False,
            "symbol": ticker,
            "timeframe": tf,
            "direction": "UNKNOWN",
            "reason": "insufficient_ohlcv",
            "provider": provider,
            "degraded": True,
            "candles": len(candles),
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
    }
    if persist:
        try:
            db = get_db()
            await db.kronos_candle_predictions.insert_one(stamped(result))
        except Exception as exc:
            logger.debug("kronos candle prediction persistence skipped: %s", exc)
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


def _ret(vals: list[float], days: int) -> float:
    if len(vals) <= days or vals[-days - 1] == 0:
        return 0.0
    return (vals[-1] - vals[-days - 1]) / vals[-days - 1] * 100


def _vol(vals: list[float], days: int = 20) -> float:
    if len(vals) < days + 1:
        return 1.0
    returns = []
    for i in range(-days, 0):
        prev = vals[i - 1]
        if prev:
            returns.append((vals[i] - prev) / prev * 100)
    if not returns:
        return 1.0
    mean = sum(returns) / len(returns)
    var = sum((x - mean) ** 2 for x in returns) / max(1, len(returns) - 1)
    return max(0.35, math.sqrt(var))


async def market_forecast() -> dict[str, Any]:
    candle = await candle_forecast_suite("SPY", persist=False)
    primary = candle.get("primary") if isinstance(candle, dict) else None
    if isinstance(primary, dict) and primary.get("ok"):
        return {
            "symbol": "SPY",
            "direction": primary.get("direction") or "UNKNOWN",
            "forecast_pct": round(_num(primary.get("forecast_pct"), 0.0) or 0.0, 2),
            "cone_low_pct": round(_num(primary.get("cone_low_pct"), -1.0) or -1.0, 2),
            "cone_high_pct": round(_num(primary.get("cone_high_pct"), 1.0) or 1.0, 2),
            "confidence": primary.get("confidence") or 20,
            "last_price": (primary.get("features") or {}).get("last_close"),
            "realized_vol_20d": (primary.get("features") or {}).get("atr_pct"),
            "source": "kronos_candle_engine/raw_ohlcv",
            "reason": "candle-aware OHLCV model with adaptive noise band",
            "candle_engine": candle,
        }
    vals = await _spy_history()
    if len(vals) < 5:
        return {
            "symbol": "SPY",
            "direction": "UNKNOWN",
            "forecast_pct": 0.0,
            "cone_low_pct": -1.0,
            "cone_high_pct": 1.0,
            "confidence": 20,
            "source": "degraded",
            "reason": "insufficient SPY history",
        }
    r1, r5, r20 = _ret(vals, 1), _ret(vals, 5), _ret(vals, 20)
    vol = _vol(vals, 20)
    base = (r1 * 0.42) + (r5 / 5 * 0.35) + (r20 / 20 * 0.23)
    base = max(-2.8, min(2.8, base))
    direction = "UP" if base > 0.06 else "DOWN" if base < -0.06 else "FLAT"
    confidence = int(max(25, min(82, 50 + abs(base) * 12 + min(12, abs(r5)))))
    return {
        "symbol": "SPY",
        "direction": direction,
        "forecast_pct": round(base, 2),
        "cone_low_pct": round(base - vol * 1.15, 2),
        "cone_high_pct": round(base + vol * 1.15, 2),
        "confidence": confidence,
        "last_price": round(vals[-1], 2),
        "r1_pct": round(r1, 2),
        "r5_pct": round(r5, 2),
        "r20_pct": round(r20, 2),
        "realized_vol_20d": round(vol, 2),
        "source": "pricer_normalized/yfinance",
        "reason": "momentum plus realized-vol cone",
    }


def _score(row: dict[str, Any], pm_row: dict[str, Any]) -> float | None:
    score = _num(
        row.get("trade_score")
        or row.get("signal_score")
        or row.get("case_score")
        or row.get("learning_score")
        or pm_row.get("pm_score")
        or pm_row.get("score")
    )
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
    day = str(ts or _now().isoformat())[:10]
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


def _action_allows_position(action: str, instrument: str) -> bool:
    action = str(action or "").upper()
    if action == "BOTH":
        return True
    if action == "EQUITY" and instrument == "EQUITY":
        return True
    if action == "OPTION" and instrument == "OPTION":
        return True
    if action in {"ACCUMULATE", "STARTER", "WATCH"}:
        return instrument == "EQUITY"
    return False


def _bias(score: float | None, pm_action: str, instrument: str, pnl_pct: float | None, risk: dict[str, Any]) -> dict[str, Any]:
    theta_watch = str(risk.get("theta_status") or "").upper() == "WATCH"
    if pm_action == "PASS":
        return {"label": "BEARISH", "base": -2.2, "bear": -6.5, "bull": 2.8}
    if theta_watch and instrument == "OPTION":
        return {"label": "HEDGE", "base": 0.8, "bear": -8.0, "bull": 8.5}
    if pm_action == "ACCUMULATE":
        return {"label": "BULLISH", "base": 5.5, "bear": -4.5, "bull": 12.0}
    if pm_action == "STARTER":
        return {"label": "BULLISH", "base": 3.8, "bear": -3.5, "bull": 8.5}
    if pm_action == "WATCH":
        return {"label": "CHOP", "base": 1.2, "bear": -3.8, "bull": 4.5}
    if pm_action == "BOTH" or (score or 0) >= 8.2:
        return {"label": "BULLISH", "base": 24.0 if instrument == "OPTION" else 5.5, "bear": -20.0 if instrument == "OPTION" else -4.5, "bull": 85.0 if instrument == "OPTION" else 12.0}
    if pm_action == "OPTION" or (score or 0) >= 7:
        return {"label": "BULLISH", "base": 16.0 if instrument == "OPTION" else 3.8, "bear": -20.0 if instrument == "OPTION" else -3.5, "bull": 55.0 if instrument == "OPTION" else 8.5}
    if (score is not None and score <= 4.5) or (pnl_pct is not None and pnl_pct <= -8):
        return {"label": "BEARISH", "base": -3.2, "bear": -20.0 if instrument == "OPTION" else -7.5, "bull": 3.2}
    return {"label": "CHOP", "base": 5.0 if instrument == "OPTION" else 1.2, "bear": -16.0 if instrument == "OPTION" else -3.8, "bull": 22.0 if instrument == "OPTION" else 4.5}


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


def _horizons(base: float, instrument: str) -> dict[str, dict[str, float]]:
    mult = {"1D": 0.35, "5D": 1.0, "10D": 1.45, "30D": 2.2, "EARNINGS": 1.8}
    if instrument == "OPTION":
        mult = {"1D": 0.45, "5D": 1.0, "10D": 1.65, "30D": 2.8, "EARNINGS": 2.4}
    return {k: {"base_pct": round(base * m, 2), "low_pct": round(base * m - abs(base) * 0.7 - 1.2, 2), "high_pct": round(base * m + abs(base) * 0.9 + 1.2, 2)} for k, m in mult.items()}


def _probabilities(base: float, instrument: str) -> dict[str, int]:
    power = max(-1.0, min(1.0, base / (24 if instrument == "OPTION" else 6)))
    return {
        "plus_5": int(max(8, min(88, 45 + power * 28))),
        "plus_10": int(max(4, min(78, 30 + power * 26))),
        "minus_5": int(max(6, min(82, 38 - power * 22))),
        "minus_10": int(max(3, min(68, 22 - power * 18))),
        "stop_hit": int(max(5, min(72, 25 - power * 14))),
        "ratchet_hit": int(max(8, min(84, 35 + power * 30))),
    }


def _exit_forecast(base: float, instrument: str) -> dict[str, Any]:
    if instrument == "OPTION":
        tiers = [
            {"trigger_pct": 25, "locked_floor_pct": 5, "probability": min(86, max(10, int(35 + base * 0.9)))},
            {"trigger_pct": 50, "locked_floor_pct": 25, "probability": min(76, max(6, int(26 + base * 0.65)))},
            {"trigger_pct": 75, "locked_floor_pct": 50, "probability": min(66, max(4, int(18 + base * 0.45)))},
            {"trigger_pct": 100, "locked_floor_pct": 75, "probability": min(56, max(3, int(12 + base * 0.35)))},
            {"trigger_pct": 150, "locked_floor_pct": 120, "probability": min(42, max(2, int(8 + base * 0.22)))},
            {"trigger_pct": 200, "locked_floor_pct": 150, "probability": min(34, max(1, int(5 + base * 0.16)))},
        ]
        return {"style": "NO_TP_RATCHET", "hard_stop_pct": -20, "tiers": tiers}
    return {"style": "PM_EQUITY_STOP_RATCHET", "hard_stop_pct": None, "tiers": []}


def _kronos_score(
    confidence: int,
    score: float | None,
    aligned: bool,
    probs: dict[str, int],
    risk_flags: int = 0,
    forecast_abs: float = 0.0,
) -> int:
    """Composite Kronos confidence.

    Tuned to avoid rewarding big-but-sloppy forecasts. PM alignment and Case
    Score carry more weight; raw forecast size helps only modestly; risk flags
    pull the score down.
    """
    case_component = ((score or 5.0) / 10.0) * 36.0
    confidence_component = confidence * 0.22
    ratchet_component = probs.get("ratchet_hit", 0) * 0.16
    alignment_component = 14.0 if aligned else -12.0
    magnitude_component = min(8.0, abs(forecast_abs) * 0.35)
    risk_penalty = min(24.0, risk_flags * 7.0)
    raw = case_component + confidence_component + ratchet_component + alignment_component + magnitude_component - risk_penalty
    return int(max(0, min(100, round(raw))))


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
            "quantity": p.get("qty") or p.get("quantity") or p.get("shares"),
            "market_value": _num(p.get("market_value") or p.get("marketValue") or p.get("notional"), 0.0),
            "unrealized_pct": _pct(p.get("unrealized_plpc") or p.get("unrealized_pct")),
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
            "unrealized_pct": _pct(risk.get("pnl_pct") or p.get("unrealized_plpc") or p.get("unrealized_pct")),
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
    market = await market_forecast()
    scan_rows = _rows(ctx.get("scan"))
    scan_by_ticker = {_ticker(r.get("ticker") or r.get("symbol")): r for r in scan_rows}
    pm_rows = _pm_rows(ctx.get("pm") if isinstance(ctx.get("pm"), dict) else {})
    pm_by_ticker = {_ticker(r.get("ticker") or r.get("symbol")): r for r in pm_rows}
    forecasts = []
    cumulative_low = 0.0
    cumulative_base = 0.0
    cumulative_high = 0.0
    for pos in _instrument_rows(ctx):
        signal = scan_by_ticker.get(pos["ticker"], {})
        pm_row = pm_by_ticker.get(pos["ticker"], {})
        pm_action = str(pm_row.get("action") or pm_row.get("route") or pm_row.get("decision") or signal.get("pm_action") or signal.get("pm_route") or "UNMAPPED").upper()
        if not pm_row and not signal and (pos.get("market_value") or pos.get("quantity")):
            pm_action = "HELD_NOT_IN_LATEST_PM"
        score = _score(signal, pm_row)
        bias = _bias(score, pm_action, pos["instrument"], pos.get("unrealized_pct"), pos.get("risk") or {})
        confidence = int(max(18, min(92, 38 + ((score or 5) * 3) + (12 if pm_row else 0) + (10 if signal else 0) - (5 if pos["instrument"] == "OPTION" else 0))))
        aligned = _aligned(pm_action, bias["label"]) or _action_allows_position(pm_action, pos["instrument"])
        probs = _probabilities(bias["base"], pos["instrument"])
        tripwires = _tripwires(pos, score, pm_action)
        kscore = _kronos_score(confidence, score, aligned, probs, len(tripwires), bias["base"])
        mv = pos.get("market_value") or 0.0
        cumulative_low += mv * (bias["bear"] / 100)
        cumulative_base += mv * (bias["base"] / 100)
        cumulative_high += mv * (bias["bull"] / 100)
        row = {
            **pos,
            "pm_action": pm_action,
            "case_score": score,
            "forecast_bias": bias["label"],
            "forecast_pct": round(bias["base"], 2),
            "bear_pct": round(bias["bear"], 2),
            "bull_pct": round(bias["bull"], 2),
            "confidence": confidence,
            "kronos_score": kscore,
            "aligned_with_pm": aligned,
            "attribution": _attribution(score, pm_action, signal, pos["instrument"], pos.get("risk") or {}),
            "horizons": _horizons(bias["base"], pos["instrument"]),
            "probabilities": probs,
            "exit_forecast": _exit_forecast(bias["base"], pos["instrument"]),
            "tripwires": tripwires,
            "catalysts": _catalysts(signal),
        }
        forecasts.append(row)

    forecasts.sort(key=lambda r: r.get("kronos_score") or 0, reverse=True)
    disagreements = [
        r for r in forecasts
        if not r.get("aligned_with_pm") and r.get("pm_action") not in {"UNMAPPED", "HELD_NOT_IN_LATEST_PM"}
    ]
    payload = {
        "ok": True,
        "generated_at": _now().isoformat(),
        "snapshot_key": None,
        "model_mode": "proxy_live_advisory",
        "read_only": True,
        "market_forecast": market,
        "portfolio_day_cone": {
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
            "stale_position_context": sum(1 for r in forecasts if r.get("pm_action") == "HELD_NOT_IN_LATEST_PM"),
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
                    "ticker": r["ticker"],
                    "instrument": r["instrument"],
                    "contract": r.get("contract"),
                    "pm_action": r["pm_action"],
                    "forecast_bias": r["forecast_bias"],
                    "kronos_score": r["kronos_score"],
                    "forecast_pct": r["forecast_pct"],
                    "generated_at": payload["generated_at"],
                    "status": "OPEN_AUDIT",
                    })},
                    upsert=True,
                )
        except Exception as exc:
            logger.debug("kronos persistence skipped: %s", exc)
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


async def disagreement_performance(limit: int = 200) -> dict[str, Any]:
    db = get_db()
    rows = await db.kronos_pm_disagreements.find({}, {"_id": 0}).sort("generated_at", -1).to_list(limit)
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = f"{row.get('forecast_bias')} vs {row.get('pm_action')}"
        g = grouped.setdefault(key, {"setup": key, "count": 0, "open_audits": 0})
        g["count"] += 1
        if row.get("status") == "OPEN_AUDIT":
            g["open_audits"] += 1
    return {
        "rows": rows,
        "summary": list(grouped.values()),
        "note": "Performance resolves as future P/L records mature; open audits are retained for review.",
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


async def candle_accuracy(limit: int = 800, persist: bool = False) -> dict[str, Any]:
    """Score persisted candle forecasts against the next available OHLCV candle.

    Pending rows stay pending until a future candle exists. This makes Kronos'
    accuracy display falsifiable without mutating the original forecast.
    """
    db = get_db()
    rows = await db.kronos_candle_predictions.find(
        {"ok": True},
        {"_id": 0},
    ).sort("generated_at", -1).to_list(max(50, min(int(limit or 800), 2500)))
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (_ticker(row.get("symbol")) or "SPY", str(row.get("timeframe") or "5m").lower())
        grouped.setdefault(key, []).append(row)

    scored: list[dict[str, Any]] = []
    pending = 0
    try:
        from . import london_strategic_edge as lse
    except Exception as exc:
        return {"ok": False, "error": f"lse_unavailable:{exc}", "sample": 0, "pending": len(rows)}

    for (symbol, timeframe), preds in grouped.items():
        try:
            payload = await lse.candles(symbol, timeframe=timeframe, limit=500, order="asc")
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
            generated = _parse_dt(pred.get("generated_at") or pred.get("created_at"))
            base = _num((pred.get("features") or {}).get("last_close") or (pred.get("predicted_next_candle") or {}).get("open"))
            if generated is None or base is None or base <= 0:
                pending += 1
                continue
            actual_candle = next((c for dt, c in candle_pairs if dt > generated), None)
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
        "source": "kronos_candle_predictions + next raw OHLCV candle",
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
    latest = await db.kronos_forecast_snapshots.find_one({}, {"_id": 0}, sort=[("generated_at", -1)])
    latest_key = (latest or {}).get("snapshot_key") or _snapshot_key((latest or {}).get("generated_at") or _now().isoformat())
    disagreements = await db.kronos_pm_disagreements.count_documents({
        "status": "OPEN_AUDIT",
        "audit_id": {"$regex": f"^{re.escape(str(latest_key))}"},
    })
    age = _age_minutes((latest or {}).get("generated_at"))
    summary = (latest or {}).get("summary") or {}
    now = _now()
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    end_day = calendar.monthrange(now.year, now.month)[1]
    end = datetime(now.year, now.month, end_day, 23, 59, 59, tzinfo=timezone.utc)
    scored_days = await db.kronos_forecast_snapshots.count_documents(
        {"generated_at": {"$gte": start.isoformat(), "$lte": end.isoformat()}}
    )
    health = _freshness_status(age)
    pm_coverage = "FULL" if not summary.get("unmapped_pm", 0) else "PARTIAL"
    try:
        accuracy = await candle_accuracy(limit=600, persist=False)
        proof = accuracy.get("overall") if accuracy.get("ok") else {}
    except Exception as exc:
        logger.debug("kronos status accuracy degraded: %s", exc)
        proof = {}
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
        "open_disagreement_audits": disagreements,
        "calendar": {
            "direction_win_rate_pct": None,
            "cone_win_rate_pct": None,
            "scored_days": scored_days,
        },
        "proof": proof,
    }


async def refresh_snapshot() -> dict[str, Any]:
    payload = await forecast(persist=True)
    stat = await status()
    return {"ok": True, "forecast": payload, "status": stat}


async def calendar_month(year: int, month: int) -> dict[str, Any]:
    """Kronos accountability calendar.

    One row per calendar day. Forecasts are the latest persisted Kronos
    snapshot for that day; outcomes use SPY daily close movement plus the
    terminal benchmark curve when available.
    """
    month = max(1, min(12, int(month)))
    year = int(year)
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end_day = calendar.monthrange(year, month)[1]
    end = datetime(year, month, end_day, 23, 59, 59, tzinfo=timezone.utc)
    db = get_db()
    snapshots = await db.kronos_forecast_snapshots.find(
        {"generated_at": {"$gte": start.isoformat(), "$lte": end.isoformat()}},
        {"_id": 0},
    ).sort("generated_at", 1).to_list(500)

    by_day: dict[str, dict[str, Any]] = {}
    for snap in snapshots:
        day = str(snap.get("generated_at") or snap.get("created_at") or "")[:10]
        if day:
            by_day[day] = snap

    try:
        from . import pnl_tracker, pricer
        days_back = max(45, (datetime.now(timezone.utc).date() - start.date()).days + 10)
        benchmark = await pnl_tracker.daily_total_vs_spy_curve(days=days_back)
        total_by_day = {r["date"]: r for r in benchmark.get("curve") or []}
        spy_hist = await pricer.get_history("SPY", days=days_back + 10)
    except Exception as exc:
        logger.debug("kronos calendar outcome source degraded: %s", exc)
        total_by_day = {}
        spy_hist = {}

    sorted_spy_dates = sorted(spy_hist.keys())
    out = []
    for day_num in range(1, end_day + 1):
      day = f"{year:04d}-{month:02d}-{day_num:02d}"
      snap = by_day.get(day)
      market = (snap or {}).get("market_forecast") or {}
      cone = (snap or {}).get("portfolio_day_cone") or {}
      forecast_pct = _num(market.get("forecast_pct"))
      cone_low = _num(market.get("cone_low_pct"))
      cone_high = _num(market.get("cone_high_pct"))
      total_row = total_by_day.get(day) or {}
      spy_actual = _spy_day_return(day, sorted_spy_dates, spy_hist)
      total_actual = _num(total_row.get("terminal_total_pct"))
      verdict = _calendar_verdict(forecast_pct, spy_actual, total_actual)
      direction_win = _direction_win(forecast_pct, spy_actual)
      cone_win = _cone_win(cone_low, cone_high, spy_actual)
      out.append({
          "date": day,
          "has_prediction": bool(snap),
          "status": verdict["status"],
          "score": verdict["score"],
          "direction_win": direction_win,
          "cone_win": cone_win,
          "spy_prediction_pct": forecast_pct,
          "spy_actual_pct": spy_actual,
          "spy_cone_low_pct": cone_low,
          "spy_cone_high_pct": cone_high,
          "fund_prediction_usd": _num(cone.get("base_usd")),
          "fund_cone_low_usd": _num(cone.get("low_usd")),
          "fund_cone_high_usd": _num(cone.get("high_usd")),
          "fund_actual_pct": total_actual,
          "relative_pct": _num(total_row.get("relative_pct")),
          "confidence": _num(market.get("confidence")),
          "snapshot_at": (snap or {}).get("generated_at"),
      })

    years = await _calendar_years(db)
    evaluated_direction = [d for d in out if d.get("direction_win") is not None]
    evaluated_cone = [d for d in out if d.get("cone_win") is not None]
    direction_wins = sum(1 for d in evaluated_direction if d.get("direction_win"))
    cone_wins = sum(1 for d in evaluated_cone if d.get("cone_win"))
    return {
        "ok": True,
        "year": year,
        "month": month,
        "month_label": start.strftime("%B %Y"),
        "days": out,
        "summary": {
            "direction_wins": direction_wins,
            "direction_losses": len(evaluated_direction) - direction_wins,
            "direction_win_rate_pct": round(direction_wins / len(evaluated_direction) * 100, 1) if evaluated_direction else None,
            "cone_wins": cone_wins,
            "cone_losses": len(evaluated_cone) - cone_wins,
            "cone_win_rate_pct": round(cone_wins / len(evaluated_cone) * 100, 1) if evaluated_cone else None,
        },
        "available_years": years or [year],
        "source": "kronos_forecast_snapshots + performance benchmark curve",
    }


def _spy_day_return(day: str, sorted_dates: list[str], history: dict[str, float]) -> float | None:
    if day not in history:
        return None
    idx = sorted_dates.index(day)
    if idx <= 0:
        return None
    prev = history.get(sorted_dates[idx - 1])
    cur = history.get(day)
    if not prev or cur is None:
        return None
    return round((cur - prev) / prev * 100.0, 2)


def _calendar_verdict(forecast_pct: float | None, spy_actual_pct: float | None, total_actual_pct: float | None) -> dict[str, Any]:
    if forecast_pct is None:
        return {"status": "NO_FORECAST", "score": 0}
    if spy_actual_pct is None:
        return {"status": "PENDING", "score": 0}
    if abs(forecast_pct) < 0.06:
        return {"status": "WATCH", "score": 50}
    direction_ok = (forecast_pct >= 0 and spy_actual_pct >= 0) or (forecast_pct < 0 and spy_actual_pct < 0)
    total_ok = total_actual_pct is None or (forecast_pct >= 0 and total_actual_pct >= 0) or (forecast_pct < 0 and total_actual_pct < 0)
    if direction_ok and total_ok:
        return {"status": "GOOD", "score": 85}
    if direction_ok:
        return {"status": "WATCH", "score": 62}
    return {"status": "BAD", "score": 20}


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
    scan_rows = _rows(ctx.get("scan"))
    pm_rows = _pm_rows(ctx.get("pm") if isinstance(ctx.get("pm"), dict) else {})
    row = next((r for r in scan_rows if _ticker(r.get("ticker") or r.get("symbol")) == t), {})
    pm_row = next((r for r in pm_rows if _ticker(r.get("ticker") or r.get("symbol")) == t), {})

    pm_action = str(
        pm_row.get("action")
        or pm_row.get("route")
        or pm_row.get("decision")
        or row.get("pm_action")
        or row.get("pm_route")
        or "UNMAPPED"
    ).upper()
    instrument = str(pm_row.get("instrument") or pm_row.get("asset_class") or "SCAN").upper()
    if instrument not in {"EQUITY", "OPTION", "BOTH", "SCAN"}:
        instrument = "SCAN"
    score = _score(row, pm_row)
    bias = _bias(score, pm_action, "OPTION" if instrument == "OPTION" else "EQUITY", None, {})
    confidence = int(max(18, min(92, 36 + ((score or 5) * 3.2) + (14 if pm_row else 0) + (10 if row else 0))))
    aligned = _aligned(pm_action, bias["label"])
    probs = _probabilities(bias["base"], "OPTION" if instrument == "OPTION" else "EQUITY")
    match = {
        "ticker": t,
        "instrument": instrument,
        "pm_action": pm_action,
        "case_score": score,
        "forecast_bias": bias["label"],
        "forecast_pct": bias["base"],
        "bear_pct": bias["bear"],
        "bull_pct": bias["bull"],
        "confidence": confidence,
        "kronos_score": _kronos_score(confidence, score, aligned, probs, 0, bias["base"]),
        "aligned_with_pm": aligned,
        "attribution": _attribution(score, pm_action, row, instrument, {}),
        "horizons": _horizons(bias["base"], "OPTION" if instrument == "OPTION" else "EQUITY"),
        "probabilities": probs,
        "exit_forecast": _exit_forecast(bias["base"], "OPTION" if instrument == "OPTION" else "EQUITY"),
        "tripwires": _tripwires({"instrument": "EQUITY", "unrealized_pct": None, "risk": {}}, score, pm_action),
        "catalysts": _catalysts(row),
    }
    return {"ok": True, "ticker": t, "battle_card": match, "generated_at": _now().isoformat()}


def build_morning_message(payload: dict[str, Any]) -> str:
    market = payload.get("market_forecast") or {}
    cone = payload.get("portfolio_day_cone") or {}
    direction = market.get("direction", "UNKNOWN")
    pct = market.get("forecast_pct", 0)
    sign = "+" if _num(pct, 0) >= 0 else ""
    lines = [
        "<b>CASE CAPITAL | KRONOS MORNING BRIEF</b>",
        f"<code>{datetime.now(ZoneInfo('America/New_York')).strftime('%b %d %H:%M ET')}</code>",
        "",
        f"<b>SPY today:</b> {direction} {sign}{pct}% "
        f"(cone {market.get('cone_low_pct')}% to {market.get('cone_high_pct')}%)",
        f"Confidence: {market.get('confidence', 0)}/100",
        "",
        "<b>Open-position day P/L cone:</b>",
        f"Low: ${cone.get('low_usd', 0)}",
        f"Base: ${cone.get('base_usd', 0)}",
        f"High: ${cone.get('high_usd', 0)}",
        "",
        f"PM disagreements: {(payload.get('summary') or {}).get('pm_disagreements', 0)}",
        "<i>Advisory only. Kronos does not execute or override PM.</i>",
    ]
    return "\n".join(lines)


async def dispatch_morning_forecast(force: bool = False) -> dict[str, Any]:
    from . import telegram_service

    payload = await forecast(persist=True)
    sent = await telegram_service.send_message(build_morning_message(payload))
    try:
        await get_db().telegram_reports.insert_one(stamped({
            "type": "kronos_morning_forecast",
            "sent": bool(sent),
            "force": force,
            "payload": {
                "market_forecast": payload.get("market_forecast"),
                "portfolio_day_cone": payload.get("portfolio_day_cone"),
                "summary": payload.get("summary"),
            },
        }))
    except Exception:
        pass
    return {"ok": bool(sent), "sent": bool(sent), "forecast": payload}
