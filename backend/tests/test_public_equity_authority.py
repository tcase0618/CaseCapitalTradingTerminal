import asyncio

from services import trade_floor


def test_legacy_alpaca_equity_execution_is_retired():
    result = asyncio.run(trade_floor.evaluate_and_execute([]))

    assert result["skipped"] is True
    assert result["reason"] == "alpaca_equity_execution_retired_public_is_sole_equity_broker"


def test_legacy_alpaca_equity_queue_is_retired():
    result = asyncio.run(trade_floor.flush_queued_equity_orders())

    assert result["submitted"] == []
    assert result["reason"] == "alpaca_equity_execution_retired_public_is_sole_equity_broker"
