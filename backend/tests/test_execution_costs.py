from types import SimpleNamespace

import pytest

from services.execution_costs import estimate_spread_fees, measure_fill_costs, measure_fill_pnl


@pytest.mark.parametrize("side,fill,expected", [
    ("BUY", 101, 100), ("SELL", 99, 100),
    ("BUY", 99, -100), ("SELL", 101, -100),
    (" sell ", 100, 0),
])
def test_arrival_cost_is_side_signed(side, fill, expected):
    costs = measure_fill_costs(side=side, fill_price=fill, arrival_mid=100,
                               filled_quantity=2, actual_fees_usd=0.5)
    assert costs["arrival_mid_slippage_bps"] == pytest.approx(expected)
    assert costs["implementation_cost_gross_usd"] == pytest.approx(expected / 50)
    assert costs["implementation_cost_net_usd"] == pytest.approx(expected / 50 + 0.5)


@pytest.mark.parametrize("side,fill,limit,mid", [("BUY", 101, 102, 100), ("SELL", 99, 98, 100)])
def test_limit_improvement_can_coexist_with_adverse_arrival_cost(side, fill, limit, mid):
    costs = measure_fill_costs(side=side, fill_price=fill, limit_price=limit, arrival_mid=mid)
    assert costs["limit_price_improvement_bps"] > 0
    assert costs["limit_relative_cost_bps"] < 0
    assert costs["arrival_mid_slippage_bps"] > 0


@pytest.mark.parametrize("side", [None, "", "UNKNOWN", "SHORT", "BUY_TO_COVER", 1, True])
def test_unknown_side_is_never_assumed_buy(side):
    costs = measure_fill_costs(side=side, fill_price=100, arrival_mid=100,
                               limit_price=100, filled_quantity=1, actual_fees_usd=0)
    assert costs["side"] == "UNKNOWN"
    assert costs["unknown_reason"] == "unknown_side"
    for key in ("arrival_mid_slippage_bps", "limit_relative_cost_bps",
                "limit_price_improvement_bps", "implementation_cost_gross_usd",
                "implementation_cost_net_usd"):
        assert costs[key] is None


@pytest.mark.parametrize("value", [None, "bad", "", 0, -1, float("nan"), float("inf"), True])
def test_invalid_price_does_not_become_zero_slippage(value):
    costs = measure_fill_costs(side="BUY", fill_price=value, arrival_mid=100, limit_price=100)
    assert costs["arrival_mid_slippage_bps"] is None
    assert costs["limit_price_improvement_bps"] is None


@pytest.mark.parametrize("fees", [None, "bad", -1, float("nan"), float("inf"), False])
def test_unknown_or_invalid_fee_preserves_gross_but_not_net(fees):
    costs = measure_fill_costs(side="SELL", fill_price=99, arrival_mid=100,
                               filled_quantity="0.25", actual_fees_usd=fees)
    assert costs["implementation_cost_gross_usd"] == 0.25
    assert costs["implementation_cost_net_usd"] is None


@pytest.mark.parametrize("side,entry,exit_price", [("BUY", 101, 109), ("SELL", 109, 101)])
def test_fill_pnl_subtracts_fees_once_not_spread(side, entry, exit_price):
    pnl = measure_fill_pnl(entry_side=side, entry_fill=entry, exit_fill=exit_price,
                           quantity=2, multiplier=100, entry_fees_usd=1, exit_fees_usd=2)
    assert pnl == {"gross_pnl_usd": 1600, "actual_fees_usd": 3,
                   "net_pnl_usd": 1597, "spread_already_embedded": True}


def test_fill_pnl_unknown_fees_and_explicit_zero_are_distinct():
    args = dict(entry_side="BUY", entry_fill=100, exit_fill=101, quantity=1)
    assert measure_fill_pnl(**args)["net_pnl_usd"] is None
    assert measure_fill_pnl(**args, entry_fees_usd=0, exit_fees_usd=0)["net_pnl_usd"] == 1
    assert measure_fill_pnl(**{**args, "entry_side": None})["gross_pnl_usd"] is None


@pytest.mark.parametrize("legs,spread", [(1, 2), (2, 4)])
def test_research_estimate_declares_crossing_and_total_fee_assumptions(legs, spread):
    estimate = estimate_spread_fees(bid=99, ask=101, quantity=2, fees_usd=0.5, legs=legs)
    assert estimate["estimated_spread_cost_usd"] == spread
    assert estimate["estimated_total_cost_usd"] == spread + 0.5
    assert estimate["impact_cost_usd"] is None
    assert estimate["impact_model"] == "not_estimated"
    assert estimate["research_only"] is True
    assert "fees_are_total_usd_all_legs" in estimate["assumptions"]


@pytest.mark.parametrize("overrides", [
    {"bid": 102}, {"bid": None}, {"ask": float("inf")}, {"quantity": 0},
    {"quantity": -1}, {"multiplier": True}, {"legs": True}, {"legs": 3}, {"legs": 1.0},
])
def test_research_estimate_rejects_invalid_inputs(overrides):
    args = dict(bid=99, ask=101, quantity=1, fees_usd=0)
    result = estimate_spread_fees(**{**args, **overrides})
    assert result["estimated_spread_cost_usd"] is None
    assert result["estimated_total_cost_usd"] is None


def test_locked_spread_is_zero_but_missing_fees_are_unknown():
    result = estimate_spread_fees(bid=100, ask=100, quantity=1)
    assert result["estimated_spread_cost_usd"] == 0
    assert result["estimated_total_cost_usd"] is None


@pytest.mark.asyncio
async def test_analytics_is_read_only_and_separates_paid_from_estimated_fees(monkeypatch):
    from services import public_execution

    rows = [
        {"side": "BUY", "filled_avg_price": 101, "limit_price": 102, "qty_total": 2,
         "qty_remaining": 0, "execution_quote": {"mid": 100}, "actual_fees_usd": 0.5,
         "public_preflight_economics": {"estimated_total_fees": 999}},
        {"side": "SELL", "filled_avg_price": 99, "limit_price": 98, "qty_total": 1,
         "execution_quote": {"mid": 100}, "actual_fees_usd": 0},
        {"filled_avg_price": 100, "limit_price": 100, "execution_quote": {"mid": 100}},
        {"side": "BUY", "filled_avg_price": "nan", "execution_quote": None,
         "public_preflight_economics": {"estimated_total_fees": "nan"}},
        {"side": "SELL", "filled_avg_price": 99, "quantity": 999, "qty_remaining": 998,
         "execution_quote": {"mid": 100}},
    ]

    class Cursor:
        def sort(self, *_args):
            return self

        async def to_list(self, limit):
            assert limit == 5000
            return rows

    class Collection:
        def find(self, query, projection):
            assert query == {"broker_base": public_execution.BROKER_BASE}
            assert projection == {"_id": 0}
            return Cursor()

    monkeypatch.setattr(public_execution, "get_db", lambda: SimpleNamespace(tf_trades=Collection()))
    result = await public_execution.analytics(limit=99999)
    assert result["read_only"] is True
    assert result["arrival_mid_slippage_bps"] == {"n": 3, "avg": 100, "worst": 100}
    assert result["unknown_side_records"] == 1
    assert result["limit_price_improvement_bps"]["n"] == 2
    assert result["limit_price_improvement_bps"]["worst"] == 98.04
    assert result["implementation_cost_gross_usd"] == {"n": 2, "avg": 1.5, "total": 3}
    assert result["implementation_cost_net_usd"] == {"n": 2, "avg": 1.75, "total": 3.5}
    assert result["actual_fees_usd"]["total"] == 0.5
    assert result["preflight_estimated_fees_usd"]["total"] == 999


@pytest.mark.asyncio
async def test_empty_analytics_returns_unknown_not_fake_zero(monkeypatch):
    from services import public_execution

    class Cursor:
        def sort(self, *_args):
            return self

        async def to_list(self, _limit):
            return []

    monkeypatch.setattr(public_execution, "get_db", lambda: SimpleNamespace(
        tf_trades=SimpleNamespace(find=lambda *_args: Cursor())))
    result = await public_execution.analytics()
    assert result["arrival_mid_slippage_bps"] == {"n": 0, "avg": None, "worst": None}
    assert result["actual_fees_usd"] == {"n": 0, "avg": None, "total": None}
