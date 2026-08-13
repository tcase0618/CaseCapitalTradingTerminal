from services import data_truth, execution_gate, scanner, telegram_events


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
