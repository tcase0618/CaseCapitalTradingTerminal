from __future__ import annotations

import importlib
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from services import ibkr_research  # noqa: E402


def _reload(monkeypatch, **env):
    defaults = {
        "IBKR_ENABLED": "true",
        "IBKR_MODE": "live",
        "IBKR_DATA_ONLY": "true",
        "IBKR_HOST": "127.0.0.1",
        "IBKR_PORT": "7496",
        "IBKR_CLIENT_ID": "1",
        "IBKR_ALLOW_TRADING": "false",
    }
    defaults.update(env)
    for key, value in defaults.items():
        monkeypatch.setenv(key, value)
    return importlib.reload(ibkr_research)


@pytest.mark.parametrize("method", ["place_order", "modify_order", "cancel_order", "transmit_order", "stage_order"])
def test_ibkr_order_mutations_blocked_when_data_only(monkeypatch, method):
    svc = _reload(monkeypatch, IBKR_DATA_ONLY="true", IBKR_ALLOW_TRADING="false")

    with pytest.raises(svc.IbkrTradingBlocked):
        getattr(svc, method)({"symbol": "AAPL"})


def test_ibkr_allow_trading_false_blocks_even_if_data_only_misconfigured(monkeypatch):
    svc = _reload(monkeypatch, IBKR_DATA_ONLY="false", IBKR_ALLOW_TRADING="false")

    with pytest.raises(svc.IbkrTradingBlocked):
        svc.place_order({"symbol": "AAPL"})


def test_ibkr_data_only_blocks_even_if_allow_trading_misconfigured(monkeypatch):
    svc = _reload(monkeypatch, IBKR_DATA_ONLY="true", IBKR_ALLOW_TRADING="true")

    with pytest.raises(svc.IbkrTradingBlocked):
        svc.cancel_order({"order_id": 123})


def test_ibkr_live_mode_never_implies_trading_enabled(monkeypatch):
    svc = _reload(monkeypatch, IBKR_MODE="live", IBKR_DATA_ONLY="true", IBKR_ALLOW_TRADING="false")
    cfg = svc.config()
    state = svc.safety_state()

    assert cfg.mode == "live"
    assert cfg.allow_trading is False
    assert state["order_mutation_policy"] == "blocked_before_gateway"
    assert state["account_data_policy"] == "blocked_use_alpaca_for_account_truth"


@pytest.mark.parametrize("method", ["account", "positions", "open_orders", "executions"])
def test_ibkr_account_and_broker_truth_reads_are_blocked(monkeypatch, method):
    svc = _reload(monkeypatch, IBKR_DATA_ONLY="true", IBKR_ALLOW_TRADING="false")

    with pytest.raises(svc.IbkrAccountDataBlocked):
        getattr(svc, method)()


def test_ibkr_status_fails_closed_when_data_only_false(monkeypatch):
    svc = _reload(monkeypatch, IBKR_DATA_ONLY="false", IBKR_ALLOW_TRADING="false")

    status = svc.status()

    assert status["ok"] is False
    assert "IBKR_DATA_ONLY" in status["reason"]


def test_ibkr_option_contract_normalizes_inputs(monkeypatch):
    svc = _reload(monkeypatch, IBKR_DATA_ONLY="true", IBKR_ALLOW_TRADING="false")

    contract = svc._make_option_contract("spy", "2026-09-18", "650", "call")

    assert contract.symbol == "SPY"
    assert contract.secType == "OPT"
    assert contract.lastTradeDateOrContractMonth == "20260918"
    assert contract.strike == 650.0
    assert contract.right == "C"
    assert contract.exchange == "SMART"
    assert contract.currency == "USD"
    assert contract.multiplier == "100"


@pytest.mark.parametrize("expiry", ["202609", "2026/09/18", ""])
def test_ibkr_option_contract_rejects_bad_expiry(monkeypatch, expiry):
    svc = _reload(monkeypatch, IBKR_DATA_ONLY="true", IBKR_ALLOW_TRADING="false")

    with pytest.raises(ValueError):
        svc._make_option_contract("SPY", expiry, 650, "C")


@pytest.mark.parametrize("right", ["", "LONG", "X"])
def test_ibkr_option_contract_rejects_bad_right(monkeypatch, right):
    svc = _reload(monkeypatch, IBKR_DATA_ONLY="true", IBKR_ALLOW_TRADING="false")

    with pytest.raises(ValueError):
        svc._make_option_contract("SPY", "20260918", 650, right)
