"""Pure helpers for evidence-first strategy replay.

This module deliberately does not place orders or infer missing market facts.
It turns frozen candidate observations into independent ticker/strategy episodes
and reports robust outcome statistics. Callers must label unavailable outcomes
instead of quietly treating them as zero-return observations.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from math import sqrt
from statistics import median, stdev
from typing import Any, Iterable


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None


def strategy_id(row: dict[str, Any]) -> str:
    """Return the narrowest stable strategy identifier carried by a row."""
    scanner = row.get("strategy_scanner") if isinstance(row.get("strategy_scanner"), dict) else {}
    return str(
        row.get("strategy_id")
        or row.get("screener_id")
        or row.get("source_scan")
        or scanner.get("screener_id")
        or row.get("scanner_family")
        or "CORE"
    ).upper()


def observation_quality(row: dict[str, Any]) -> tuple[bool, list[str]]:
    """Return whether a row has minimum facts for a path-dependent replay."""
    reasons: list[str] = []
    try:
        price = float(row.get("entry_price") or row.get("price") or 0)
    except (TypeError, ValueError):
        price = 0.0
    if price <= 0:
        reasons.append("missing_entry_price")
    if row.get("target_is_proxy"):
        reasons.append("proxy_target")
    targets = row.get("targets") if isinstance(row.get("targets"), dict) else {}
    target = row.get("target") or targets.get("target_blended")
    try:
        target_ok = float(target or 0) > price
    except (TypeError, ValueError):
        target_ok = False
    if not target_ok:
        reasons.append("missing_verified_target")
    risk = row.get("risk") if isinstance(row.get("risk"), dict) else {}
    stop = row.get("stop") or row.get("stop_loss") or risk.get("stop_loss")
    try:
        stop_ok = 0 < float(stop or 0) < price
    except (TypeError, ValueError):
        stop_ok = False
    if not stop_ok:
        reasons.append("missing_verified_stop")
    if row.get("read_only"):
        reasons.append("read_only")
    return not reasons, reasons


def episodeize(observations: Iterable[dict[str, Any]], *, cooldown_days: int = 5) -> list[dict[str, Any]]:
    """Keep the first observation in each ticker/strategy activity episode.

    A new episode starts after ``cooldown_days`` without a same-strategy
    observation. It is a conservative replay approximation until live records
    carry an explicit lifecycle exit/reset event.
    """
    if cooldown_days < 1:
        raise ValueError("cooldown_days must be at least one")
    ordered = sorted(
        (dict(row) for row in observations),
        key=lambda row: (
            strategy_id(row),
            str(row.get("ticker") or "").upper(),
            _as_date(row.get("observed_at") or row.get("date") or row.get("ts")) or date.max,
            str(row.get("observation_id") or ""),
        ),
    )
    last_seen: dict[tuple[str, str], date] = {}
    episode_numbers: dict[tuple[str, str], int] = defaultdict(int)
    out: list[dict[str, Any]] = []
    for row in ordered:
        ticker = str(row.get("ticker") or "").upper()
        observed = _as_date(row.get("observed_at") or row.get("date") or row.get("ts"))
        if not ticker or not observed:
            continue
        key = (ticker, strategy_id(row))
        previous = last_seen.get(key)
        if previous is not None and (observed - previous).days <= cooldown_days:
            last_seen[key] = observed
            continue
        episode_numbers[key] += 1
        row["episode_id"] = f"{key[0]}:{key[1]}:{episode_numbers[key]}"
        row["episode_started_at"] = observed.isoformat()
        row["episode_gap_days"] = cooldown_days
        out.append(row)
        last_seen[key] = observed
    return out


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] * (1 - (position - lower)) + ordered[upper] * (position - lower)


def summarize_returns(rows: Iterable[dict[str, Any]], *, return_key: str = "return_pct") -> dict[str, Any]:
    """Summarize explicit outcomes, retaining missingness in the output."""
    rows = list(rows)
    values: list[float] = []
    benchmarks: list[float] = []
    alphas: list[float] = []
    excluded: dict[str, int] = defaultdict(int)
    for row in rows:
        try:
            value = float(row.get(return_key))
        except (TypeError, ValueError):
            excluded["missing_return"] += 1
            continue
        values.append(value)
        try:
            benchmark = float(row.get("benchmark_return_pct"))
        except (TypeError, ValueError):
            excluded["missing_benchmark"] += 1
            continue
        benchmarks.append(benchmark)
        alphas.append(value - benchmark)
    if not values:
        return {"observations": len(rows), "matured": 0, "excluded": dict(excluded), "mean_return_pct": None, "median_return_pct": None, "win_rate": None, "mean_benchmark_return_pct": None, "mean_alpha_pct": None, "ci95_mean_return_pct": None}
    n = len(values)
    deviation = stdev(values) if n > 1 else 0.0
    margin = 1.96 * deviation / sqrt(n) if n > 1 else None
    average = sum(values) / n
    return {
        "observations": len(rows), "matured": n, "excluded": dict(excluded),
        "mean_return_pct": round(average, 2), "median_return_pct": round(median(values), 2),
        "win_rate": round(sum(value > 0 for value in values) / n, 3),
        "p10_return_pct": round(_percentile(values, 0.10), 2), "p90_return_pct": round(_percentile(values, 0.90), 2),
        "stdev_return_pct": round(deviation, 2),
        "ci95_mean_return_pct": [round(average - margin, 2), round(average + margin, 2)] if margin is not None else None,
        "mean_benchmark_return_pct": round(sum(benchmarks) / len(benchmarks), 2) if benchmarks else None,
        "mean_alpha_pct": round(sum(alphas) / len(alphas), 2) if alphas else None,
    }
