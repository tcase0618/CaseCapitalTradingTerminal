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
    direction = "UP" if base > 0.12 else "DOWN" if base < -0.12 else "FLAT"
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
    if summary.get("unmapped_pm", 0) and summary.get("positions", 0):
        health = "DEGRADED" if health == "LIVE" else health
    return {
        "ok": True,
        "health": health,
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
    if abs(forecast_pct) < 0.12:
        return {"status": "WATCH", "score": 50}
    direction_ok = (forecast_pct >= 0 and spy_actual_pct >= 0) or (forecast_pct < 0 and spy_actual_pct < 0)
    total_ok = total_actual_pct is None or (forecast_pct >= 0 and total_actual_pct >= 0) or (forecast_pct < 0 and total_actual_pct < 0)
    if direction_ok and total_ok:
        return {"status": "GOOD", "score": 85}
    if direction_ok:
        return {"status": "WATCH", "score": 62}
    return {"status": "BAD", "score": 20}


def _direction_win(forecast_pct: float | None, spy_actual_pct: float | None) -> bool | None:
    if forecast_pct is None or spy_actual_pct is None or abs(forecast_pct) < 0.12:
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
        "<b>CASE CAPITAL KRONOS MORNING FORECAST</b>",
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
