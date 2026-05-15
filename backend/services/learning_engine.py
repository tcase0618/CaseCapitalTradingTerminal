"""Progressive Learning Engine — analyzes completed P&L records, adjusts
signal weights based on real win rates. Runs weekly. Never adjusts on noise."""
from __future__ import annotations
import itertools
import logging
from datetime import datetime, timezone
from typing import Any

from .db import get_db, log_activity, stamped

logger = logging.getLogger(__name__)


DEFAULT_WEIGHTS = {
    "insider_cluster_buy":   {"value": 15.0, "min": 6.0,  "max": 25.0},
    "high_short_interest":   {"value": 10.0, "min": 4.0,  "max": 18.0},
    "CONTRACT_SURGE":        {"value": 12.0, "min": 5.0,  "max": 20.0},
    "CONGRESSIONAL_BUY":     {"value": 10.0, "min": 4.0,  "max": 18.0},
    "NEW_WINNER":            {"value": 8.0,  "min": 3.0,  "max": 15.0},
    "CONCENTRATION_WIN":     {"value": 6.0,  "min": 2.0,  "max": 12.0},
    "MOMENTUM_STACK":        {"value": 7.0,  "min": 3.0,  "max": 14.0},
    "BUDGET_SURGE":          {"value": 5.0,  "min": 2.0,  "max": 10.0},
    "UNUSUAL_FLOW":          {"value": 6.0,  "min": 2.0,  "max": 12.0},
    "CALL_SWEEP":            {"value": 8.0,  "min": 3.0,  "max": 14.0},
    "upcoming_earnings":     {"value": 4.0,  "min": 1.0,  "max": 8.0},
    "squeeze_bonus":         {"value": 10.0, "min": 4.0,  "max": 18.0},
    "committee_match_bonus": {"value": 3.0,  "min": 1.0,  "max": 6.0},
}

BASELINE_WR = 0.50    # 50% win rate baseline
MAX_CHANGE = 0.15     # max ±15% per cycle
MIN_SAMPLES = 10      # min trades before adjusting a weight


async def ensure_weights_exist():
    """Seed weights collection if empty. Idempotent — safe to call on every startup."""
    db = get_db()
    count = await db.learning_weights.count_documents({})
    if count > 0:
        return
    docs = []
    now = datetime.now(timezone.utc).isoformat()
    for key, cfg in DEFAULT_WEIGHTS.items():
        docs.append(stamped({
            "weight_key": key,
            "current_value": cfg["value"],
            "default_value": cfg["value"],
            "min_value": cfg["min"],
            "max_value": cfg["max"],
            "sample_count": 0,
            "win_rate": None,
            "avg_return": None,
            "confidence": 0.0,
            "last_updated": now,
        }))
    await db.learning_weights.insert_many(docs)
    await log_activity(f"Seeded {len(docs)} learning weights with defaults", "info")


async def get_weights() -> dict[str, float]:
    """Returns flat dict {weight_key: current_value} for scanner consumption."""
    db = get_db()
    rows = await db.learning_weights.find({}, {"_id": 0}).to_list(100)
    out = {r["weight_key"]: r["current_value"] for r in rows}
    # Fill any missing keys with defaults (in case a new signal was added)
    for k, cfg in DEFAULT_WEIGHTS.items():
        out.setdefault(k, cfg["value"])
    return out


async def reset_weights() -> int:
    """Reset all weights to their defaults. Returns count reset."""
    db = get_db()
    now = datetime.now(timezone.utc).isoformat()
    n = 0
    for key, cfg in DEFAULT_WEIGHTS.items():
        res = await db.learning_weights.update_one(
            {"weight_key": key},
            {"$set": {
                "current_value": cfg["value"],
                "default_value": cfg["value"],
                "min_value": cfg["min"],
                "max_value": cfg["max"],
                "last_updated": now,
                "sample_count": 0,
                "confidence": 0.0,
            }},
            upsert=True,
        )
        if res.modified_count or res.upserted_id:
            n += 1
    await log_activity(f"Learning weights reset to defaults ({n})", "info")
    return n


async def run_learning_cycle() -> dict[str, Any]:
    """Weekly cycle. Analyze completed P&L (30d returns), adjust weights."""
    db = get_db()
    await ensure_weights_exist()
    await log_activity("Learning cycle started", "info")

    docs = await db.signal_performance.find(
        {"return_30d": {"$ne": None}}, {"_id": 0},
    ).to_list(5000)

    if len(docs) < MIN_SAMPLES:
        msg = f"Learning skipped — only {len(docs)} completed trades (need {MIN_SAMPLES}+)"
        await log_activity(msg, "warn")
        return {"skipped": True, "reason": msg, "trades": len(docs)}

    # Per-signal performance
    signal_stats: dict[str, dict | None] = {}
    for key in DEFAULT_WEIGHTS.keys():
        relevant = [d for d in docs if key in (d.get("signals") or [])]
        if len(relevant) < 5:
            signal_stats[key] = None
            continue
        wins = [d for d in relevant if (d.get("return_30d") or 0) > 0]
        signal_stats[key] = {
            "count": len(relevant),
            "wins": len(wins),
            "win_rate": round(len(wins) / len(relevant), 3),
            "avg_return": round(sum(d["return_30d"] for d in relevant) / len(relevant), 2),
        }

    # Per-combo performance
    combo_map: dict[str, list[dict]] = {}
    for d in docs:
        sigs = sorted(d.get("signals", []) or [])
        for size in (2, 3):
            if len(sigs) < size:
                continue
            for combo in itertools.combinations(sigs, size):
                key = "|".join(combo)
                combo_map.setdefault(key, []).append(d)

    # Persist combo stats
    for key, trades in combo_map.items():
        if len(trades) < 3:
            continue
        wins = sum(1 for t in trades if (t.get("return_30d") or 0) > 0)
        avg_ret = sum(t["return_30d"] for t in trades) / len(trades)
        best = max(t["return_30d"] for t in trades)
        worst = min(t["return_30d"] for t in trades)
        await db.combo_stats.update_one(
            {"signal_combo": key},
            {"$set": stamped({
                "signal_combo": key,
                "trade_count": len(trades),
                "win_count": wins,
                "win_rate": round(wins / len(trades), 3),
                "avg_return_30d": round(avg_ret, 2),
                "best_return": round(best, 2),
                "worst_return": round(worst, 2),
                "last_updated": datetime.now(timezone.utc).isoformat(),
            })},
            upsert=True,
        )

    # Adjust weights
    weight_rows = await db.learning_weights.find({}, {"_id": 0}).to_list(100)
    changes: dict[str, dict] = {}
    now = datetime.now(timezone.utc).isoformat()
    for row in weight_rows:
        key = row["weight_key"]
        current = row["current_value"]
        wmin = row["min_value"]
        wmax = row["max_value"]
        stats = signal_stats.get(key)
        if not stats or stats["count"] < MIN_SAMPLES:
            continue
        confidence = min(stats["count"] / 50.0, 1.0)
        wr_delta = stats["win_rate"] - BASELINE_WR
        max_adj = current * MAX_CHANGE
        adjustment = wr_delta * max_adj * confidence
        new_value = max(wmin, min(wmax, current + adjustment))
        if abs(new_value - current) > 0.05:
            await db.learning_weights.update_one(
                {"weight_key": key},
                {"$set": {
                    "current_value": round(new_value, 2),
                    "sample_count": stats["count"],
                    "win_rate": stats["win_rate"],
                    "avg_return": stats["avg_return"],
                    "confidence": round(confidence, 2),
                    "last_updated": now,
                }},
            )
            changes[key] = {
                "old": round(current, 2),
                "new": round(new_value, 2),
                "delta": round(new_value - current, 2),
                "pct": round((new_value - current) / current * 100, 1) if current else 0,
            }

    insights = _build_insights(signal_stats, combo_map, changes, docs)
    overall_wr = sum(1 for d in docs if (d.get("return_30d") or 0) > 0) / len(docs)

    await db.learning_runs.insert_one(stamped({
        "run_at": now,
        "trades_analyzed": len(docs),
        "weights_changed": changes,
        "overall_win_rate": round(overall_wr, 3),
        "insights": insights,
    }))
    await log_activity(
        f"Learning cycle complete — {len(docs)} trades, {len(changes)} weights "
        f"adjusted, WR={overall_wr:.1%}", "info",
    )

    return {"trades": len(docs), "changes": len(changes),
             "win_rate": overall_wr, "insights": insights}


def _build_insights(signal_stats, combo_map, changes, docs) -> list[str]:
    insights = []
    for signal, stats in signal_stats.items():
        if not stats:
            continue
        if stats["win_rate"] > 0.70:
            insights.append(
                f"🟢 {signal}: {stats['win_rate']:.0%} win rate "
                f"({stats['count']} trades, avg {stats['avg_return']:+.1f}%)"
            )
        elif stats["win_rate"] < 0.40 and stats["count"] >= MIN_SAMPLES:
            insights.append(
                f"🔴 {signal}: only {stats['win_rate']:.0%} win rate — weight reduced"
            )
    valid_combos = [(k, v) for k, v in combo_map.items() if len(v) >= 5]
    if valid_combos:
        best = max(valid_combos,
                   key=lambda x: sum(d["return_30d"] for d in x[1]) / len(x[1]))
        k, trades = best
        avg = sum(d["return_30d"] for d in trades) / len(trades)
        wr = sum(1 for d in trades if (d.get("return_30d") or 0) > 0) / len(trades)
        insights.append(
            f"⚡ Best combo: [{k.replace('|', ' + ').upper()}] "
            f"— {wr:.0%} WR, avg {avg:+.1f}%"
        )
    for key, ch in changes.items():
        arrow = "📈" if ch["delta"] > 0 else "📉"
        insights.append(f"{arrow} {key}: {ch['old']} → {ch['new']} ({ch['pct']:+.1f}%)")
    return insights
