from datetime import datetime, timezone

from services import pm_portfolio, pm_rebalance


def _candidate(ticker="NEW", score=82, action="ACCUMULATE"):
    return {
        "ticker": ticker,
        "pm_score": score,
        "case_score": 75,
        "strategy_confidence": 0.8,
        "risk_reward": 3.0,
        "action": action,
        "route": "EQUITY",
        "stop": 9.0,
        "target": 15.0,
    }


def test_queue_keeps_exclusions_and_ranks_executable_candidates():
    blocked = _candidate("BLOCKED")
    blocked["equity_execution_blocker"] = "quote_stale"
    queue = pm_portfolio.build_opportunity_queue([_candidate("HELD"), blocked, _candidate("BEST", 90)], {"HELD"})
    best = next(row for row in queue if row["ticker"] == "BEST")
    assert best["executable"] is True
    assert best["rank"] == 1
    assert next(row for row in queue if row["ticker"] == "BLOCKED")["exclusion_reason"] == "equity_execution_blocked"


def test_holding_score_requires_portfolio_review_before_rebalance():
    candidate = _candidate("BEST", 95)
    plan = {"recommendations": [candidate]}
    position = {"ticker": "WEAK", "quantity": 1, "market_value": 10, "unrealized_pct": -0.08}
    no_score = pm_rebalance.build_rebalance_plan(plan, [position])
    assert no_score["actions"]
    reviewed = pm_rebalance.build_rebalance_plan(
        plan,
        [position],
        {"WEAK": {"portfolio_score": 70, "recommended_state": "HOLD"}},
    )
    assert reviewed["actions"] == []
    assert reviewed["position_reviews"][0]["reason"].startswith("portfolio-quality review retains")


def test_holding_score_labels_only_genuinely_weak_replacement_candidate():
    position = {"ticker": "WEAK", "market_value": 30, "unrealized_pct": -6.0, "lastPrice": 10}
    score = pm_portfolio._holding_score(
        position,
        {"ticker": "WEAK", "pm_score": 30, "case_score": 30},
        {"current_stop": 8.0},
        {"ticker": "BEST", "candidate_edge": 90},
        100,
    )
    assert score["recommended_state"] == "REPLACE_REVIEW"
    assert score["portfolio_score"] < 55


def test_deep_loss_without_a_current_thesis_is_an_exit_review():
    score = pm_portfolio._holding_score(
        {"ticker": "LEGACY", "market_value": 4, "unrealized_pct": -27.0, "lastPrice": 2.6},
        None,
        None,
        None,
        100,
    )

    assert score["recommended_state"] == "EXIT_REVIEW"
    assert "no current PM thesis" in score["reasons"][-1]


def test_holding_score_distinguishes_verified_terminal_monitor_from_broker_stop():
    score = pm_portfolio._holding_score(
        {"ticker": "MON", "market_value": 10, "unrealized_pct": 1.0, "lastPrice": 10},
        {"ticker": "MON", "pm_score": 60, "case_score": 60},
        {
            "pm_active_stop": 9.0,
            "pm_last_ratchet_check": datetime.now(timezone.utc).isoformat(),
            "pm_ratchet_plan": {"enabled": True},
            "protection_state": "MONITORED_EXIT_ONLY",
        },
        None,
        100,
    )
    assert score["risk_state"] == "TERMINAL_MONITORED"
    assert score["components"]["risk_quality"] > 15
