import asyncio

from services import postgres_store


def test_get_pool_fails_closed_when_postgres_is_disabled(monkeypatch):
    monkeypatch.setattr(postgres_store, "enabled", lambda: False)

    assert asyncio.run(postgres_store.get_pool()) is None
