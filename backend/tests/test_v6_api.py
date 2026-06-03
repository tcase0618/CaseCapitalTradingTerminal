"""
V6 backend tests — Pipeline Criteria, PHARMA tab, CONTRACTS tab.

Covers new endpoints added in v3.2:
- /api/admin/pipeline_criteria
- /api/pharma/pdufa, /api/pharma/scan, /api/pharma/active, /api/pharma/track_record
- /api/contracts, /api/contracts/sub_awards, /api/contracts/recent
- Regression checks on existing /api/v32/*, /api/scan/*, /api/learning/*
"""
import os
import time

import pytest
import requests
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parents[2] / "frontend" / ".env")
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")


# ----- Pipeline Criteria -----
class TestPipelineCriteria:
    def test_pipeline_criteria_shape(self):
        r = requests.get(f"{BASE_URL}/api/admin/pipeline_criteria", timeout=30)
        assert r.status_code == 200
        data = r.json()
        assert "pre_filter" in data and isinstance(data["pre_filter"], list)
        assert "final_screener" in data and isinstance(data["final_screener"], list)
        assert "axiom_score_formula" in data
        # Spec: ~10 pre-filter rules, ~13 final-screener weights
        assert len(data["pre_filter"]) >= 8, f"pre_filter has {len(data['pre_filter'])}"
        assert len(data["final_screener"]) >= 10, f"final_screener has {len(data['final_screener'])}"
        # Validate row shapes
        for row in data["pre_filter"]:
            assert "rule" in row and "detail" in row
        for row in data["final_screener"]:
            assert "key" in row and "weight" in row and "description" in row
            assert isinstance(row["weight"], (int, float))


# ----- PHARMA -----
class TestPharma:
    def test_pdufa_get(self):
        r = requests.get(f"{BASE_URL}/api/pharma/pdufa", timeout=30)
        assert r.status_code == 200
        data = r.json()
        assert "results" in data and isinstance(data["results"], list)

    def test_pharma_scan(self):
        r = requests.post(f"{BASE_URL}/api/pharma/scan", timeout=300)
        assert r.status_code == 200, r.text[:500]
        data = r.json()
        assert "duration_sec" in data
        assert "results" in data and isinstance(data["results"], list)
        # Seed list fallback should yield ≥5 rows per spec
        assert len(data["results"]) >= 5, f"expected ≥5, got {len(data['results'])}"
        # Validate row shape
        row = data["results"][0]
        for k in ("ticker", "binary_event_score", "tier", "prevalence",
                  "days_until", "current_price"):
            assert k in row, f"missing key {k} in {list(row.keys())}"
        assert 0 <= row["binary_event_score"] <= 100

    def test_pharma_active(self):
        r = requests.get(f"{BASE_URL}/api/pharma/active", timeout=30)
        assert r.status_code == 200
        data = r.json()
        assert "plays" in data and isinstance(data["plays"], list)

    def test_pharma_track_record(self):
        r = requests.get(f"{BASE_URL}/api/pharma/track_record", timeout=30)
        assert r.status_code == 200
        data = r.json()
        for k in ("settled", "winners", "hit_rate", "history"):
            assert k in data, f"missing {k}"
        assert isinstance(data["history"], list)


# ----- CONTRACTS -----
class TestContracts:
    def test_contracts_list(self):
        r = requests.get(
            f"{BASE_URL}/api/contracts",
            params={"days": 90, "min_amount": 10000000},
            timeout=60,
        )
        assert r.status_code == 200, r.text[:500]
        data = r.json()
        assert "contracts" in data and isinstance(data["contracts"], list)
        assert "filters" in data
        assert "fetched_at" in data
        assert len(data["contracts"]) > 0, "expected non-zero contracts in 90d"
        row = data["contracts"][0]
        for k in ("ticker", "recipient", "agency", "amount",
                  "award_id", "generated_internal_id"):
            assert k in row, f"missing {k}"

    def test_contracts_sub_awards_no_500(self):
        # First get a real generated_internal_id
        lst = requests.get(
            f"{BASE_URL}/api/contracts",
            params={"days": 90, "min_amount": 10000000},
            timeout=60,
        ).json()
        if not lst.get("contracts"):
            pytest.skip("no contracts to test sub_awards")
        gid = lst["contracts"][0]["generated_internal_id"]
        r = requests.get(
            f"{BASE_URL}/api/contracts/sub_awards",
            params={"award_id": gid},
            timeout=60,
        )
        assert r.status_code == 200, r.text[:500]
        data = r.json()
        assert "sub_awards" in data and isinstance(data["sub_awards"], list)
        assert "cached" in data

    def test_contracts_recent_legacy(self):
        # The renamed legacy dashboard tile endpoint
        r = requests.get(
            f"{BASE_URL}/api/contracts/recent",
            params={"limit": 5},
            timeout=30,
        )
        assert r.status_code == 200, r.text[:500]
        # should not 404 / conflict with new /api/contracts


# ----- REGRESSION -----
class TestRegression:
    @pytest.mark.parametrize("ep", [
        "/api/v32/lottery",
        "/api/v32/macro",
        "/api/v32/conviction",
        "/api/v32/dark_horse",
        "/api/v32/x_factor",
        "/api/v32/earnings_week",
        "/api/scan/latest",
        "/api/learning/status",
        "/api/learning/preview",
        "/api/signals/tracker",
        "/api/performance/summary",
    ])
    def test_endpoint_200(self, ep):
        r = requests.get(f"{BASE_URL}{ep}", timeout=60)
        assert r.status_code == 200, f"{ep} → {r.status_code} {r.text[:200]}"

    def test_lottery_has_track_record(self):
        r = requests.get(f"{BASE_URL}/api/v32/lottery", timeout=60)
        assert r.status_code == 200
        data = r.json()
        assert "track_record" in data, f"keys: {list(data.keys())}"
