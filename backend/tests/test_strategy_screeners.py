from services import portfolio_manager, strategy_screeners


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
