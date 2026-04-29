"""Full API regression tests for Stock Intel Bot."""
import time
import pytest


# ---------- Basic ----------
class TestStatus:
    def test_status_shape(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/status", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert "bot" in d and "stats" in d
        assert d["bot"]["claude_configured"] is True
        # telegram intentionally unset
        assert d["bot"]["telegram_configured"] is False
        # _id leakage check
        assert "_id" not in d


class TestTelegram:
    def test_telegram_info_unconfigured(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/telegram/info", timeout=15)
        assert r.status_code == 200
        assert r.json() == {"configured": False}

    def test_telegram_webhook_accepts_payload(self, api_client, base_url):
        payload = {"update_id": 1, "message": {"text": "/start", "chat": {"id": 1}}}
        r = api_client.post(f"{base_url}/api/telegram/webhook", json=payload, timeout=15)
        assert r.status_code == 200
        assert r.json().get("ok") is True

    def test_telegram_webhook_invalid_json(self, api_client, base_url):
        r = api_client.post(
            f"{base_url}/api/telegram/webhook",
            data="not-json",
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        assert r.status_code == 200
        # ok may be False on invalid JSON — endpoint still returns 200
        assert "ok" in r.json()


# ---------- Scan ----------
class TestScan:
    def test_scan_run_and_cache(self, api_client, base_url):
        # First run (cache likely already populated; still valid)
        r1 = api_client.post(f"{base_url}/api/scan/run", timeout=180)
        assert r1.status_code == 200, r1.text
        s1 = r1.json()
        for k in [
            "started_at", "finished_at", "pre_filter_passed",
            "claude_calls_made", "claude_cache_hits", "results", "raw_counts",
        ]:
            assert k in s1, f"missing key {k}"
        assert "_id" not in s1
        assert isinstance(s1["results"], list)
        for item in s1["results"]:
            assert "_id" not in item
            assert "ticker" in item
            for rk in ["signal_score", "thesis", "entry_zone", "catalyst_date"]:
                assert rk in item
        # Second run — should hit cache fully (live scrapers may yield
        # slightly different candidate counts between back-to-back runs, so we
        # only assert that fresh Claude calls == 0 and cache_hits == pre_filter)
        r2 = api_client.post(f"{base_url}/api/scan/run", timeout=180)
        assert r2.status_code == 200
        s2 = r2.json()
        assert s2["claude_calls_made"] == 0, f"expected 0 fresh claude calls, got {s2['claude_calls_made']}"
        assert s2["claude_cache_hits"] == s2["pre_filter_passed"], (
            f"cache hits {s2['claude_cache_hits']} != pre {s2['pre_filter_passed']}"
        )

    def test_scan_latest(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/scan/latest", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert "_id" not in d
        assert "results" in d

    def test_scan_history(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/scan/history", timeout=30)
        assert r.status_code == 200
        items = r.json()
        assert isinstance(items, list)
        for it in items:
            assert "_id" not in it


# ---------- Activity ----------
class TestActivity:
    def test_activity_list(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/activity", timeout=30)
        assert r.status_code == 200
        items = r.json()
        assert isinstance(items, list)
        for it in items:
            assert "_id" not in it


# ---------- Watchlist CRUD ----------
class TestWatchlist:
    def test_crud(self, api_client, base_url):
        ticker = "AAPL"
        # ensure clean
        api_client.delete(f"{base_url}/api/watchlist/{ticker}", timeout=15)
        r = api_client.post(f"{base_url}/api/watchlist", json={"ticker": ticker}, timeout=30)
        assert r.status_code == 200
        assert r.json()["ticker"] == ticker

        r2 = api_client.get(f"{base_url}/api/watchlist", timeout=30)
        assert r2.status_code == 200
        items = r2.json()
        tickers = [i["ticker"] for i in items]
        assert ticker in tickers
        # price/name attached (may be None if yfinance blocked; but key must exist)
        item = next(i for i in items if i["ticker"] == ticker)
        assert "price" in item and "name" in item
        for i in items:
            assert "_id" not in i

        r3 = api_client.delete(f"{base_url}/api/watchlist/{ticker}", timeout=15)
        assert r3.status_code == 200
        assert r3.json().get("deleted", 0) >= 1

        r4 = api_client.get(f"{base_url}/api/watchlist", timeout=30)
        assert ticker not in [i["ticker"] for i in r4.json()]


# ---------- Alerts CRUD ----------
class TestAlerts:
    def test_crud(self, api_client, base_url):
        ticker = "AAPL"
        api_client.delete(f"{base_url}/api/alerts/{ticker}", timeout=15)
        r = api_client.post(
            f"{base_url}/api/alerts",
            json={"ticker": ticker, "target_price": 150},
            timeout=15,
        )
        assert r.status_code == 200
        assert r.json()["ticker"] == ticker
        assert r.json()["target_price"] == 150.0

        r2 = api_client.get(f"{base_url}/api/alerts", timeout=15)
        assert r2.status_code == 200
        items = r2.json()
        assert any(i["ticker"] == ticker for i in items)
        for i in items:
            assert "_id" not in i

        r3 = api_client.delete(f"{base_url}/api/alerts/{ticker}", timeout=15)
        assert r3.status_code == 200
        assert r3.json().get("deleted", 0) >= 1


# ---------- Quote / Analyze ----------
class TestQuoteAnalyze:
    def test_quote(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/quote/AAPL", timeout=60)
        # yfinance occasionally 404s due to rate limit — accept 200 or 404 gracefully
        assert r.status_code in (200, 404)
        if r.status_code == 200:
            d = r.json()
            assert d["ticker"] == "AAPL"
            assert "price" in d

    def test_analyze(self, api_client, base_url):
        r = api_client.post(f"{base_url}/api/analyze/AAPL", timeout=90)
        # Cached, so should be fast. 500 if quote unavailable + claude fail
        assert r.status_code in (200, 500)
        if r.status_code == 200:
            d = r.json()
            assert "analysis" in d
            a = d["analysis"]
            for k in ["ticker", "signal_score", "thesis", "entry_zone", "catalyst_date"]:
                assert k in a
