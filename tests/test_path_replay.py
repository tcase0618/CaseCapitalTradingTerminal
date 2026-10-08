from datetime import datetime, timedelta, timezone
import copy
import math

import pytest

from services.path_replay import (
    CostAssumptions,
    chronological_train_holdout_split,
    replay_equity_path,
)
from services.research_path_service import run_requested


START = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
ZERO = CostAssumptions(0, 0, 0, 0)


def bar(day=0, opening=100, high=104, low=99, close=102):
    timestamp = START + timedelta(days=day)
    return {"timestamp": timestamp, "end_timestamp": timestamp + timedelta(hours=6),
            "open": opening, "high": high, "low": low, "close": close}


def replay(bars, **kwargs):
    return replay_equity_path(bars, entry_price=100, entry_timestamp=START, **kwargs)


def test_research_only_and_no_fabricated_returns_or_forced_close():
    result = replay([bar()])
    assert result["research_only"] and result["noexecution"]
    assert result["execution_allowed"] is False
    assert result["status"] == "open"
    assert result["metrics"] is None
    assert result["active_stop"] == 90
    assert result["take_profit"] is None
    assert not result["partial_trims"]
    assert result["coverage"]["missing_bar_check"] == "not_assessed"


@pytest.mark.parametrize("high,stop", [(105, 90), (105.01, 105), (110, 105),
                                         (110.01, 110), (120, 110), (120.01, 120)])
def test_floor_arming_is_strict(high, stop):
    result = replay([bar(high=high, low=100, close=high)])
    assert result["active_stop"] == stop


def test_armed_floor_touch_exits_everything():
    result = replay([bar(),
                     bar(1, 106, 108, 105, 105),
                     bar(2, 105, 106, 104, 104)], costs=ZERO)
    assert result["exit"]["timestamp"] == (START + timedelta(days=1)).isoformat()
    assert result["exit"]["price"] == 105
    assert result["exit"]["fraction"] == 1
    assert result["metrics"]["gross_return_pct"] == pytest.approx(5)


def test_old_stop_first_wins_when_daily_high_and_low_compete():
    result = replay([bar(high=130, low=80, close=125)], costs=ZERO)
    assert result["ambiguous"]
    assert result["ambiguities"][0]["selected_order"] == "O-L-H-C"
    assert result["exit"]["price"] == 90
    assert result["active_stop"] == 90


def test_new_floor_same_day_is_ambiguous_and_cannot_choose_best_path():
    result = replay([bar(high=108, low=99, close=107)])
    assert result["ambiguous"]
    assert result["exit"]["price"] == 105
    assert result["ambiguities"][0]["selected_order"] == "O-H-L-C"


def test_equal_fill_outcomes_still_report_unknown_intrabar_timing():
    result = replay([bar(high=108, low=99, close=100)])
    assert result["ambiguous"]
    assert result["ambiguities"][0]["outcome_differs"] is False
    assert result["ambiguities"][0]["intrabar_timing_unknown"]


def test_initial_stop_touch_sells():
    assert replay([bar(high=104, low=90, close=100)])["exit"]["price"] == 90


def test_higher_threshold_equality_does_not_arm_higher_floor():
    result = replay([bar(), bar(1, 130, 130, 121, 129)], higher_floor_thresholds=[(30, 25)])
    assert result["active_stop"] == 120
    assert result["exit"] is None


def test_low_first_can_exit_at_lower_floor_than_high_first():
    result = replay([bar(), bar(1, 106, 108, 105.5, 106),
                     bar(2, 106, 125, 104, 124)])
    assert result["exit"]["price"] == 105
    assert result["ambiguities"][-1]["selected_order"] == "O-L-H-C"


@pytest.mark.parametrize("opening,expected", [(80, 80), (90, 90)])
def test_initial_gap_below_stop_fills_at_open(opening, expected):
    result = replay([bar(), bar(1, opening, 102, opening - 1, 100)])
    assert result["exit"]["price"] == expected
    assert result["exit"]["reason"] == ("gap_below_stop" if opening < 90 else "stop_touch")


def test_gap_under_armed_floor_does_not_receive_imaginary_floor_price():
    result = replay([bar(), bar(1, 106, 108, 105.5, 106), bar(2, 96, 130, 95, 125)])
    assert result["exit"]["price"] == 96
    assert result["exit"]["reason"] == "gap_below_stop"


def test_open_arms_before_intrabar_low_without_ambiguity():
    result = replay([bar(), bar(1, 111, 112, 109, 111)])
    assert result["exit"]["price"] == 110
    assert result["ambiguous"] is False


def test_higher_existing_thresholds_are_retained_without_target_or_trim():
    result = replay([bar(high=241, low=100, close=240)],
                    higher_floor_thresholds=[(30, 25), (40, 35), (100, 95), (140, 135)])
    assert result["exit"]["price"] == 235
    assert result["exit"]["fraction"] == 1
    assert result["take_profit"] is None


def test_no_capped_tp_and_stop_never_loosens():
    result = replay([bar(), bar(1, 121, 250, 121, 240),
                     bar(2, 240, 245, 130, 140)], costs=ZERO, dynamic_uncapped_floors=False)
    assert result["status"] == "open"
    assert result["active_stop"] == 120
    assert result["metrics"] is None


def test_tighter_initial_stop_is_preserved():
    result = replay([bar(low=93)], initial_stop_price=95)
    assert result["exit"]["price"] == 95


def test_entry_midbar_excludes_preentry_extrema_and_suppresses_metrics():
    result = replay_equity_path([bar(high=150, low=50), bar(1, 100, 104, 89, 95)],
                                entry_price=100, entry_timestamp=START + timedelta(hours=1), costs=ZERO)
    assert result["exit"]["price"] == 90
    assert result["metrics"] is None
    assert result["coverage"]["issues"][0]["reason"] == "entry_boundary_bar_skipped"


def test_entry_open_mismatch_skipped_and_preentry_complete_bars_are_ignored():
    result = replay([bar(-1, 100, 150, 50, 110), bar(0, 101, 150, 50, 110), bar(1)])
    assert result["active_stop"] == 90
    assert [i["reason"] for i in result["coverage"]["issues"]] == ["incompatible_entry_open"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 0, None])
def test_invalid_prices_flag_coverage(value):
    broken = bar()
    broken["high"] = value
    result = replay([broken, bar(1, 100, 104, 89, 95)], costs=ZERO)
    assert result["coverage"]["status"] == "partial"
    assert result["metrics"] is None


def test_duplicate_and_out_of_order_bars_are_not_silently_sorted():
    result = replay([bar(), bar(), bar(2), bar(1)])
    reasons = {i["reason"] for i in result["coverage"]["issues"]}
    assert reasons == {"duplicate_timestamp", "nonchronological_bar"}
    assert result["coverage"]["processed_bars"] == 2


def test_missing_expected_bar_is_flagged_without_guessing_calendar():
    result = replay([bar(), bar(2)], expected_timestamps=[START + timedelta(days=i) for i in range(3)])
    assert result["coverage"]["issues"] == [
        {"reason": "missing_bar", "timestamp": (START + timedelta(days=1)).isoformat()}]


def test_missing_fields_and_empty_bars_flag_coverage():
    missing = bar()
    del missing["close"]
    assert replay([missing])["coverage"]["status"] == "partial"
    assert replay([])["coverage"]["issues"][-1]["reason"] == "no_compatible_post_entry_bars"


def test_naive_timestamp_and_overlapping_intervals_flag_coverage():
    naive = bar()
    naive["timestamp"] = START.replace(tzinfo=None)
    assert replay([naive])["coverage"]["issues"][0]["reason"] == "invalid_timestamp"
    overlap = bar(1)
    overlap["timestamp"] = START + timedelta(hours=1)
    assert replay([bar(), overlap])["coverage"]["issues"][0]["reason"] == "overlapping_bar"


def test_iso_timestamp_normalization_and_expected_calendar():
    row = bar(low=89)
    row["timestamp"] = START.isoformat().replace("+00:00", "Z")
    row["end_timestamp"] = row["end_timestamp"].isoformat()
    result = replay([row], costs=ZERO, expected_timestamps=[START.isoformat()])
    assert result["coverage"]["status"] == "complete"
    assert result["metrics_status"] == "available"


def test_returns_require_explicit_costs_and_costs_are_adverse():
    bars = [bar(low=89)]
    assert replay(bars)["metrics"] is None
    zero = replay(bars, costs=ZERO)["metrics"]
    assumptions = CostAssumptions(fee_bps=10, slippage_bps=20, half_spread_bps=5, fee_per_share=0.1)
    charged = replay(bars, costs=assumptions)["metrics"]
    assert zero["gross_pnl_per_share"] == zero["net_pnl_per_share"] == -10
    assert charged["gross_pnl_per_share"] == -10
    buy, sell = 100 * 1.0025, 90 * 0.9975
    assert charged["net_pnl_per_share"] == pytest.approx(sell - buy - (buy + sell) * 0.001 - 0.2)
    assert charged["net_return_pct"] < charged["gross_return_pct"]


@pytest.mark.parametrize("kwargs", [{"entry_price": math.nan}, {"initial_stop_pct": 10},
                                     {"initial_stop_price": 101}, {"costs": {}},
                                     {"higher_floor_thresholds": [(10, 10)]}])
def test_invalid_configuration_raises(kwargs):
    params = {"entry_price": 100, "entry_timestamp": START, **kwargs}
    with pytest.raises(ValueError):
        replay_equity_path([bar()], **params)


@pytest.mark.parametrize("args", [(-1, 0, 0, 0), (0, math.nan, 0, 0), (0, 10000, 0, 0)])
def test_invalid_costs_raise(args):
    with pytest.raises(ValueError):
        CostAssumptions(*args)


def test_split_is_chronological_and_has_no_shared_mapping_mutation():
    bars = [bar(i) for i in range(4)]
    original = copy.deepcopy(bars)
    result = chronological_train_holdout_split(bars, holdout_start=START + timedelta(days=2))
    assert result["train"] == bars[:2]
    assert result["holdout"] == bars[2:]
    assert result["research_only"] and result["noexecution"]
    replay(bars)
    assert bars == original
    result["train"][0]["high"] = 999
    assert bars == original


def test_split_rejects_leakage_duplicates_unsorted_and_empty_partitions():
    with pytest.raises(ValueError, match="straddles"):
        chronological_train_holdout_split([bar(), bar(1)], holdout_start=START + timedelta(hours=1))
    for bars, boundary in [([bar(), bar()], START), ([bar(1), bar()], START),
                           ([bar()], START), ([bar()], START + timedelta(days=2))]:
        with pytest.raises(ValueError):
            chronological_train_holdout_split(bars, holdout_start=boundary)
    missing_end = bar()
    del missing_end["end_timestamp"]
    with pytest.raises(ValueError, match="end_timestamp"):
        chronological_train_holdout_split([missing_end, bar(1)], holdout_start=START + timedelta(days=1))


@pytest.mark.parametrize("gain,floor", [
    (5, 90), (5.01, 105), (10, 105), (10.01, 110),
    (20, 110), (20.01, 120), (30, 120), (30.01, 125),
    (40, 125), (40.01, 135), (50.01, 145), (80.01, 175),
    (100.01, 195), (200.01, 295), (1000.01, 1095),
])
def test_dynamic_public_policy_milestones_without_upper_cap(gain, floor):
    price = 100 + gain
    result = replay([bar(), bar(1, price, price, price, price)])
    assert result["active_stop"] == floor
    assert result["exit"] is None


def test_dynamic_override_replaces_same_milestone_and_preserves_earlier_stop():
    result = replay([bar(), bar(1, 141, 141, 141, 141)],
                    higher_floor_thresholds=[(40, 22)])
    # Default 40->35 is overridden, but the already-crossed 30->25 stays.
    assert result["active_stop"] == 125
    raised = replay([bar(), bar(1, 141, 141, 141, 141)],
                    higher_floor_thresholds=[(40, 39)])
    assert raised["active_stop"] == 139


def test_gap_exactly_at_armed_floor_exits_at_open():
    result = replay([bar(), bar(1, 111, 112, 110.5, 111),
                     bar(2, 110, 150, 110, 145)])
    assert result["exit"]["price"] == 110
    assert result["exit"]["reason"] == "stop_touch"
    assert result["active_stop"] == 110


def test_split_purges_protected_label_horizon_without_returning_it_as_training():
    bars = [bar(i) for i in range(8)]
    result = chronological_train_holdout_split(
        bars, holdout_start=START + timedelta(days=6), purge_bars=1, protect_horizon_bars=3)
    assert result["train"] == bars[:3]
    assert result["purged"] == bars[3:6]
    assert result["holdout"] == bars[6:]
    with pytest.raises(ValueError):
        chronological_train_holdout_split(bars, holdout_start=START + timedelta(days=6), purge_bars=6)


def request_payload(**kwargs):
    return {"bars": [bar(low=89)], "entry_price": 100,
            "entry_timestamp": START.isoformat(), "source": "fixture:equity:v1", **kwargs}


def test_facade_missing_optional_references_stay_unavailable():
    result = run_requested(request_payload())
    assert result["research_only"] and result["noexecution"]
    assert not result["execution_allowed"]
    assert result["comparisons"]["spy"] == {"status": "not_supplied"}
    assert result["comparisons"]["oldway_terminal_mark"] == {"status": "not_supplied"}
    assert result["comparisons"]["newpath"]["metrics"] is None
    assert "events" not in result["replay"]
    assert result["source"] == "fixture:equity:v1"


def test_facade_exact_period_comparisons_and_explicit_costs():
    end = START + timedelta(hours=6)
    payload = request_payload(
        costs={"fee_bps": 0, "slippage_bps": 0, "half_spread_bps": 0, "fee_per_share": 0},
        terminal_mark={"price": 102, "timestamp": end.isoformat(), "source": "fixture:terminal:v2"},
        spy={"entry_price": 500, "entry_timestamp": START.isoformat(),
             "terminal_price": 505, "terminal_timestamp": end.isoformat(), "source": "fixture:SPY:v3"})
    original = copy.deepcopy(payload)
    result = run_requested(payload)
    assert payload == original
    assert result["comparisons"]["newpath"]["metrics"]["gross_return_pct"] == pytest.approx(-10)
    old = result["comparisons"]["oldway_terminal_mark"]
    assert old["source"] == "fixture:terminal:v2"
    assert old["metrics"]["gross_return_pct"] == pytest.approx(2)
    spy = result["comparisons"]["spy"]
    assert spy["source"] == "fixture:SPY:v3"
    assert spy["metrics"]["net_return_pct"] == pytest.approx(1)


@pytest.mark.parametrize("update", [
    {"bars": []}, {"bars": [bar()] * 10001}, {"source": ""},
    {"entry_timestamp": "2026-01-05"}, {"costs": {}}, {"costs": {"fee_bps": 0}},
    {"costs": {"fee_bps": -1, "slippage_bps": 0, "half_spread_bps": 0, "fee_per_share": 0}},
    {"terminal_mark": {"timestamp": START.isoformat(), "price": 101, "source": "wrong-period"}},
    {"spy": {"entry_timestamp": START.isoformat(), "terminal_timestamp": START.isoformat(),
             "entry_price": 500, "terminal_price": 501, "source": "wrong-period"}},
    {"dynamic_uncapped_floors": "false"}, {"period_end": START.isoformat()},
    {"expected_timestamps": [START.isoformat()] * 10001},
    {"higher_floor_thresholds": [30]}, {"bars": [None]},
])
def test_facade_rejects_invalid_or_oversized_requests(update):
    with pytest.raises(ValueError):
        run_requested(request_payload(**update))


def test_facade_bounded_diagnostics_and_compact_split():
    rows = [bar(i) for i in range(30)]
    rows.extend([bar(29)] * 50)
    result = run_requested(request_payload(bars=rows))
    assert result["replay"]["coverage"]["issue_count"] == 50
    assert len(result["replay"]["coverage"]["issues"]) == 20
    split = run_requested(request_payload(
        bars=[bar(i) for i in range(6)],
        split={"holdout_start": (START + timedelta(days=4)).isoformat(), "protect_horizon_bars": 2}))
    assert split["split"]["train_count"] == 2
    assert split["split"]["holdout_count"] == 2
    assert split["split"]["purged_count"] == 2
    assert "train" not in split["split"]


def test_facade_references_without_costs_never_invent_zero_fees():
    end = (START + timedelta(hours=6)).isoformat()
    result = run_requested(request_payload(
        terminal_mark={"price": 102, "timestamp": end, "source": "fixture:mark"},
        spy={"entry_price": 500, "entry_timestamp": START.isoformat(),
             "terminal_price": 505, "terminal_timestamp": end, "source": "fixture:spy"}))
    for name in ("oldway_terminal_mark", "spy"):
        assert result["comparisons"][name]["status"] == "costs_unspecified"
        assert result["comparisons"][name]["metrics"] is None


def test_facade_accepts_ten_thousand_bars_without_echoing_them():
    result = run_requested(request_payload(bars=[bar(i) for i in range(10000)]))
    assert result["replay"]["event_count"] == 10000
    assert "bars" not in result
    assert "events" not in result["replay"]


def test_duplicate_override_milestones_rejected():
    with pytest.raises(ValueError, match="unique"):
        replay([bar()], higher_floor_thresholds=[(40, 39), (40, 22)])
