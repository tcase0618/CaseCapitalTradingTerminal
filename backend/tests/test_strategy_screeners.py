from services import portfolio_manager, strategy_ideology, strategy_screeners


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
