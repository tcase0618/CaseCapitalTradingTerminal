"""V2 backend tests for Stock Intel Bot — gov contracts, risk, targets, scan v2 schema."""
import pytest


# ---------- Government Contracts ----------
class TestContracts:
    def test_contracts_endpoint(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/contracts?limit=5", timeout=60)
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, list)
        # Each item should have a ticker (mapped to public companies)
        for item in data[:5]:
            assert isinstance(item, dict)
            assert "_id" not in item

    def test_agency_endpoint_dod(self, api_client, base_url):
        r = api_client.get(
            f"{base_url}/api/agency/Department%20of%20Defense?days=30", timeout=90
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, list)


# ---------- Risk + Target ----------
class TestRisk:
    def test_risk_lmt(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/risk/LMT", timeout=60)
        # yfinance may rate-limit; treat 404 as degraded but acceptable
        assert r.status_code in (200, 404), r.text
        if r.status_code != 200:
            pytest.skip("yfinance unavailable for LMT")
        d = r.json()
        assert d["ticker"] == "LMT"
        assert "fundamentals" in d
        risk = d["risk"]
        for k in ["score", "level", "emoji", "factors"]:
            assert k in risk
        assert risk["level"] in ["LOW", "MEDIUM", "HIGH", "EXTREME"]
        assert risk["emoji"] in ["🟢", "🟡", "🔴", "☠️"]
        assert isinstance(risk["factors"], list)
        # Threshold mapping check
        s = risk["score"]
        if s <= 1:
            assert risk["level"] == "LOW"
        elif s <= 3:
            assert risk["level"] == "MEDIUM"
        elif s <= 5:
            assert risk["level"] == "HIGH"
        else:
            assert risk["level"] == "EXTREME"


class TestTarget:
    def test_target_aapl(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/target/AAPL", timeout=60)
        assert r.status_code in (200, 404), r.text
        if r.status_code != 200:
            pytest.skip("yfinance unavailable for AAPL")
        d = r.json()
        assert d["ticker"] == "AAPL"
        targets = d["targets"]
        assert "methods" in targets
        methods = targets["methods"]
        for m in ["contract_revenue_multiple", "analyst_consensus", "signal_adjusted"]:
            assert m in methods, f"missing method {m}"
        for k in ["target_blended", "upside_blended", "current_price"]:
            assert k in targets


class TestCompare:
    def test_compare_lmt_rtx(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/compare/LMT/RTX", timeout=90)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "LMT" in d and "RTX" in d
        for t in ("LMT", "RTX"):
            assert "risk" in d[t]
            assert "targets" in d[t]
            assert d[t]["risk"]["level"] in ["LOW", "MEDIUM", "HIGH", "EXTREME"]


# ---------- Gov-only scan (no Claude) ----------
class TestGovScan:
    def test_scan_gov_endpoint(self, api_client, base_url):
        r = api_client.post(f"{base_url}/api/scan/gov", timeout=180)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "results" in d
        assert "budget_surges" in d
        assert isinstance(d["results"], list)
        assert "_id" not in d
        # Each result item should have ticker + signals + risk + targets
        for item in d["results"][:5]:
            assert "ticker" in item
            assert "signals" in item
            assert "_id" not in item


# ---------- V2 scan schema verification ----------
class TestScanV2Schema:
    def test_latest_v2_schema(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/scan/latest", timeout=30)
        assert r.status_code == 200
        scan = r.json()
        assert "_id" not in scan
        assert "results" in scan
        assert "budget_surges" in scan
        results = scan["results"]
        assert isinstance(results, list)
        if not results:
            pytest.skip("No latest scan results to validate v2 schema")

        # Validate first result has v2 schema
        r0 = results[0]
        for k in [
            "ticker", "signal_score", "signals", "risk", "targets",
            "thesis", "entry_low", "entry_high", "stop_loss",
            "conviction", "time_horizon",
        ]:
            assert k in r0, f"missing v2 key {k}"
        # Risk shape
        risk = r0["risk"]
        for k in ["score", "level", "emoji", "factors"]:
            assert k in risk
        assert risk["level"] in ["LOW", "MEDIUM", "HIGH", "EXTREME"]
        # Targets shape
        targets = r0["targets"]
        assert "methods" in targets
        for m in ["contract_revenue_multiple", "analyst_consensus", "signal_adjusted"]:
            assert m in targets["methods"]
        for k in ["target_blended", "upside_blended"]:
            assert k in targets

    def test_gov_signals_firing(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/scan/latest", timeout=30)
        assert r.status_code == 200
        scan = r.json()
        results = scan.get("results", [])
        if not results:
            pytest.skip("no scan results")
        gov_tickers = {"LDOS", "LHX", "BAH", "DELL", "SAIC", "BWXT", "PLTR", "HII"}
        gov_signals = {"CONTRACT_SURGE", "NEW_WINNER", "MOMENTUM_STACK",
                       "CONCENTRATION_WIN", "BUDGET_SURGE"}
        found_gov_ticker = False
        found_gov_signal = False
        for r0 in results:
            if r0["ticker"] in gov_tickers:
                found_gov_ticker = True
            sigs = set(r0.get("signals", []))
            if sigs & gov_signals:
                found_gov_signal = True
        assert found_gov_ticker, f"no gov ticker in {[r0['ticker'] for r0 in results]}"
        assert found_gov_signal, "no gov signals firing in latest scan"

    def test_token_efficiency_cache(self, api_client, base_url):
        """Second scan should hit cache fully (claude_calls_made=0)."""
        r = api_client.post(f"{base_url}/api/scan/run", timeout=240)
        assert r.status_code == 200, r.text
        s = r.json()
        assert "claude_calls_made" in s
        assert "claude_cache_hits" in s
        # Within same UTC day, should hit cache fully
        assert s["claude_calls_made"] == 0, (
            f"expected 0 fresh claude calls but got {s['claude_calls_made']}"
        )
        assert s["claude_cache_hits"] == s["pre_filter_passed"], (
            f"cache hits {s['claude_cache_hits']} != pre {s['pre_filter_passed']}"
        )


# ---------- Telegram webhook v2 commands ----------
class TestTelegramV2Commands:
    @pytest.mark.parametrize("cmd", [
        "/contracts", "/scan_gov", "/agency Department of Defense",
        "/watchlist_contracts", "/risk LMT", "/target AAPL", "/compare LMT RTX",
    ])
    def test_command_does_not_crash(self, api_client, base_url, cmd):
        payload = {
            "update_id": hash(cmd) & 0xFFFFFF,
            "message": {
                "text": cmd,
                "chat": {"id": 8073083936},
                "from": {"id": 1, "username": "tester"},
            },
        }
        r = api_client.post(
            f"{base_url}/api/telegram/webhook", json=payload, timeout=15
        )
        assert r.status_code == 200
        assert r.json().get("ok") is True
