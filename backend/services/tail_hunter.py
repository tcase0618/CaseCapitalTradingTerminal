"""Tail Hunter options sleeve.

This is separate from the grind options lane. It hunts small, convex call shots
with hard portfolio limits, no hard stop, tiered profit taking, and a wide
ratchet after the first double.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import get_db, log_activity, stamped

TAIL_SHOT_SIZE_USD = 125.0
TAIL_MAX_CONCURRENT = 3
TAIL_MAX_SHOTS_PER_WEEK = 4
TAIL_TAKE_PROFIT_PCT = 100.0
TAIL_TAKE_PROFIT_SELL_FRACTION = 0.5
TAIL_RATCHET_TRAIL_PCT = 40.0
TAIL_DTE_MIN = 7
TAIL_DTE_MAX = 21
TAIL_DELTA_MIN = 0.10
TAIL_DELTA_MAX = 0.25
TAIL_IV_RANK_MAX = 50.0
TAIL_BANK_TO_GRIND_PCT = 0.50
TAIL_MAX_SPREAD_PCT = 0.20
TAIL_MIN_VOLUME = 50
TAIL_MIN_OPEN_INTEREST = 100
OCC_SYMBOL_RE = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        n = float(v)
        if math.isnan(n) or math.isinf(n):
            return default
        return n
    except (TypeError, ValueError):
        return default


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _signals(row: dict[str, Any]) -> set[str]:
    raw = row.get("signals") or []
    if isinstance(raw, dict):
        return {str(k).upper() for k, v in raw.items() if v}
    return {str(s).upper() for s in raw}


def _ticker(row: dict[str, Any]) -> str:
    return str(row.get("ticker") or row.get("symbol") or "").upper()


def _target_date(row: dict[str, Any]) -> str | None:
    tt = row.get("time_target") or {}
    for value in (
        tt.get("target_date"),
        row.get("catalyst_date"),
        row.get("earnings_date"),
        row.get("next_earnings_date"),
    ):
        if value:
            return str(value)
    return None


def _parse_date(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _dte_from_expiration(value: Any) -> int:
    dt = _parse_date(value)
    if not dt:
        return 0
    return max(0, (dt.date() - datetime.now(timezone.utc).date()).days)


def _dte_from_occ(symbol: str) -> int:
    m = OCC_SYMBOL_RE.match(str(symbol or "").upper())
    if not m:
        return 0
    try:
        exp = datetime.strptime(m.group(2), "%y%m%d").replace(tzinfo=timezone.utc)
        return max(0, (exp.date() - datetime.now(timezone.utc).date()).days)
    except Exception:
        return 0


def _has_dated_catalyst(row: dict[str, Any]) -> bool:
    dt = _parse_date(_target_date(row))
    if not dt:
        return False
    dte = max(0, (dt.date() - datetime.now(timezone.utc).date()).days)
    return TAIL_DTE_MIN <= dte <= TAIL_DTE_MAX


def _has_flow(row: dict[str, Any], opts: dict[str, Any] | None = None) -> bool:
    sigs = _signals(row)
    flow = ((opts or {}).get("flow") or row.get("flow") or {})
    return (
        "UNUSUAL_FLOW" in sigs
        or "CALL_SWEEP" in sigs
        or bool(flow.get("call_sweep"))
        or bool(flow.get("unusual_calls"))
    )


def _has_squeeze_or_float(row: dict[str, Any], chain: dict[str, Any] | None = None) -> bool:
    sq = row.get("squeeze") or {}
    short_float = _num(row.get("short_float") or row.get("short_interest_pct"))
    float_shares = _num(row.get("float_shares") or row.get("float"))
    return (
        _num(sq.get("score")) > 75
        or (short_float >= 10 and 0 < float_shares <= 25_000_000)
        or _has_oi_wall(chain)
    )


def _has_oi_wall(chain: dict[str, Any] | None) -> bool:
    try:
        calls = (chain or {}).get("calls")
        spot = _num((chain or {}).get("price"))
        if calls is None or getattr(calls, "empty", True) or spot <= 0:
            return False
        rows = calls[calls["strike"].astype(float) > spot]
        if rows.empty or "openInterest" not in rows.columns:
            return False
        oi = rows["openInterest"].fillna(0).astype(float)
        median = float(oi.median()) if len(oi) else 0.0
        mx = float(oi.max()) if len(oi) else 0.0
        return mx >= TAIL_MIN_OPEN_INTEREST and (median <= 0 or mx >= median * 2)
    except Exception:
        return False


def _iv_rank(chain: dict[str, Any] | None, opts: dict[str, Any] | None = None) -> float:
    return _num((chain or {}).get("iv_rank") if chain else (opts or {}).get("iv_rank"), 50.0)


def _candidate_id(ticker: str, scan_finished_at: str | None, symbol: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", f"tail-{ticker}-{scan_finished_at or _now()}-{symbol}")
    return base[:160]


def _row_value(row: Any, key: str, fallback: Any = None) -> Any:
    try:
        value = row.get(key)
    except AttributeError:
        value = getattr(row, key, fallback)
    return fallback if value is None else value


def tail_contract_from_chain(chain: dict[str, Any]) -> dict[str, Any] | None:
    calls = (chain or {}).get("calls")
    if calls is None or getattr(calls, "empty", True):
        return None
    viable: list[dict[str, Any]] = []
    for _, row in calls.iterrows():
        bid = _num(_row_value(row, "bid"))
        ask = _num(_row_value(row, "ask"))
        if bid <= 0 or ask <= 0:
            continue
        spread = max(0.0, ask - bid)
        spread_pct = spread / ask if ask > 0 else 1.0
        if spread_pct > TAIL_MAX_SPREAD_PCT:
            continue
        delta = abs(_num(_row_value(row, "delta")))
        if delta < TAIL_DELTA_MIN or delta > TAIL_DELTA_MAX:
            continue
        dte = _int(_row_value(row, "days_to_exp")) or _dte_from_expiration(_row_value(row, "expiration") or (chain or {}).get("expiration"))
        if dte < TAIL_DTE_MIN or dte > TAIL_DTE_MAX:
            continue
        oi = _int(_row_value(row, "openInterest"))
        volume = _int(_row_value(row, "volume"))
        if oi < TAIL_MIN_OPEN_INTEREST and volume < TAIL_MIN_VOLUME:
            continue
        symbol = str(_row_value(row, "contractSymbol") or _row_value(row, "symbol") or "").upper()
        if not symbol:
            continue
        contracts = int(TAIL_SHOT_SIZE_USD // (ask * 100))
        if contracts < 1:
            continue
        strike = _num(_row_value(row, "strike"))
        viable.append({
            "kind": "single_leg",
            "symbol": symbol,
            "contractSymbol": symbol,
            "strike": round(strike, 2),
            "expiration": str(_row_value(row, "expiration") or (chain or {}).get("expiration") or ""),
            "days_to_expiration": dte,
            "premium": round(ask, 2),
            "bid": round(bid, 2),
            "ask": round(ask, 2),
            "spread": round(spread, 2),
            "spread_pct": round(spread_pct * 100, 2),
            "delta": round(delta, 4),
            "iv": round(_num(_row_value(row, "impliedVolatility")), 4),
            "open_interest": oi,
            "volume": volume,
            "max_loss": round(ask * 100, 2),
            "data_provider": (chain or {}).get("data_provider"),
            "data_feed": (chain or {}).get("data_feed"),
            "data_quality": (chain or {}).get("data_quality"),
            "provider_delta_present": True,
            "open_interest_source": "reported" if oi else "unavailable",
            "type": "C",
            "contracts_at_budget": contracts,
        })
    if not viable:
        return None
    viable.sort(key=lambda r: (abs(r["delta"] - 0.18), r["spread_pct"], -r["open_interest"], r["ask"]))
    return viable[0]


def gate_check(
    row: dict[str, Any],
    pm_row: dict[str, Any] | None = None,
    instrument: dict[str, Any] | None = None,
    opts: dict[str, Any] | None = None,
    chain: dict[str, Any] | None = None,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if _has_dated_catalyst(row):
        reasons.append("dated catalyst inside 7-21 DTE window")
    if _iv_rank(chain, opts) < TAIL_IV_RANK_MAX:
        reasons.append("IV rank under 50")
    if _has_squeeze_or_float(row, chain):
        reasons.append("squeeze/low-float/upper-OI-wall pressure")
    if _has_flow(row, opts):
        reasons.append("unusual call flow or sweep signal")
    delta = abs(_num((instrument or {}).get("delta")))
    if TAIL_DELTA_MIN <= delta <= TAIL_DELTA_MAX:
        reasons.append("contract delta in 0.10-0.25 tail band")
    return len(reasons) >= 2, reasons


async def _latest_scan_rows() -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    db = get_db()
    scan = await db.scan_results.find_one({}, {"_id": 0}, sort=[("finished_at", -1)])
    return scan, (scan or {}).get("results") or []


async def build_tail_candidates(limit: int = 25, persist: bool = True) -> dict[str, Any]:
    from . import options_desk, options_engine, portfolio_manager

    db = get_db()
    scan, rows = await _latest_scan_rows()
    pm_rows = portfolio_manager.evaluate_rows(rows, equity=portfolio_manager.DEFAULT_EQUITY, mode="BALANCED")
    by_ticker = {r["ticker"]: r for r in pm_rows}
    ordered = sorted(
        rows,
        key=lambda r: float((by_ticker.get(_ticker(r)) or {}).get("pm_score") or r.get("score") or 0),
        reverse=True,
    )
    out: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for row in ordered[: max(limit * 3, limit)]:
        ticker = _ticker(row)
        pm = by_ticker.get(ticker) or {}
        if not ticker:
            continue
        action = str(pm.get("action") or "").upper()
        route = str(pm.get("route") or pm.get("expression") or "")
        if action in {"PASS", "REJECT"}:
            rejected.append({"ticker": ticker, "reason": "pm_rejected"})
            continue
        if route and route not in {"OPTION", "BOTH", "CALL", "OPTIONS"} and float(pm.get("pm_score") or 0) < 70:
            rejected.append({"ticker": ticker, "reason": "pm_not_option_or_high_score"})
            continue
        chain = await options_engine.get_options_data(ticker, _target_date(row))
        if not chain:
            rejected.append({"ticker": ticker, "reason": "chain_unavailable"})
            continue
        if chain.get("data_provider") != "ALPACA_OPTIONS" or not options_desk._execution_grade_allowed(chain.get("data_quality")):
            rejected.append({"ticker": ticker, "reason": "alpaca_execution_grade_chain_required"})
            continue
        instrument = tail_contract_from_chain(chain)
        if not instrument:
            rejected.append({"ticker": ticker, "reason": "no_tail_contract"})
            continue
        passed, reasons = gate_check(row, pm, instrument, row.get("options") or {}, chain)
        contracts = int(TAIL_SHOT_SIZE_USD // (_num(instrument.get("ask")) * 100)) if _num(instrument.get("ask")) > 0 else 0
        blocked: list[str] = []
        if not passed:
            blocked.append("tail gate requires 2+ exhibits")
        if not options_desk.OPTIONS_EXECUTION_ENABLED:
            blocked.append("options execution is disabled")
        if not options_desk.paper_only():
            blocked.append("options desk is not pointed at Alpaca paper")
        if contracts < 1:
            blocked.append("premium exceeds tail shot budget")
        candidate = stamped({
            "candidate_id": _candidate_id(ticker, (scan or {}).get("finished_at"), instrument.get("symbol")),
            "ticker": ticker,
            "route": "OPTION",
            "pm_action": action or "WATCH",
            "pm_score": round(_num(pm.get("pm_score") or pm.get("score")), 2),
            "risk_reward": _num(pm.get("risk_reward") or row.get("risk_reward")),
            "strategy": "TAIL_HUNTER_CALL",
            "strategy_reason": "Small capped call shot with convex payoff. No hard stop; exits are double, wide ratchet, or DTE drain.",
            "strategy_lane": {
                "lane": "TAIL_HUNTER",
                "risk_posture": "CAPPED_CONVEX_SHOT",
                "preferred_structure": "single_long_call",
                "reasons": reasons,
            },
            "lane": "TAIL",
            "direction": "BULL",
            "instrument": instrument,
            "expiration": instrument.get("expiration"),
            "contracts": contracts,
            "risk_budget": TAIL_SHOT_SIZE_USD,
            "max_loss": round(contracts * _num(instrument.get("ask")) * 100, 2),
            "data_provider": instrument.get("data_provider"),
            "data_quality": instrument.get("data_quality"),
            "data_feed": instrument.get("data_feed"),
            "iv_rank": _iv_rank(chain, row.get("options") or {}),
            "tail_gate": {"passed": passed, "count": len(reasons), "reasons": reasons},
            "blocked_reasons": blocked,
            "manual_fire_ready": not blocked,
            "scan_finished_at": (scan or {}).get("finished_at"),
            "tail_policy": {
                "shot_size_usd": TAIL_SHOT_SIZE_USD,
                "max_concurrent": TAIL_MAX_CONCURRENT,
                "max_shots_per_week": TAIL_MAX_SHOTS_PER_WEEK,
                "take_profit_pct": TAIL_TAKE_PROFIT_PCT,
                "take_profit_sell_fraction": TAIL_TAKE_PROFIT_SELL_FRACTION,
                "ratchet_trail_pct": TAIL_RATCHET_TRAIL_PCT,
                "hard_stop": "none",
            },
            "source": "tail_hunter.build_tail_candidates",
        })
        out.append(candidate)
        if len(out) >= limit:
            break
    if persist:
        await db.tail_hunter_candidates.delete_many({})
        if out:
            await db.tail_hunter_candidates.insert_many(out)
    return {
        "ok": True,
        "generated_at": _now(),
        "scan_finished_at": (scan or {}).get("finished_at") if scan else None,
        "candidates": out,
        "summary": {
            "candidates": len(out),
            "ready": len([c for c in out if c.get("manual_fire_ready")]),
            "rejected": len(rejected),
        },
        "rejected": rejected[:25],
        "policy": policy(),
    }


async def latest_tail_candidates() -> dict[str, Any]:
    db = get_db()
    rows = await db.tail_hunter_candidates.find({}, {"_id": 0}).sort("pm_score", -1).to_list(50)
    return {"ok": True, "generated_at": _now(), "candidates": rows, "status": await status(), "policy": policy()}


def policy() -> dict[str, Any]:
    return {
        "shot_size_usd": TAIL_SHOT_SIZE_USD,
        "max_concurrent": TAIL_MAX_CONCURRENT,
        "max_shots_per_week": TAIL_MAX_SHOTS_PER_WEEK,
        "take_profit_pct": TAIL_TAKE_PROFIT_PCT,
        "take_profit_sell_fraction": TAIL_TAKE_PROFIT_SELL_FRACTION,
        "ratchet_trail_pct": TAIL_RATCHET_TRAIL_PCT,
        "dte_window": [TAIL_DTE_MIN, TAIL_DTE_MAX],
        "delta_window": [TAIL_DELTA_MIN, TAIL_DELTA_MAX],
        "iv_rank_max": TAIL_IV_RANK_MAX,
        "bank_to_grind_pct": TAIL_BANK_TO_GRIND_PCT,
    }


async def _active_tail_count() -> int:
    from . import options_desk

    db = get_db()
    return await db.options_desk_trades.count_documents({
        "$or": [
            {"lane": "TAIL"},
            {"strategy_lane.lane": "TAIL_HUNTER"},
            {"candidate.strategy_lane.lane": "TAIL_HUNTER"},
        ],
        "status": {"$in": sorted(options_desk.OPTION_ACTIVE_STATUSES)},
    })


async def _shots_this_week() -> int:
    db = get_db()
    now = datetime.now(timezone.utc)
    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return await db.options_desk_orders.count_documents({
        "$or": [
            {"lane": "TAIL"},
            {"candidate.lane": "TAIL"},
            {"candidate.strategy_lane.lane": "TAIL_HUNTER"},
        ],
        "submitted_at": {"$gte": week_start.isoformat()},
    })


async def status() -> dict[str, Any]:
    active = await _active_tail_count()
    shots = await _shots_this_week()
    db = get_db()
    latest = await db.tail_hunter_risk_checks.find_one({}, {"_id": 0}, sort=[("checked_at", -1)])
    return {
        "ok": True,
        "active_tail": active,
        "shots_this_week": shots,
        "concurrent_remaining": max(0, TAIL_MAX_CONCURRENT - active),
        "weekly_remaining": max(0, TAIL_MAX_SHOTS_PER_WEEK - shots),
        "latest_risk_check": latest,
    }


async def execute_tail(candidate_id: str) -> dict[str, Any]:
    from . import options_desk

    db = get_db()
    active = await _active_tail_count()
    if active >= TAIL_MAX_CONCURRENT:
        return {"ok": False, "reason": "tail_max_concurrent_reached", "active_tail": active}
    shots = await _shots_this_week()
    if shots >= TAIL_MAX_SHOTS_PER_WEEK:
        return {"ok": False, "reason": "tail_weekly_shot_limit_reached", "shots_this_week": shots}
    candidate = await db.tail_hunter_candidates.find_one({"candidate_id": candidate_id}, {"_id": 0})
    if not candidate:
        return {"ok": False, "reason": "tail_candidate_not_found"}
    if not candidate.get("manual_fire_ready"):
        return {"ok": False, "reason": "tail_candidate_not_ready", "blocked": candidate.get("blocked_reasons"), "candidate": candidate}
    await db.options_desk_candidates.update_one({"candidate_id": candidate_id}, {"$set": candidate}, upsert=True)
    result = await options_desk.execute(candidate_id)
    if result.get("ok"):
        order_id = (result.get("order") or {}).get("id")
        marker = {
            "lane": "TAIL",
            "candidate.lane": "TAIL",
            "candidate.strategy_lane.lane": "TAIL_HUNTER",
            "tail_policy": policy(),
        }
        if order_id:
            await db.options_desk_orders.update_one({"order.id": order_id}, {"$set": marker})
        await log_activity(f"Tail Hunter order submitted: {candidate.get('ticker')} {candidate.get('instrument', {}).get('symbol')}", "success", result)
    return result


async def _active_tail_trades() -> list[dict[str, Any]]:
    from . import options_desk

    db = get_db()
    return await db.options_desk_trades.find({
        "$or": [
            {"lane": "TAIL"},
            {"strategy_lane.lane": "TAIL_HUNTER"},
            {"candidate.strategy_lane.lane": "TAIL_HUNTER"},
        ],
        "status": {"$in": sorted(options_desk.OPTION_ACTIVE_STATUSES)},
    }, {"_id": 0}).to_list(100)


async def monitor_tail_positions() -> dict[str, Any]:
    from . import options_desk

    db = get_db()
    live = await options_desk.positions()
    by_symbol = {str(p.get("symbol") or "").upper(): p for p in live.get("positions") or []}
    trades = await _active_tail_trades()
    checks: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    for trade in trades:
        symbol = str(trade.get("symbol") or "").upper()
        position = by_symbol.get(symbol)
        if not symbol or not position:
            continue
        snap = await options_desk._option_snapshot(symbol)
        context = options_desk._option_position_context(position, snap)
        pnl_pct = _num(context.get("pnl_pct"))
        peak = max(_num(trade.get("tail_peak_gain_pct")), pnl_pct)
        dte = _int(trade.get("dte_at_entry") or ((trade.get("candidate") or {}).get("instrument") or {}).get("days_to_expiration")) or _dte_from_occ(symbol)
        update = {"current_premium": context.get("current_premium"), "unrealized_pct": pnl_pct, "tail_peak_gain_pct": peak, "tail_last_checked_at": _now()}
        await db.options_desk_trades.update_one({"trade_id": trade.get("trade_id")}, {"$set": update})
        action = None
        qty = _int(position.get("qty"))
        if pnl_pct >= TAIL_TAKE_PROFIT_PCT and not trade.get("tail_tier1_taken") and qty > 0:
            sell_qty = max(1, int(qty * TAIL_TAKE_PROFIT_SELL_FRACTION))
            await db.options_desk_trades.update_one(
                {"trade_id": trade.get("trade_id")},
                {"$set": {"close_reason": "tail_take_profit_tier1", "tail_tier1_taken": True}},
            )
            close_result = await options_desk.close(symbol, qty=sell_qty)
            if close_result.get("ok"):
                await db.options_desk_trades.update_one(
                    {"trade_id": trade.get("trade_id")},
                    {"$set": {"status": "tail_take_profit_tier1_close_submitted", "close_order": close_result.get("order")}},
                )
            action = {"symbol": symbol, "action": "tail_take_profit_tier1", "qty": sell_qty, "result": close_result}
        elif trade.get("tail_tier1_taken") and peak >= TAIL_TAKE_PROFIT_PCT and pnl_pct <= peak - TAIL_RATCHET_TRAIL_PCT and qty > 0:
            await db.options_desk_trades.update_one({"trade_id": trade.get("trade_id")}, {"$set": {"close_reason": "tail_ratchet_trail"}})
            close_result = await options_desk.close(symbol)
            if close_result.get("ok"):
                await db.options_desk_trades.update_one(
                    {"trade_id": trade.get("trade_id")},
                    {"$set": {"status": "tail_ratchet_trail_close_submitted", "close_order": close_result.get("order")}},
                )
            action = {"symbol": symbol, "action": "tail_ratchet_trail", "qty": qty, "result": close_result}
        elif dte <= 5 and qty > 0:
            await db.options_desk_trades.update_one({"trade_id": trade.get("trade_id")}, {"$set": {"close_reason": "tail_dte_exit"}})
            close_result = await options_desk.close(symbol)
            if close_result.get("ok"):
                await db.options_desk_trades.update_one(
                    {"trade_id": trade.get("trade_id")},
                    {"$set": {"status": "tail_dte_exit_close_submitted", "close_order": close_result.get("order")}},
                )
            action = {"symbol": symbol, "action": "tail_dte_exit", "qty": qty, "result": close_result}
        check = {
            "symbol": symbol,
            "ticker": trade.get("ticker"),
            "pnl_pct": round(pnl_pct, 2),
            "peak_gain_pct": round(peak, 2),
            "dte": dte,
            "tier1_taken": bool(trade.get("tail_tier1_taken")),
            "current_premium": context.get("current_premium"),
            "action": action,
        }
        checks.append(check)
        if action:
            actions.append(action)
    doc = stamped({"ok": True, "checked_at": _now(), "positions_checked": len(checks), "checks": checks, "actions": actions})
    await db.tail_hunter_risk_checks.insert_one(doc)
    return {k: v for k, v in doc.items() if k != "_id"}
