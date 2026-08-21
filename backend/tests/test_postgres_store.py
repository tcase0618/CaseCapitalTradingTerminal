from datetime import datetime, timezone

import pytest

from services import postgres_store


def test_postgres_disabled_without_env(monkeypatch):
    monkeypatch.setenv("POSTGRES_ENABLED", "false")
    monkeypatch.delenv("POSTGRES_DSN", raising=False)
    assert postgres_store.enabled() is False


def test_postgres_doc_key_prefers_business_keys():
    assert postgres_store.doc_key("scan_results", {"finished_at": "2026-08-21T12:00:00Z"}) == "2026-08-21T12:00:00Z"
    assert postgres_store.doc_key("pharma_pdufa", {"ticker": "MRNA", "pdufa_date": "2026-09-01"}) == "MRNA:2026-09-01"
    assert postgres_store.doc_key("options_desk_orders", {"order_id": "abc"}) == "abc"


def test_postgres_json_normalization_handles_dates_and_unknown_objects():
    class Odd:
        def __str__(self):
            return "odd-object"

    payload = postgres_store.normalize_json({
        "ts": datetime(2026, 8, 21, tzinfo=timezone.utc),
        "odd": Odd(),
    })
    assert payload["ts"] == "2026-08-21T00:00:00+00:00"
    assert payload["odd"] == "odd-object"


@pytest.mark.asyncio
async def test_postgres_status_disabled(monkeypatch):
    monkeypatch.setenv("POSTGRES_ENABLED", "false")
    status = await postgres_store.status()
    assert status["enabled"] is False
    assert status["ready"] is False
