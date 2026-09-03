import asyncio
from datetime import timezone

from services import execution_gate, pricer, trade_floor


def test_parse_dt_restored_and_normalized_to_utc():
    parsed = execution_gate._parse_dt("2026-09-02T12:00:00Z")
    assert parsed is not None
    assert parsed.tzinfo == timezone.utc
    assert parsed.hour == 12
    assert execution_gate._parse_dt("not-a-date") is None


def test_stale_execution_authority_is_revalidated(monkeypatch):
    async def account():
        return {"equity": "1000", "cash": "500"}

    monkeypatch.setattr(pricer, "execution_source_label", lambda: "alpaca")
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

    monkeypatch.setattr(pricer, "execution_source_label", lambda: "alpaca")
    monkeypatch.setattr(trade_floor, "get_account", account)
    blockers = [{"key": "integration:alpaca", "status": "STALE"}]

    remaining = asyncio.run(
        execution_gate._revalidate_stale_execution_authority({}, "options", blockers)
    )

    assert remaining == blockers
