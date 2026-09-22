from services.strategy_ideology import apply_lottery_learning


def _case() -> dict:
    return {"case_score": 40.0, "confidence": 0.60, "strategy_id": "lottery_day2_continuation"}


def test_lottery_learning_is_neutral_without_resolved_sample():
    result = apply_lottery_learning(
        _case(),
        native_score=37.0,
        row={},
        learned_config={"version": "test", "sample_count": 0, "min_ticket_score": 60},
    )

    assert result["case_score"] == 40.0
    assert result["learning_adjustment"]["active"] is False
    assert result["learning_adjustment"]["badges"] == ["LEARNING_GATHERING"]


def test_lottery_learning_penalizes_below_minimum_after_sample_is_available():
    result = apply_lottery_learning(
        _case(),
        native_score=37.0,
        row={},
        learned_config={"version": "test", "sample_count": 10, "min_ticket_score": 60},
    )

    assert result["case_score"] == 32.0
    assert result["learning_adjustment"]["active"] is True
