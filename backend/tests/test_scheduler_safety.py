import pytest

from services import scheduler


@pytest.mark.asyncio
async def test_scheduler_holds_when_calendar_credentials_are_missing(monkeypatch):
    monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
    allowed, reason = await scheduler._stock_scan_market_day_now()
    assert allowed is False
    assert "weekend" in reason or "scheduled scan held" in reason
