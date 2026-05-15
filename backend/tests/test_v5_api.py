"""V5 regression tests — Massive rate-limit fix, yfinance batch current prices,
intraday entry-price restore, learning engine LIVE-basis trades."""
from __future__ import annotations

import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
assert BASE_URL, "REACT_APP_BACKEND_URL must be set"


# ---------- /api/admin/price_source ----------
class TestPriceSource:
    def test_returns_massive_available(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/admin/price_source", timeout=20)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("source") == "massive"
        assert d.get("massive_available") is True


# ---------- /api/admin/refresh_prices (current-only semantics) ----------
class TestRefreshPricesCurrentOnly:
    def test_refresh_prices_returns_current_only_shape(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/admin/refresh_prices", timeout=180)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("ok") is True
        # New current-only semantics — must NOT return first_seen_updated
        assert "tickers_requested" in d
        assert "tickers_refreshed" in d
        assert "tickers_missing" in d
        assert isinstance(d["tickers_requested"], int)
        assert isinstance(d["tickers_refreshed"], int)
        # Most tickers should be refreshed (>80%)
        if d["tickers_requested"] > 0:
            ratio = d["tickers_refreshed"] / d["tickers_requested"]
            assert ratio >= 0.8, f"only {d['tickers_refreshed']}/{d['tickers_requested']} refreshed"


# ---------- /api/admin/restore_entry_prices ----------
class TestRestoreEntryPrices:
    def test_restore_entry_prices_returns_count(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/admin/restore_entry_prices", timeout=120)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "restored" in d
        assert isinstance(d["restored"], int)
        assert d["restored"] >= 0


# ---------- /api/admin/fill_missing_entry_prices ----------
class TestFillMissingEntryPrices:
    def test_fill_missing_entry_prices_returns_shape(self, api_client):
        r = api_client.post(f"{BASE_URL}/api/admin/fill_missing_entry_prices", timeout=120)
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ("first_seen_filled", "perf_rows_filled", "failures", "source"):
            assert k in d, f"missing {k}"
        assert d["source"] == "massive"


# ---------- /api/signals/tracker — REAL gains ----------
class TestSignalsTrackerRealGains:
    def test_tracker_has_real_movement(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/signals/tracker?limit=200", timeout=60)
        assert r.status_code == 200
        d = r.json()
        rows = d["rows"]
        assert len(rows) > 0, "tracker returned empty"
        # At least some tickers must have non-zero gains (yfinance fix)
        non_zero = [r for r in rows if r.get("gain_pct") is not None and abs(r["gain_pct"]) > 0.5]
        assert len(non_zero) >= 5, f"expected >=5 movers, got {len(non_zero)} out of {len(rows)}"
        # Winners + losers both > 0
        assert d["winners"] > 0
        assert d["losers"] > 0
        # avg_gain_pct should be present
        assert "avg_gain_pct" in d


# ---------- /api/learning/preview — LIVE basis ----------
class TestLearningPreviewLiveBasis:
    def test_preview_uses_live_basis(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/learning/preview", timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ("trades_available", "would_run", "would_change_count", "rows"):
            assert k in d
        # With 58 trades available, would_run should be True
        if d["trades_available"] >= 3:
            assert d["would_run"] is True, f"would_run=False with {d['trades_available']} trades"
        # Rows must have basis field
        if d["rows"]:
            for row in d["rows"]:
                if row.get("samples", 0) > 0:
                    assert "basis" in row, f"row missing basis: {row}"
                    # basis is one of live, 30d, or None for skipped
                    if row.get("basis") is not None:
                        assert row["basis"] in ("live", "30d"), f"unexpected basis={row['basis']}"


# ---------- /api/learning/run — persistence ----------
class TestLearningRunPersistence:
    def test_run_then_runs_endpoint_shows_persisted(self, api_client):
        # Trigger a cycle
        r = api_client.post(f"{BASE_URL}/api/learning/run", timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        # Either ran or skipped — both ok. Run response shape: {trades, changes, win_rate, insights}
        assert ("trades_analyzed" in d) or ("trades" in d) or ("skipped" in d) or ("reason" in d)
        # If it ran, fetch /runs to confirm persistence
        if d.get("trades_analyzed") or d.get("trades"):
            r2 = api_client.get(f"{BASE_URL}/api/learning/runs?limit=1", timeout=30)
            assert r2.status_code == 200
            runs = r2.json()
            assert len(runs) >= 1
            latest = runs[0]
            assert "trades_analyzed" in latest
            # New fields per spec: trades_30d + trades_live
            assert "trades_30d" in latest or "trades_live" in latest


# ---------- /api/learning/signal_stats — new fields ----------
class TestSignalStatsLiveFields:
    def test_signal_stats_has_live_and_30d_fields(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/learning/signal_stats", timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        assert isinstance(d, list)
        assert len(d) >= 13, f"expected 13 signals, got {len(d)}"
        # Each row should have n_live and win_rate_live (new fields). n_30d is omitted on empty signals.
        for row in d:
            for k in ("signal", "n_live", "win_rate_live", "avg_live"):
                assert k in row, f"signal_stats row missing {k}: {row}"
        # At least one signal should have all 30d fields (those with n>0)
        with_data = [r for r in d if r.get("n_live", 0) > 0]
        assert any("n_30d" in r for r in with_data), "no signal row exposes n_30d field"
        # At least one signal should have live trades
        with_live = [r for r in d if r.get("n_live", 0) > 0]
        assert len(with_live) > 0, "no signals have live trades"


# ---------- /api/learning/combos — lowered threshold to 2 ----------
class TestLearningCombosThreshold:
    def test_combos_surfaces_pairs_with_2_plus_trades(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/learning/combos", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert isinstance(d, list)
        # All combos returned should have trade_count >= 2
        for c in d:
            assert c.get("trade_count", 0) >= 2, f"combo with trade_count <2: {c}"


# ---------- Regression: v4 surface still intact ----------
class TestV4Regression:
    def test_signals_curve(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/signals/curve?days=90", timeout=60)
        assert r.status_code == 200
        assert "curve" in r.json()

    def test_options_curve(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/signals/options_curve?days=90", timeout=60)
        assert r.status_code == 200
        assert "curve" in r.json()

    def test_performance_summary(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/performance/summary", timeout=60)
        assert r.status_code == 200
        d = r.json()
        assert "signals" in d
        assert "options" in d

    def test_weight_history(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/learning/weight_history", timeout=30)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_scan_latest(self, api_client):
        r = api_client.get(f"{BASE_URL}/api/scan/latest", timeout=30)
        assert r.status_code == 200


@pytest.fixture
def api_client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s
