from datetime import datetime, timedelta, timezone

from services import pricer


def test_grouped_research_cache_is_bounded_and_expired_entries_removed(monkeypatch):
    now = datetime(2026, 10, 7, tzinfo=timezone.utc)
    monkeypatch.setattr(pricer, '_grouped_cache', {})
    monkeypatch.setattr(pricer, '_grouped_cache_at', {})
    monkeypatch.setattr(pricer, '_now', lambda: now)
    for day in range(30):
        pricer._store_grouped(str(day), {'ABC': float(day)})
    assert len(pricer._grouped_cache) == 8
    assert len(pricer._grouped_cache_at) == 8
    now += timedelta(hours=2)
    pricer._store_grouped('latest', {'ABC': 10})
    assert pricer._grouped_cache == {'latest': {'ABC': 10}}
