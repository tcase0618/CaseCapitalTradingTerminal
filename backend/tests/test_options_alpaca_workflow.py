from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from services import options_desk, options_engine  # noqa: E402


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


def test_options_headers_use_options_credentials_only(monkeypatch):
    monkeypatch.setenv("APCA_API_KEY_ID", "EQUITY_KEY")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "EQUITY_SECRET")
    monkeypatch.setenv("OPTIONS_APCA_API_KEY_ID", "OPTIONS_KEY")
    monkeypatch.setenv("OPTIONS_APCA_API_SECRET_KEY", "OPTIONS_SECRET")

    headers = options_desk._options_headers()
    route = options_desk.options_account_route_guard()

    assert headers["APCA-API-KEY-ID"] == "OPTIONS_KEY"
    assert headers["APCA-API-SECRET-KEY"] == "OPTIONS_SECRET"
    assert route["ok"] is True
    assert route["options_key_id"] != route["equity_key_id"]


def test_options_desk_does_not_fallback_to_equity_credentials(monkeypatch):
    monkeypatch.setenv("APCA_API_KEY_ID", "EQUITY_KEY")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "EQUITY_SECRET")
    monkeypatch.delenv("OPTIONS_APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("OPTIONS_APCA_API_SECRET_KEY", raising=False)

    route = options_desk.options_account_route_guard()

    assert options_desk.configured() is False
    assert route["ok"] is False
    assert route["reason"] == "missing_options_alpaca_keys"


def test_options_route_guard_blocks_equity_key_reuse(monkeypatch):
    monkeypatch.setenv("APCA_API_KEY_ID", "SAME_KEY")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "EQUITY_SECRET")
    monkeypatch.setenv("OPTIONS_APCA_API_KEY_ID", "SAME_KEY")
    monkeypatch.setenv("OPTIONS_APCA_API_SECRET_KEY", "OPTIONS_SECRET")

    route = options_desk.options_account_route_guard()

    assert route["ok"] is False
    assert route["reason"] == "options_credentials_match_equity_account"


def test_options_engine_data_does_not_use_equity_credentials(monkeypatch):
    monkeypatch.setenv("APCA_API_KEY_ID", "EQUITY_KEY")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "EQUITY_SECRET")
    monkeypatch.delenv("OPTIONS_APCA_API_KEY_ID", raising=False)
    monkeypatch.delenv("OPTIONS_APCA_API_SECRET_KEY", raising=False)

    assert options_engine._alpaca_options_configured() is False
    assert options_engine._alpaca_headers()["APCA-API-KEY-ID"] == ""
