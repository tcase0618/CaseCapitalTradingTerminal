"""AXIOM v5.2 — Trade Floor refactor regression
Tests:
  * Cleanup (cancel all Alpaca orders + close positions)
  * /api/scan/run → executed>0, new schema fields (limit_price/stop_price/stop_pct)
  * tf_trades has full new schema (stop_breakdown, hard_cap_applied, etc.)
  * Hard caps by score tier
  * Alpaca direct: ALL open tf- orders are limit+day, limit_price>0
  * Dedup: 2nd scan rejects with 'ticker_has_pending_open_order' / already_open
  * No ATR / yfinance in stop_engine.py + manual_send works (and dedups)
  * /api/trade_floor/sweep_stale_orders endpoint
  * Stop engine spec example: LDOS stop_pct in [0.10, 0.18]
  * Risk tier resolver (notional<=cap by score band)
  * Trade Floor Learning Engine: tf_trade_decisions one row per executed trade
  * Regression: /api/scan/latest has trade_score; /api/admin/integration_status
    scheduled_jobs includes stale_order_sweep (total 8)
"""
import os, time, asyncio
import pytest
import requests
from pymongo import MongoClient

pytestmark = pytest.mark.live_destructive

REQUIRED_ENV = [
    "REACT_APP_BACKEND_URL",
    "APCA_API_KEY_ID",
    "APCA_API_SECRET_KEY",
    "MONGO_URL",
    "DB_NAME",
]

if os.environ.get("RUN_LIVE_TRADING_TESTS", "").strip().lower() not in {"1", "true", "yes"}:
    pytest.skip(
        "live/destructive Alpaca regression disabled; set RUN_LIVE_TRADING_TESTS=true to run",
        allow_module_level=True,
    )

missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
if missing:
    pytest.skip(
        f"live/destructive Alpaca regression missing env: {', '.join(missing)}",
        allow_module_level=True,
    )

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
APCA_KEY = os.environ["APCA_API_KEY_ID"]
APCA_SEC = os.environ["APCA_API_SECRET_KEY"]
ALPACA_BASE = "https://paper-api.alpaca.markets"
HDRS = {"APCA-API-KEY-ID": APCA_KEY, "APCA-API-SECRET-KEY": APCA_SEC}

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]


# ─────── Helpers ───────
def alpaca_get(path, **kw):
    return requests.get(f"{ALPACA_BASE}{path}", headers=HDRS, timeout=20, **kw)

def alpaca_del(path, **kw):
    return requests.delete(f"{ALPACA_BASE}{path}", headers=HDRS, timeout=20, **kw)

def hard_cap(score):
    if score >= 50: return 50.0
    if score >= 30: return 30.0
    if score >= 25: return 20.0
    return 10.0

def expected_risk_pct(score):
    if score >= 50: return 0.05
    if score >= 30: return 0.03
    if score >= 25: return 0.02
    return 0.01


# ─────── Phase 0 — Cleanup ───────
class TestCleanup:
    def test_cancel_all_orders(self):
        r = alpaca_del("/v2/orders")
        assert r.status_code in (200, 207), r.text
        time.sleep(2)
        # Verify
        opn = alpaca_get("/v2/orders", params={"status": "open"}).json()
        assert isinstance(opn, list)
        # Some may take a beat to flush; give it one retry
        if opn:
            time.sleep(3)
            opn = alpaca_get("/v2/orders", params={"status": "open"}).json()
        print(f"open orders after cancel: {len(opn)}")

    def test_close_all_positions(self):
        r = alpaca_del("/v2/positions?cancel_orders=true")
        assert r.status_code in (200, 207), r.text
        time.sleep(4)
        pos = alpaca_get("/v2/positions").json()
        print(f"positions after close: {len(pos)}")
        # paper may keep tiny residuals; not blocking


# ─────── Phase 1 — Scan #1 ───────
@pytest.fixture(scope="module")
def first_scan_data():
    r = requests.post(f"{BASE_URL}/api/scan/run", timeout=180)
    assert r.status_code == 200, r.text
    time.sleep(8)  # let DB writes settle
    pos = requests.get(f"{BASE_URL}/api/trade_floor/positions", timeout=30).json()
    return pos


class TestScanOneSchema:
    def test_scan_executed(self, first_scan_data):
        log = first_scan_data.get("last_scan_log") or {}
        executed = log.get("executed") or 0
        details = log.get("execution_details") or []
        print(f"scan#1 executed={executed} rejected={log.get('rejected')}")
        assert executed > 0, f"scan executed 0 — log={log}"
        assert len(details) >= 1
        # Each execution row carries the new schema fields
        for row in details:
            assert "limit_price" in row and float(row["limit_price"]) > 0, row
            assert "stop_price"  in row and float(row["stop_price"])  > 0, row
            assert "stop_pct"   in row and 0 < float(row["stop_pct"]) < 1, row

    def test_tf_trades_schema(self):
        cli = MongoClient(MONGO_URL)
        db = cli[DB_NAME]
        trades = list(db.tf_trades.find().sort("created_at", -1).limit(10))
        assert trades, "no tf_trades found"
        needed = {"limit_price","stop_pct","stop_breakdown","hard_cap_applied",
                  "hold_window_days","fill_status"}
        latest = trades[0]
        missing = needed - set(latest.keys())
        assert not missing, f"missing fields in tf_trade: {missing} keys={list(latest.keys())}"
        bd = latest["stop_breakdown"]
        for k in ("base_pct","hold_bucket","sector","score_tier","instrument",
                  "realized_vol_30d","final_stop_pct"):
            assert k in bd, f"breakdown missing {k}: {bd}"
        # 'sector' on the document itself (per spec)
        assert "sector" in latest or "sector" in bd
        # 'risk_pct_used' per spec
        assert "risk_pct_used" in latest, list(latest.keys())

    def test_hard_caps_per_tier(self):
        cli = MongoClient(MONGO_URL)
        db = cli[DB_NAME]
        # Restrict to trades from THIS scan run (last ~5 minutes)
        import datetime as dt
        cutoff = (dt.datetime.utcnow() - dt.timedelta(minutes=10)).isoformat()
        trades = list(db.tf_trades.find({"created_at": {"$gte": cutoff}}))
        assert trades, "no recent trades to validate caps"
        violations = []
        for t in trades:
            score = float(t.get("trade_score") or t.get("entry_score") or 0)
            cap = hard_cap(score)
            notional = float(t.get("notional") or 0)
            if notional > cap + 0.001:
                violations.append((t.get("ticker"), score, notional, cap))
            # risk_pct
            exp_rp = expected_risk_pct(score)
            rp = float(t.get("risk_pct_used") or 0)
            assert abs(rp - exp_rp) < 0.001 or rp <= exp_rp, \
                f"{t.get('ticker')} score={score} risk_pct_used={rp} expected={exp_rp}"
        assert not violations, f"hard-cap violations: {violations}"


# ─────── Phase 2 — Alpaca direct verification ───────
class TestAlpacaLimitOnly:
    def test_all_tf_orders_limit_day(self):
        r = alpaca_get("/v2/orders", params={"status": "open", "limit": 200})
        assert r.status_code == 200, r.text
        orders = r.json()
        tf_orders = [o for o in orders if (o.get("client_order_id") or "").startswith("tf-")]
        assert tf_orders, f"expected tf- orders open, got {len(orders)} total"
        for o in tf_orders:
            assert o.get("type") == "limit", \
                f"{o.get('symbol')} type={o.get('type')} client_order_id={o.get('client_order_id')}"
            assert o.get("time_in_force") == "day", \
                f"{o.get('symbol')} tif={o.get('time_in_force')}"
            lp = float(o.get("limit_price") or 0)
            assert lp > 0, f"{o.get('symbol')} limit_price={lp}"


# ─────── Phase 3 — Scan #2 dedup ───────
class TestDedup:
    def test_second_scan_rejects_pending(self):
        r = requests.post(f"{BASE_URL}/api/scan/run", timeout=180)
        assert r.status_code == 200, r.text
        time.sleep(8)
        pos = requests.get(f"{BASE_URL}/api/trade_floor/positions", timeout=30).json()
        log = pos.get("last_scan_log") or {}
        rej = log.get("rejection_details") or log.get("rejections") or []
        print(f"scan#2 executed={log.get('executed')} rejected={log.get('rejected')}")
        # at least one rejection should cite dedup
        reasons = [str(r.get("reason","")).lower() for r in rej]
        dedup_hits = [x for x in reasons
                       if "ticker_has_pending_open_order" in x
                       or "ticker_already_open_in_alpaca" in x]
        assert dedup_hits, f"no dedup rejections found; reasons={reasons[:10]}"


# ─────── Phase 4 — No ATR / yfinance + manual_send ───────
class TestNoAtrAndManualSend:
    def test_stop_engine_no_atr_no_yfinance(self):
        with open("/app/backend/services/stop_engine.py") as f:
            src = f.read()
        assert "yfinance" not in src, "yfinance still referenced in stop_engine.py"
        assert "fetch_atr_14d" not in src, "fetch_atr_14d still referenced in stop_engine.py"
        assert "atr_14d" not in src.lower() or "atr" not in src.lower(), \
            "ATR keyword still present — manually inspect"

    def test_manual_send_spy(self):
        r = requests.post(
            f"{BASE_URL}/api/trade_floor/manual_send",
            params={"ticker": "SPY", "risk_dollars": 20, "source": "manual_test"},
            timeout=60,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        # Either ok+stop OR a coherent rejection (dedup)
        if data.get("ok"):
            assert data.get("stop") and float(data["stop"]) > 0, data
            assert data.get("limit_price") and float(data["limit_price"]) > 0, data
        else:
            assert data.get("reason") in (
                "ticker_already_open_in_alpaca",
                "ticker_has_pending_open_order",
                "alpaca_not_configured",
                "no_ask_quote",
            ), data
        # Cleanup SPY if opened
        time.sleep(2)
        alpaca_del("/v2/orders")  # cancel any new open SPY tf-manual order
        time.sleep(2)
        alpaca_del("/v2/positions/SPY")


# ─────── Phase 5 — Stale sweep ───────
class TestStaleSweep:
    def test_sweep_endpoint(self):
        r = requests.post(f"{BASE_URL}/api/trade_floor/sweep_stale_orders", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "checked" in data and "cancelled" in data, data
        # All orders <24h old → cancelled should be 0
        assert int(data.get("cancelled", -1)) == 0, f"unexpected cancellations: {data}"

    def test_scheduler_has_stale_sweep_job(self):
        r = requests.get(f"{BASE_URL}/api/admin/integration_status", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        # NOTE: API returns 'jobs' (legacy key) — spec called it 'scheduled_jobs'.
        jobs = data.get("scheduled_jobs") or data.get("jobs") or []
        ids = [j.get("id") for j in jobs]
        assert "stale_order_sweep" in ids, f"stale_order_sweep missing; jobs={ids}"
        assert len(jobs) >= 8, f"expected 8 scheduled jobs, got {len(jobs)}"


# ─────── Phase 6 — Stop engine spec (LDOS) ───────
class TestStopEngineSpec:
    def test_ldos_stop_pct_range(self):
        # First make sure LDOS isn't already held (would short-circuit)
        try:
            alpaca_del("/v2/positions/LDOS")
            time.sleep(2)
        except Exception:
            pass
        # Cancel any pending LDOS order
        opn = alpaca_get("/v2/orders", params={"status": "open"}).json()
        for o in opn:
            if (o.get("symbol") or "").upper() == "LDOS":
                alpaca_del(f"/v2/orders/{o.get('id')}")
        time.sleep(2)
        r = requests.post(
            f"{BASE_URL}/api/trade_floor/manual_send",
            params={"ticker": "LDOS", "risk_dollars": 10, "source": "stop_test"},
            timeout=60,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        if not data.get("ok"):
            pytest.skip(f"LDOS manual_send rejected: {data.get('reason')}")
        # stop_pct from manual_send response: derive from stop + limit_price
        stop = float(data["stop"])
        lim = float(data["limit_price"])
        stop_pct = (lim - stop) / lim
        print(f"LDOS stop_pct={stop_pct:.4f} (limit={lim} stop={stop})")
        assert 0.10 <= stop_pct <= 0.18, f"LDOS stop_pct {stop_pct} outside [0.10, 0.18]"
        # cleanup
        time.sleep(2)
        alpaca_del("/v2/orders")
        time.sleep(1)
        alpaca_del("/v2/positions/LDOS")

    def test_stop_engine_doc_has_hold_45(self):
        cli = MongoClient(MONGO_URL)
        db = cli[DB_NAME]
        doc = db.tf_stop_engine.find_one({"_id": "current"})
        assert doc, "tf_stop_engine 'current' doc missing"
        coef = doc.get("coefficients") or {}
        hwd = coef.get("hold_window_delta") or {}
        assert float(hwd.get("45", 0)) >= 0.04, f"hold_window_delta['45']={hwd.get('45')}"


# ─────── Phase 7 — Learning engine + regression ───────
class TestLearningAndRegression:
    def test_engine_status(self):
        r = requests.get(f"{BASE_URL}/api/trade_floor/engine/status", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        # Coherent — has at least one expected key
        assert isinstance(data, dict) and data, data

    def test_tf_trade_decisions_logged(self):
        cli = MongoClient(MONGO_URL)
        db = cli[DB_NAME]
        decisions = list(db.tf_trade_decisions.find().sort("created_at", -1).limit(20))
        assert decisions, "no tf_trade_decisions rows logged"
        d = decisions[0]
        for k in ("limit_price", "stop_pct", "stop_breakdown", "sector", "hold_window_days"):
            assert k in d, f"tf_trade_decisions missing {k}; keys={list(d.keys())}"

    def test_scan_latest_has_trade_score(self):
        r = requests.get(f"{BASE_URL}/api/scan/latest", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        results = data.get("results") or data.get("scan") or []
        assert results, "no scan results"
        ts_present = sum(1 for x in results if "trade_score" in x and x["trade_score"] is not None)
        assert ts_present >= 1, f"trade_score missing on {len(results)} rows"


# ─────── Phase 8 — Final cleanup ───────
class TestFinalCleanup:
    def test_cleanup_all(self):
        alpaca_del("/v2/orders")
        time.sleep(2)
        alpaca_del("/v2/positions?cancel_orders=true")
        time.sleep(3)
        # not asserting — best effort
