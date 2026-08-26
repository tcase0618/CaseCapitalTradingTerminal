import asyncio

from services import options_desk, pharma, portfolio_manager, pricer, scrapers, strategy_ideology, strategy_screeners, trade_floor, x_factor
from services import lottery


def test_sec_bearish_filing_is_read_only_and_not_pm_routable():
    row = {
        "ticker": "ABC",
        "form": "424B5",
        "title": "ABC files prospectus supplement for offering",
        "significance": 80,
        "explanation": {"bias": "BEARISH"},
    }

    built = strategy_screeners._base_row(
        row=row,
        screener_id="sec_filings",
        family="SEC",
        lane=f"{strategy_screeners._sec_bias(row)}_FILING",
        score=80,
        pm_routable=False,
        read_only=True,
        notes=["SEC bearish is read-only and cannot veto or block PM routing."],
    )

    assert built["read_only"] is True
    assert built["pm_routable"] is False
    assert built["strategy_scanner"]["read_only"] is True
    assert built["strategy_scanner"]["pm_routable"] is False
    assert built["strategy_scanner"]["lane"] == "BEARISH_FILING"


def test_sec_all_biases_are_research_only_contract():
    row = {
        "ticker": "XYZ",
        "form": "SC 13D",
        "title": "Activist discloses stake",
        "significance": 95,
        "explanation": {"bias": "BULLISH"},
    }

    built = strategy_screeners._base_row(
        row=row,
        screener_id="sec_filings",
        family="SEC",
        lane=f"{strategy_screeners._sec_bias(row)}_FILING",
        score=95,
        pm_routable=False,
        read_only=True,
        notes=["SEC scanner is research-only and tracked outside PM routing."],
    )

    assert built["strategy_scanner"]["lane"] == "BULLISH_FILING"
    assert built["pm_routable"] is False
    assert built["read_only"] is True


def test_strategy_row_carries_case_score_and_confidence():
    row = strategy_screeners._base_row(
        row={
            "ticker": "RUNR",
            "price": 3.25,
            "signals": ["RVOL", "ROTATION"],
            "triggers": ["RVOL", "ROTATION"],
            "components": {"rvol": 12, "rotation": 11, "structure": 7},
        },
        screener_id="lottery_supernova",
        family="LOTTERY",
        lane="SUPERNOVA",
        score=82,
    )

    assert row["strategy_case"]["strategy_id"] == "lottery_supernova"
    assert row["strategy_case"]["case_score"] > 70
    assert 0.15 <= row["strategy_case"]["confidence"] <= 0.78
    assert row["strategy_case"]["risk_shape"] == "very_high_variance_fat_tail"
    assert row["case_score"] == row["strategy_scanner"]["case_score"]
    assert row["strategy_confidence"] == row["strategy_scanner"]["confidence"]


def test_strategy_ideology_unknown_has_safe_defaults():
    case = strategy_ideology.case_score(
        strategy_id="missing_strategy",
        native_score=55,
        row={"ticker": "ABC", "price": 10},
        family="UNKNOWN",
        lane="GENERIC",
    )

    assert case["strategy_id"] == "missing_strategy"
    assert case["preferred_expression"] == "pm_decides"
    assert case["confidence"] <= 0.65


def test_summary_separates_pm_and_read_only_families():
    rows = [
        strategy_screeners._base_row(
            row={"ticker": "AAA", "price": 10},
            screener_id="lottery_day2_continuation",
            family="LOTTERY",
            lane="DAY2_CONTINUATION",
            score=65,
        ),
        strategy_screeners._base_row(
            row={"ticker": "BBB", "price": 20},
            screener_id="earnings_calendar",
            family="EARNINGS",
            lane="EARNINGS",
            score=52,
            pm_routable=False,
            read_only=True,
        ),
    ]

    summary = strategy_screeners._summary(rows)

    assert summary["pm_routable"] == 1
    assert summary["read_only"] == 1
    assert summary["by_pm_family"] == {"LOTTERY": 1}
    assert summary["by_read_only_family"] == {"EARNINGS": 1}


def test_pm_merge_preserves_core_and_adds_strategy_signals():
    core = [{
        "ticker": "LDOS",
        "price": 100,
        "signal_score": 4,
        "trade_score": 20,
        "signals": ["CORE_SIGNAL"],
        "targets": {"target_blended": 120},
        "risk": {"score": 30, "stop_loss": 92},
    }]
    strategy = [strategy_screeners._base_row(
        row={"ticker": "LDOS", "price": 101, "signals": ["LOTTERY_SIGNAL"]},
        screener_id="lottery_supernova",
        family="LOTTERY",
        lane="SUPERNOVA",
        score=70,
    )]

    merged = portfolio_manager._merge_strategy_rows(core, strategy)

    assert len(merged) == 1
    assert merged[0]["ticker"] == "LDOS"
    assert merged[0]["price"] == 100
    assert "CORE_SIGNAL" in merged[0]["signals"]
    assert "LOTTERY_SIGNAL" in merged[0]["signals"]
    assert "lottery_supernova" in merged[0]["scanner_sources"]
    assert merged[0]["case_score"] == strategy[0]["case_score"]
    assert merged[0]["strategy_confidence"] == strategy[0]["strategy_confidence"]


def test_trade_floor_execution_input_includes_pm_routable_strategy_rows(monkeypatch):
    core = [{
        "ticker": "CORE",
        "price": 100,
        "signal_score": 4,
        "trade_score": 20,
        "signals": ["CORE_SIGNAL"],
        "targets": {"target_blended": 120},
        "risk": {"score": 30, "stop_loss": 92},
    }]
    strategy = [strategy_screeners._base_row(
        row={"ticker": "LOTTO", "price": 5, "signals": ["LOTTERY_SIGNAL"]},
        screener_id="lottery_day2_continuation",
        family="LOTTERY",
        lane="DAY2_CONTINUATION",
        score=75,
    )]

    async def fake_pm_rows(scan=None, persist=True):
        return {"rows": strategy, "summary": {"pm_routable": 1}}

    async def fake_log_activity(*args, **kwargs):
        return None

    monkeypatch.setattr(strategy_screeners, "pm_rows", fake_pm_rows)
    monkeypatch.setattr(trade_floor, "log_activity", fake_log_activity)

    merged = asyncio.run(trade_floor._merge_pm_routable_strategy_rows(core))

    assert {row["ticker"] for row in merged} == {"CORE", "LOTTO"}
    assert any(row.get("strategy_scanner", {}).get("screener_id") == "lottery_day2_continuation" for row in merged)


def test_dedicated_lottery_scan_does_not_merge_latest_core_scan(monkeypatch):
    async def fake_halted():
        return set()

    async def fake_finviz():
        return [{"ticker": "LTRY", "price": 4, "change_pct": 18, "volume": 2_500_000, "relative_volume": 7}]

    async def fake_latest_scan_rows():
        raise AssertionError("dedicated lottery scanner must not read latest core scan rows")

    async def fake_dilution(ticker):
        return {"active": False, "label": "CLEAR", "forms": []}

    async def fake_regime():
        return {"status": "green"}

    class Collection:
        async def insert_one(self, doc):
            return None

        async def update_one(self, *args, **kwargs):
            return None

    class DB:
        ll_scans = Collection()

    monkeypatch.setattr(lottery, "get_db", lambda: DB())
    async def fake_log_activity(*args, **kwargs):
        return None

    monkeypatch.setattr(lottery, "log_activity", fake_log_activity)
    monkeypatch.setattr(lottery, "_halted_symbols", fake_halted)
    monkeypatch.setattr(lottery, "_finviz_candidates", fake_finviz)
    monkeypatch.setattr(lottery, "_latest_scan_rows", fake_latest_scan_rows)
    monkeypatch.setattr(lottery, "_dilution_flag", fake_dilution)
    monkeypatch.setattr(lottery, "_latest_regime", fake_regime)

    payload = asyncio.run(lottery.run_dedicated_lottery_scan(triggered_by="test"))

    assert payload["count"] == 1
    assert payload["scan"]["source_counts"]["finviz_low_float_screen"] == 1
    assert payload["scan"]["source_counts"]["latest_scan"] == 0


def test_independent_options_rows_do_not_use_core_scan(monkeypatch):
    async def fake_batch_live_price_meta(tickers, concurrency=8):
        return {t: {"price": 10.0, "age_seconds": 1, "source": "test_live"} for t in tickers}

    async def fake_high_short(min_pct=10.0, limit=35):
        return [{"ticker": "OPTA", "short_float_pct": 22, "source": "FINVIZ_HIGH_SHORT"}]

    async def fake_trending():
        return {"OPTB"}

    async def fake_pdufa(days=90):
        return [{"ticker": "OPTC", "binary_event_score": 71, "event_type": "PDUFA"}]

    monkeypatch.setattr(scrapers, "fetch_finviz_high_short_interest", fake_high_short)
    monkeypatch.setattr(x_factor, "yahoo_trending_set", fake_trending)
    monkeypatch.setattr(pharma, "get_pdufa_within_days", fake_pdufa)
    monkeypatch.setattr(pricer, "batch_live_price_meta", fake_batch_live_price_meta)

    rows = asyncio.run(strategy_screeners._independent_options_rows())
    tickers = {row["ticker"] for row in rows}

    assert {"OPTA", "OPTB", "OPTC"} <= tickers
    assert all(row.get("source_scan", "").startswith("options_") for row in rows)
    assert all((row.get("raw_source") or {}).get("source") != "latest_terminal_scan" for row in rows)


def test_options_desk_build_candidates_uses_strategy_rows_not_core_scan(monkeypatch):
    strategy_row = strategy_screeners._base_row(
        row={
            "ticker": "OPTX",
            "price": 10,
            "signals": ["OPTION_MOMENTUM"],
            "options": {"strategy": "LONG_CALL_SCOUT", "iv_rank": 40},
        },
        screener_id="options_tactical_momentum_call",
        family="OPTIONS",
        lane="TACTICAL_MOMENTUM_CALL",
        score=72,
    )

    async def fake_pm_rows(scan=None, persist=True):
        return {"generated_at": "2026-08-25T20:00:00", "rows": [strategy_row], "summary": {"pm_rows": 1}}

    def fake_evaluate_rows(rows, equity=None, mode=None):
        assert [row["ticker"] for row in rows] == ["OPTX"]
        return [{
            "ticker": "OPTX",
            "action": "STARTER",
            "pm_score": 72,
            "risk_reward": 1.2,
            "option_view": "OPTION_OK",
        }]

    class Collection:
        async def find_one(self, *args, **kwargs):
            return None

        async def delete_many(self, *args, **kwargs):
            return None

        async def insert_many(self, *args, **kwargs):
            return None

    class ScanResults(Collection):
        async def find_one(self, *args, **kwargs):
            return {"finished_at": "2026-08-25T19:59:00", "results": [{"ticker": "CORE"}]}

    class DB:
        scan_results = ScanResults()
        options_lane_throttle = Collection()
        options_desk_candidates = Collection()
        options_desk_candidate_history = Collection()

    monkeypatch.setattr(options_desk, "get_db", lambda: DB())
    monkeypatch.setattr(strategy_screeners, "pm_rows", fake_pm_rows)
    monkeypatch.setattr(portfolio_manager, "evaluate_rows", fake_evaluate_rows)

    payload = asyncio.run(options_desk.build_candidates(limit=25, persist=True))

    assert [row["ticker"] for row in payload["candidates"]] == ["OPTX"]
    assert all(row["ticker"] != "CORE" for row in payload["candidates"])


def test_opportunity_cost_flags_weak_holding_for_replacement():
    recommendations = [
        {
            "ticker": "WEAK",
            "action": "WATCH",
            "pm_score": 42,
            "allocation_usd": 0,
            "case_score": 0,
            "strategy_confidence": 0,
        },
        {
            "ticker": "HOT",
            "action": "ACCUMULATE",
            "pm_score": 82,
            "allocation_usd": 100,
            "case_score": 88,
            "strategy_confidence": 0.72,
        },
    ]
    positions = [{"symbol": "WEAK", "unrealized_plpc": "-0.08", "market_value": "250"}]

    review = portfolio_manager._opportunity_cost_review(recommendations, positions, equity=1000)

    assert review["positions_reviewed"] == 1
    assert review["trim_reviews"][0]["ticker"] == "WEAK"
    assert review["replacement_candidates"][0]["sell_review"] == "WEAK"
    assert review["replacement_candidates"][0]["buy_candidate"] == "HOT"
