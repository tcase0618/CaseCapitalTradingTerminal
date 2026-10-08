"""Bounded, synchronous research request facade; no I/O or execution imports.

Required payload: bars (<=10000), entry_price, entry_timestamp, source.
Optional terminal_mark: {price, timestamp, source}; optional spy:
{entry_price, entry_timestamp, terminal_price, terminal_timestamp, source}.
Comparison dates must exactly match entry_timestamp and period_end (explicit,
or the final bar's end_timestamp). References are supplied, never fetched.
Costs require all four CostAssumptions fields. No assumed zero fees.
"""
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from .path_replay import (
    CostAssumptions, _number, _time, chronological_train_holdout_split,
    replay_equity_path,
)

MAX_BARS = 10000
MAX_DIAGNOSTICS = 20


def _source(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError("a nonempty source of at most 512 characters is required")
    return value


def _returns(entry: float, terminal: float, costs: CostAssumptions | None) -> dict | None:
    if costs is None:
        return None
    friction = (costs.slippage_bps + costs.half_spread_bps) / 10000
    buy, sell = entry * (1 + friction), terminal * (1 - friction)
    fees = (buy + sell) * costs.fee_bps / 10000 + 2 * costs.fee_per_share
    return {"gross_return_pct": (terminal / entry - 1) * 100,
            "net_return_pct": (sell - buy - fees) /
            (buy * (1 + costs.fee_bps / 10000) + costs.fee_per_share) * 100}


def run_requested(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a JSON-style request and return compact, provenance-labelled research.

    ValueError denotes malformed configuration (suitable for an HTTP 422).
    Invalid OHLC remains a replay coverage condition, not invented data.
    Optional split contains holdout_start, purge_bars, protect_horizon_bars;
    only counts and boundary dates are returned, not duplicated input bars.
    """
    if not isinstance(payload, Mapping):
        raise ValueError("payload must be an object")
    bars = payload.get("bars")
    if not isinstance(bars, list) or not 1 <= len(bars) <= MAX_BARS:
        raise ValueError("bars must be a list containing 1..10000 bars")
    if any(not isinstance(row, Mapping) for row in bars):
        raise ValueError("each bar must be an object")
    try:
        entry = _number(payload["entry_price"], "entry_price", positive=True)
        start = _time(payload["entry_timestamp"])
        source = _source(payload["source"])
        end_value = payload.get("period_end", bars[-1].get("end_timestamp"))
        end = _time(end_value)
        if end <= start:
            raise ValueError("period_end must follow entry_timestamp")
        for row in bars:
            timestamp = _time(row["timestamp"])
            if timestamp >= end or ("end_timestamp" in row and _time(row["end_timestamp"]) > end):
                raise ValueError("bars extend beyond period_end")
        cost_dict = payload.get("costs")
        costs = None
        if cost_dict is not None:
            if not isinstance(cost_dict, Mapping) or set(cost_dict) != set(CostAssumptions.__dataclass_fields__):
                raise ValueError("costs must explicitly supply all four cost fields")
            costs = CostAssumptions(**cost_dict)
        expected = payload.get("expected_timestamps")
        if expected is not None:
            if not isinstance(expected, list) or len(expected) > MAX_BARS:
                raise ValueError("expected_timestamps must be a list of at most 10000 dates")
            if any(not start <= _time(t) < end for t in expected):
                raise ValueError("expected timestamps must be inside the research period")
        thresholds = payload.get("higher_floor_thresholds", [])
        if not isinstance(thresholds, list) or len(thresholds) > 1000:
            raise ValueError("higher_floor_thresholds must be a list of at most 1000 pairs")
        result = replay_equity_path(
            bars, entry_price=entry, entry_timestamp=start,
            initial_stop_pct=payload.get("initial_stop_pct", -10),
            initial_stop_price=payload.get("initial_stop_price"),
            higher_floor_thresholds=thresholds,
            dynamic_uncapped_floors=payload.get("dynamic_uncapped_floors", True),
            costs=costs, expected_timestamps=expected,
        )
        comparisons = {"oldway_terminal_mark": {"status": "not_supplied"},
                       "newpath": {"status": result["metrics_status"], "metrics": result["metrics"],
                                   "basis": "realized_exit_or_open_no_forced_liquidation"},
                       "spy": {"status": "not_supplied"}}
        mark = payload.get("terminal_mark")
        if mark is not None:
            if not isinstance(mark, Mapping) or _time(mark["timestamp"]) != end:
                raise ValueError("terminal_mark must match period_end exactly")
            mark_price = _number(mark["price"], "terminal_mark.price", positive=True)
            comparisons["oldway_terminal_mark"] = {
                "status": "available" if costs else "costs_unspecified",
                "source": _source(mark["source"]), "price": mark_price,
                "timestamp": end.isoformat(), "basis": "buy_and_hold_mark_not_actual_exit",
                "metrics": _returns(entry, mark_price, costs)}
        spy = payload.get("spy")
        if spy is not None:
            if (not isinstance(spy, Mapping) or _time(spy["entry_timestamp"]) != start
                    or _time(spy["terminal_timestamp"]) != end):
                raise ValueError("SPY must match entry and period_end exactly")
            spy_entry = _number(spy["entry_price"], "spy.entry_price", positive=True)
            spy_terminal = _number(spy["terminal_price"], "spy.terminal_price", positive=True)
            comparisons["spy"] = {"status": "available" if costs else "costs_unspecified",
                                  "source": _source(spy["source"]),
                                  "basis": "caller_supplied_same_period_buy_and_hold",
                                  "entry_price": spy_entry, "terminal_price": spy_terminal,
                                  "entry_timestamp": start.isoformat(), "terminal_timestamp": end.isoformat(),
                                  "metrics": _returns(spy_entry, spy_terminal, costs)}
        split_summary = None
        if payload.get("split") is not None:
            config = payload["split"]
            if not isinstance(config, Mapping):
                raise ValueError("split must be an object")
            split = chronological_train_holdout_split(bars, **config)
            split_summary = {"train_count": len(split["train"]),
                             "holdout_count": len(split["holdout"]),
                             "purged_count": len(split["purged"]),
                             "holdout_start": split["holdout_start"]}
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError(f"invalid research request: {exc}") from exc
    result["event_count"] = len(result.pop("events"))
    result["ambiguity_count"] = len(result["ambiguities"])
    result["ambiguities"] = result["ambiguities"][:MAX_DIAGNOSTICS]
    coverage = result["coverage"]
    coverage["issue_count"] = len(coverage["issues"])
    coverage["issues"] = coverage["issues"][:MAX_DIAGNOSTICS]
    return {"research_only": True, "noexecution": True, "execution_allowed": False,
            "provenance_status": "caller_supplied_not_independently_verified",
            "source": source, "period_start": start.isoformat(), "period_end": end.isoformat(),
            "cost_assumptions": asdict(costs) if costs else None,
            "replay": result, "comparisons": comparisons, "split": split_summary}
