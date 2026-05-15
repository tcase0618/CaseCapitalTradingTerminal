"""V4 regression tests — Massive API price source, options performance curve,
and learning page expansions (preview, weight_history, signal_stats)."""
from __future__ import annotations

import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
assert BASE_URL, "REACT_APP_BACKEND_URL must be set"


# ---------- /api/admin/price_source ----------
class TestPriceSource:
    def test_price_source_returns_massive(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/admin/price_source", timeout=20)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "source" in d
        assert "massive_available" in d
        assert isinstance(d["massive_available"], bool)
        # Key is set in .env so massive_available should be True
        assert d["massive_available"] is True
        assert d["source"] == "massive"


# ---------- /api/admin/refresh_prices ----------
class TestRefreshPrices:
    def test_refresh_prices_succeeds(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/admin/refresh_prices", timeout=120)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("ok") is True
        assert "first_seen_updated" in d
        assert "perf_rows_updated" in d
        assert isinstance(d["first_seen_updated"], int)
        assert isinstance(d["perf_rows_updated"], int)
        assert d.get("source") == "massive"


# ---------- /api/signals/curve ----------
class TestSignalsCurve:
    def test_stock_curve_default(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/signals/curve?days=90", timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("days") == 90
        assert "curve" in d
        assert isinstance(d["curve"], list)


# ---------- /api/signals/options_curve ----------
class TestOptionsCurve:
    def test_options_curve_returns_shape(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/signals/options_curve?days=90", timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("days") == 90
        assert "curve" in d
        curve = d["curve"]
        assert isinstance(curve, list)
        if curve:
            sample = curve[0]
            # Spec: avg_gain_pct, positions, winners, losers, strategies
            for k in ("avg_gain_pct", "positions", "winners", "losers", "strategies"):
                assert k in sample, f"missing field {k} in curve row {sample}"

    def test_options_curve_days_param(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/signals/options_curve?days=30", timeout=60)
        assert r.status_code == 200
        assert r.json().get("days") == 30


# ---------- /api/learning/preview ----------
class TestLearningPreview:
    def test_preview_returns_required_fields(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/learning/preview", timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ("trades_available", "would_run", "would_change_count", "rows"):
            assert k in d, f"preview missing {k}"
        assert isinstance(d["trades_available"], int)
        assert isinstance(d["would_run"], bool)
        assert isinstance(d["would_change_count"], int)
        assert isinstance(d["rows"], list)


# ---------- /api/learning/weight_history ----------
class TestWeightHistory:
    def test_weight_history_returns_list(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/learning/weight_history", timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        assert isinstance(d, list)

    def test_weight_history_with_key_filter(self, api_client):
        r = api_client.get(
            f"{BASE_URL}/api/learning/weight_history?weight_key=signal:CONGRESS_BUY",
            timeout=30,
        )
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# ---------- /api/learning/signal_stats ----------
class TestSignalStats:
    def test_signal_stats_returns_league(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/learning/signal_stats", timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        # Could be list or dict with "rows" — be liberal
        assert isinstance(d, (list, dict))


# ---------- regression: tracker + performance summary ----------
class TestRegression:
    def test_signals_tracker_still_works(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/signals/tracker?limit=50", timeout=60)
        assert r.status_code == 200
        d = r.json()
        assert "rows" in d and isinstance(d["rows"], list)
        assert "total" in d
        assert "winners" in d
        assert "losers" in d

    def test_performance_summary_still_works(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/performance/summary", timeout=60)
        assert r.status_code == 200
        d = r.json()
        assert "signals" in d
        assert "options" in d

    def test_scan_latest_still_works(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/scan/latest", timeout=30)
        assert r.status_code == 200


@pytest.fixture
def api_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s
