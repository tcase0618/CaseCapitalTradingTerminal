import sys
import asyncio
from pathlib import Path


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


def test_research_bundle_is_research_only(monkeypatch):
    monkeypatch.setenv("FINANCIAL_MODELING_PREP_API_KEY", "research-token")

    class Response:
        status_code = 200
        text = ""

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, params=None):
            if "income-statement" in url:
                return Response([{"date": "2025-12-31", "revenue": 120, "grossProfitRatio": 0.5, "netIncomeRatio": 0.1}])
            if "balance-sheet" in url:
                return Response([{"cashAndCashEquivalents": 40, "totalDebt": 10, "currentRatio": 2.0}])
            if "cash-flow" in url:
                return Response([{"freeCashFlow": 8}])
            return Response([])

    monkeypatch.setattr(finance_toolkit_source.httpx, "AsyncClient", lambda **kwargs: Client())
    result = asyncio.run(finance_toolkit_source.research_bundle("AAPL", "income,balance,cashflow"))

    assert result["ok"] is True
    assert result["research_only"] is True
    assert result["decision_authority"] == "NONE"
    assert result["metrics"]["latest_revenue"] == 120.0
    assert result["metrics"]["cash_balance"] == 40.0
    assert result["metrics"]["latest_free_cash_flow"] == 8.0
    assert not {"buy", "sell", "route", "size", "gate"}.intersection(result)


def test_research_bundle_rejects_unknown_sections(monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "research-token")
    result = asyncio.run(finance_toolkit_source.research_bundle("AAPL", "income,orders"))

    assert result["ok"] is False
    assert result["reason"] == "invalid_sections"
    assert result["invalid_sections"] == ["orders"]


def test_profile_returns_not_configured_without_key(monkeypatch):
    for key in finance_toolkit_source.ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    result = asyncio.run(finance_toolkit_source.company_profile("AAPL"))

    assert result["ok"] is False
    assert result["configured"] is False
    assert result["reason"] == "missing_fmp_api_key"
