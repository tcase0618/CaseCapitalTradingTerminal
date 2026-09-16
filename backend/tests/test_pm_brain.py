from services import pm_brain


def _row(**overrides):
    row = {
        "ticker": "ACME",
        "action": "ACCUMULATE",
        "pm_score": 81.5,
        "case_score": 77,
        "strategy_confidence": 0.8,
        "price": 12.5,
        "target_blended": 17.5,
        "stop_loss": 10.5,
        "signals": ["RVOL", "GAP/SURGE"],
        "source_scan": "lottery_day2_continuation",
        "strategy_views": [{"screener_id": "lottery_day2_continuation", "family": "LOTTERY", "lane": "DAY2_CONTINUATION"}],
    }
    row.update(overrides)
    return row


def test_pm_observation_tracks_decision_change_and_source_backed_evidence():
    observation = pm_brain.build_observation(
        _row(),
        cycle_id="cycle-1",
        observed_at="2026-09-15T14:00:00+00:00",
        prior={"latest_action": "WATCH", "latest_pm_score": 70},
    )
    assert observation["ticker"] == "ACME"
    assert observation["action"] == "ACCUMULATE"
    assert observation["strategy_lanes"] == ["DAY2_CONTINUATION"]
    assert observation["evidence_integrity"]["has_stop"] is True
    assert "PM action changed from WATCH to ACCUMULATE" in observation["change_reasons"]


def test_pm_synopsis_reports_execution_constraint_without_inventing_a_thesis():
    observation = pm_brain.build_observation(
        _row(action="WATCH", equity_execution_blocker="Public cash buying power $3.99 is below $6.00 minimum live allocation"),
        cycle_id="cycle-2",
        observed_at="2026-09-15T15:00:00+00:00",
    )
    synopsis = pm_brain.render_synopsis(observation)
    assert "WATCH" in synopsis
    assert "No new equity order is authorized" in synopsis
    assert "Public cash buying power" in synopsis
