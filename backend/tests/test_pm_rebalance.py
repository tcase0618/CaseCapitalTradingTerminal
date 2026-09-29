from types import SimpleNamespace

import pytest

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


def test_rebalance_does_not_convert_public_percent_return_twice():
    candidate = _candidate(pm_score=99)
    plan = pm_rebalance.build_rebalance_plan(
        {"recommendations": [candidate]},
        [{
            "ticker": "SMALL_LOSS",
            "quantity": 1,
            "market_value": 10,
            "unrealized_pct": -1.33,
            "return_units": "percent",
        }],
    )

    assert plan["position_reviews"][0]["unrealized_pct"] == -1.33


def test_exit_review_can_release_cash_without_an_available_replacement():
    plan = pm_rebalance.build_rebalance_plan(
        {"recommendations": []},
        [{"ticker": "LEGACY", "quantity": 1.5, "market_value": 4, "unrealized_pct": -27.0}],
        {"LEGACY": {"recommended_state": "EXIT_REVIEW", "portfolio_score": 35}},
    )

    assert plan["actions"][0]["action"] == "EXIT_TO_CASH"
    assert plan["actions"][0]["candidate"] is None


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


@pytest.mark.asyncio
async def test_rebalance_releases_capital_only_after_broker_confirmed_sell_fill(monkeypatch):
    """A replacement buy cannot be submitted until a later confirmed-fill pass."""

    intent = {
        "intent_id": "replace-weak-with-betr",
        "broker_base": "public",
        "status": "SELL_SUBMITTED",
        "sell_order_id": "sell-1",
        "candidate": _candidate(),
    }

    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        async def to_list(self, _limit):
            return [dict(row) for row in self.rows]

    class Intents:
        def find(self, query, *_args, **_kwargs):
            allowed = set(query["status"]["$in"])
            return Cursor([intent] if intent["status"] in allowed else [])

        async def update_one(self, query, update, *_args, **_kwargs):
            if query.get("intent_id") == intent["intent_id"]:
                intent.update(update.get("$set") or {})

    monkeypatch.setattr(pm_rebalance, "get_db", lambda: SimpleNamespace(pm_rebalance_intents=Intents()))

    class Broker:
        async def get_order(self, _order_id):
            return {"status": "FILLED", "filledQuantity": "0.5", "averagePrice": "12.0"}

    submitted_candidates = []

    async def execute(rows, **_kwargs):
        submitted_candidates.extend(rows)
        return {"executed": [{"order_id": "buy-1"}], "rejected": []}

    monkeypatch.setattr(pm_rebalance.public_execution, "execute_pm_equity", execute)

    first_pass = await pm_rebalance._reconcile_intents(Broker())
    assert first_pass["sell_filled"] == 1
    assert first_pass["replacement_submitted"] == []
    assert intent["status"] == "SELL_FILLED"
    assert submitted_candidates == []

    second_pass = await pm_rebalance._reconcile_intents(Broker())
    assert second_pass["replacement_submitted"] == [{"order_id": "buy-1"}]
    assert intent["status"] == "BUY_SUBMITTED"
    assert submitted_candidates[0]["action"] == "STARTER"
    assert submitted_candidates[0]["allocation_usd"] == 6.0
