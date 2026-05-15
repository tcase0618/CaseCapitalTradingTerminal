"""V3 backend tests for Stock Intel Bot — Congress buys, Squeeze, FY, Time-target, NLQ.

Covers the 6 v3 Pass A features:
  1. CONGRESSIONAL_BUY signal + /api/congress/recent
  2. 4-dim squeeze score + /api/squeeze/{ticker} + /api/squeeze/leaderboard/top
  3. Fiscal year multiplier + /api/fy/status
  4. Time-target with future-clamp (per-result time_target block)
  5. NLQ routing via /api/telegram/webhook
  6. Updated scan schema and new telegram commands
"""
import datetime as dt
import pytest


# ---------- Congress ----------
class TestCongress:
    def test_congress_recent_shape(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/congress/recent?days=30", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, list)
        if not data:
            pytest.skip("no congress rows in curated set for last 30d")
        required = {
            "name", "chamber", "ticker", "tx_type", "amount_min", "amount_max",
            "tx_date", "committee_match", "sector", "weight_points",
        }
        for row in data[:5]:
            assert "_id" not in row
            missing = required - set(row.keys())
            assert not missing, f"missing fields {missing} in {row}"
            assert row["weight_points"] in (1, 3)
            if row["committee_match"]:
                assert row["weight_points"] == 3
            else:
                assert row["weight_points"] == 1


# ---------- Squeeze ----------
class TestSqueeze:
    def test_squeeze_ticker(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/squeeze/TSLA", timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["ticker"] == "TSLA"
        sq = d["squeeze"]
        assert "score" in sq and isinstance(sq["score"], int)
        assert 0 <= sq["score"] <= 100
        assert "band" in sq and "emoji" in sq
        comps = sq["components"]
        for k in ["short_pct", "days_to_cover", "rate_of_change_30d", "borrow_score"]:
            assert k in comps
        assert "_id" not in d

    def test_squeeze_leaderboard(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/squeeze/leaderboard/top?limit=10", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, list)
        assert len(data) <= 10
        prev = 101
        for row in data:
            assert "_id" not in row
            assert "ticker" in row
            score = row.get("score") or row.get("squeeze_score") or row.get("squeeze", {}).get("score")
            assert score is not None
            assert 0 <= score <= 100
            # Sorted descending
            assert score <= prev
            prev = score


# ---------- Fiscal year ----------
class TestFY:
    def test_fy_status(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/fy/status", timeout=15)
        assert r.status_code == 200
        d = r.json()
        for k in ["fy_multiplier_active", "days_to_fy_end", "multiplier"]:
            assert k in d
        assert isinstance(d["fy_multiplier_active"], bool)
        assert isinstance(d["days_to_fy_end"], int)
        # Multiplier on only Jul-Sep (months 7..9)
        month = dt.datetime.utcnow().month
        if month in (7, 8, 9):
            assert d["fy_multiplier_active"] is True
            assert d["multiplier"] >= 1.0
        else:
            assert d["fy_multiplier_active"] is False


# ---------- Performance ----------
class TestPerformance:
    def test_performance_summary(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/performance/summary", timeout=15)
        assert r.status_code == 200
        d = r.json()
        # v4: shape changed to {signals: [...], options: {...}} (main agent expanded it)
        assert "signals" in d and "options" in d
        assert isinstance(d["signals"], list)


# ---------- V3 scan schema ----------
class TestScanV3Schema:
    def test_latest_scan_v3_shape(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/scan/latest", timeout=30)
        assert r.status_code == 200
        scan = r.json()
        assert "_id" not in scan
        results = scan.get("results", [])
        if not results:
            pytest.skip("no latest scan")
        today = dt.date.today()
        found_congress = False
        for r0 in results:
            assert "signals" in r0
            assert "squeeze" in r0, f"{r0['ticker']} missing squeeze"
            sq = r0["squeeze"]
            assert isinstance(sq["score"], int)
            assert 0 <= sq["score"] <= 100
            assert "components" in sq
            # time_target block
            assert "time_target" in r0, f"{r0['ticker']} missing time_target"
            tt = r0["time_target"]
            for k in ["target_date", "days_remaining", "hold_period_low", "hold_period_high"]:
                assert k in tt, f"time_target missing {k}"
            # Future clamp check
            tgt = dt.date.fromisoformat(tt["target_date"][:10])
            assert tgt >= today, f"{r0['ticker']} target_date {tgt} is NOT future (today={today})"
            assert "fy_multiplier_applied" in r0
            if "CONGRESSIONAL_BUY" in r0.get("signals", []):
                found_congress = True
        # Expectation: at least one defense ticker gets CONGRESSIONAL_BUY
        defense = {"LMT", "RTX", "BWXT", "GD", "NOC"}
        any_defense = any(r0["ticker"] in defense for r0 in results)
        if any_defense:
            assert found_congress, "defense ticker in scan but CONGRESSIONAL_BUY never fired"

    def test_token_cache_efficient(self, api_client, base_url):
        r = api_client.post(f"{base_url}/api/scan/run", timeout=240)
        assert r.status_code == 200, r.text
        s = r.json()
        assert s["claude_calls_made"] == 0, (
            f"expected cached run, got claude_calls_made={s['claude_calls_made']}"
        )
        assert s["claude_cache_hits"] == s["pre_filter_passed"]


# ---------- Telegram v3 commands + NLQ ----------
class TestTelegramV3:
    @pytest.mark.parametrize("text", [
        "/squeeze TSLA",
        "/congress",
        "/performance",
        "/help",
        "/backtest",
        "/geo",
        "/premarket",
        "/add NVDA",
        "/remove NVDA",
    ])
    def test_cmd_no_crash(self, api_client, base_url, text):
        payload = {
            "update_id": abs(hash(text)) & 0xFFFFFF,
            "message": {
                "text": text,
                "chat": {"id": 8073083936},
                "from": {"id": 1, "username": "tester"},
            },
        }
        r = api_client.post(f"{base_url}/api/telegram/webhook", json=payload, timeout=30)
        assert r.status_code == 200
        assert r.json().get("ok") is True

    def test_nlq_catchall(self, api_client, base_url):
        payload = {
            "update_id": 987654,
            "message": {
                "text": "What defense stocks look strongest right now?",
                "chat": {"id": 8073083936},
                "from": {"id": 1, "username": "tester"},
            },
        }
        r = api_client.post(f"{base_url}/api/telegram/webhook", json=payload, timeout=60)
        assert r.status_code == 200
        assert r.json().get("ok") is True
        # Verify activity log captured it
        import time
        time.sleep(1)
        a = api_client.get(f"{base_url}/api/activity?limit=50", timeout=15)
        assert a.status_code == 200
        logs = a.json()
        hit = any(
            "8073083936" in (str(x.get("message", "")) + str(x.get("detail", "")) + str(x))
            for x in logs
        )
        assert hit, "activity log missing telegram chat id 8073083936"
