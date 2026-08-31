import asyncio

from services import execution_gate, pricer, trade_floor


def test_stale_execution_authority_is_revalidated(monkeypatch):
    async def account():
        return {"equity": "1000", "cash": "500"}

    monkeypatch.setattr(pricer, "source_label", lambda: "alpaca paper")
    monkeypatch.setattr(trade_floor, "get_account", account)
    truth = {}
    blockers = [
        {"key": "integration:alpaca", "status": "STALE"},
        {"key": "integration:price_path", "status": "STALE"},
        {"key": "other", "status": "DOWN"},
    ]

    remaining = asyncio.run(
        execution_gate._revalidate_stale_execution_authority(truth, "equity", blockers)
    )

    assert [row["key"] for row in remaining] == ["other"]
    assert truth["execution_authority_revalidation"]["alpaca_account"] is True


def test_failed_execution_authority_revalidation_keeps_blockers(monkeypatch):
    async def account():
        return None

    monkeypatch.setattr(pricer, "source_label", lambda: "alpaca paper")
    monkeypatch.setattr(trade_floor, "get_account", account)
    blockers = [{"key": "integration:alpaca", "status": "STALE"}]

    remaining = asyncio.run(
        execution_gate._revalidate_stale_execution_authority({}, "options", blockers)
    )

    assert remaining == blockers
