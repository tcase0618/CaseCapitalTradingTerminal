from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from services import options_desk  # noqa: E402


def test_occ_symbol_maps_to_ibkr_option_params():
    params = options_desk._ibkr_params_from_occ_symbol("SPY260918C00650000")

    assert params == {
        "symbol": "SPY",
        "expiry": "20260918",
        "strike": 650.0,
        "right": "C",
        "exchange": "SMART",
        "trading_class": "SPY",
    }


def test_alpaca_order_preview_never_targets_ibkr():
    ticket = {
        "candidate_id": "opt-SPY-2026-08-12T12:00:00",
        "contracts": 2,
        "instrument": {
            "symbol": "SPY260918C00650000",
            "ask": 3.25,
        },
    }

    preview = options_desk._alpaca_order_preview_from_ticket(ticket)

    assert preview["broker"] == "ALPACA_OPTIONS"
    assert preview["submit_endpoint"] == "/api/options_desk/execute"
    assert preview["not_submitted"] is True
    assert preview["execution_authority"] == "alpaca_options_only"
    assert preview["payload"]["symbol"] == "SPY260918C00650000"
    assert preview["payload"]["qty"] == "2"
    assert preview["payload"]["side"] == "buy"
    assert preview["payload"]["type"] == "limit"
    assert preview["payload"]["time_in_force"] == "day"
    assert preview["payload"]["limit_price"] == 3.25
