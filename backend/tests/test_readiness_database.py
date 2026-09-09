import pytest

from services import readiness


@pytest.mark.asyncio
async def test_database_check_uses_postgres_status_when_postgres_is_active(monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "postgres")

    async def status():
        return {"enabled": True, "ready": True, "schema_ready": True}

    monkeypatch.setattr("services.postgres_store.status", status)
    assert await readiness._database_check() == {
        "ok": True,
        "backend": "postgres",
        "schema_ready": True,
    }


@pytest.mark.asyncio
async def test_database_check_blocks_when_postgres_is_unavailable(monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "postgres")

    async def status():
        return {"enabled": True, "ready": False, "last_error": "connection refused"}

    monkeypatch.setattr("services.postgres_store.status", status)
    result = await readiness._database_check()
    assert result == {"ok": False, "backend": "postgres", "reason": "connection refused"}
