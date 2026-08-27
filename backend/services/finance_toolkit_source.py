"""Financial Modeling Prep / FinanceToolkit-compatible research data adapter."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import httpx

ENV_KEYS = (
    "FINANCIAL_MODELING_PREP_API_KEY",
    "FMP_API_KEY",
    "FINANCETOOLKIT_API_KEY",
)
DEFAULT_BASE_URL = "https://financialmodelingprep.com"
DEFAULT_SECTIONS = ("profile", "income", "balance", "cashflow", "ratios", "metrics")
ALLOWED_SECTIONS = frozenset(DEFAULT_SECTIONS + ("estimates", "earnings"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _api_key() -> str:
    for key in ENV_KEYS:
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return ""


def _configured_env_key() -> str | None:
    for key in ENV_KEYS:
        if os.environ.get(key, "").strip():
            return key
    return None


def _mask(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return "set"
    return f"{value[:4]}...{value[-4:]}"


def _redact(text: str) -> str:
    api_key = _api_key()
    if api_key:
        text = text.replace(api_key, "***")
    return text


def base_url() -> str:
    return os.environ.get("FMP_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def status() -> dict[str, Any]:
    api_key = _api_key()
    return {
        "ok": bool(api_key),
        "configured": bool(api_key),
        "provider": "Financial Modeling Prep",
        "adapter": "FinanceToolkit/FMP research adapter",
        "env_key": _configured_env_key(),
        "key_state": _mask(api_key),
        "base_url": base_url(),
        "enabled": os.environ.get("FINANCE_TOOLKIT_RESEARCH_ENABLED", "true").lower() not in {"0", "false", "no"},
        "data_role": "research_data_only",
        "wired_to_pm": False,
        "wired_to_execution": False,
        "generated_at": _now(),
        "coverage": [
            "company profiles",
            "financial statements",
            "financial ratios",
            "key metrics",
            "analyst estimates",
            "earnings calendar and surprises",
            "SEC filing metadata",
            "insider trading",
            "institutional ownership",
            "shares float",
            "enterprise value",
            "DCF and valuation fields",
            "sector/industry peers",
        ],
    }


async def company_profile(ticker: str) -> dict[str, Any]:
    symbol = "".join(ch for ch in ticker.upper().strip() if ch.isalnum() or ch in {".", "-"})
    api_key = _api_key()
    out: dict[str, Any] = {
        "ok": False,
        "configured": bool(api_key),
        "provider": "Financial Modeling Prep",
        "adapter": "FinanceToolkit/FMP research adapter",
        "symbol": symbol,
        "generated_at": _now(),
        "data": None,
        "reason": None,
    }
    if not symbol:
        out["reason"] = "missing_symbol"
        return out
    if not api_key:
        out["reason"] = "missing_fmp_api_key"
        return out

    root = base_url()
    profile_requests = [
        (f"{root}/stable/profile", {"symbol": symbol, "apikey": api_key}, "stable_profile"),
        (f"{root}/api/v3/profile/{symbol}", {"apikey": api_key}, "legacy_v3_profile"),
    ]
    last_detail = None
    try:
        async with httpx.AsyncClient(timeout=float(os.environ.get("FMP_TIMEOUT_SECONDS", "8"))) as client:
            for url, params, endpoint in profile_requests:
                response = await client.get(url, params=params)
                out["status_code"] = response.status_code
                out["endpoint"] = endpoint
                if response.status_code != 200:
                    last_detail = _redact((response.text or "")[:220])
                    continue
                payload = response.json()
                row = payload[0] if isinstance(payload, list) and payload else payload if isinstance(payload, dict) else None
                if not row:
                    out["reason"] = "empty_profile"
                    continue
                out["ok"] = True
                out["data"] = row
                return out
        out["reason"] = f"http_{out.get('status_code')}"
        out["detail"] = last_detail
        return out
    except Exception as exc:
        out["reason"] = _redact(str(exc)[:220])
        return out


def _symbol(ticker: str) -> str:
    return "".join(ch for ch in ticker.upper().strip() if ch.isalnum() or ch in {".", "-"})


def _latest(rows: Any) -> dict[str, Any] | None:
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        return rows[0]
    if isinstance(rows, dict):
        return rows
    return None


def _number(row: dict[str, Any] | None, *keys: str) -> float | None:
    if not row:
        return None
    for key in keys:
        value = row.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def _cagr(first: float | None, last: float | None, periods: int) -> float | None:
    if first is None or last is None or periods <= 0 or first <= 0 or last <= 0:
        return None
    return (last / first) ** (1 / periods) - 1


def _research_metrics(data: dict[str, Any]) -> dict[str, Any]:
    income = data.get("income") if isinstance(data.get("income"), list) else []
    latest_income = _latest(income)
    latest_balance = _latest(data.get("balance"))
    latest_cashflow = _latest(data.get("cashflow"))
    latest_ratios = _latest(data.get("ratios"))
    latest_metrics = _latest(data.get("metrics"))
    revenue_values = [
        row.get("revenue") for row in reversed(income)
        if isinstance(row, dict) and isinstance(row.get("revenue"), (int, float)) and row.get("revenue") > 0
    ]
    revenue_cagr = _cagr(revenue_values[0], revenue_values[-1], len(revenue_values) - 1) if len(revenue_values) > 1 else None
    cash = _number(latest_balance, "cashAndCashEquivalents", "cashAndShortTermInvestments", "cashAndShortTermInvestmentsTotal")
    fcf = _number(latest_cashflow, "freeCashFlow", "freeCashFlowPerShare")
    debt = _number(latest_balance, "totalDebt", "netDebt")
    revenue = _number(latest_income, "revenue", "salesRevenue")
    gross_profit = _number(latest_income, "grossProfit")
    operating_income = _number(latest_income, "operatingIncome")
    net_income = _number(latest_income, "netIncome")
    operating_cash_flow = _number(latest_cashflow, "operatingCashFlow", "netCashProvidedByOperatingActivities")
    capital_expenditure = _number(latest_cashflow, "capitalExpenditure", "capitalExpenditures")
    free_cash_flow = fcf if fcf is not None else (
        operating_cash_flow - abs(capital_expenditure)
        if operating_cash_flow is not None and capital_expenditure is not None else None
    )
    return {
        "revenue_cagr": revenue_cagr,
        "latest_revenue": revenue,
        "latest_gross_margin": _number(latest_income, "grossProfitRatio", "grossMargin") or (gross_profit / revenue if gross_profit is not None and revenue else None),
        "latest_operating_margin": _number(latest_income, "operatingIncomeRatio", "operatingMargin") or (operating_income / revenue if operating_income is not None and revenue else None),
        "latest_net_margin": _number(latest_income, "netIncomeRatio", "netMargin") or (net_income / revenue if net_income is not None and revenue else None),
        "latest_free_cash_flow": free_cash_flow,
        "cash_balance": cash,
        "total_debt": debt,
        "cash_runway_proxy_years": (cash / abs(fcf)) if cash is not None and fcf is not None and fcf < 0 else None,
        "current_ratio": _number(latest_ratios, "currentRatio"),
        "return_on_equity": _number(latest_ratios, "returnOnEquity", "roe"),
        "return_on_invested_capital": _number(latest_metrics, "roic", "returnOnInvestedCapital"),
        "statement_period": (latest_income or {}).get("date"),
    }


async def _fetch_section(client: httpx.AsyncClient, symbol: str, section: str, api_key: str) -> tuple[Any, dict[str, Any]]:
    paths = {
        "profile": ("profile", {"symbol": symbol}),
        "income": ("income-statement", {"symbol": symbol, "period": "annual", "limit": 5}),
        "balance": ("balance-sheet-statement", {"symbol": symbol, "period": "annual", "limit": 5}),
        "cashflow": ("cash-flow-statement", {"symbol": symbol, "period": "annual", "limit": 5}),
        "ratios": ("ratios", {"symbol": symbol, "period": "annual", "limit": 5}),
        "metrics": ("key-metrics", {"symbol": symbol, "period": "annual", "limit": 5}),
        "estimates": ("analyst-estimates", {"symbol": symbol, "period": "annual", "limit": 5}),
        "earnings": ("earnings-surprises", {"symbol": symbol, "limit": 8}),
    }
    path, params = paths[section]
    params["apikey"] = api_key
    # FMP's current account surface is the stable API. Keep the provider path
    # isolated here so a provider migration cannot touch any trading code.
    response = await client.get(f"{base_url()}/stable/{path}", params=params)
    if response.status_code != 200:
        return None, {"ok": False, "status_code": response.status_code, "detail": _redact((response.text or "")[:180])}
    try:
        return response.json(), {"ok": True, "status_code": response.status_code}
    except ValueError:
        return None, {"ok": False, "status_code": response.status_code, "detail": "invalid_json"}


async def research_bundle(ticker: str, sections: str | None = None) -> dict[str, Any]:
    """Fetch cached-quality fundamental research; this function has no trading inputs or outputs."""
    symbol = _symbol(ticker)
    selected = tuple(dict.fromkeys(s.strip().lower() for s in (sections or ",".join(DEFAULT_SECTIONS)).split(",") if s.strip()))
    out: dict[str, Any] = {
        "ok": False,
        "configured": bool(_api_key()),
        "research_only": True,
        "decision_authority": "NONE",
        "provider": "Financial Modeling Prep",
        "adapter": "FinanceToolkit/FMP research adapter",
        "symbol": symbol,
        "sections_requested": list(selected),
        "data": {},
        "metrics": {},
        "section_status": {},
        "generated_at": _now(),
    }
    if not symbol:
        out["reason"] = "missing_symbol"
        return out
    if not _api_key():
        out["reason"] = "missing_fmp_api_key"
        return out
    invalid = [section for section in selected if section not in ALLOWED_SECTIONS]
    if invalid:
        out["reason"] = "invalid_sections"
        out["invalid_sections"] = invalid
        return out
    try:
        async with httpx.AsyncClient(timeout=float(os.environ.get("FMP_TIMEOUT_SECONDS", "8"))) as client:
            for section in selected:
                payload, section_status = await _fetch_section(client, symbol, section, _api_key())
                out["section_status"][section] = section_status
                if section_status.get("ok"):
                    out["data"][section] = payload
            out["metrics"] = _research_metrics(out["data"])
            out["ok"] = any(item.get("ok") for item in out["section_status"].values())
            if not out["ok"]:
                out["reason"] = "all_sections_failed"
            return out
    except Exception as exc:
        out["reason"] = _redact(str(exc)[:220])
        return out
