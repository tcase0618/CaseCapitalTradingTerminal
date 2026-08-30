from services.schedule_control import SOURCES, _status


def test_options_candidates_are_standby_outside_market_session():
    source = next(item for item in SOURCES if item.key == "options_candidates")
    assert source.market_session_only is True
    assert _status(10_000, source.max_age_minutes, market_paused=True) == "STANDBY"


def test_standby_does_not_hide_missing_evidence_when_market_is_open():
    assert _status(None, 390, market_paused=False) == "MISSING"


def test_source_status_boundaries_are_explicit():
    assert _status(10, 10) == "LIVE"
    assert _status(10.01, 10) == "STALE"
    assert _status(20.01, 10) == "DOWN"
