from __future__ import annotations

import asyncio
import os
import sys
import pytest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from services import options_desk  # noqa: E402
from services import options_engine  # noqa: E402
from services import portfolio_manager  # noqa: E402
from services import tail_hunter  # noqa: E402
from services import options_contract_learning  # noqa: E402
from services import options_policy  # noqa: E402


@pytest.fixture(autouse=True)
def standard_options_policy_by_default(monkeypatch):
    """Keep unit tests deterministic; individual tests may opt into paper_scout."""
    monkeypatch.setenv("OPTIONS_FILTER_POLICY", "standard")
    monkeypatch.setenv("OPTIONS_APCA_API_BASE_URL", "https://api.alpaca.markets")
    monkeypatch.setenv("OPTIONS_ALLOW_INDICATIVE_EXECUTION", "false")


def test_shared_policy_paper_scout_profile_is_explicit(monkeypatch):
    monkeypatch.setenv("OPTIONS_FILTER_POLICY", "paper_scout")
    monkeypatch.setenv("OPTIONS_APCA_API_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("OPTIONS_ALLOW_INDICATIVE_EXECUTION", "true")
    policy = options_policy.get_policy()
    assert policy.min_open_interest == 100
    assert policy.min_volume_when_low_oi == 50
    assert policy.max_indicative_spread_pct == 0.30
    assert policy.min_abs_delta == 0.25
    assert options_policy.paper_scout_allowed() is True


def test_paper_scout_profile_cannot_be_used_on_live_endpoint(monkeypatch):
    monkeypatch.setenv("OPTIONS_FILTER_POLICY", "paper_scout")
    monkeypatch.setenv("OPTIONS_APCA_API_BASE_URL", "https://api.alpaca.markets")
    monkeypatch.setenv("OPTIONS_ALLOW_INDICATIVE_EXECUTION", "true")
    assert options_policy.paper_scout_allowed() is False


def test_spread_cost_context_uses_mid_basis():
    context = options_desk._spread_cost_context(2.1, {"bid": 1.9, "ask": 2.1})

    assert context["price_basis"] == "mid"
    assert context["mid_at_fill"] == 2.0
    assert context["spread_at_fill"] == 0.2
    assert context["spread_pct_at_fill"] == 9.52
    assert context["spread_cost_paid"] == 0.1
    assert context["spread_cost_pct"] == 5.0


def test_options_summary_distinguishes_routed_names_from_contract_readiness():
    rows = [
        {"route": "OPTION", "manual_fire_ready": False, "instrument": {"symbol": "A"}, "quality_state": "RESEARCH_ONLY", "blocked_reasons": ["missing data"]},
        {"route": "BOTH", "manual_fire_ready": True, "instrument": {"contractSymbol": "B"}, "quality_state": "EXECUTION_GRADE", "blocked_reasons": []},
        {"route": "EQUITY", "manual_fire_ready": False, "instrument": {}, "quality_state": "RESEARCH_ONLY", "blocked_reasons": ["equity"]},
    ]

    summary = options_desk._summary(rows)

    assert summary["total"] == 3
    assert summary["routed"] == 2
    assert summary["ready"] == 1
    assert summary["contract_selected"] == 2
    assert summary["execution_grade"] == 1
    assert summary["blocked"] == 1


def test_spread_gate_computes_spread_from_bid_ask():
    tight = {"bid": 2.0, "ask": 2.12, "premium": 2.12}
    wide = {"bid": 2.0, "ask": 2.3, "premium": 2.3}
    cheap = {"bid": 0.01, "ask": 0.05, "premium": 0.05}

    assert options_desk._spread_is_too_wide(tight) is False
    assert options_desk._spread_is_too_wide(wide) is True
    assert options_desk._spread_is_too_wide(cheap) is True


def test_low_premium_is_allowed_when_spread_is_usable():
    instrument = {"bid": 0.045, "ask": 0.05, "premium": 0.05, "spread": 0.005}

    assert options_desk._spread_is_too_wide(instrument) is False


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


def test_basic_indicative_policy_uses_quote_checks_and_does_not_require_oi(monkeypatch):
    monkeypatch.setenv("OPTIONS_ALLOW_INDICATIVE_EXECUTION", "true")
    monkeypatch.setenv("OPTIONS_APCA_API_BASE_URL", "https://paper-api.alpaca.markets")
    instrument = {
        "data_provider": "ALPACA_OPTIONS",
        "data_quality": "INDICATIVE",
        "bid": 1.00,
        "ask": 1.10,
        "spread": 0.10,
        "open_interest_source": "unavailable",
        "volume": 125,
        "delta": 0.55,
        "delta_estimated": True,
        "provider_delta_present": False,
    }
    assert options_desk._execution_grade_allowed("INDICATIVE") is True
    assert options_desk._open_interest_is_too_low(instrument) is False
    assert options_desk._provider_delta_missing(instrument) is False
    assert options_desk._indicative_execution_too_thin(instrument) is False


def test_indicative_spread_uses_wider_paper_threshold():
    instrument = {
        "data_provider": "ALPACA_OPTIONS",
        "data_quality": "INDICATIVE",
        "bid": 1.00,
        "ask": 1.22,
        "spread": 0.22,
    }

    assert options_desk._spread_is_too_wide(instrument) is False


def test_unknown_oi_requires_volume_or_two_sided_quote():
    base = {
        "data_provider": "ALPACA_OPTIONS",
        "open_interest_source": "unavailable",
        "volume": 0,
    }
    assert options_desk._open_interest_is_too_low(base) is True
    assert options_desk._open_interest_is_too_low({**base, "volume": 100}) is False
    assert options_desk._open_interest_is_too_low({**base, "bid_size": 2, "ask_size": 3}) is False


def test_indicative_policy_never_allows_non_paper(monkeypatch):
    monkeypatch.setenv("OPTIONS_ALLOW_INDICATIVE_EXECUTION", "true")
    monkeypatch.setenv("OPTIONS_APCA_API_BASE_URL", "https://api.alpaca.markets")
    assert options_desk._execution_grade_allowed("INDICATIVE") is False


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


def test_options_engine_uses_scanner_price_as_spot_hint(monkeypatch):
    seen = {}

    async def fake_get_options_data(ticker, catalyst_date=None, spot_hint=None):
        seen["ticker"] = ticker
        seen["spot_hint"] = spot_hint
        return None

    monkeypatch.setattr(options_engine, "get_options_data", fake_get_options_data)

    result = asyncio.run(options_engine.analyze_ticker({"ticker": "OPTX", "price": 12.34}))

    assert result is None
    assert seen == {"ticker": "OPTX", "spot_hint": 12.34}


def test_options_engine_keeps_estimated_delta_for_indicative_paper_scout():
    import pandas as pd

    chain = {
        "price": 10.0,
        "data_provider": "ALPACA_OPTIONS",
        "data_feed": "indicative",
        "data_quality": "INDICATIVE",
        "expiration": "2026-09-18",
        "atm_iv": 0.55,
        "calls": pd.DataFrame([
            {
                "contractSymbol": "TEST260918C00010000",
                "strike": 10.0,
                "bid": 1.00,
                "ask": 1.20,
                "lastPrice": 1.10,
                "openInterest": 0,
                "volume": 125,
                "expiration": "2026-09-18",
                "impliedVolatility": 0.55,
            }
        ]),
    }

    contract = options_engine.find_best_contract(chain, "BULL", budget=300)

    assert contract is not None
    assert contract["delta_estimated"] is True
    assert contract["provider_delta_present"] is False
    assert contract["delta"] > 0


def test_options_engine_prefers_policy_eligible_affordable_contract(monkeypatch):
    import pandas as pd

    monkeypatch.setenv("OPTIONS_FILTER_POLICY", "paper_scout")

    chain = {
        "price": 10.0,
        "data_provider": "ALPACA_OPTIONS",
        "data_quality": "INDICATIVE",
        "calls": pd.DataFrame([
            {
                "contractSymbol": "TEST260918C00010000",
                "strike": 10.0, "bid": 1.00, "ask": 1.20,
                "lastPrice": 1.10, "openInterest": 0, "volume": 0,
                "expiration": "2026-09-18", "delta": 0.50,
            },
            {
                "contractSymbol": "TEST260918C00010500",
                "strike": 10.5, "bid": 0.70, "ask": 0.80,
                "lastPrice": 0.75, "openInterest": 0, "volume": 75,
                "expiration": "2026-09-18", "delta": 0.30,
            },
        ]),
    }

    contract = options_engine.find_best_contract(chain, "BULL", budget=300)

    assert contract is not None
    assert contract["symbol"] == "TEST260918C00010500"
    assert contract["selection_tier"] == "POLICY_ELIGIBLE"


def test_options_engine_keeps_affordable_fallback_when_no_contract_passes_policy(monkeypatch):
    import pandas as pd

    monkeypatch.setenv("OPTIONS_FILTER_POLICY", "paper_scout")
    chain = {
        "price": 10.0,
        "data_quality": "INDICATIVE",
        "calls": pd.DataFrame([
            {
                "contractSymbol": "TEST260918C00010000",
                "strike": 10.0, "bid": 0.10, "ask": 0.20,
                "lastPrice": 0.15, "openInterest": 0, "volume": 0,
                "expiration": "2026-09-18", "delta": 0.50,
            },
        ]),
    }

    contract = options_engine.find_best_contract(chain, "BULL", budget=300)

    assert contract is not None
    assert contract["selection_tier"] == "AFFORDABLE_FALLBACK"
    assert contract["selection_fallback"] == "no_contract_met_all_active_policy_gates"


def test_contract_selection_record_tracks_alternatives_in_shadow_mode():
    candidate = {
        "candidate_id": "opt-TEST-1",
        "ticker": "TEST",
        "route": "OPTION",
        "strategy": "LONG_CALL_SCOUT",
        "strategy_lane": {"lane": "TACTICAL_MOMENTUM_CALL"},
        "pm_score": 62,
        "risk_budget": 200,
        "data_quality": "INDICATIVE",
        "data_feed": "indicative",
        "instrument": {
            "symbol": "TEST260918C00010000",
            "strike": 10,
            "expiration": "2026-09-18",
            "bid": 1.00,
            "ask": 1.20,
            "premium": 1.10,
            "spread": 0.20,
            "delta": 0.52,
            "selection_alternatives": [
                {"symbol": "TEST260918C00010500", "strike": 10.5, "bid": 0.8, "ask": 1.0, "premium": 0.9, "delta": 0.45, "selection_score": 1.2}
            ],
        },
    }
    record = options_contract_learning.build_selection_record(candidate)

    assert record["learning_mode"] == "shadow_only"
    assert record["selected"]["entry_mid"] == 1.1
    assert record["alternatives"][0]["symbol"] == "TEST260918C00010500"


def test_contract_selection_resolution_compares_selected_to_alternatives():
    record = {
        "selected": {"entry_mid": 1.0},
        "alternatives": [{"symbol": "ALT", "bid": 0.8, "ask": 1.0}],
        "status": "PENDING",
    }
    resolved = options_contract_learning.resolve_record(record, 1.5, {"ALT": 1.2})

    assert resolved["selected_return_pct"] == 50.0
    assert resolved["counterfactuals"][0]["return_pct"] == 33.3333


def test_contract_learning_promotion_requires_100_and_clear_alpha_at_150():
    shadow = options_contract_learning.promotion_state([
        {"status": "RESOLVED", "selected_return_pct": 10.0, "best_counterfactual_return_pct": 4.0}
        for _ in range(99)
    ])
    assert shadow["mode"] == "shadow_only"
    assert shadow["live_eligible"] is False

    advisory = options_contract_learning.promotion_state([
        {"status": "RESOLVED", "selected_return_pct": 10.0, "best_counterfactual_return_pct": 4.0}
        for _ in range(100)
    ])
    assert advisory["mode"] == "advisory"
    assert advisory["advisory_ready"] is True
    assert advisory["live_eligible"] is False

    eligible = options_contract_learning.promotion_state([
        {"status": "RESOLVED", "selected_return_pct": 10.0, "best_counterfactual_return_pct": 4.0}
        for _ in range(150)
    ])
    assert eligible["mode"] == "advisory"
    assert eligible["live_eligible"] is True


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


def test_options_scanner_intent_stays_option_when_contract_missing():
    pm_row = {
        "action": "WATCH",
        "pm_score": 48,
        "risk_reward": 1.3,
        "option_view": "CALL_ALLOWED",
    }
    scan_row = {
        "strategy_scanner": {"family": "OPTIONS", "screener_id": "options_tactical_momentum_call"},
        "options": {
            "strategy": "LONG_CALL_SCOUT",
            "options_intent": True,
            "preferred_route": "OPTION",
            "iv_rank": 55,
        },
    }

    route, reasons = options_desk._route(pm_row, scan_row)

    assert route == "OPTION"
    assert "no executable contract" in reasons[0].lower()


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
