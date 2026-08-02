from services import case_court as court


def test_pm_reject_and_qc_block_outweigh_scanner_context():
    scan_row = {
        "ticker": "TEST",
        "signals": ["high_short_interest", "upcoming_earnings"],
        "price": 8.10,
        "entry_low": 7.94,
        "entry_high": 8.26,
        "risk": {"score": 3.0},
    }
    pm_row = {
        "action": "REJECT",
        "pm_score": 40.5,
        "risk_reward": 0.88,
        "price": 8.10,
        "entry_low": 7.94,
        "entry_high": 8.26,
        "downside_pct": 12,
    }
    qc = {"trading_gate": {"decision": "BLOCK", "blockers": []}}

    exhibits = [
        court._scanner_exhibit(scan_row, scan_age=2.4),
        court._pm_exhibit(pm_row),
        court._entry_exhibit(scan_row, pm_row),
        court._risk_exhibit(scan_row, pm_row),
        *court._qc_exhibits(qc, "EQUITY"),
        court._precedent_exhibit(6, scan_row),
    ]

    defense = court._brief(court.DEFENSE, exhibits)
    prosecution = court._brief(court.PROSECUTOR, exhibits)
    judge = court._judge([], defense, prosecution, pm_row, "PASS")

    assert defense["score"] == 12.0
    assert prosecution["score"] > defense["score"]
    assert judge["advisory_posture"] == "PM_REJECTED"
    assert judge["expression_hint"] == "NO_AUTHORITY"
    assert "advisory_alignment_ok" in judge
    assert "live_run_ready" not in judge


def test_pm_only_synthetic_scan_row_is_missing_required_scanner_evidence():
    scan_row = {
        "ticker": "TEST",
        "signals": ["high_short_interest", "upcoming_earnings"],
        "synthetic_from_pm": True,
    }

    exhibit = court._scanner_exhibit(scan_row, scan_age=2.4)

    assert exhibit["status"] == court.MISSING_REQUIRED
    assert exhibit["side"] == court.PROSECUTOR
    assert exhibit["required"] is True
    assert "cannot borrow scanner freshness" in exhibit["detail"]
