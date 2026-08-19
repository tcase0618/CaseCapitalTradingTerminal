from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from services import ibkr_terminal  # noqa: E402


@pytest.mark.asyncio
async def test_ibkr_applications_are_data_only(monkeypatch):
    def _status():
        return {
            "ok": True,
            "connected": True,
            "config": {
                "enabled": True,
                "mode": "live",
                "data_only": True,
                "allow_trading": False,
                "order_mutation_policy": "blocked_before_gateway",
            },
        }

    monkeypatch.setattr("services.ibkr_research.status", _status)
    result = await ibkr_terminal.applications()

    assert result["ok"] is True
    assert result["policy"]["data_only"] is True
    assert result["policy"]["allow_trading"] is False
    assert result["policy"]["account_source"] == "alpaca_only"
    assert result["policy"]["execution_source"] == "alpaca_only"
    assert len(result["applications"]) == 10
    assert any(app["key"] == "options_desk_validation" for app in result["applications"])
    assert any(app["key"] == "macro_futures_later" and app["status"] == "planned" for app in result["applications"])
