"""Trade Floor Three-Phase Exit System.

Every open Trade Floor position runs through up to three exit phases:

  Phase 1 — Confirmation Exit
    Trigger: price reaches the AXIOM scan target (phase1_target).
    Action:  close 40% of original qty at market.
             move stop on remaining 60% to entry (breakeven).
             fire Telegram alert.

  Phase 2 — Extension Exit
    Trigger: price reaches entry + 1.5 × (axiom_target − entry).
    Action:  close 30% of original qty at market.
             move stop on remaining 30% to the phase-1 exit price
             (position can no longer lose money).
             fire Telegram alert.

  Phase 3 — Trailing Stop
    The last 30% runs with a trailing stop set at 50% of the peak gain from
    entry. The trail tightens to 25% of peak gain once the position is
    past 90% of its recommended hold window. The position closes when:
       (a) trailing stop triggers, OR
       (b) hold window expires (full close at market), OR
       (c) current_stop is hit on any open portion at any time.
    A Telegram alert fires with final P&L across all three phases.

Hard stop: every open portion respects `current_stop`. Stop only moves
favorably — never widened, never moved against the position.

Price source for all trigger checks: Alpaca → Finnhub → yfinance (the
existing pricer waterfall). No exceptions.

Learning Engine integration: every closed trade logs the three phase
levels + actuals + peak + hold-times to `tf_phase_outcomes`, consumed by
the TF Learning Engine recalibrator to evolve the phase-2 multiplier and
the trailing-stop percentage per signal combo.
"""
from __future__ import annotations
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from .db import get_db, stamped

logger = logging.getLogger(__name__)

ALPACA_KEY = os.environ.get("APCA_API_KEY_ID", "").strip()
ALPACA_SECRET = os.environ.get("APCA_API_SECRET_KEY", "").strip()
ALPACA_TRADE_BASE = "https://paper-api.alpaca.markets"

# Defaults — overwritten by Learning Engine recalibration once enough data.
DEFAULTS = {
    "phase1_close_pct": 0.40,
    "phase2_close_pct": 0.30,
    "phase2_multiplier": 1.5,
    "trail_pct_normal": 0.50,
    "trail_pct_tight": 0.25,
    "tight_hold_threshold": 0.90,   # past 90% of hold window → use tight trail
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _params() -> dict[str, Any]:
    db = get_db()
    doc = await db.tf_phase_engine.find_one({"_id": "current"})
    if not doc:
        await db.tf_phase_engine.insert_one({
            "_id": "current",
            "params_by_combo": {},
            "defaults": DEFAULTS,
            "initialized_at": _now().isoformat(),
        })
        return {"params_by_combo": {}, "defaults": dict(DEFAULTS)}
    return {"params_by_combo": doc.get("params_by_combo") or {},
             "defaults": doc.get("defaults") or DEFAULTS}


def _params_for(combo: list[str], all_params: dict[str, Any]) -> dict[str, Any]:
    key = "+".join(sorted(combo or []))
    return {**all_params["defaults"], **(all_params["params_by_combo"].get(key) or {})}


async def _alpaca_market_sell(ticker: str, qty: float,
                               client_order_id: str) -> dict[str, Any] | None:
    """Market sell — used ONLY for phase exits on existing long positions.
    Buy orders for opening positions remain limit/day per the v5.2 spec."""
    if not (ALPACA_KEY and ALPACA_SECRET) or qty <= 0:
        return None
    headers = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}
    payload = {
        "symbol": ticker.upper(),
        "qty": str(round(qty, 9)),  # Alpaca accepts fractional qty as string
        "side": "sell",
        "type": "market",
        "time_in_force": "day",
        "client_order_id": client_order_id,
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers) as c:
            r = await c.post(f"{ALPACA_TRADE_BASE}/v2/orders", json=payload)
            if r.status_code in (200, 201):
                return r.json()
            logger.warning("alpaca market sell %s qty=%s: %s %s",
                            ticker, qty, r.status_code, r.text[:200])
    except Exception as e:
        logger.warning("alpaca market sell exception %s: %s", ticker, e)
    return None


async def _current_price(ticker: str) -> float | None:
    """Strict pricer waterfall: Alpaca → Finnhub → yfinance (per spec)."""
    try:
        from . import pricer
        return await pricer.get_latest_close(ticker)
    except Exception as e:
        logger.debug("price waterfall %s: %s", ticker, e)
        return None


async def _send_telegram(text: str) -> None:
    try:
        from . import telegram_service
        # Plain text — uses default chat_id from env
        await telegram_service.send_message(text, parse_mode="")
    except Exception as e:
        logger.debug("telegram broadcast: %s", e)


def _days_in_trade(t: dict[str, Any]) -> float:
    f_at = t.get("filled_at") or t.get("submitted_at")
    if not f_at:
        return 0.0
    try:
        ts = datetime.fromisoformat(f_at.replace("Z", "+00:00"))
        return max(0.0, (_now() - ts).total_seconds() / 86400.0)
    except Exception:
        return 0.0


async def process_phase_exits() -> dict[str, Any]:
    """Iterate every OPEN filled trade and apply the three-phase logic.

    Returns a summary dict so the scheduler/admin endpoint can show what
    happened on each tick."""
    db = get_db()
    all_params = await _params()

    open_trades = await db.tf_trades.find(
        {"status": "OPEN", "fill_status": "FILLED",
          "qty_remaining": {"$gt": 0}},
        {"_id": 0},
    ).to_list(500)
    actions: list[dict[str, Any]] = []
    for t in open_trades:
        ticker = (t.get("ticker") or "").upper()
        cli = t.get("client_order_id")
        entry = float(t.get("filled_avg_price") or t.get("entry_price_ref") or 0)
        if entry <= 0:
            continue
        cur = await _current_price(ticker)
        if not cur or cur <= 0:
            continue
        # Running peak — first time seen if None
        prev_peak = float(t.get("peak_price_since_entry") or entry)
        peak = max(prev_peak, cur)
        if peak != prev_peak:
            await db.tf_trades.update_one({"client_order_id": cli},
                                             {"$set": {"peak_price_since_entry": peak}})
        phase = int(t.get("phase") or 1)
        qty_total = float(t.get("qty_total") or 0)
        qty_rem = float(t.get("qty_remaining") or 0)
        phases_hit = dict(t.get("phases_hit") or {})
        params = _params_for(t.get("signal_combo") or [], all_params)

        # ── Hard stop check (any phase) ──
        cur_stop = float(t.get("current_stop") or t.get("stop_price") or 0)
        if cur_stop and cur <= cur_stop and qty_rem > 0:
            stop_reason = "phase3_trailing_stop" if phase >= 3 else "hard_stop"
            actions.append(await _close_remaining(t, cur, reason=stop_reason))
            continue

        # ── Hold window expiry check ──
        hold_window = float(t.get("hold_window_days") or 30)
        days_in = _days_in_trade(t)
        if hold_window > 0 and days_in >= hold_window and qty_rem > 0:
            actions.append(await _close_remaining(t, cur, reason="hold_window_expired"))
            continue

        # ── Phase 1 ──
        p1_target = float(t.get("pm_active_target") or t.get("phase1_target") or 0)
        if phase == 1 and p1_target and cur >= p1_target:
            qty_sell = round(qty_total * float(params["phase1_close_pct"]), 9)
            qty_sell = min(qty_sell, qty_rem)
            sold_at = await _market_sell_and_record(
                t, qty_sell, reason="phase1_target_hit",
                exit_price=cur,
            )
            if sold_at:
                # Move stop to breakeven (entry). Only move favorably.
                new_stop = max(cur_stop, entry)
                phases_hit["1"] = {
                    "hit_at": _now().isoformat(),
                    "trigger_price": cur,
                    "exit_price": sold_at,
                    "qty_sold": qty_sell,
                    "realized_pct_on_slice": round((sold_at - entry) / entry * 100, 2),
                    "stop_moved_to": new_stop,
                    "days_in_trade": round(days_in, 2),
                }
                qty_rem = round(qty_rem - qty_sell, 9)
                await db.tf_trades.update_one(
                    {"client_order_id": cli},
                    {"$set": {
                        "phase": 2,
                        "qty_remaining": qty_rem,
                        "current_stop": new_stop,
                        "phases_hit": phases_hit,
                    }},
                )
                await _send_telegram(
                    f"⚡ TF · {ticker} · Phase 1 hit\n"
                    f"  Exit: ${sold_at:.2f} (target ${p1_target:.2f})\n"
                    f"  Sold 40% · realized {phases_hit['1']['realized_pct_on_slice']:+.2f}%\n"
                    f"  Remaining: {qty_rem:.6f} · stop → ${new_stop:.2f} (breakeven)"
                )
                actions.append({"ticker": ticker, "phase": 1, "exit": sold_at})
                # One monitor tick should advance only one exit phase. If price
                # gaps through multiple targets, the next scheduled pass will
                # process the next phase using the freshly persisted state.
                continue

        # ── Phase 2 ──
        p2_target = float(t.get("pm_active_target") or t.get("phase2_target") or 0)
        if phase == 2 and p2_target and cur >= p2_target and qty_rem > 0:
            qty_sell = round(qty_total * float(params["phase2_close_pct"]), 9)
            qty_sell = min(qty_sell, qty_rem)
            sold_at = await _market_sell_and_record(
                t, qty_sell, reason="phase2_target_hit",
                exit_price=cur,
            )
            if sold_at:
                # Move stop to phase 1 exit price. Only move favorably.
                p1_exit = float(phases_hit.get("1", {}).get("exit_price") or entry)
                new_stop = max(float(t.get("current_stop") or entry), p1_exit)
                phases_hit["2"] = {
                    "hit_at": _now().isoformat(),
                    "trigger_price": cur,
                    "exit_price": sold_at,
                    "qty_sold": qty_sell,
                    "realized_pct_on_slice": round((sold_at - entry) / entry * 100, 2),
                    "stop_moved_to": new_stop,
                    "days_in_trade": round(days_in, 2),
                }
                qty_rem = round(qty_rem - qty_sell, 9)
                await db.tf_trades.update_one(
                    {"client_order_id": cli},
                    {"$set": {
                        "phase": 3,
                        "qty_remaining": qty_rem,
                        "current_stop": new_stop,
                        "phases_hit": phases_hit,
                    }},
                )
                await _send_telegram(
                    f"⚡⚡ TF · {ticker} · Phase 2 hit\n"
                    f"  Exit: ${sold_at:.2f} (target ${p2_target:.2f})\n"
                    f"  Sold 30% · realized {phases_hit['2']['realized_pct_on_slice']:+.2f}%\n"
                    f"  Remaining: {qty_rem:.6f} · stop → ${new_stop:.2f} (locked profit)"
                )
                actions.append({"ticker": ticker, "phase": 2, "exit": sold_at})
                # Keep phase transitions discrete for cleaner risk accounting
                # and audit history.
                continue

        # ── Phase 3 (trailing stop) ──
        if phase == 3 and qty_rem > 0:
            peak_gain = (peak - entry) / entry if entry > 0 else 0.0
            # Tighten trail past 90% of hold window
            tight = (days_in / hold_window) >= float(params["tight_hold_threshold"]) \
                       if hold_window > 0 else False
            trail_pct = float(params["trail_pct_tight"]) if tight \
                            else float(params["trail_pct_normal"])
            # Trail stop in $ terms: entry × (1 + peak_gain × (1 − trail_pct))
            trail_stop = round(entry * (1 + peak_gain * (1 - trail_pct)), 4)
            # Lift current_stop only if trail_stop is higher (favorable only)
            new_stop = max(float(t.get("current_stop") or entry), trail_stop)
            if new_stop != float(t.get("current_stop") or 0):
                await db.tf_trades.update_one(
                    {"client_order_id": cli},
                    {"$set": {"current_stop": new_stop,
                                "trail_pct_active": trail_pct,
                                "tight_trail_active": tight}},
                )
            # Stop hit?
            if cur <= new_stop:
                actions.append(await _close_remaining(t, cur, reason="phase3_trailing_stop"))
    return {"checked": len(open_trades), "actions": actions,
             "ran_at": _now().isoformat()}


async def _market_sell_and_record(t: dict[str, Any], qty: float,
                                     reason: str, exit_price: float) -> float | None:
    """Submit Alpaca market sell + record the leg into tf_phase_exits.
    Returns the actual fill price (or current price if Alpaca doesn't echo
    a fill yet — Alpaca market orders execute near-instantly during hours
    and queue OPG outside hours)."""
    db = get_db()
    ticker = (t.get("ticker") or "").upper()
    cli = f"tf-sell-{ticker}-{reason}-{int(_now().timestamp())}"
    order = await _alpaca_market_sell(ticker, qty, client_order_id=cli)
    if not order:
        logger.warning("phase exit sell failed %s qty=%s reason=%s", ticker, qty, reason)
        return None
    sold_at = float(order.get("filled_avg_price") or exit_price)
    await db.tf_phase_exits.insert_one(stamped({
        "parent_client_order_id": t.get("client_order_id"),
        "ticker": ticker,
        "reason": reason,
        "qty": qty,
        "fill_price": sold_at,
        "alpaca_order_id": order.get("id"),
        "submitted_at": _now().isoformat(),
    }))
    return sold_at


async def _close_remaining(t: dict[str, Any], cur: float, *, reason: str) -> dict[str, Any]:
    """Close the rest of the position at market. Marks the trade CLOSED.
    Used by hard stop, hold-window expiry, and phase-3 trailing stop."""
    db = get_db()
    ticker = (t.get("ticker") or "").upper()
    qty_rem = float(t.get("qty_remaining") or 0)
    if qty_rem <= 0:
        return {"ticker": ticker, "reason": reason, "no_op": True}
    sold_at = await _market_sell_and_record(t, qty_rem, reason=reason, exit_price=cur)
    if not sold_at:
        return {"ticker": ticker, "reason": reason, "no_op": True}
    entry = float(t.get("filled_avg_price") or t.get("entry_price_ref") or 0)
    phases_hit = dict(t.get("phases_hit") or {})
    days_in = _days_in_trade(t)
    phases_hit["3"] = {
        "hit_at": _now().isoformat(),
        "trigger_price": cur,
        "exit_price": sold_at,
        "qty_sold": qty_rem,
        "realized_pct_on_slice": round((sold_at - entry) / entry * 100, 2) if entry else None,
        "reason": reason,
        "days_in_trade": round(days_in, 2),
    }
    # Aggregate realized P&L across all phases (qty-weighted on entry basis)
    total = 0.0
    total_qty = 0.0
    for ph in phases_hit.values():
        q = float(ph.get("qty_sold") or 0)
        px = float(ph.get("exit_price") or 0)
        if q > 0 and px > 0 and entry > 0:
            total += (px - entry) * q
            total_qty += q
    avg_pct = round((total / (entry * total_qty)) * 100, 2) if (entry and total_qty) else None
    await db.tf_trades.update_one(
        {"client_order_id": t.get("client_order_id")},
        {"$set": {
            "status": "CLOSED",
            "phase": 3,
            "phases_hit": phases_hit,
            "qty_remaining": 0,
            "exit_price": sold_at,
            "closed_at": _now().isoformat(),
            "close_reason": reason,
            "realized_pct": avg_pct,
            "lowest_price_reached": min(
                float(t.get("lowest_price_reached") or cur), cur,
            ),
        }},
    )
    # Telegram final close alert
    hold_dur = _days_in_trade(t)
    await _send_telegram(
        f"🏁 TF · {ticker} · CLOSED ({reason})\n"
        f"  Final exit: ${sold_at:.2f}\n"
        f"  Total realized: {avg_pct:+.2f}% across {len(phases_hit)} phase(s)\n"
        f"  Held: {hold_dur:.1f}d"
    )
    # Phase-outcomes record for the learning engine
    await db.tf_phase_outcomes.insert_one(stamped({
        "parent_client_order_id": t.get("client_order_id"),
        "ticker": ticker,
        "signal_combo": t.get("signal_combo"),
        "score_tier": t.get("entry_score"),
        "axiom_target": t.get("axiom_target"),
        "phase1_target": t.get("phase1_target"),
        "phase1_hit": "1" in phases_hit,
        "phase1": phases_hit.get("1"),
        "phase2_target": t.get("phase2_target"),
        "phase2_hit": "2" in phases_hit,
        "phase2": phases_hit.get("2"),
        "phase3": phases_hit.get("3"),
        "trail_pct_active": t.get("trail_pct_active"),
        "tight_trail_active": t.get("tight_trail_active"),
        "peak_price_since_entry": t.get("peak_price_since_entry"),
        "peak_gain_pct": round(((float(t.get("peak_price_since_entry") or entry) - entry) / entry * 100), 2)
            if entry else None,
        "hold_window_days": t.get("hold_window_days"),
        "actual_hold_days": round(hold_dur, 2),
        "entry_price": entry,
        "final_exit_price": sold_at,
        "final_realized_pct": avg_pct,
        "close_reason": reason,
        "closed_at": _now().isoformat(),
    }))
    return {"ticker": ticker, "reason": reason, "exit": sold_at, "realized_pct": avg_pct}
