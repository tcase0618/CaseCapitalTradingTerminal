from services import data_quality, data_truth, execution_gate, pricer, scanner, telegram_events


def test_scan_signature_changes_when_price_changes():
    rows = [{"ticker": "LDOS", "signals": ["a", "b"], "signal_score": 9, "price": 100}]
    changed = [{"ticker": "LDOS", "signals": ["a", "b"], "signal_score": 9, "price": 101}]

    assert scanner._scan_signature(rows) != scanner._scan_signature(changed)


def test_scan_signature_is_order_stable():
    rows = [
        {"ticker": "BAH", "signals": ["x"], "signal_score": 5, "price": 10},
        {"ticker": "LDOS", "signals": ["b", "a"], "signal_score": 9, "price": 100},
    ]
    reordered = [
        {"ticker": "LDOS", "signals": ["a", "b"], "signal_score": 9, "price": 100},
        {"ticker": "BAH", "signals": ["x"], "signal_score": 5, "price": 10},
    ]

    assert scanner._scan_signature(rows) == scanner._scan_signature(reordered)


def test_same_scan_normalizes_zulu_suffix():
    assert data_truth._same_scan("2026-08-13T12:00:30Z", "2026-08-13T12:00:30+00:00")
    assert telegram_events._same_scan("2026-08-13T12:00:30Z", "2026-08-13T12:00:30+00:00")


def test_execution_gate_blocks_all_scopes_on_truth_block():
    truth = {
        "decision": "BLOCK",
        "truth_grade": "F",
        "qc": {"scoped_blockers": {}},
        "execution": {
            "equity_execution_enabled": True,
            "options_execution_enabled": True,
            "equity_paper": True,
            "options_paper": True,
        },
    }

    import asyncio

    async def run():
        equity = await execution_gate.check(scope="equity", truth=truth, record=False)
        options = await execution_gate.check(scope="options", truth=truth, record=False)
        return equity, options

    equity, options = asyncio.run(run())
    assert equity["decision"] == "BLOCK"
    assert options["decision"] == "BLOCK"
    assert "data_truth_block:F" in equity["blockers"]
    assert "data_truth_block:F" in options["blockers"]


def test_edgar_outage_is_warning_not_execution_blocker():
    row = data_quality._qc_row(
        "integration:edgar",
        "SEC EDGAR RSS",
        "DOWN",
        critical=False,
        source="edgar",
        detail="SEC Atom probe failed",
        warnings=["SEC Atom probe failed"],
        blocks_trading=False,
        execution_scopes=[],
    )

    assert row["status"] == "DOWN"
    assert row["blocks_trading"] is False
    assert row["execution_scopes"] == []


def test_data_truth_persistence_quota_error_is_explicit_blocker():
    err = RuntimeError("you are over your space quota, using 512 MB of 512 MB. Writes are blocked on your cluster")
    row = data_truth._persistence_blocker(err)

    assert row["key"] == "database_persistence_block"
    assert row["blocks_trading"] is True
    assert row["execution_scopes"] == ["system", "equity", "options"]
    assert "storage quota" in row["detail"].lower()


def test_fully_stale_scan_prices_block_new_entries():
    row = data_truth._scan_price_freshness_status(38, 0, 38)
    assert row["key"] == "scan_prices_fully_stale"
    assert row["blocks_trading"] is True
    assert row["execution_scopes"] == ["equity", "options"]


def test_partially_stale_scan_prices_warn_without_global_block():
    row = data_truth._scan_price_freshness_status(38, 37, 1)
    assert row["key"] == "scan_prices_partially_stale"
    assert row["blocks_trading"] is False


def test_fresh_scan_prices_have_no_staleness_status():
    assert data_truth._scan_price_freshness_status(38, 38, 0) is None


def test_execution_gate_uses_fresh_cached_truth_before_refresh(monkeypatch):
    cached = {
        "decision": "PASS",
        "truth_grade": "B",
        "qc": {"scoped_blockers": {}},
        "execution": {
            "equity_execution_enabled": True,
            "options_execution_enabled": True,
            "equity_paper": True,
            "options_paper": True,
        },
    }

    async def cached_truth(*args, **kwargs):
        return dict(cached)

    async def should_not_refresh(*args, **kwargs):
        raise AssertionError("fresh cached truth should short-circuit refresh")

    import asyncio

    monkeypatch.setattr(execution_gate, "_cached_truth_snapshot", cached_truth)
    monkeypatch.setattr(execution_gate, "_truth_snapshot", execution_gate._truth_snapshot)

    async def run():
        from services import data_truth
        monkeypatch.setattr(data_truth, "overview", should_not_refresh)
        return await execution_gate.check(scope="equity", record=False)

    result = asyncio.run(run())
    assert result["decision"] == "PASS"
    assert result["truth"]["gate_source"] == "fresh_cached_truth"


def test_execution_gate_force_refresh_timeout_blocks_cached_truth(monkeypatch):
    cached = {
        "decision": "PASS",
        "truth_grade": "B",
        "qc": {"scoped_blockers": {}},
        "execution": {
            "equity_execution_enabled": True,
            "options_execution_enabled": True,
            "equity_paper": True,
            "options_paper": True,
        },
    }

    async def cached_truth(*args, **kwargs):
        # Simulate the normal 300s cache missing, then the wider forced-refresh
        # fallback finding a recent valid snapshot.
        if kwargs.get("max_age_seconds") == 1800:
            return dict(cached)
        return None

    async def slow_refresh(*args, **kwargs):
        await asyncio.sleep(0.05)
        return {"decision": "BLOCK", "truth_grade": "F"}

    import asyncio

    monkeypatch.setenv("EXECUTION_GATE_TRUTH_TIMEOUT_SECONDS", "0.001")
    monkeypatch.setenv("EXECUTION_GATE_REFRESH_FALLBACK_SECONDS", "1800")
    monkeypatch.setattr(execution_gate, "_cached_truth_snapshot", cached_truth)
    monkeypatch.setattr(data_truth, "overview", slow_refresh)

    async def run():
        return await execution_gate.check(scope="options", force_refresh=True, record=False)

    result = asyncio.run(run())
    assert result["decision"] == "BLOCK"
    assert "truth_refresh_failed" in result["blockers"]
    assert result["truth"]["gate_source"] == "cached_truth_after_refresh_error"
    assert result["truth"]["refresh_error"] == "TimeoutError"


def test_scanner_alpaca_feed_order_includes_24h_feeds(monkeypatch):
    monkeypatch.setenv("ALPACA_SCANNER_FEEDS", "overnight,boats,sip,iex,")

    assert pricer._scanner_alpaca_feeds() == ["overnight", "boats", "sip", "iex", None]


def test_scanner_default_includes_delayed_sip_for_free_tier_research(monkeypatch):
    monkeypatch.delenv("ALPACA_SCANNER_FEEDS", raising=False)
    assert pricer._scanner_alpaca_feeds() == ["overnight", "boats", "delayed_sip", "sip", "iex", None]


def test_delayed_sip_is_never_execution_eligible(monkeypatch):
    monkeypatch.setenv("SCANNER_DELAYED_PRICE_MAX_AGE_SECONDS", "1200")

    async def fake_alpaca(ticker, *, feed=None):
        if feed == "delayed_sip":
            return {
                "price": 101.25,
                "source": "alpaca_latest_trade_delayed_sip",
                "provider_ts": "2026-08-24T04:01:00Z",
                "age_seconds": 300,
                "fresh": True,
                "delayed": True,
                "execution_eligible": False,
            }
        return None

    import asyncio

    monkeypatch.setenv("ALPACA_SCANNER_FEEDS", "delayed_sip")
    monkeypatch.delenv("ALPACA_STOCK_FEED", raising=False)
    monkeypatch.setattr(pricer, "_alpaca_trade_meta", fake_alpaca)
    meta = asyncio.run(pricer.live_price_meta("SPY"))
    assert meta["fresh"] is True
    assert meta["delayed"] is True
    assert meta["execution_eligible"] is False
    assert meta["warning"] == "alpaca_delayed_sip_15m"


def test_live_price_meta_prefers_fresh_alpaca_feed(monkeypatch):
    calls = []

    async def fake_alpaca(ticker, *, feed=None):
        calls.append(feed)
        if feed == "overnight":
            return None
        if feed == "boats":
            return {
                "price": 101.25,
                "source": "alpaca_latest_trade_boats",
                "provider_ts": "2026-08-24T04:01:00Z",
                "age_seconds": 12,
                "fresh": True,
            }
        raise AssertionError("fresh feed should short-circuit slower fallbacks")

    import asyncio

    monkeypatch.delenv("ALPACA_STOCK_FEED", raising=False)
    monkeypatch.setenv("ALPACA_SCANNER_FEEDS", "overnight,boats,sip,iex,")
    monkeypatch.setattr(pricer, "_alpaca_trade_meta", fake_alpaca)

    meta = asyncio.run(pricer.live_price_meta("ldos"))
    assert calls == ["overnight", "boats"]
    assert meta["ticker"] == "LDOS"
    assert meta["price"] == 101.25
    assert meta["premarket_confirmed"] is True
    assert meta["warning"] is None


def test_live_price_meta_marks_stale_alpaca_without_hiding_it(monkeypatch):
    async def fake_alpaca(ticker, *, feed=None):
        if feed == "overnight":
            return {
                "price": 99.5,
                "source": "alpaca_latest_trade_overnight",
                "provider_ts": "2026-08-21T20:00:00Z",
                "age_seconds": 100000,
                "fresh": False,
            }
        return None

    async def should_not_fallback(ticker):
        raise AssertionError("stale Alpaca evidence should be returned with warning")

    import asyncio

    monkeypatch.delenv("ALPACA_STOCK_FEED", raising=False)
    monkeypatch.setenv("ALPACA_SCANNER_FEEDS", "overnight,iex")
    monkeypatch.setattr(pricer, "_alpaca_trade_meta", fake_alpaca)
    monkeypatch.setattr(pricer, "_finnhub_quote", should_not_fallback)
    monkeypatch.setattr(pricer, "_yf_latest_close", should_not_fallback)

    meta = asyncio.run(pricer.live_price_meta("BAH"))
    assert meta["price"] == 99.5
    assert meta["fresh"] is False
    assert meta["premarket_confirmed"] is False
    assert meta["warning"] == "alpaca_trade_timestamp_stale"
