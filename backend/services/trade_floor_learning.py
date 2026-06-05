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
