from services import execution_funnel


def _pm(ticker: str, action: str = "STARTER", **extra):
    row = {"ticker": ticker, "action": action, "source_scan": "lottery_day2_continuation", "scanner_family": "LOTTERY", "pm_score": 80, "allocation_usd": 4}
    row.update(extra)
    return row


def test_equity_rejection_is_visible_as_one_terminal_state():
    payload = execution_funnel.build_cycle(
        cycle_id="cycle-1", pm_recommendations=[_pm("AAA")],
        equity_execution={"skipped": False, "rejected": [{"ticker": "AAA", "reason": "public_final_quote_stale_or_unverifiable"}]},
        options_payload={}, options_execution={}, observed_at="2026-09-17T14:00:00Z",
    )
    event = payload["events"][0]
    assert event["terminal_state"] == "EQUITY_BLOCKED"
    assert event["terminal_reason"] == "public_final_quote_stale_or_unverifiable"
    assert payload["summary"]["pm_approved"] == 1


def test_options_contract_blockers_are_not_lost_after_pm_approval():
    payload = execution_funnel.build_cycle(
        cycle_id="cycle-2", pm_recommendations=[_pm("OPT", preferred_route="OPTION")], equity_execution={},
        options_payload={"candidates": [{"ticker": "OPT", "route": "OPTION", "blocked_reasons": ["spread too wide", "open interest too low"]}]},
        options_execution={"skipped": True, "reason": "options_execution_disabled"}, observed_at="2026-09-17T14:00:00Z",
    )
    event = payload["events"][0]
    assert event["terminal_state"] == "OPTIONS_NOT_READY"
    assert "spread too wide" in event["terminal_reason"]


def test_order_submission_is_distinct_from_pm_approval_and_nonapproval():
    payload = execution_funnel.build_cycle(
        cycle_id="cycle-3", pm_recommendations=[_pm("BUY"), _pm("WATCH", action="WATCH")],
        equity_execution={"executed": [{"ticker": "BUY", "order_id": "broker-order"}]}, options_payload={}, options_execution={}, observed_at="2026-09-17T14:00:00Z",
    )
    states = {row["ticker"]: row["terminal_state"] for row in payload["events"]}
    assert states == {"BUY": "ORDER_SUBMITTED", "WATCH": "PM_NOT_APPROVED"}
