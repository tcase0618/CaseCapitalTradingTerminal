from services import telegram_events


def test_scan_report_uses_portfolio_manager_recommendations_shape():
    pm_payload = {
        "recommendations": [
            {"ticker": "LDOS", "action": "STARTER", "pm_score": 80.4},
            {"ticker": "BAH", "action": "STARTER", "pm_score": 70.1},
            {"ticker": "SAIC", "action": "WATCH", "pm_score": 67.4},
            {"ticker": "XYZ", "action": "REJECT", "pm_score": 30.0},
        ]
    }

    rows = telegram_events._pm_rows(pm_payload)
    routes = telegram_events._expression_counts(rows, [])
    actions = telegram_events._pm_action_counts(rows)

    assert len(rows) == 4
    assert routes["EQUITY"] == 2
    assert routes["WATCH"] == 1
    assert routes["PASS"] == 1
    assert actions["STARTER"] == 2
    assert actions["WATCH"] == 1
    assert actions["REJECT"] == 1
