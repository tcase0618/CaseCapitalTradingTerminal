"""
v5.0 backend tests:
 - Trade Floor (account, regime, positions, manual_send, engine status/recalibrate)
 - SEC Filings (poll, list with ticker filter)
 - Contracts public-only subcontractors
 - Price source priority (Alpaca first)
 - Regression on legacy endpoints
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
TIMEOUT = 60


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ---------- Trade Floor ----------
class TestTradeFloor:
    def test_account(self, client):
        r = client.get(f"{BASE_URL}/api/trade_floor/account", timeout=TIMEOUT)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "alpaca_configured" in d
        assert "account" in d
        if d.get("alpaca_configured"):
            acct = d["account"] or {}
            for k in ("cash", "equity", "buying_power"):
                assert k in acct, f"missing {k} in account: {acct}"

    def test_regime(self, client):
        r = client.get(f"{BASE_URL}/api/trade_floor/regime", timeout=TIMEOUT)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("status") in ("green", "yellow", "red"), d
        for k in ("vix", "spy_last", "spy_ema200", "halt_new_entries"):
            assert k in d, f"missing {k}"

    def test_engine_status(self, client):
        r = client.get(f"{BASE_URL}/api/trade_floor/engine/status", timeout=TIMEOUT)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("phase") in ("pre_adjustment", "signal_weight_adjustment", "full_adjustment"), d
        assert "closed_trades" in d
        assert "weights" in d
        assert isinstance(d["weights"], dict)
        assert len(d["weights"]) >= 10, f"expected >=10 inherited weights, got {len(d['weights'])}"

    def test_engine_recalibrate(self, client):
        r = client.post(f"{BASE_URL}/api/trade_floor/engine/recalibrate", timeout=TIMEOUT)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "phase" in d
        assert "changes" in d
        if d["phase"] == "pre_adjustment":
            # In pre_adjustment phase, no changes expected
            ch = d["changes"]
            if isinstance(ch, int):
                assert ch == 0
            elif isinstance(ch, (list, dict)):
                assert len(ch) == 0

    def test_positions(self, client):
        r = client.get(f"{BASE_URL}/api/trade_floor/positions", timeout=TIMEOUT)
        assert r.status_code == 200, r.text
        d = r.json()
        assert "db_positions" in d
        assert "live_alpaca" in d
        assert "last_scan_log" in d

    def test_manual_send(self, client):
        r = client.post(
            f"{BASE_URL}/api/trade_floor/manual_send",
            params={"ticker": "AAPL", "risk_dollars": 50},
            timeout=TIMEOUT,
        )
        assert r.status_code == 200, r.text
        d = r.json()
        assert "ok" in d
        if d["ok"] is False:
            assert "reason" in d
            assert d["reason"] in (
                "alpaca_not_configured",
                "no_atr_for_stop",
                "invalid_stop",
                "no_price",
            ) or isinstance(d["reason"], str)
        else:
            assert "notional" in d or "stop" in d or "order" in d


# ---------- SEC Filings ----------
class TestSEC:
    @pytest.fixture(scope="class")
    def polled(self, client):
        r = client.post(f"{BASE_URL}/api/sec/poll", timeout=120)
        assert r.status_code == 200, r.text
        return r.json()

    def test_poll_shape(self, polled):
        for k in ("inserted", "duration_sec", "activist_count"):
            assert k in polled, f"missing {k}"

    def test_filings_list(self, client, polled):
        r = client.get(f"{BASE_URL}/api/sec/filings", params={"days": 7}, timeout=TIMEOUT)
        assert r.status_code == 200, r.text
        body = r.json()
        filings = body if isinstance(body, list) else body.get("filings") or body.get("items") or []
        assert len(filings) >= 10, f"expected >=10 filings, got {len(filings)}"
        # every filing should have ticker
        missing = [f for f in filings if not f.get("ticker")]
        assert not missing, f"{len(missing)} filings have no ticker"
        # spot check schema
        sample = filings[0]
        for k in ("ticker", "form", "company", "significance"):
            assert k in sample, f"sample missing {k}: keys={list(sample.keys())}"


# ---------- Contracts public-only subs ----------
class TestContracts:
    def test_contracts_subs_have_ticker(self, client):
        r = client.get(
            f"{BASE_URL}/api/contracts",
            params={"days": 180, "min_amount": 100000000},
            timeout=TIMEOUT,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        contracts = body.get("contracts") if isinstance(body, dict) else body
        assert isinstance(contracts, list), f"unexpected shape: {type(body)}"
        bad_subs = []
        total_subs = 0
        for c in contracts:
            for s in c.get("sub_awards") or []:
                total_subs += 1
                if not s.get("ticker"):
                    bad_subs.append({"prime": c.get("ticker"), "recipient": s.get("recipient")})
        assert not bad_subs, f"non-tickered subs found ({len(bad_subs)}/{total_subs}): {bad_subs[:5]}"


# ---------- Price source ----------
class TestPriceSource:
    def test_alpaca_in_priority(self, client):
        r = client.get(f"{BASE_URL}/api/admin/price_source", timeout=TIMEOUT)
        assert r.status_code == 200, r.text
        d = r.json()
        # support various shapes
        src_list = d.get("sources") or d.get("priority") or d.get("price_sources") or []
        if not src_list and isinstance(d, list):
            src_list = d
        as_str = str(d).lower()
        assert "alpaca" in as_str, f"alpaca not present in price source response: {d}"


# ---------- Regression ----------
LEGACY_ENDPOINTS = [
    "/api/admin/pipeline_criteria",
    "/api/v32/lottery",
    "/api/v32/macro",
    "/api/v32/conviction",
    "/api/v32/dark_horse",
    "/api/v32/x_factor",
    "/api/pharma/pdufa",
    "/api/scan/latest",
    "/api/learning/preview",
    "/api/performance/summary",
]


@pytest.mark.parametrize("ep", LEGACY_ENDPOINTS)
def test_legacy_endpoints_200(client, ep):
    r = client.get(f"{BASE_URL}{ep}", timeout=TIMEOUT)
    assert r.status_code == 200, f"{ep} returned {r.status_code}: {r.text[:200]}"
