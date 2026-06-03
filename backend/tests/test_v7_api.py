"""v3.5 AXIOM Intel test suite covering X-Factor, Contracts (with sub_awards),
Macro Pulse dedup, and Pharma research panel data."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
TIMEOUT = 90


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ─────────────── X-Factor ───────────────
class TestXFactor:
    def test_sentiment_endpoint_returns_stocktwits_bullish(self, session):
        r = session.get(f"{BASE_URL}/api/v32/sentiment/TSLA", timeout=TIMEOUT)
        assert r.status_code == 200
        data = r.json()
        assert "stocktwits" in data
        st = data["stocktwits"]
        assert st is not None, "stocktwits is None - StockTwits fetch failed"
        assert "mentions_24h" in st and isinstance(st["mentions_24h"], int)
        # bullish_pct may legitimately be None if no tagged messages
        if st.get("bullish_pct") is not None:
            assert 0.0 <= st["bullish_pct"] <= 1.0
            print(f"TSLA stocktwits: mentions={st['mentions_24h']} bullish_pct={st['bullish_pct']}")

    def test_seed_baseline_idempotent(self):
        import asyncio, sys
        from dotenv import load_dotenv
        load_dotenv("/app/backend/.env")
        sys.path.insert(0, "/app/backend")
        from services.x_factor import seed_baseline
        # Use rare ticker unlikely to exist
        tickers = ["TSLA", "NVDA"]
        n1 = asyncio.get_event_loop().run_until_complete(seed_baseline(tickers))
        n2 = asyncio.get_event_loop().run_until_complete(seed_baseline(tickers))
        print(f"seed_baseline first={n1} second={n2}")
        assert n2 == 0, f"second call should be idempotent, got {n2}"

    def test_reddit_subs_list_has_six(self):
        import sys
        sys.path.insert(0, "/app/backend")
        from services.x_factor import REDDIT_SUBS
        expected = {"wallstreetbets", "investing", "options", "SecurityAnalysis",
                    "stocks", "StockMarket"}
        assert set(REDDIT_SUBS) == expected
        assert len(REDDIT_SUBS) == 6

    def test_x_factor_endpoint_responds(self, session):
        r = session.get(f"{BASE_URL}/api/v32/x_factor", timeout=TIMEOUT)
        assert r.status_code == 200
        data = r.json()
        alerts = data.get("alerts", data) if isinstance(data, dict) else data
        assert isinstance(alerts, list)
        print(f"x_factor alerts count={len(alerts)}")


# ─────────────── Contracts ───────────────
class TestContracts:
    def test_contracts_returns_subawards(self, session):
        r = session.get(f"{BASE_URL}/api/contracts",
                        params={"days": 180, "min_amount": 100_000_000},
                        timeout=TIMEOUT)
        assert r.status_code == 200
        body = r.json()
        rows = body.get("contracts") if isinstance(body, dict) else body
        assert isinstance(rows, list)
        if not rows:
            pytest.skip("no contracts in window")
        # Every row should have a sub_awards field (list, even if empty)
        for row in rows:
            assert "sub_awards" in row, f"missing sub_awards in {row.get('award_id')}"
            assert isinstance(row["sub_awards"], list)
        with_subs = [r for r in rows if r["sub_awards"]]
        pct_with_subs = 100 * len(with_subs) / len(rows)
        print(f"Contracts: {len(rows)} total, {len(with_subs)} with subs ({pct_with_subs:.1f}%)")
        # Check first sub row structure
        if with_subs:
            sub = with_subs[0]["sub_awards"][0]
            assert "recipient" in sub
            assert "amount" in sub
            print(f"sample sub: {sub.get('recipient')} ${sub.get('amount')} tk={sub.get('ticker')}")

    def test_contracts_recent_regression(self, session):
        r = session.get(f"{BASE_URL}/api/contracts/recent?limit=5", timeout=30)
        assert r.status_code == 200


# ─────────────── Macro Pulse ───────────────
class TestMacro:
    def test_macro_events_dedup_by_release_and_month(self, session):
        r = session.get(f"{BASE_URL}/api/v32/macro", timeout=30)
        assert r.status_code == 200
        events = r.json().get("events", [])
        keys = [(e["release_id"], e["date"][:7]) for e in events]
        assert len(keys) == len(set(keys)), \
            f"duplicate (release_id, YYYY-MM) keys: {[k for k in keys if keys.count(k) > 1]}"
        print(f"Macro events: {len(events)}, unique keys: {len(set(keys))}")


# ─────────────── Pharma ───────────────
class TestPharma:
    def test_pharma_pdufa_endpoint(self, session):
        r = session.get(f"{BASE_URL}/api/pharma/pdufa", timeout=30)
        assert r.status_code == 200

    def test_pharma_scan_returns_trial_short_insider(self, session):
        r = session.post(f"{BASE_URL}/api/pharma/scan", json={}, timeout=180)
        assert r.status_code == 200
        results = r.json().get("results", []) or r.json()
        if not isinstance(results, list):
            results = results.get("results", []) if isinstance(results, dict) else []
        assert isinstance(results, list)
        if not results:
            pytest.skip("no pharma results")
        # Check enrichment fields
        trial_present = sum(1 for x in results
                            if (x.get("trial") or {}).get("nct_id"))
        short_present = sum(1 for x in results if x.get("short_pct") is not None)
        insider_present = sum(1 for x in results
                              if (x.get("insider_summary") or {}).get("buy_count") is not None)
        print(f"Pharma {len(results)} rows: trial={trial_present} short={short_present} insider={insider_present}")
        sample = results[0]
        print(f"sample={sample.get('ticker')} trial={sample.get('trial')} "
              f"short={sample.get('short_pct')} ins={sample.get('insider_summary')}")
        # Expectation: at least SOME enrichment must work
        assert trial_present + short_present + insider_present > 0


# ─────────────── Regression ───────────────
class TestRegression:
    @pytest.mark.parametrize("path", [
        "/api/v32/lottery",
        "/api/v32/conviction",
        "/api/v32/dark_horse",
        "/api/admin/pipeline_criteria",
        "/api/contracts/recent?limit=5",
        "/api/learning/preview",
        "/api/scan/latest",
        "/api/pharma/pdufa",
        "/api/pharma/active",
        "/api/pharma/track_record",
    ])
    def test_endpoint_responds_200(self, session, path):
        r = session.get(f"{BASE_URL}{path}", timeout=60)
        assert r.status_code == 200, f"{path} returned {r.status_code}: {r.text[:200]}"
