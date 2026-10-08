"""Research-only, fixed-session equity outcomes and score calibration.

Reads immutable PM observations, never orders or active learning weights.
Signal-mark returns are not executable backtest returns. Missing closes and
costs remain missing; repeated sightings are excluded per ticker/lane/horizon.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any

from .db import get_db
from .kronos_contract import ET, exchange, parse_time

VERSION = "strategy_evidence_v1"
HORIZONS = (1, 5, 20)
_refresh_lock = asyncio.Lock()


def number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


@lru_cache(maxsize=20000)
def target_close(observed_at: str, horizon: int) -> datetime | None:
    """Nth session close strictly after the observation, including today if open."""
    observed = parse_time(observed_at)
    if not observed or horizon not in HORIZONS:
        return None
    schedule = exchange(observed.year).schedule
    after = schedule.loc[observed.astimezone(ET).date().isoformat():]
    closes = [r["close"].to_pydatetime() for _, r in after.iterrows()
              if r["close"].to_pydatetime() > observed]
    return closes[horizon - 1] if len(closes) >= horizon else None


def lanes(row: dict) -> list[str]:
    views = row.get("strategy_views") or []
    identities = [str(v.get("screener_id") or v.get("lane") or "") for v in views if isinstance(v, dict)]
    identities += [str(v) for v in row.get("source_scans") or []]
    return sorted(set(v.strip().upper() for v in identities if v.strip())) or ["UNATTRIBUTED"]


def episodes(observations: list[dict]) -> tuple[list[dict], dict]:
    """Disjoint forward windows per ticker/strategy/horizon, not raw sightings."""
    rows = []
    excluded = Counter()
    last_end: dict[tuple, datetime] = {}
    ordered = sorted(observations, key=lambda r: (str(r.get("observed_at") or ""), str(r.get("observation_id") or "")))
    for row in ordered:
        observed = parse_time(row.get("observed_at"))
        ticker = str(row.get("ticker") or "").strip().upper()
        price = number(row.get("price"))
        score = number(row.get("pm_score"))
        if not observed or not ticker or price is None or price <= 0 or score is None:
            excluded["invalid_observation"] += 1
            continue
        for strategy in lanes(row):
            for horizon in HORIZONS:
                target = target_close(observed.isoformat(), horizon)
                if target is None:
                    excluded["session_calendar_unavailable"] += 1
                    continue
                key = (ticker, strategy, horizon)
                if observed <= last_end.get(key, datetime.min.replace(tzinfo=timezone.utc)):
                    excluded["overlapping_sighting"] += 1
                    continue
                last_end[key] = target
                identity = f"{VERSION}:{row.get('observation_id') or observed.isoformat()}:{ticker}:{strategy}:{horizon}"
                rows.append({
                    "episode_id": hashlib.sha256(identity.encode()).hexdigest(),
                    "observation_id": row.get("observation_id"), "ticker": ticker,
                    "strategy_id": strategy, "horizon_sessions": horizon,
                    "observed_at": observed.isoformat(), "target_at": target.isoformat(),
                    "target_date": target.astimezone(ET).date().isoformat(),
                    "cohort_date": observed.astimezone(ET).date().isoformat(),
                    "entry_mark": price, "pm_score": score, "action": row.get("action"),
                    "signals": row.get("signals") or [], "route": row.get("route"),
                    "scoring_version": row.get("scoring_version") or "LEGACY_UNVERSIONED",
                    "mode": row.get("mode") or "UNKNOWN", "regime": row.get("regime") or "UNKNOWN",
                    "price_evidence": row.get("price_evidence") or {"status": "UNVERIFIED_LEGACY_MARK"},
                    "return_basis": "observed_signal_mark_to_exact_session_close",
                    "research_only": True, "version": VERSION,
                })
    return rows, dict(excluded)


def resolve(episode: dict, closes: dict, spy_closes: dict, now: datetime) -> dict:
    result = {**episode, "status": "PENDING", "gross_return_pct": None,
              "benchmark_return_pct": None, "excess_return_pct": None,
              "net_return_pct": None, "cost_status": "UNAVAILABLE",
              "benchmark_basis": "prior_completed_close_to_target_close",
              "benchmark_mismatch": "SPY entry is prior close, not simultaneous intraday mark"}
    target = parse_time(episode["target_at"])
    if target > now:
        return result
    end = number(closes.get(episode["target_date"]))
    if end is None or end <= 0:
        return {**result, "status": "MISSING_EXACT_TARGET_CLOSE"}
    observed = parse_time(episode["observed_at"])
    prior = exchange(observed.year).schedule.loc[:observed.astimezone(ET).date().isoformat()]
    completed = [day.date().isoformat() for day, r in prior.iterrows() if r["close"].to_pydatetime() <= observed]
    spy_start = number(spy_closes.get(completed[-1])) if completed else None
    spy_end = number(spy_closes.get(episode["target_date"]))
    gross = (end / episode["entry_mark"] - 1) * 100
    result.update(status="RESOLVED", exit_mark=end, gross_return_pct=round(gross, 6))
    if spy_start and spy_start > 0 and spy_end and spy_end > 0:
        benchmark = (spy_end / spy_start - 1) * 100
        result.update(benchmark_return_pct=round(benchmark, 6), excess_return_pct=round(gross - benchmark, 6))
    else:
        result["status"] = "RESOLVED_GROSS_ONLY"
    return result


def ranks(values: list[float]) -> list[float]:
    grouped = defaultdict(list)
    for index, value in enumerate(values):
        grouped[value].append(index)
    out = [0.0] * len(values)
    position = 1
    for value in sorted(grouped):
        indexes = grouped[value]
        rank = position + (len(indexes) - 1) / 2
        for index in indexes:
            out[index] = rank
        position += len(indexes)
    return out


def spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) < 3 or len(x) != len(y) or len(set(x)) < 2 or len(set(y)) < 2:
        return None
    return statistics.correlation(ranks(x), ranks(y))


def scorecard(rows: list[dict]) -> dict:
    complete = [r for r in rows if r.get("status") in {"RESOLVED", "RESOLVED_GROSS_ONLY"}]
    gross = [r["gross_return_pct"] for r in complete]
    cohorts = defaultdict(list)
    for row in complete:
        cohorts[row["cohort_date"]].append(row)
    daily_ic = []
    deciles = defaultdict(list)
    for day, items in sorted(cohorts.items()):
        valid = [r for r in items if r.get("excess_return_pct") is not None]
        if len(valid) < 5:
            continue
        ic = spearman([r["pm_score"] for r in valid], [r["excess_return_pct"] for r in valid])
        daily_ic.append({"date": day, "n": len(valid), "ic": ic})
        scores = ranks([r["pm_score"] for r in valid])
        for row, rank in zip(valid, scores):
            decile = min(10, int((rank - 1) * 10 / len(valid)) + 1)
            deciles[decile].append(row["excess_return_pct"])
    ics = [r["ic"] for r in daily_ic if r["ic"] is not None]
    alphas = [r["excess_return_pct"] for r in complete if r.get("excess_return_pct") is not None]
    signal_groups = defaultdict(list)
    for row in complete:
        for signal in set(str(s) for s in row.get("signals") or []):
            signal_groups[signal].append(row["gross_return_pct"])
    return {
        "episodes": len(rows), "resolved": len(complete), "statuses": dict(Counter(r["status"] for r in rows)),
        "mean_gross_return_pct": statistics.mean(gross) if gross else None,
        "median_gross_return_pct": statistics.median(gross) if gross else None,
        "win_rate": sum(r > 0 for r in gross) / len(gross) if gross else None,
        "benchmark_pairs": len(alphas), "mean_excess_return_pct": statistics.mean(alphas) if alphas else None,
        "mean_net_return_pct": None, "net_sample": 0,
        "daily_rank_ic": daily_ic, "mean_daily_rank_ic": statistics.mean(ics) if ics else None,
        "ic_days": len(ics), "ic_t_stat": None,
        "inference_status": "DESCRIPTIVE_ONLY_DEPENDENT_COHORTS_NO_SIGNIFICANCE_CLAIM",
        "score_deciles": [{"decile": d, "n": len(v), "mean_excess_return_pct": statistics.mean(v)} for d, v in sorted(deciles.items())],
        "by_action": {action: {"n": len(v), "mean_gross_return_pct": statistics.mean(v)}
                      for action, v in _action_returns(complete).items()},
        "signal_associations": [{"signal": signal, "n": len(values), "mean_gross_return_pct": statistics.mean(values),
                                 "causal_attribution": False} for signal, values in sorted(signal_groups.items())],
        "regimes": dict(Counter(str(r.get("regime") or "UNKNOWN") for r in complete)),
        "live_promotion_allowed": False,
    }


def _action_returns(rows: list[dict]) -> dict:
    out = defaultdict(list)
    for row in rows:
        out[str(row.get("action") or "UNKNOWN")].append(row["gross_return_pct"])
    return out


async def refresh(*, limit: int = 5000, lookback_days: int = 90, max_symbols: int = 120) -> dict:
    if _refresh_lock.locked():
        return {"ok": False, "reason": "refresh_already_running", "research_only": True}
    async with _refresh_lock:
        return await _refresh(limit=min(20000, max(1, limit)), lookback_days=min(365, max(1, lookback_days)), max_symbols=max_symbols)


async def _refresh(*, limit: int, lookback_days: int, max_symbols: int) -> dict:
    from . import pricer
    db = get_db()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=lookback_days)
    observations = await db.pm_company_observations.find({"observed_at": {"$gte": cutoff.isoformat()}}, {"_id": 0}).sort("observed_at", -1).to_list(limit + 1)
    truncated = len(observations) > limit
    episode_rows, exclusions = episodes(observations[:limit])
    # First retain persisted resolved facts. Provider outages must not erase them.
    saved = await db.strategy_evidence_outcomes.find({"version": VERSION}, {"_id": 0}).to_list(None)
    saved_map = {r["episode_id"]: r for r in saved}
    due = [r for r in episode_rows if r["episode_id"] not in saved_map and parse_time(r["target_at"]) <= now]
    symbols = sorted(set(r["ticker"] for r in due))[:max(1, min(500, max_symbols))]
    start = (cutoff - timedelta(days=10)).date().isoformat()
    end = now.date().isoformat()
    sem = asyncio.Semaphore(3)
    histories = {}
    errors = {}
    async def pull(symbol):
        async with sem:
            try:
                histories[symbol] = await asyncio.wait_for(pricer.get_history_range(symbol, start, end), timeout=20)
            except Exception as exc:
                errors[symbol] = type(exc).__name__
                histories[symbol] = {}
    await asyncio.gather(*(pull(symbol) for symbol in sorted(set(symbols + ["SPY"]))))
    resolved = []
    for episode in episode_rows:
        outcome = saved_map.get(episode["episode_id"])
        if outcome is None:
            outcome = resolve(episode, histories.get(episode["ticker"], {}), histories.get("SPY", {}), now)
            if episode["ticker"] not in symbols and parse_time(episode["target_at"]) <= now:
                outcome["status"] = "DEFERRED_PROVIDER_BUDGET"
            if outcome["status"] == "RESOLVED":
                outcome["resolved_at"] = now.isoformat()
                await db.strategy_evidence_outcomes.update_one({"episode_id": episode["episode_id"]}, {"$setOnInsert": outcome}, upsert=True)
        resolved.append(outcome)
    groups = defaultdict(list)
    for row in resolved:
        groups[(row["strategy_id"], row["horizon_sessions"], row["scoring_version"], row["mode"])].append(row)
    report = {
        "ok": not truncated and not errors and all(r["status"] in {"RESOLVED", "PENDING"} for r in resolved),
        "research_only": True, "decision_authority": "NONE", "version": VERSION,
        "generated_at": now.isoformat(), "observations": len(observations[:limit]),
        "truncated": truncated, "lookback_days": lookback_days,
        "exclusions": exclusions, "provider_errors": errors, "provider_symbols": len(symbols),
        "scorecards": [{"strategy_id": strategy, "horizon_sessions": horizon, "scoring_version": version, "mode": mode, **scorecard(rows)}
                       for (strategy, horizon, version, mode), rows in sorted(groups.items())],
        "limitations": ["Signal marks are not fills; historical mark freshness may be unverified.",
                        "Excess returns use prior-close SPY, not a simultaneous entry; not risk-adjusted alpha.",
                        "Net expectancy is unavailable without explicit round-trip costs.",
                        "Per-lane windows are disjoint, but cross-symbol and cross-lane outcomes remain correlated.",
                        "No model or active strategy weights are promoted by this report."],
    }
    await db.bot_state.update_one({"_id": "strategy_evidence_latest"}, {"$set": {"report": report}}, upsert=True)
    return report


async def latest() -> dict:
    row = await get_db().bot_state.find_one({"_id": "strategy_evidence_latest"}, {"_id": 0})
    return (row or {}).get("report") or {"ok": False, "status": "NOT_RUN", "research_only": True, "decision_authority": "NONE", "scorecards": []}
