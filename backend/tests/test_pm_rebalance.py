from services import pm_rebalance, portfolio_manager


def _candidate(**overrides):
    row = {
        "ticker": "BETR",
        "action": "WATCH",
        "pre_execution_action": "STARTER",
        "pre_execution_allocation_usd": 6,
        "pm_score": 82,
        "case_score": 80,
        "strategy_confidence": 0.9,
        "stop": 8.5,
        "price": 10,
        "source_scan": "lottery_day2_continuation",
        "scanner_family": "LOTTERY",
    }
    row.update(overrides)
    return row


def test_cash_demoted_pm_candidate_remains_available_for_rebalance():
    candidate = _candidate()
    plan = pm_rebalance.build_rebalance_plan(
        {"scan_finished_at": "2026-09-15T14:00:00+00:00", "recommendations": [candidate]},
        [{"ticker": "WEAK", "quantity": 1, "market_value": 10, "unrealized_pct": -9}],
    )
    assert plan["best_candidate"] == "BETR"
    assert plan["candidate_count"] == 1
    assert plan["actions"][0]["ticker"] == "WEAK"
    assert plan["actions"][0]["replacement"] == "BETR"


def test_rebalance_does_not_sell_a_protected_winner():
    candidate = _candidate(pm_score=99)
    plan = pm_rebalance.build_rebalance_plan(
        {"recommendations": [candidate]},
        [{"ticker": "WIN", "quantity": 1, "market_value": 10, "unrealized_pct": 12}],
    )
    assert plan["actions"] == []
    assert plan["position_reviews"][0]["action"] == "HOLD"
    assert plan["position_reviews"][0]["protected_winner"] is True


def test_pm_constraints_preserve_original_approval_for_capital_rotation():
    rows = [_candidate(action="STARTER", pre_execution_action=None, allocation_usd=6)]
    rows[0].pop("pre_execution_action")
    result = portfolio_manager._apply_equity_book_constraints(
        rows,
        {"broker": "public", "cash_buying_power": 3.99, "equity": 100, "positions": []},
    )
    assert result["blocked"]["cash_insufficient"] == 1
    assert rows[0]["action"] == "WATCH"
    assert rows[0]["pre_execution_action"] == "STARTER"
    assert rows[0]["pre_execution_allocation_usd"] == 6
