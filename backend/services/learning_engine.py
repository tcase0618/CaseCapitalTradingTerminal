"""Progressive Learning Engine — analyzes completed P&L records, adjusts
signal weights based on real win rates. Runs weekly. Never adjusts on noise."""
from __future__ import annotations
import itertools
import logging
from datetime import datetime, timezone
from typing import Any

from .db import get_db, log_activity, stamped
from . import pricer

logger = logging.getLogger(__name__)


DEFAULT_WEIGHTS = {
    "DAY2_CONTINUATION": {"value": 4.0, "min": 1.0, "max": 9.0},
    "SUPERNOVA": {"value": 4.0, "min": 1.0, "max": 9.0},
    "RED_GREEN": {"value": 4.0, "min": 1.0, "max": 9.0},
    "SERIAL_RUNNER": {"value": 4.0, "min": 1.0, "max": 9.0},
    "CATALYST_RUNNER": {"value": 5.0, "min": 1.0, "max": 12.0},
    "TACTICAL_MOMENTUM_CALL": {"value": 4.0, "min": 1.0, "max": 9.0},
    "BREAKOUT_CALL": {"value": 4.0, "min": 1.0, "max": 9.0},
    "LEAPS_TREND": {"value": 4.0, "min": 1.0, "max": 9.0},
    # ── Hard-catalyst / institutional signals (weighted UP) ──
    "insider_cluster_buy":   {"value": 18.0, "min": 8.0,  "max": 28.0},
    "CONGRESSIONAL_BUY":     {"value": 15.0, "min": 6.0,  "max": 24.0},
    "CONTRACT_SURGE":        {"value": 15.0, "min": 6.0,  "max": 22.0},
    "CALL_SWEEP":            {"value": 12.0, "min": 5.0,  "max": 18.0},
    "NEW_WINNER":            {"value": 10.0, "min": 4.0,  "max": 16.0},
    "BUDGET_SURGE":          {"value": 8.0,  "min": 3.0,  "max": 14.0},
    "upcoming_earnings":     {"value": 6.0,  "min": 2.0,  "max": 10.0},
    # ── Retail / technical / pattern signals (weighted DOWN) ──
    "high_short_interest":   {"value": 6.0,  "min": 2.0,  "max": 12.0},
    "squeeze_bonus":         {"value": 6.0,  "min": 2.0,  "max": 12.0},
    "UNUSUAL_FLOW":          {"value": 5.0,  "min": 2.0,  "max": 10.0},
    "MOMENTUM_STACK":        {"value": 4.0,  "min": 1.0,  "max": 9.0},
    "CONCENTRATION_WIN":     {"value": 4.0,  "min": 1.0,  "max": 9.0},
    "committee_match_bonus": {"value": 2.0,  "min": 1.0,  "max": 5.0},
}

BASELINE_WR = 0.50    # 50% win rate baseline
MAX_CHANGE = 0.15     # max ±15% per cycle
MIN_SAMPLES = 10      # min trades before adjusting a weight (30d basis)
MIN_SAMPLES_LIVE = 10  # minimum scanned marks before any live-basis adjustment


async def _collect_live_trades() -> list[dict[str, Any]]:
    """Build display-only live marks for surfaced tickers.

    Live marks may inform reporting, but weight changes require a larger
    minimum sample and prefer closed 30-day observations when available.
    """
    db = get_db()
    rows = await db.signal_first_seen.find({}, {"_id": 0}).to_list(5000)
    if not rows:
        return []
    tickers = sorted({r["ticker"] for r in rows if r.get("ticker")})
    cur_prices = await pricer.batch_latest_closes(tickers)
    now = datetime.now(timezone.utc).date()

    trades: list[dict[str, Any]] = []
    for r in rows:
        t = r.get("ticker")
        entry = r.get("first_seen_price")
        if not t or not entry or entry <= 0:
            continue
        cur = cur_prices.get(t)
        if cur is None or cur <= 0:
            continue
        try:
            d = datetime.fromisoformat(r["first_seen_date"]).date()
        except Exception:
            continue
        age_days = (now - d).days
        ret_live = round((cur - entry) / entry * 100.0, 2)
        trades.append({
            "ticker": t,
            "date": r["first_seen_date"],
            "signals": r.get("first_signals") or [],
            "strategy_lanes": r.get("first_strategy_lanes") or [],
            "signal_score": r.get("first_signal_score"),
            "entry_price": entry,
            "current_price": cur,
            "age_days": age_days,
            "return_live": ret_live,
            "risk_level": r.get("first_risk_level"),
        })
    return trades


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


async def refresh_combo_stats_live() -> int:
    """Cheap data refresh: recompute combo_stats from live trades (every scan
    counts as a trade). Does NOT adjust weights. Called after every scan so the
    Learning page reflects newly-surfaced signals immediately."""
    db = get_db()
    live_trades = await _collect_live_trades()
    perf_docs = await db.signal_performance.find(
        {"return_30d": {"$ne": None}}, {"_id": 0},
    ).to_list(5000)

    combo_map: dict[str, list[dict]] = {}
    combo_basis: dict[str, str] = {}
    for d in perf_docs:
        sigs = sorted(d.get("signals", []) or [])
        for size in (2, 3):
            if len(sigs) < size:
                continue
            for combo in itertools.combinations(sigs, size):
                key = "|".join(combo)
                combo_map.setdefault(key, []).append({**d, "_ret": d["return_30d"]})
                combo_basis[key] = "30d"
    for t in live_trades:
        sigs = sorted(t.get("signals", []) or [])
        for size in (2, 3):
            if len(sigs) < size:
                continue
            for combo in itertools.combinations(sigs, size):
                key = "|".join(combo)
                if combo_basis.get(key) == "30d":
                    continue
                combo_map.setdefault(key, []).append({**t, "_ret": t["return_live"]})
                combo_basis.setdefault(key, "live")

    written = 0
    now = datetime.now(timezone.utc).isoformat()
    for key, trades in combo_map.items():
        if len(trades) < 2:
            continue
        wins = sum(1 for tr in trades if (tr.get("_ret") or 0) > 0)
        avg_ret = sum(tr["_ret"] for tr in trades) / len(trades)
        best = max(tr["_ret"] for tr in trades)
        worst = min(tr["_ret"] for tr in trades)
        await db.combo_stats.update_one(
            {"signal_combo": key},
            {"$set": stamped({
                "signal_combo": key,
                "trade_count": len(trades),
                "win_count": wins,
                "win_rate": round(wins / len(trades), 3),
                "avg_return_30d": round(avg_ret, 2),
                "basis": combo_basis.get(key, "live"),
                "best_return": round(best, 2),
                "worst_return": round(worst, 2),
                "last_updated": now,
            })},
            upsert=True,
        )
        written += 1
    return written


async def run_learning_cycle() -> dict[str, Any]:
    """Weekly cycle. Analyze EVERY scanned stock (live return basis), adjust
    weights. Uses 30d returns when ≥10 trades available, else live returns
    with ≥3 trades and reduced confidence weighting."""
    db = get_db()
    await ensure_weights_exist()
    await log_activity("Learning cycle started", "info")

    live_trades = await _collect_live_trades()
    perf_docs = await db.signal_performance.find(
        {"return_30d": {"$ne": None}}, {"_id": 0},
    ).to_list(5000)

    if len(live_trades) < MIN_SAMPLES_LIVE:
        msg = (f"Learning skipped — only {len(live_trades)} scanned stocks "
                f"(need {MIN_SAMPLES_LIVE}+)")
        await log_activity(msg, "warn")
        return {"skipped": True, "reason": msg, "trades": len(live_trades)}
    # Per-signal performance: prefer 30d basis when available, fall back to live
    signal_stats: dict[str, dict | None] = {}
    for key in DEFAULT_WEIGHTS.keys():
        perf_rel = [d for d in perf_docs if key in (d.get("signals") or []) or key in (d.get("strategy_lanes") or [])]
        live_rel = [t for t in live_trades if key in (t.get("signals") or []) or key in (t.get("strategy_lanes") or [])]
        if len(perf_rel) >= MIN_SAMPLES:
            wins = [d for d in perf_rel if (d.get("return_30d") or 0) > 0]
            signal_stats[key] = {
                "count": len(perf_rel),
                "wins": len(wins),
                "win_rate": round(len(wins) / len(perf_rel), 3),
                "avg_return": round(sum(d["return_30d"] for d in perf_rel) / len(perf_rel), 2),
                "basis": "30d",
            }
        elif len(live_rel) >= MIN_SAMPLES_LIVE:
            returns = [t["return_live"] for t in live_rel]
            wins = sum(1 for r in returns if r > 0)
            signal_stats[key] = {
                "count": len(live_rel),
                "wins": wins,
                "win_rate": round(wins / len(returns), 3),
                "avg_return": round(sum(returns) / len(returns), 2),
                "basis": "live",
            }
        else:
            signal_stats[key] = None

    # Per-combo performance — use live trades by default (more data) and
    # include 30d trades as well
    combo_map: dict[str, list[dict]] = {}
    combo_source: dict[str, str] = {}
    for d in perf_docs:
        sigs = sorted(d.get("signals", []) or [])
        for size in (2, 3):
            if len(sigs) < size:
                continue
            for combo in itertools.combinations(sigs, size):
                key = "|".join(combo)
                combo_map.setdefault(key, []).append({**d, "_ret": d["return_30d"]})
                combo_source[key] = "30d"
    for t in live_trades:
        sigs = sorted(t.get("signals", []) or [])
        for size in (2, 3):
            if len(sigs) < size:
                continue
            for combo in itertools.combinations(sigs, size):
                key = "|".join(combo)
                # Only add live trades to combos that DON'T already have 30d data
                if combo_source.get(key) == "30d":
                    continue
                combo_map.setdefault(key, []).append({**t, "_ret": t["return_live"]})
                combo_source.setdefault(key, "live")

    # Persist combo stats
    for key, trades in combo_map.items():
        if len(trades) < 3:
            continue
        wins = sum(1 for tr in trades if (tr.get("_ret") or 0) > 0)
        avg_ret = sum(tr["_ret"] for tr in trades) / len(trades)
        best = max(tr["_ret"] for tr in trades)
        worst = min(tr["_ret"] for tr in trades)
        await db.combo_stats.update_one(
            {"signal_combo": key},
            {"$set": stamped({
                "signal_combo": key,
                "trade_count": len(trades),
                "win_count": wins,
                "win_rate": round(wins / len(trades), 3),
                "avg_return_30d": round(avg_ret, 2),
                "basis": combo_source.get(key, "live"),
                "best_return": round(best, 2),
                "worst_return": round(worst, 2),
                "last_updated": datetime.now(timezone.utc).isoformat(),
            })},
            upsert=True,
        )

    # Adjust weights — live basis gets reduced confidence
    weight_rows = await db.learning_weights.find({}, {"_id": 0}).to_list(100)
    changes: dict[str, dict] = {}
    now = datetime.now(timezone.utc).isoformat()
    snapshot: dict[str, float] = {}
    for row in weight_rows:
        key = row["weight_key"]
        current = row["current_value"]
        wmin = row["min_value"]
        wmax = row["max_value"]
        snapshot[key] = round(current, 2)
        stats = signal_stats.get(key)
        if not stats:
            continue
        # Confidence: 30d basis caps at 100%, live basis caps at 60%
        denom = 30.0 if stats["basis"] == "live" else 50.0
        confidence = min(stats["count"] / denom, 1.0)
        if stats["basis"] == "live":
            confidence *= 0.6
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
            # Append per-weight history row
            await db.learning_weight_history.insert_one(stamped({
                "weight_key": key,
                "ts": now,
                "old_value": round(current, 2),
                "new_value": round(new_value, 2),
                "win_rate": stats["win_rate"],
                "sample_count": stats["count"],
                "confidence": round(confidence, 2),
            }))
            changes[key] = {
                "old": round(current, 2),
                "new": round(new_value, 2),
                "delta": round(new_value - current, 2),
                "pct": round((new_value - current) / current * 100, 1) if current else 0,
            }
            snapshot[key] = round(new_value, 2)

    # Build insights — use live trades since they're the always-present set
    # (rename for _build_insights compatibility: pass live-trade-shaped docs)
    docs_for_insights = [
        {**d, "return_30d": d["return_30d"]} for d in perf_docs
    ] + [
        {**t, "return_30d": t["return_live"]} for t in live_trades
        if not any(p["ticker"] == t["ticker"] and p["date"] == t["date"]
                    for p in perf_docs)
    ]
    insights = _build_insights(signal_stats, combo_map, changes, docs_for_insights)
    wins_all = sum(1 for d in docs_for_insights if (d.get("return_30d") or 0) > 0)
    overall_wr = wins_all / len(docs_for_insights) if docs_for_insights else 0

    await db.learning_runs.insert_one(stamped({
        "run_at": now,
        "trades_analyzed": len(docs_for_insights),
        "trades_30d": len(perf_docs),
        "trades_live": len(live_trades),
        "weights_changed": changes,
        "weights_snapshot": snapshot,
        "overall_win_rate": round(overall_wr, 3),
        "insights": insights,
    }))
    await log_activity(
        f"Learning cycle complete — {len(docs_for_insights)} trades "
        f"({len(perf_docs)} 30d + {len(live_trades)} live), "
        f"{len(changes)} weights adjusted, WR={overall_wr:.1%}", "info",
    )

    return {"trades": len(docs_for_insights), "changes": len(changes),
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
    valid_combos = [(k, v) for k, v in combo_map.items() if len(v) >= 3]
    if valid_combos:
        def _ret(d):
            return d.get("_ret") if d.get("_ret") is not None else d.get("return_30d", 0)
        best = max(valid_combos,
                   key=lambda x: sum(_ret(d) for d in x[1]) / len(x[1]))
        k, trades = best
        avg = sum(_ret(d) for d in trades) / len(trades)
        wr = sum(1 for d in trades if (_ret(d) or 0) > 0) / len(trades)
        insights.append(
            f"⚡ Best combo: [{k.replace('|', ' + ').upper()}] "
            f"— {wr:.0%} WR, avg {avg:+.1f}%"
        )
    for key, ch in changes.items():
        arrow = "📈" if ch["delta"] > 0 else "📉"
        insights.append(f"{arrow} {key}: {ch['old']} → {ch['new']} ({ch['pct']:+.1f}%)")
    return insights



async def preview_learning_cycle() -> dict[str, Any]:
    """Dry-run: compute the adjustments the next cycle WOULD make, without
    writing anything. Uses LIVE return data (every scanned stock counts as
    a trade immediately) — gives the user actionable signal-quality numbers
    on day one instead of waiting 30 days for return_30d to fill."""
    db = get_db()
    await ensure_weights_exist()
    live_trades = await _collect_live_trades()
    perf_docs = await db.signal_performance.find(
        {"return_30d": {"$ne": None}}, {"_id": 0},
    ).to_list(5000)
    weight_rows = await db.learning_weights.find({}, {"_id": 0}).to_list(100)
    weights_by_key = {w["weight_key"]: w for w in weight_rows}

    # Per-signal stats: LIVE basis (every scanned stock) with optional 30d enhancement
    signal_stats: dict[str, dict] = {}
    for key in DEFAULT_WEIGHTS.keys():
        live_rel = [t for t in live_trades if key in (t.get("signals") or [])]
        perf_rel = [d for d in perf_docs if key in (d.get("signals") or [])]
        # Prefer 30d data when we have enough; fall back to live
        if len(perf_rel) >= MIN_SAMPLES:
            wins = [d for d in perf_rel if (d.get("return_30d") or 0) > 0]
            signal_stats[key] = {
                "count": len(perf_rel),
                "win_rate": round(len(wins) / len(perf_rel), 3),
                "avg_return": round(sum(d["return_30d"] for d in perf_rel) / len(perf_rel), 2),
                "basis": "30d",
            }
        elif len(live_rel) >= MIN_SAMPLES_LIVE:
            returns = [t["return_live"] for t in live_rel]
            wins = sum(1 for r in returns if r > 0)
            signal_stats[key] = {
                "count": len(live_rel),
                "win_rate": round(wins / len(returns), 3),
                "avg_return": round(sum(returns) / len(returns), 2),
                "basis": "live",
            }
        else:
            signal_stats[key] = None

    preview: list[dict[str, Any]] = []
    for key, w in weights_by_key.items():
        stats = signal_stats.get(key)
        row: dict[str, Any] = {
            "weight_key": key,
            "current": w["current_value"],
            "min": w["min_value"],
            "max": w["max_value"],
            "samples": stats["count"] if stats else 0,
            "basis": stats["basis"] if stats else None,
            "win_rate": stats["win_rate"] if stats else None,
            "avg_return": stats["avg_return"] if stats else None,
            "projected": w["current_value"],
            "would_change": False,
            "blocked_reason": None,
        }
        if not stats:
            live_count = sum(1 for t in live_trades if key in (t.get("signals") or []))
            row["blocked_reason"] = (
                f"need {MIN_SAMPLES_LIVE}+ scanned stocks "
                f"({live_count} so far)"
            )
        else:
            # Live basis uses smaller confidence weighting (less stable)
            denom = 30.0 if stats["basis"] == "live" else 50.0
            confidence = min(stats["count"] / denom, 1.0)
            if stats["basis"] == "live":
                confidence *= 0.6  # cap live-basis confidence at 60%
            wr_delta = stats["win_rate"] - BASELINE_WR
            max_adj = w["current_value"] * MAX_CHANGE
            adjustment = wr_delta * max_adj * confidence
            new_value = max(w["min_value"], min(w["max_value"],
                              w["current_value"] + adjustment))
            row["projected"] = round(new_value, 2)
            row["delta"] = round(new_value - w["current_value"], 2)
            row["pct"] = round((new_value - w["current_value"]) / w["current_value"] * 100, 1) if w["current_value"] else 0
            row["confidence"] = round(confidence, 2)
            row["would_change"] = abs(new_value - w["current_value"]) > 0.05
        preview.append(row)

    eligible_changes = sum(1 for p in preview if p["would_change"])
    total_trades = len(live_trades)
    return {
        "trades_available": total_trades,
        "trades_30d": len(perf_docs),
        "trades_live": len(live_trades),
        "min_required": MIN_SAMPLES_LIVE,
        "would_run": total_trades >= MIN_SAMPLES_LIVE,
        "would_change_count": eligible_changes,
        "rows": preview,
    }


async def weight_history(weight_key: str | None = None,
                          limit: int = 500) -> list[dict[str, Any]]:
    db = get_db()
    q = {"weight_key": weight_key} if weight_key else {}
    rows = await db.learning_weight_history.find(q, {"_id": 0}).sort("ts", 1).to_list(limit)
    return rows


async def signal_lifetime_stats() -> list[dict[str, Any]]:
    """Per-signal historical win rate + avg return across EVERY scanned stock
    (live return basis). Also blends in 30d-return data when available."""
    db = get_db()
    live = await _collect_live_trades()
    perf_docs = await db.signal_performance.find(
        {"return_30d": {"$ne": None}}, {"_id": 0},
    ).to_list(5000)
    out: list[dict[str, Any]] = []
    for key in DEFAULT_WEIGHTS.keys():
        live_rel = [t for t in live if key in (t.get("signals") or []) or key in (t.get("strategy_lanes") or [])]
        perf_rel = [d for d in perf_docs if key in (d.get("signals") or []) or key in (d.get("strategy_lanes") or [])]
        if not live_rel and not perf_rel:
            out.append({
                "signal": key, "n": 0, "n_live": 0,
                "win_rate": None, "win_rate_live": None,
                "avg_live": None, "avg_30d": None, "avg_7d": None, "avg_90d": None,
                "best": None, "worst": None,
            })
            continue
        # Live (every scanned stock counts)
        live_returns = [t["return_live"] for t in live_rel]
        wins_live = sum(1 for x in live_returns if x > 0)
        # 30d perf (when fully matured)
        r30 = [d["return_30d"] for d in perf_rel if d.get("return_30d") is not None]
        r7 = [d["return_7d"] for d in perf_rel if d.get("return_7d") is not None]
        r90 = [d["return_90d"] for d in perf_rel if d.get("return_90d") is not None]
        wins_30 = sum(1 for x in r30 if x > 0)
        out.append({
            "signal": key,
            "n": len(live_rel),
            "n_live": len(live_rel),
            "n_30d": len(r30),
            "win_rate": round(wins_live / len(live_returns), 3) if live_returns else None,
            "win_rate_live": round(wins_live / len(live_returns), 3) if live_returns else None,
            "win_rate_30d": round(wins_30 / len(r30), 3) if r30 else None,
            "avg_live": round(sum(live_returns) / len(live_returns), 2) if live_returns else None,
            "avg_30d": round(sum(r30) / len(r30), 2) if r30 else None,
            "avg_7d": round(sum(r7) / len(r7), 2) if r7 else None,
            "avg_90d": round(sum(r90) / len(r90), 2) if r90 else None,
            "best": round(max(live_returns), 2) if live_returns else None,
            "worst": round(min(live_returns), 2) if live_returns else None,
        })
    out.sort(key=lambda x: (x["avg_live"] is None, -(x["avg_live"] or -999)))
    return out
