"""Pure long-equity research replay, with no broker, persistence, or execution.

Bars are mappings with UTC-aware ``timestamp`` (interval start), open, high,
low, close and optional ``end_timestamp``. Prices must share the entry's split
adjustment basis. A bar containing an intrabar entry is skipped: its extrema
cannot establish a post-entry path. Supply expected_timestamps from the actual
market calendar to detect missing bars; cadence is never guessed.

OHLC cannot reveal tick order or stop liquidity. Both O-L-H-C and O-H-L-C
paths are evaluated; the lower exit/close value wins, preferring stop-first
on ties. Fills are conditional continuous-segment research assumptions, not
claims of executable prices. Known coverage defects suppress return metrics.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any


def _time(value: Any) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware datetimes or ISO strings")
    return value.astimezone(timezone.utc)


def _number(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric, not boolean")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError(f"invalid {name}")
    return result


@dataclass(frozen=True)
class CostAssumptions:
    """Explicit per-side costs; zero is allowed only when caller supplies it.

    Slippage and half-spread bps worsen each side; fee_bps applies to each
    side's modeled notional. fee_per_share is charged on each side as well.
    """

    fee_bps: float
    slippage_bps: float
    half_spread_bps: float
    fee_per_share: float

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = _number(getattr(self, name), name)
            if value < 0:
                raise ValueError("costs must be nonnegative")
            object.__setattr__(self, name, value)
        if self.slippage_bps + self.half_spread_bps >= 10000:
            raise ValueError("price friction must be below 10000 bps")


def _validated_bars(bars: Iterable[Mapping[str, Any]]) -> tuple[list[dict], list[dict]]:
    valid, issues = [], []
    seen: set[datetime] = set()
    previous = None
    for index, raw in enumerate(bars):
        try:
            timestamp = _time(raw["timestamp"])
        except (KeyError, TypeError, ValueError):
            issues.append({"index": index, "reason": "invalid_timestamp"})
            continue
        reason = None
        if timestamp in seen:
            reason = "duplicate_timestamp"
        elif previous is not None and timestamp < previous:
            reason = "nonchronological_bar"
        seen.add(timestamp)
        previous = max(previous, timestamp) if previous else timestamp
        try:
            row = {key: _number(raw[key], key, positive=True)
                   for key in ("open", "high", "low", "close")}
            end = _time(raw["end_timestamp"]) if "end_timestamp" in raw else None
            if (row["low"] > min(row["open"], row["close"])
                    or row["high"] < max(row["open"], row["close"])
                    or (end is not None and end <= timestamp)):
                raise ValueError("inconsistent bar")
        except (KeyError, TypeError, ValueError):
            reason = reason or "invalid_or_missing_ohlc"
        if reason:
            issues.append({"index": index, "timestamp": timestamp.isoformat(), "reason": reason})
            continue
        if valid and valid[-1]["end_timestamp"] is not None and timestamp < valid[-1]["end_timestamp"]:
            issues.append({"index": index, "reason": "overlapping_bar"})
            continue
        valid.append({**row, "timestamp": timestamp, "end_timestamp": end, "index": index})
    return valid, issues


def replay_equity_path(
    bars: Iterable[Mapping[str, Any]], *, entry_price: float,
    entry_timestamp: datetime | str, initial_stop_pct: float = -10.0,
    initial_stop_price: float | None = None,
    higher_floor_thresholds: Sequence[tuple[float, float]] = (),
    dynamic_uncapped_floors: bool = True,
    costs: CostAssumptions | None = None,
    expected_timestamps: Iterable[datetime | str] | None = None,
) -> dict[str, Any]:
    """Replay a full-position exit with strict arming and inclusive stop touches.

    Higher thresholds are (arm_gain_pct, locked_floor_pct), e.g. (30, 25),
    (40, 35) from public_stop_policy. Mandatory floors are (5,5),(10,10),
    (20,20). Dynamic mode adds public-policy floors at every ten-point
    milestone above 20, locking milestone minus five, without a ceiling.
    Caller thresholds override the dynamic floor at the same milestone.
    An initial_stop_price can retain a caller's tighter existing stop.
    Open positions are not forcibly liquidated and have no realized returns.
    """
    entry = _number(entry_price, "entry_price", positive=True)
    entered = _time(entry_timestamp)
    loss = _number(initial_stop_pct, "initial_stop_pct")
    if not -100 < loss < 0:
        raise ValueError("initial_stop_pct must be between -100 and 0")
    stop = entry + entry * loss / 100
    if initial_stop_price is not None:
        stop = max(stop, _number(initial_stop_price, "initial_stop_price", positive=True))
        if stop >= entry:
            raise ValueError("initial stop must be below entry")
    if costs is not None and not isinstance(costs, CostAssumptions):
        raise ValueError("costs must be explicit CostAssumptions")
    if not isinstance(dynamic_uncapped_floors, bool):
        raise ValueError("dynamic_uncapped_floors must be boolean")
    floors = [(5.0, 5.0), (10.0, 10.0), (20.0, 20.0)]
    supplied_arms = set()
    for arm, floor in higher_floor_thresholds:
        arm, floor = _number(arm, "arm"), _number(floor, "floor")
        if arm <= 20 or not 20 <= floor <= arm:
            raise ValueError("higher floors require arm > 20 and 20 <= floor <= arm")
        if arm in supplied_arms:
            raise ValueError("higher floor milestones must be unique")
        supplied_arms.add(arm)
        floors.append((arm, floor))
    floors = sorted(set(floors))
    levels = [(entry + entry * arm / 100, entry + entry * floor / 100)
              for arm, floor in floors]
    overrides = {arm: floor for arm, floor in floors}
    rows, issues = _validated_bars(bars)
    if expected_timestamps is not None:
        expected = {_time(t) for t in expected_timestamps if _time(t) >= entered}
        present = {row["timestamp"] for row in rows}
        issues.extend({"reason": "missing_bar", "timestamp": t.isoformat()}
                      for t in sorted(expected - present))

    def arm_at(price: float, active: float) -> float:
        active = max([active] + [floor for trigger, floor in levels if price > trigger])
        if dynamic_uncapped_floors:
            gain = round((price / entry - 1) * 100, 10)
            milestone = (math.ceil(gain / 10) - 1) * 10
            # Inspect overridden milestones plus the highest unoverridden one;
            # never iterate through arbitrarily large price gains.
            while milestone >= 30 and milestone in overrides:
                milestone -= 10
            if milestone >= 30:
                active = max(active, entry + entry * (milestone - 5) / 100)
        return active

    def walk(row: dict, active: float, order: str) -> dict:
        opening = row["open"]
        if opening <= active:
            return {"stop": active, "fill": opening,
                    "reason": "gap_below_stop" if opening < active else "stop_touch", "order": order}
        active = arm_at(opening, active)
        previous_price = opening
        for key in (("low", "high", "close") if order == "O-L-H-C" else ("high", "low", "close")):
            price = row[key]
            if price <= active and price <= previous_price:
                return {"stop": active, "fill": active,
                        "reason": "stop_touch" if price == active else "stop_downcross", "order": order}
            active = arm_at(price, active)
            previous_price = price
        return {"stop": active, "fill": None, "reason": None, "order": order}

    events, ambiguities = [], []
    exit_record = None
    processed = 0
    for row in rows:
        timestamp = row["timestamp"]
        if timestamp < entered:
            if row["end_timestamp"] is None or row["end_timestamp"] > entered:
                issues.append({"index": row["index"], "reason": "entry_boundary_bar_skipped"})
            continue
        if timestamp == entered and row["open"] != entry:
            issues.append({"index": row["index"], "reason": "incompatible_entry_open"})
            continue
        if exit_record is not None:
            continue
        processed += 1
        paths = [walk(row, stop, order) for order in ("O-L-H-C", "O-H-L-C")]
        selected = min(paths, key=lambda p: (p["fill"] if p["fill"] is not None else row["close"],
                                             p["fill"] is None))
        outcome_differs = ((paths[0]["fill"], paths[0]["stop"])
                           != (paths[1]["fill"], paths[1]["stop"]))
        open_stop = arm_at(row["open"], stop)
        high_stop = arm_at(row["high"], open_stop)
        # Equal fills do not prove equal timing: the low may precede arming.
        timing_ambiguous = (row["open"] >= stop and high_stop > open_stop
                            and row["low"] <= high_stop)
        if outcome_differs or timing_ambiguous:
            ambiguities.append({"timestamp": timestamp.isoformat(), "paths": paths,
                                "selected_order": selected["order"],
                                "outcome_differs": outcome_differs,
                                "intrabar_timing_unknown": True})
        stop = selected["stop"]
        events.append({"timestamp": timestamp.isoformat(), "active_stop": stop,
                       "selected_order": selected["order"]})
        if selected["fill"] is not None:
            exit_record = {"timestamp": timestamp.isoformat(), "price": selected["fill"],
                           "fraction": 1.0, "reason": selected["reason"]}
    if not processed:
        issues.append({"reason": "no_compatible_post_entry_bars"})
    metrics = None
    if costs is not None and exit_record is not None and not issues:
        fill = exit_record["price"]
        friction = (costs.slippage_bps + costs.half_spread_bps) / 10000
        buy, sell = entry * (1 + friction), fill * (1 - friction)
        fees = (buy + sell) * costs.fee_bps / 10000 + 2 * costs.fee_per_share
        net = sell - buy - fees
        metrics = {"gross_pnl_per_share": fill - entry, "gross_return_pct": (fill / entry - 1) * 100,
                   "net_pnl_per_share": net,
                   "net_return_pct": net / (buy * (1 + costs.fee_bps / 10000) + costs.fee_per_share) * 100}
    return {"research_only": True, "noexecution": True, "execution_allowed": False,
            "status": "closed" if exit_record else "open", "exit": exit_record,
            "active_stop": stop, "take_profit": None, "partial_trims": False,
            "events": events, "ambiguous": bool(ambiguities), "ambiguities": ambiguities,
            "coverage": {"status": "partial" if issues else "complete", "issues": issues,
                         "processed_bars": processed,
                         "missing_bar_check": "performed" if expected_timestamps is not None else "not_assessed"},
            "costs_supplied": costs is not None, "metrics": metrics,
            "metrics_status": ("available" if metrics is not None else
                               "costs_unspecified" if costs is None else
                               "incomplete_coverage" if issues else "no_realized_exit"),
            "fill_model": "conditional_continuous_segments_worst_ohlc_path"}


def chronological_train_holdout_split(
    bars: Iterable[Mapping[str, Any]], *, holdout_start: datetime | str,
    purge_bars: int = 0, protect_horizon_bars: int = 0,
) -> dict[str, Any]:
    """Split at an explicit, unshuffled boundary; reject invalid/overlapping data.

    A train bar ending after holdout_start would leak future extrema and is
    rejected. Bars starting exactly at the boundary belong to holdout.
    Purge max(purge_bars, protect_horizon_bars) trailing training rows so
    forward labels with that bar-count horizon cannot enter holdout.
    """
    boundary = _time(holdout_start)
    for count in (purge_bars, protect_horizon_bars):
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("purge and protection horizon must be nonnegative integer bar counts")
    raw = list(bars)
    rows, issues = _validated_bars(raw)
    if issues:
        raise ValueError(f"invalid split coverage: {issues}")
    train, holdout = [], []
    for row in rows:
        if row["timestamp"] < boundary:
            if row["end_timestamp"] is None:
                raise ValueError("train bars require end_timestamp to exclude boundary leakage")
            if row["end_timestamp"] > boundary:
                raise ValueError("train bar straddles holdout boundary")
            train.append(dict(raw[row["index"]]))
        else:
            holdout.append(dict(raw[row["index"]]))
    purge_count = max(purge_bars, protect_horizon_bars)
    purged = train[-purge_count:] if purge_count else []
    if purge_count:
        train = train[:-purge_count]
    if not train or not holdout:
        raise ValueError("both train and holdout must be nonempty")
    return {"train": train, "holdout": holdout, "holdout_start": boundary.isoformat(),
            "purged": purged, "purge_bars": purge_count,
            "research_only": True, "noexecution": True}
