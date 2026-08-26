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
