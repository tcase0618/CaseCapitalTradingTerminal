"""Forward metrics and benchmark proof layer.

Read-only service. It grades persisted scan/trade rows and compares forward
returns against SPY when enough data exists. No execution and no learning
mutation happen here.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import get_db
from . import pricer


FORWARD_WINDOWS = (7, 30, 90)
MIN_SHARPE_N = 50


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _num(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        n = float(v)
        return n if math.isfinite(n) else None
    except Exception:
        return None


def _parse_dt(v: Any) -> datetime | None:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d
    except Exception:
        return None


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    mid = len(s) // 2
    if len(s) % 2:
        return round(s[mid], 4)
    return round((s[mid - 1] + s[mid]) / 2, 4)


def _std(xs: list[float]) -> float | None:
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _sharpe(xs: list[float]) -> dict[str, Any]:
    if len(xs) < MIN_SHARPE_N:
        return {"value": None, "n": len(xs), "reason": f"n<{MIN_SHARPE_N}"}
    sd = _std(xs)
    if not sd:
        return {"value": None, "n": len(xs), "reason": "zero_variance"}
    daily_like = [x / 100.0 for x in xs]
    value = (sum(daily_like) / len(daily_like)) / (sd / 100.0)
    return {"value": round(value, 3), "n": len(xs), "reason": None}


def _distribution(xs: list[float]) -> dict[str, Any]:
    if not xs:
        return {
            "n": 0,
            "avg_pct": None,
            "median_pct": None,
            "win_rate_pct": None,
            "expectancy_pct": None,
            "best_pct": None,
            "worst_pct": None,
            "sharpe": _sharpe(xs),
        }
    wins = [x for x in xs if x > 0]
    losses = [x for x in xs if x <= 0]
    win_rate = len(wins) / len(xs) * 100.0
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    expectancy = (len(wins) / len(xs)) * avg_win + (len(losses) / len(xs)) * avg_loss
    return {
        "n": len(xs),
        "avg_pct": _mean(xs),
        "median_pct": _median(xs),
        "win_rate_pct": round(win_rate, 2),
        "expectancy_pct": round(expectancy, 4),
        "best_pct": round(max(xs), 4),
        "worst_pct": round(min(xs), 4),
        "sharpe": _sharpe(xs),
    }


async def _spy_return(entry_date: str, days: int) -> float | None:
    start = await pricer.get_close_on_date("SPY", entry_date)
    try:
        target_date = (datetime.fromisoformat(entry_date).date() + timedelta(days=days)).isoformat()
    except Exception:
        return None
    end = await pricer.get_close_on_date("SPY", target_date)
    if not start or not end:
        return None
    return round((end - start) / start * 100.0, 4)


async def _forward_rows(limit: int) -> list[dict[str, Any]]:
    db = get_db()
    rows = await db.signal_performance.find({}, {"_id": 0}).sort("ts", -1).to_list(limit)
    out: list[dict[str, Any]] = []
    for r in rows:
        date = r.get("date")
        if not date:
            continue
        item = {
            "ticker": r.get("ticker"),
            "date": date,
            "signals": r.get("signals") or [],
            "signal_score": r.get("signal_score"),
            "regime": r.get("regime"),
            "returns": {},
            "spy": {},
            "alpha": {},
        }
        for days in FORWARD_WINDOWS:
            ret = _num(r.get(f"return_{days}d"))
            item["returns"][f"{days}d"] = ret
            if ret is None:
                continue
            spy = await _spy_return(date, days)
            item["spy"][f"{days}d"] = spy
            item["alpha"][f"{days}d"] = round(ret - spy, 4) if spy is not None else None
        out.append(item)
    return out


def _bucket_signal_returns(rows: list[dict[str, Any]], window: str = "30d") -> list[dict[str, Any]]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        value = r.get("returns", {}).get(window)
        if value is None:
            continue
        sigs = r.get("signals") or ["UNKNOWN"]
        for sig in sigs:
            buckets[str(sig)].append(float(value))
    ranked = [
        {"signal": sig, **_distribution(vals)}
        for sig, vals in buckets.items()
    ]
    ranked.sort(key=lambda x: (x["n"], x.get("expectancy_pct") or -999), reverse=True)
    return ranked[:30]


def _aggregate_forward(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for days in FORWARD_WINDOWS:
        key = f"{days}d"
        terminal = [float(r["returns"][key]) for r in rows if r.get("returns", {}).get(key) is not None]
        spy = [float(r["spy"][key]) for r in rows if r.get("spy", {}).get(key) is not None]
        alpha = [float(r["alpha"][key]) for r in rows if r.get("alpha", {}).get(key) is not None]
        out[key] = {
            "terminal": _distribution(terminal),
            "spy": _distribution(spy),
            "alpha_vs_spy": _distribution(alpha),
            "coverage": {
                "terminal_n": len(terminal),
                "spy_n": len(spy),
                "alpha_n": len(alpha),
            },
        }
    return out


async def _trade_stats(limit: int) -> dict[str, Any]:
    db = get_db()
    trades = await db.tf_trades.find({}, {"_id": 0}).sort("submitted_at", -1).to_list(limit)
    closed = [t for t in trades if str(t.get("status") or "").upper() in {"CLOSED", "FILLED_CLOSED", "EXITED"}]
    pnl_vals: list[float] = []
    for t in closed:
        for key in ("realized_pl_pct", "pnl_pct", "return_pct"):
            v = _num(t.get(key))
            if v is not None:
                pnl_vals.append(v)
                break
    by_regime: dict[str, list[float]] = defaultdict(list)
    for t in closed:
        v = None
        for key in ("realized_pl_pct", "pnl_pct", "return_pct"):
            v = _num(t.get(key))
            if v is not None:
                break
        if v is not None:
            by_regime[str(t.get("regime") or "unknown")].append(v)
    return {
        "total_records": len(trades),
        "closed_with_pct": len(pnl_vals),
        "distribution": _distribution(pnl_vals),
        "by_regime": [{"regime": k, **_distribution(v)} for k, v in sorted(by_regime.items())],
    }


async def summary(limit: int = 2000) -> dict[str, Any]:
    rows = await _forward_rows(limit=limit)
    db = get_db()
    latest_scan = await db.scan_results.find_one({}, {"_id": 0, "finished_at": 1, "results": 1}, sort=[("finished_at", -1)])
    latest_regime = None
    try:
        from . import trade_floor
        latest_regime = await trade_floor.regime_status()
    except Exception:
        latest_regime = {"status": "unknown", "reason": "regime_unavailable"}
    scan_rows = (latest_scan or {}).get("results") or []
    pead_count = sum(1 for r in scan_rows if (r.get("pead") or {}).get("active"))
    return {
        "ok": True,
        "generated_at": _now().isoformat(),
        "source": "persisted_forward_rows",
        "latest_scan_finished_at": (latest_scan or {}).get("finished_at"),
        "latest_regime": latest_regime,
        "latest_scan_tags": {
            "rows": len(scan_rows),
            "pead_confirmed": pead_count,
            "regime_tagged": sum(1 for r in scan_rows if r.get("regime")),
        },
        "forward": _aggregate_forward(rows),
        "signals_30d": _bucket_signal_returns(rows, "30d"),
        "trades": await _trade_stats(limit=limit),
        "notes": [
            "Sharpe is null until sample size reaches 50.",
            "SPY alpha only appears where SPY close history exists for the same forward window.",
            "This service is read-only and does not mutate learning weights or execution state.",
        ],
    }
