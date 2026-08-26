import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from services import finance_toolkit_source


def test_status_masks_configured_key(monkeypatch):
    key = "abcd1234SECRET5678"
    monkeypatch.setenv("FINANCIAL_MODELING_PREP_API_KEY", key)
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    monkeypatch.delenv("FINANCETOOLKIT_API_KEY", raising=False)

    status = finance_toolkit_source.status()

    assert status["ok"] is True
    assert status["configured"] is True
    assert status["env_key"] == "FINANCIAL_MODELING_PREP_API_KEY"
    assert status["key_state"] == "abcd...5678"
    assert key not in str(status)
    assert status["wired_to_execution"] is False


def test_status_accepts_legacy_fmp_key(monkeypatch):
    monkeypatch.delenv("FINANCIAL_MODELING_PREP_API_KEY", raising=False)
    monkeypatch.setenv("FMP_API_KEY", "legacy-token")
    monkeypatch.delenv("FINANCETOOLKIT_API_KEY", raising=False)

    status = finance_toolkit_source.status()

    assert status["configured"] is True
    assert status["env_key"] == "FMP_API_KEY"


@pytest.mark.asyncio
async def test_profile_returns_not_configured_without_key(monkeypatch):
    for key in finance_toolkit_source.ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    result = await finance_toolkit_source.company_profile("AAPL")

    assert result["ok"] is False
    assert result["configured"] is False
    assert result["reason"] == "missing_fmp_api_key"
