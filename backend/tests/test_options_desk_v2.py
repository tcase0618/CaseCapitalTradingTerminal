from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from services import options_desk  # noqa: E402
from services import options_engine  # noqa: E402
from services import portfolio_manager  # noqa: E402
from services import tail_hunter  # noqa: E402


def test_spread_cost_context_uses_mid_basis():
    context = options_desk._spread_cost_context(2.1, {"bid": 1.9, "ask": 2.1})

    assert context["price_basis"] == "mid"
    assert context["mid_at_fill"] == 2.0
    assert context["spread_at_fill"] == 0.2
    assert context["spread_pct_at_fill"] == 9.52
    assert context["spread_cost_paid"] == 0.1
    assert context["spread_cost_pct"] == 5.0


def test_spread_gate_computes_spread_from_bid_ask():
    tight = {"bid": 2.0, "ask": 2.12, "premium": 2.12}
    wide = {"bid": 2.0, "ask": 2.3, "premium": 2.3}
    cheap = {"bid": 0.5, "ask": 0.65, "premium": 0.65}

    assert options_desk._spread_is_too_wide(tight) is False
    assert options_desk._spread_is_too_wide(wide) is True
    assert options_desk._spread_is_too_wide(cheap) is True


def test_execution_delta_requires_provider_delta_and_grind_band():
    missing = {"delta": 0.58, "provider_delta_present": False}
    too_low = {"delta": 0.2, "provider_delta_present": True}
    too_high = {"delta": 0.9, "provider_delta_present": True}
    accepted = {"delta": 0.55, "provider_delta_present": True}
    event_scout = {"delta": 0.34, "provider_delta_present": True}

    assert options_desk._provider_delta_missing(missing) is True
    assert options_desk._delta_out_of_band(too_low, "LONG_CALL") is True
    assert options_desk._delta_out_of_band(too_high, "LONG_CALL") is True
    assert options_desk._delta_out_of_band(accepted, "LONG_CALL") is False
    assert options_desk._delta_out_of_band(event_scout, "LONG_CALL_EVENT_SCOUT") is False


def test_risk_budget_uses_options_fund_lanes():
    assert options_desk._risk_budget("EQUITY", "ACCUMULATE", 99) == 0.0
    assert options_desk._risk_budget("OPTION", "WATCH", 52) == 200.0
    assert options_desk._risk_budget("OPTION", "STARTER", 62) == 350.0
    assert options_desk._risk_budget("OPTION", "ACCUMULATE", 72) == 600.0
    assert options_desk._risk_budget("BOTH", "ACCUMULATE", 95) == 1000.0


def test_pm_grade_anchor_gets_options_scout_strategy():
    stock = {
        "signals": ["CALL_SWEEP", "high_short_interest"],
        "score": 62,
        "risk_reward": 1.8,
        "risk": {"level": "MEDIUM"},
        "squeeze": {"score": 61},
        "time_target": {"days_remaining": 18},
    }

    strategy = options_engine.select_strategy(stock, {"iv_rank": 55})

    assert strategy["strategy"] in {"LONG_CALL", "LONG_CALL_SCOUT"}
    assert strategy["direction"] == "BULL"


def test_high_iv_near_event_can_enter_capped_paper_scout():
    stock = {
        "signals": ["CALL_SWEEP", "upcoming_earnings"],
        "score": 63,
        "risk_reward": 2.4,
        "risk": {"level": "MEDIUM"},
        "squeeze": {"score": 58},
        "time_target": {"days_remaining": 1},
    }

    strategy = options_engine.select_strategy(stock, {"iv_rank": 85})

    assert strategy["strategy"] == "LONG_CALL_EVENT_SCOUT"
    assert strategy["direction"] == "BULL"


def test_pm_option_view_allows_named_option_strategies():
    row = {"options": {"strategy": "LONG_CALL_SCOUT", "iv_rank": 62}}

    assert portfolio_manager._option_view(row, rr=1.25) == "CALL_ALLOWED"


def test_options_route_permits_pm_approved_paper_scout():
    pm_row = {
        "action": "STARTER",
        "pm_score": 60,
        "risk_reward": 1.4,
        "option_view": "CALL_ALLOWED",
    }
    scan_row = {
        "options": {
            "strategy": "LONG_CALL_SCOUT",
            "iv_rank": 58,
            "contract": {"symbol": "TEST260821C00010000", "ask": 2.0, "max_loss": 200.0},
        }
    }

    route, reasons = options_desk._route(pm_row, scan_row)

    assert route == "OPTION"
    assert reasons


def test_tail_hunter_selects_contract_in_delta_dte_budget_band():
    import pandas as pd

    expiry = (datetime.now(timezone.utc) + timedelta(days=14)).date().isoformat()
    chain = {
        "data_provider": "ALPACA_OPTIONS",
        "data_feed": "indicative",
        "data_quality": "EXECUTION_GRADE",
        "expiration": expiry,
        "calls": pd.DataFrame([
            {"contractSymbol": "TEST260821C00010000", "strike": 10, "bid": 0.85, "ask": 1.05, "delta": 0.18, "days_to_exp": 14, "openInterest": 400, "volume": 125, "impliedVolatility": 0.45},
            {"contractSymbol": "TEST260821C00012000", "strike": 12, "bid": 0.20, "ask": 0.60, "delta": 0.14, "days_to_exp": 14, "openInterest": 600, "volume": 130, "impliedVolatility": 0.55},
            {"contractSymbol": "TEST260821C00008000", "strike": 8, "bid": 1.80, "ask": 2.10, "delta": 0.55, "days_to_exp": 14, "openInterest": 600, "volume": 130, "impliedVolatility": 0.35},
        ]),
    }

    contract = tail_hunter.tail_contract_from_chain(chain)

    assert contract["symbol"] == "TEST260821C00010000"
    assert contract["contracts_at_budget"] == 1
    assert contract["spread_pct"] == 19.05


def test_tail_hunter_gate_requires_two_applicable_exhibits():
    target = (datetime.now(timezone.utc) + timedelta(days=12)).date().isoformat()
    row = {
        "ticker": "TEST",
        "signals": ["CALL_SWEEP"],
        "time_target": {"target_date": target},
    }
    instrument = {"delta": 0.18}
    chain = {"iv_rank": 42}

    passed, reasons = tail_hunter.gate_check(row, {"action": "WATCH"}, instrument, {}, chain)

    assert passed is True
    assert len(reasons) >= 2


def test_tail_hunter_gate_neutralizes_missing_optional_exhibits():
    row = {"ticker": "TEST", "signals": []}
    instrument = {"delta": 0.18}
    chain = {"iv_rank": 70}

    passed, reasons = tail_hunter.gate_check(row, {"action": "WATCH"}, instrument, {}, chain)

    assert passed is False
    assert reasons == ["contract delta in 0.10-0.25 tail band"]
