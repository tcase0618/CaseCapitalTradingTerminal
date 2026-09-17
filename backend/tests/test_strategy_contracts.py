import asyncio

from services import strategy_contracts


def _row(**overrides):
    row = {
        "ticker": "TEST",
        "source_scan": "lottery_day2_continuation",
        "scanner_family": "LOTTERY",
        "action": "STARTER",
        "pm_score": 80,
        "price": 10.0,
        "target": 14.0,
        "target_source": "thesis_lane_proxy_pending_validation",
        "target_is_proxy": True,
    }
    row.update(overrides)
    return row


def test_proxy_target_is_explicitly_rejected_in_shadow_contract():
    payload = asyncio.run(strategy_contracts.build_shadow_contracts(
        [_row()], options_payload={"candidates": []}, cycle_id="cycle-a", observed_at="2026-09-17T14:00:00Z", persist=False,
    ))
    contract = payload["contracts"][0]
    assert contract["status"] == "SHADOW_ONLY"
    assert contract["target_assessment"]["status"] == "PROXY_REJECTED"
    assert contract["option_contract_assessment"]["status"] == "NO_OPTIONS_DESK_TICKET"
    assert contract["execution_effect"] == "none"


def test_verified_target_and_sufficient_contract_are_shadow_valid():
    ticket = {
        "ticker": "TEST",
        "manual_fire_ready": True,
        "blocked_reasons": [],
        "instrument": {
            "symbol": "TEST261217C00012000",
            "type": "call",
            "strike": 12.0,
            "expiration_date": "2026-12-17",
        },
    }
    payload = asyncio.run(strategy_contracts.build_shadow_contracts(
        [_row(target_is_proxy=False, target_source="verified_resistance", target=15.0)],
        options_payload={"candidates": [ticket]}, cycle_id="cycle-b", observed_at="2026-09-17T14:00:00Z", persist=False,
    ))
    contract = payload["contracts"][0]
    assert contract["target_assessment"]["status"] == "VERIFIED"
    assert contract["option_contract_assessment"]["status"] == "SHADOW_VALID"
    assert contract["lifecycle"]["max_hold_trading_days"] == 2
    assert contract["execution_effect"] == "none"


def test_short_expiry_never_passes_shadow_hold_window():
    ticket = {
        "ticker": "TEST",
        "instrument": {"type": "call", "strike": 12.0, "expiration_date": "2026-09-20"},
    }
    payload = asyncio.run(strategy_contracts.build_shadow_contracts(
        [_row(target_is_proxy=False, target_source="verified_resistance", target=15.0)],
        options_payload={"candidates": [ticket]}, cycle_id="cycle-c", observed_at="2026-09-17T14:00:00Z", persist=False,
    ))
    assert payload["contracts"][0]["option_contract_assessment"]["status"] == "EXPIRATION_TOO_SHORT"
