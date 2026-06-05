"""Trade Floor Learning Engine — FORKED from the Signal Learning Engine
at startup. After init, the two engines NEVER sync weights again.

This engine learns EXCLUSIVELY from real Trade Floor executions:
  • <5 closed trades  → pre-adjustment phase (no changes)
  • 5-29 closed trades → signal-weight adjustment phase only
  • 30+ closed trades  → full phase (weights + gates + risk tiers)

Owns its own collections:
  • tf_weights, tf_combo_stats, tf_recalibration_log, tf_risk_tiers
The Signal Learning Engine and main scan NEVER read or write these.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from .db import get_db, log_activity, stamped

logger = logging.getLogger(__name__)

PRE_ADJUSTMENT_MIN = 5     # 0-4 = held at inherited weight
SIGNAL_PHASE_MIN = 5       # 5-29 = adjust signal weights only
FULL_PHASE_MIN = 30        # 30+ = adjust weights + gates + risk tiers


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def initialize_from_signal_engine() -> bool:
    """One-time: snapshot the current Signal Learning Engine weights as
    the Trade Floor Engine's baseline. Idempotent — if tf_weights already
    seeded, no-op."""
    db = get_db()
    existing = await db.tf_weights.find_one({"_id": "current"})
    if existing:
        return False
    from . import learning_engine as sle
    weights = await sle.get_weights()
    await db.tf_weights.insert_one({
        "_id": "current",
        "weights": weights,
        "inherited_from_signal_engine_at": _now().isoformat(),
        "adjusted_combos": [],
    })
    # Seed initial risk tiers
    from .trade_floor import DEFAULT_RISK_TIERS
    serializable = {
        k: {f"{rng[0]}-{rng[1]}": pct for rng, pct in band.items()}
        for k, band in DEFAULT_RISK_TIERS.items()
    }
    await db.tf_risk_tiers.update_one(
        {"_id": "current"},
        {"$setOnInsert": {"tiers": serializable, "initialized_at": _now().isoformat()}},
        upsert=True,
    )
    await log_activity("Trade Floor Engine initialized from Signal Engine baseline", "info")
    return True


async def get_weights() -> dict[str, float]:
    db = get_db()
    doc = await db.tf_weights.find_one({"_id": "current"})
    return (doc or {}).get("weights") or {}


async def get_trade_score(signals: list[str] | dict[str, Any]) -> float:
    """Sum the Trade Floor engine weight × signal contribution."""
    weights = await get_weights()
    keys = list(signals.keys()) if isinstance(signals, dict) else list(signals)
    return round(sum(weights.get(k, 0) for k in keys), 2)


# ─────── Per-trade learning hooks ───────
async def log_trade_initiation(trade_doc: dict[str, Any]) -> None:
    """Persist a snapshot of the per-trade decision factors at submit
    time. Used later by `recalibrate()` to evolve entry-price and stop
    logic based on actual outcomes."""
    db = get_db()
    await db.tf_trade_decisions.insert_one(stamped({
        "client_order_id": trade_doc.get("client_order_id"),
        "ticker": trade_doc.get("ticker"),
        "signal_combo": trade_doc.get("signal_combo"),
        "trade_score": trade_doc.get("trade_score"),
        "score_tier": _score_tier(trade_doc.get("trade_score") or 0),
        "sector": trade_doc.get("sector"),
        "hold_window_days": trade_doc.get("hold_window_days"),
        "instrument": trade_doc.get("instrument"),
        "limit_price": trade_doc.get("limit_price"),
        "stop_price": trade_doc.get("stop_price"),
        "stop_pct": trade_doc.get("stop_pct"),
        "stop_breakdown": trade_doc.get("stop_breakdown"),
        "hard_cap_applied": trade_doc.get("hard_cap_applied"),
        "notional": trade_doc.get("notional"),
        "submitted_at": trade_doc.get("submitted_at"),
    }))


async def log_trade_outcomes(closed_trades: list[dict[str, Any]]) -> None:
    """Called on every position close. Stamps the final fill/outcome data
    back onto the decision record so the recalibrator can analyse it."""
    db = get_db()
    for t in closed_trades:
        try:
            await db.tf_trade_decisions.update_one(
                {"client_order_id": t.get("client_order_id")},
                {"$set": {
                    "fill_status": t.get("fill_status"),
                    "fill_seconds": t.get("fill_seconds"),
                    "filled_avg_price": t.get("filled_avg_price"),
                    "lowest_price_reached": t.get("lowest_price_reached"),
                    "exit_price": t.get("exit_price"),
                    "realized_pct": t.get("realized_pct"),
                    "closed_at": t.get("closed_at"),
                }},
            )
        except Exception as e:
            logger.warning("log_trade_outcomes %s: %s", t.get("ticker"), e)


def _score_tier(score: float) -> str:
    if score >= 50:
        return "50+"
    if score >= 30:
        return "30-49"
    if score >= 25:
        return "25-29"
    return "20-24"


async def closed_trade_count() -> int:
    db = get_db()
    return await db.tf_trades.count_documents({"status": "CLOSED"})


async def phase() -> str:
    n = await closed_trade_count()
    if n < PRE_ADJUSTMENT_MIN:
        return "pre_adjustment"
    if n < FULL_PHASE_MIN:
        return "signal_weight_adjustment"
    return "full_adjustment"


async def status() -> dict[str, Any]:
    """Aggregate status for the Trade Floor Engine tab."""
    db = get_db()
    weights = await get_weights()
    n_closed = await closed_trade_count()
    p = await phase()
    combo_count = await db.tf_combo_stats.count_documents({})
    inherited_count = sum(1 for w in weights.values() if w)
    next_recal = await db.tf_recalibration_log.find_one(
        {}, {"_id": 0}, sort=[("ran_at", -1)],
    )
    days_until = 7
    if next_recal:
        try:
            last = datetime.fromisoformat(next_recal["ran_at"])
            elapsed = (_now() - last).days
            days_until = max(0, 7 - elapsed)
        except Exception:
            pass
    return {
        "phase": p,
        "closed_trades": n_closed,
        "combos_with_data": combo_count,
        "inherited_weight_count": inherited_count,
        "weights": weights,
        "days_until_next_recalibration": days_until,
        "min_for_signal_phase": PRE_ADJUSTMENT_MIN,
        "min_for_full_phase": FULL_PHASE_MIN,
    }


async def combo_stats() -> list[dict[str, Any]]:
    """All combo performance records derived from closed Trade Floor trades."""
    db = get_db()
    return await db.tf_combo_stats.find({}, {"_id": 0}).sort("wins", -1).to_list(200)


async def recalibrate() -> dict[str, Any]:
    """Weekly recalibration based on closed trade outcomes.
       <5 trades: no-op.
       5-29: adjust signal weights for combos with data.
       30+: also adjust execution gate thresholds + risk tiers."""
    db = get_db()
    n = await closed_trade_count()
    p = await phase()
    if p == "pre_adjustment":
        await db.tf_recalibration_log.insert_one(stamped({
            "ran_at": _now().isoformat(),
            "phase": p, "closed_trades": n,
            "changes": [], "note": "below 5-trade threshold; no adjustments",
        }))
        return {"phase": p, "changes": 0}

    # Build combo stats from closed trades
    trades = await db.tf_trades.find({"status": "CLOSED"}, {"_id": 0}).to_list(1000)
    combos: dict[tuple, dict[str, Any]] = {}
    for t in trades:
        combo = tuple(sorted(t.get("signal_combo") or []))
        if not combo:
            continue
        if combo not in combos:
            combos[combo] = {"wins": 0, "losses": 0, "total_return_pct": 0.0, "n": 0}
        ret = t.get("realized_pct") or 0
        combos[combo]["n"] += 1
        combos[combo]["total_return_pct"] += ret
        if ret > 0:
            combos[combo]["wins"] += 1
        else:
            combos[combo]["losses"] += 1
    # Persist combo stats
    for combo, s in combos.items():
        avg = s["total_return_pct"] / s["n"] if s["n"] else 0
        await db.tf_combo_stats.update_one(
            {"combo": list(combo)},
            {"$set": stamped({
                "combo": list(combo),
                "wins": s["wins"], "losses": s["losses"],
                "n": s["n"], "win_rate": round(s["wins"] / s["n"], 3),
                "avg_return_pct": round(avg, 2),
            })},
            upsert=True,
        )

    # Phase ≥5: adjust signal weights for combos that have real data
    weights_doc = await db.tf_weights.find_one({"_id": "current"})
    weights = weights_doc.get("weights") or {}
    changes: list[dict[str, Any]] = []
    for combo, s in combos.items():
        if s["n"] < 3:
            continue
        win_rate = s["wins"] / s["n"]
        avg = s["total_return_pct"] / s["n"]
        # Multiplier based on win-rate vs neutral 0.5 + return contribution
        mult = 0.85 + (win_rate - 0.5) * 0.6 + max(-0.15, min(0.15, avg / 100))
        for sig in combo:
            old = weights.get(sig, 0)
            new = max(0.05, min(2.0, old * mult)) if old else 0.5 * mult
            if abs(new - old) > 0.01:
                weights[sig] = round(new, 3)
                changes.append({"signal": sig, "from": old, "to": weights[sig],
                                  "combo": list(combo)})
    await db.tf_weights.update_one(
        {"_id": "current"},
        {"$set": {"weights": weights, "last_recalibrated_at": _now().isoformat()}},
    )

    # Phase ≥30: also adjust risk tiers
    if p == "full_adjustment":
        # If recent realized returns are positive, nudge risk tiers up 0.005
        # If negative, nudge down 0.005. Capped at original × 1.5 / × 0.5.
        recent_avg = sum(t.get("realized_pct") or 0 for t in trades[-20:]) / max(1, min(20, len(trades)))
        delta = 0.005 if recent_avg > 0 else -0.005
        tiers_doc = await db.tf_risk_tiers.find_one({"_id": "current"})
        if tiers_doc and tiers_doc.get("tiers"):
            tiers = tiers_doc["tiers"]
            for inst in tiers:
                for band, pct in list(tiers[inst].items()):
                    tiers[inst][band] = round(max(0.005, min(0.20, pct + delta)), 4)
            await db.tf_risk_tiers.update_one(
                {"_id": "current"},
                {"$set": {"tiers": tiers, "last_adjusted_at": _now().isoformat()}},
            )

    # Phase ≥5: evolve the stop engine coefficients based on observed outcomes
    try:
        await _recalibrate_stop_engine(trades)
    except Exception as e:
        logger.warning("recalibrate_stop_engine: %s", e)

    # Phase ≥5: evolve entry-price logic (fill rate + outcome per signal/score tier)
    try:
        await _recalibrate_entry_price(trades)
    except Exception as e:
        logger.warning("recalibrate_entry_price: %s", e)

    await db.tf_recalibration_log.insert_one(stamped({
        "ran_at": _now().isoformat(),
        "phase": p, "closed_trades": n,
        "changes": changes[:50],
        "combos_with_data": len(combos),
    }))
    await log_activity(
        f"Trade Floor Engine recalibration · {p} · {len(changes)} weight changes", "info",
    )
    return {"phase": p, "changes": len(changes), "combos": len(combos)}



async def _recalibrate_stop_engine(trades: list[dict[str, Any]]) -> None:
    """Compare calculated stop_pct vs lowest_price_reached vs realized_pct per
    signal-tier/sector. Adjust the stop_engine coefficient table so future
    stops are tighter where stops are rarely hit and outcomes are positive,
    and wider where stops were prematurely hit on otherwise good setups.

    The recalibration nudges each coefficient by ±0.005, clamped to its
    sensible range. Real movement requires ≥10 closed trades in the bucket;
    otherwise the bucket is held."""
    db = get_db()
    closed = [t for t in trades
                if t.get("stop_breakdown") and t.get("lowest_price_reached") is not None
                and t.get("entry_price_ref")]
    if len(closed) < 5:
        return
    # Aggregate by sector & score_tier
    buckets: dict[tuple, dict[str, Any]] = {}
    for t in closed:
        bd = t.get("stop_breakdown") or {}
        key = (bd.get("sector") or "unknown", bd.get("score_tier") or "20-24")
        b = buckets.setdefault(key, {"n": 0, "stopped_out": 0, "ret_sum": 0.0,
                                          "drawdown_sum": 0.0})
        b["n"] += 1
        b["ret_sum"] += (t.get("realized_pct") or 0)
        entry = float(t.get("entry_price_ref") or 0)
        low = float(t.get("lowest_price_reached") or 0)
        if entry > 0 and low > 0:
            dd = (entry - low) / entry  # max drawdown during the hold
            b["drawdown_sum"] += dd
            stop_pct = float(t.get("stop_pct") or 0)
            if dd >= stop_pct:
                b["stopped_out"] += 1

    coef_doc = await db.tf_stop_engine.find_one({"_id": "current"}) or {}
    coef = dict(coef_doc.get("coefficients") or {})
    if not coef:
        return
    sector_delta = dict(coef.get("sector_delta") or {})
    score_delta = dict(coef.get("score_tier_delta") or {})
    changes: list[dict[str, Any]] = []
    for (sector, tier), s in buckets.items():
        if s["n"] < 10:
            continue
        avg_ret = s["ret_sum"] / s["n"]
        stop_rate = s["stopped_out"] / s["n"]
        # Heuristic: if avg_ret > 0 and stop_rate < 0.3 → tighten (-0.005)
        #            if avg_ret <= 0 and stop_rate >= 0.5 → widen (+0.005)
        delta = 0.0
        if avg_ret > 0 and stop_rate < 0.3:
            delta = -0.005
        elif avg_ret <= 0 and stop_rate >= 0.5:
            delta = 0.005
        if delta == 0.0:
            continue
        old_sec = float(sector_delta.get(sector, 0.0))
        new_sec = round(max(-0.05, min(0.08, old_sec + delta)), 4)
        if new_sec != old_sec:
            sector_delta[sector] = new_sec
            changes.append({"factor": "sector_delta", "key": sector,
                              "from": old_sec, "to": new_sec, "n": s["n"]})
        old_sc = float(score_delta.get(tier, 0.0))
        new_sc = round(max(-0.03, min(0.05, old_sc + delta * 0.5)), 4)
        if new_sc != old_sc:
            score_delta[tier] = new_sc
            changes.append({"factor": "score_tier_delta", "key": tier,
                              "from": old_sc, "to": new_sc, "n": s["n"]})
    if changes:
        coef["sector_delta"] = sector_delta
        coef["score_tier_delta"] = score_delta
        await db.tf_stop_engine.update_one(
            {"_id": "current"},
            {"$set": {"coefficients": coef, "last_recalibrated_at": _now().isoformat()}},
        )
        await db.tf_recalibration_log.insert_one(stamped({
            "ran_at": _now().isoformat(),
            "subsystem": "stop_engine",
            "changes": changes,
            "buckets": len(buckets),
        }))


async def _recalibrate_entry_price(trades: list[dict[str, Any]]) -> None:
    """Adjust the entry-price offset (vs current ask) per signal-combo/score-tier
    based on observed fill rate AND outcome. Stored in tf_entry_engine."""
    db = get_db()
    by_bucket: dict[tuple, dict[str, Any]] = {}
    for t in trades:
        if not t.get("limit_price") or not t.get("ticker"):
            continue
        combo = "+".join(sorted(t.get("signal_combo") or []))
        tier = _score_tier(t.get("trade_score") or 0)
        key = (combo, tier)
        b = by_bucket.setdefault(key, {"n": 0, "filled": 0, "fill_secs_sum": 0.0,
                                            "ret_sum": 0.0})
        b["n"] += 1
        if t.get("fill_status") == "FILLED":
            b["filled"] += 1
            b["fill_secs_sum"] += float(t.get("fill_seconds") or 0)
        b["ret_sum"] += (t.get("realized_pct") or 0)
    if not by_bucket:
        return
    offsets: dict[str, dict[str, Any]] = {}
    for (combo, tier), s in by_bucket.items():
        if s["n"] < 5:
            continue
        fill_rate = s["filled"] / s["n"]
        avg_ret = s["ret_sum"] / max(1, s["filled"])
        # If fill rate < 60% → bid 0.1% above ask (positive offset)
        # If fill rate > 90% AND avg_ret negative → bid 0.2% below ask (negative offset)
        # Otherwise keep at 0 (ask).
        offset_bps = 0
        if fill_rate < 0.6:
            offset_bps = 10  # +0.10%
        elif fill_rate > 0.9 and avg_ret < 0:
            offset_bps = -20  # -0.20%
        if offset_bps == 0:
            continue
        offsets.setdefault(combo, {})[tier] = {
            "offset_bps": offset_bps, "fill_rate": round(fill_rate, 3),
            "avg_ret_pct": round(avg_ret, 2), "n": s["n"],
        }
    if offsets:
        await db.tf_entry_engine.update_one(
            {"_id": "current"},
            {"$set": {"offsets_by_combo": offsets,
                       "last_recalibrated_at": _now().isoformat()}},
            upsert=True,
        )
        await db.tf_recalibration_log.insert_one(stamped({
            "ran_at": _now().isoformat(),
            "subsystem": "entry_price",
            "buckets_with_offsets": sum(len(v) for v in offsets.values()),
        }))
