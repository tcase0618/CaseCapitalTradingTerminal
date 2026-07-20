"""Macro intelligence read model.

Builds a current-enough macro dashboard from free/available providers:
FRED for high-frequency U.S. series and World Bank for cross-country annual
macro indicators. LSE remains supplementary, not the driver of the regime
score, because its macro calendar/yield defaults can include very old rows.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

import httpx

FRED_BASE = "https://api.stlouisfed.org/fred"
WORLD_BANK_BASE = "https://api.worldbank.org/v2"

REGIONS = [
    {"key": "WORLD", "label": "World", "wb": "WLD", "proxy": "ACWI"},
    {"key": "US", "label": "United States", "wb": "USA", "proxy": "SPY"},
    {"key": "CHINA", "label": "China", "wb": "CHN", "proxy": "FXI"},
    {"key": "GERMANY", "label": "Germany", "wb": "DEU", "proxy": "EWG"},
    {"key": "INDIA", "label": "India", "wb": "IND", "proxy": "INDA"},
    {"key": "JAPAN", "label": "Japan", "wb": "JPN", "proxy": "EWJ"},
    {"key": "SOUTH_KOREA", "label": "South Korea", "wb": "KOR", "proxy": "EWY"},
    {"key": "TAIWAN", "label": "Taiwan", "wb": "TWN", "proxy": "EWT"},
]

CATEGORIES = [
    {
        "key": "growth",
        "label": "Economic Growth & Activity",
        "indicators": ["gdp_growth", "pmi", "industrial_production", "retail_sales"],
    },
    {
        "key": "labor",
        "label": "Labor Market Dynamics",
        "indicators": ["unemployment", "wage_growth", "job_creation"],
    },
    {
        "key": "prices_policy",
        "label": "Price Stability & Monetary Policy",
        "indicators": ["cpi", "ppi", "policy_rate"],
    },
    {
        "key": "external_fiscal",
        "label": "External & Fiscal Balance",
        "indicators": ["current_account", "debt_to_gdp", "bond_yield"],
    },
]

INDICATOR_META = {
    "gdp_growth": {"label": "GDP Growth", "unit": "%", "wb": "NY.GDP.MKTP.KD.ZG", "good": (2.0, None), "bad": (None, 0.0)},
    "pmi": {"label": "PMI", "unit": "idx", "fred_us": "NAPM", "good": (50.0, None), "bad": (None, 48.0)},
    "industrial_production": {"label": "Industrial Production", "unit": "%", "wb": "NV.IND.TOTL.KD.ZG", "fred_us": "INDPRO", "transform": "yoy", "good": (1.0, None), "bad": (None, -1.0)},
    "retail_sales": {"label": "Retail Sales", "unit": "%", "wb": "NE.CON.PRVT.KD.ZG", "fred_us": "RSAFS", "transform": "yoy", "good": (2.0, None), "bad": (None, 0.0)},
    "unemployment": {"label": "Unemployment Rate", "unit": "%", "wb": "SL.UEM.TOTL.ZS", "fred_us": "UNRATE", "good": (None, 5.0), "bad": (7.0, None), "inverse": True},
    "wage_growth": {"label": "Wage Growth", "unit": "%", "fred_us": "CES0500000003", "transform": "yoy", "good": (3.0, 5.0), "bad": (6.0, None)},
    "job_creation": {"label": "Job Creation", "unit": "K", "fred_us": "PAYEMS", "transform": "mom_thousands", "good": (100.0, None), "bad": (None, 0.0)},
    "cpi": {"label": "CPI Inflation", "unit": "%", "wb": "FP.CPI.TOTL.ZG", "fred_us": "CPIAUCSL", "transform": "yoy", "good": (1.5, 3.2), "bad": (4.0, None)},
    "ppi": {"label": "PPI Inflation", "unit": "%", "fred_us": "PPIACO", "transform": "yoy", "good": (0.0, 3.5), "bad": (5.0, None)},
    "policy_rate": {"label": "Central Bank Policy Rate", "unit": "%", "fred_us": "FEDFUNDS", "good": (None, 4.5), "bad": (5.5, None)},
    "current_account": {"label": "Current Account Balance", "unit": "% GDP", "wb": "BN.CAB.XOKA.GD.ZS", "good": (0.0, None), "bad": (None, -3.0)},
    "debt_to_gdp": {"label": "Debt-to-GDP Ratio", "unit": "% GDP", "wb": "GC.DOD.TOTL.GD.ZS", "fred_us": "GFDEGDQ188S", "good": (None, 80.0), "bad": (120.0, None), "inverse": True},
    "bond_yield": {"label": "10Y / Sovereign Bond Yield", "unit": "%", "fred_us": "DGS10", "good": (None, 4.5), "bad": (5.5, None), "inverse": True},
}

FRED_REGION_SERIES = {
    ("CHINA", "ppi"): "CHNPIEATI01GYM",
    ("GERMANY", "ppi"): "DEUPPDMMINMEI",
    ("GERMANY", "bond_yield"): "IRLTLT01DEM156N",
    ("INDIA", "bond_yield"): "INDIRLTLT01STM",
    ("JAPAN", "ppi"): "JPNPPDMMINMEI",
    ("JAPAN", "bond_yield"): "IRLTLT01JPM156N",
    ("SOUTH_KOREA", "ppi"): "KORPPDMMINMEI",
    ("SOUTH_KOREA", "bond_yield"): "IRLTLT01KRM156N",
    ("TAIWAN", "gdp_growth"): "RGDPNATWA666NRUG",
    ("TAIWAN", "cpi"): "TWNPCPIPCPPPT",
    ("TAIWAN", "debt_to_gdp"): "GGGDTATWA188N",
}

FRED_REGION_TRANSFORMS = {
    ("GERMANY", "ppi"): "yoy",
    ("JAPAN", "ppi"): "yoy",
    ("SOUTH_KOREA", "ppi"): "yoy",
    ("TAIWAN", "gdp_growth"): "yoy",
}

WB_PROXY_SERIES = {
    "pmi": {"indicator": "IC.BUS.EASE.XQ", "label": "Business Conditions Proxy"},
    "wage_growth": {"indicator": "NY.GDP.PCAP.KD.ZG", "label": "Real GDP Per Capita Growth Proxy"},
    "job_creation": {"indicator": "SL.EMP.TOTL.SP.ZS", "label": "Employment Ratio Proxy", "unit": "%"},
    "policy_rate": {"indicator": "FR.INR.RINR", "label": "Real Interest Rate Proxy"},
    "bond_yield": {"indicator": "FR.INR.LEND", "label": "Lending Rate Proxy"},
    "ppi": {"indicator": "FP.WPI.TOTL.ZG", "label": "Wholesale Price Inflation Proxy"},
    "debt_to_gdp": {"indicator": "GC.DOD.TOTL.GD.ZS", "label": "Central Government Debt Proxy"},
}

REGION_PROXY_SERIES = {
    ("WORLD", "policy_rate"): {"indicator": "FR.INR.RINR", "label": "World Real Interest Rate Proxy"},
    ("WORLD", "bond_yield"): {"indicator": "FR.INR.LEND", "label": "World Lending Rate Proxy"},
    ("WORLD", "current_account"): {"indicator": "BN.CAB.XOKA.GD.ZS", "label": "World Current Account Proxy"},
    ("WORLD", "debt_to_gdp"): {"indicator": "GC.DOD.TOTL.GD.ZS", "label": "World Government Debt Proxy"},
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, "", "."):
            return None
        return float(value)
    except Exception:
        return None


async def _get_json(url: str, params: dict[str, Any], timeout: float = 14.0) -> Any:
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


async def _fred_observations(series_id: str, limit: int = 24) -> list[dict[str, Any]]:
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        return []
    data = await _get_json(
        f"{FRED_BASE}/series/observations",
        {
            "series_id": series_id,
            "api_key": key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": limit,
        },
    )
    return [row for row in data.get("observations", []) if _to_float(row.get("value")) is not None]


async def _world_bank_latest(country: str, indicator: str) -> dict[str, Any] | None:
    try:
        data = await _get_json(
            f"{WORLD_BANK_BASE}/country/{country}/indicator/{indicator}",
            {"format": "json", "per_page": 8},
        )
        rows = data[1] if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list) else []
        numeric_rows = []
        for row in rows:
            value = _to_float(row.get("value"))
            if value is not None:
                numeric_rows.append(row)
        if numeric_rows:
            row = numeric_rows[0]
            previous = _to_float(numeric_rows[1].get("value")) if len(numeric_rows) > 1 else None
            return {
                "value": _to_float(row.get("value")),
                "previous_value": previous,
                "previous_date": str(numeric_rows[1].get("date")) if len(numeric_rows) > 1 else None,
                "date": str(row.get("date")),
                "source": "World Bank",
                "raw": row,
            }
    except Exception:
        return None
    return None


async def _world_bank_proxy(region: dict[str, Any], indicator_key: str) -> dict[str, Any] | None:
    country = region.get("wb")
    if not country or country == "TWN":
        return None
    proxy = REGION_PROXY_SERIES.get((region["key"], indicator_key)) or WB_PROXY_SERIES.get(indicator_key)
    if not proxy:
        return None
    row = await _world_bank_latest(country, proxy["indicator"])
    if not row:
        return None
    row["source"] = f"World Bank proxy:{proxy['label']}"
    row["proxy"] = True
    if proxy.get("unit"):
        row["unit"] = proxy["unit"]
    return row


def _transform_fred(rows: list[dict[str, Any]], transform: str | None) -> float | None:
    if not rows:
        return None
    latest = _to_float(rows[0].get("value"))
    if latest is None:
        return None
    if transform == "yoy":
        current_date = str(rows[0].get("date") or "")
        target_prefix = current_date[:5]
        prior = None
        for row in rows[1:]:
            if str(row.get("date") or "").startswith(str(int(current_date[:4]) - 1) + current_date[4:7]):
                prior = _to_float(row.get("value"))
                break
        if prior is None and len(rows) > 12:
            prior = _to_float(rows[12].get("value"))
        return ((latest - prior) / prior * 100.0) if prior not in (None, 0) else None
    if transform == "mom_thousands":
        previous = _to_float(rows[1].get("value")) if len(rows) > 1 else None
        return (latest - previous) if previous is not None else None
    return latest


def _fred_value_at(rows: list[dict[str, Any]], index: int, transform: str | None) -> float | None:
    if index < 0 or index >= len(rows):
        return None
    return _transform_fred(rows[index:], transform)


async def _fred_indicator(region_key: str, indicator_key: str, meta: dict[str, Any]) -> dict[str, Any] | None:
    series = FRED_REGION_SERIES.get((region_key, indicator_key))
    if region_key == "US":
        series = meta.get("fred_us") or series
    if not series:
        return None
    try:
        rows = await _fred_observations(series, limit=30)
        transform = FRED_REGION_TRANSFORMS.get((region_key, indicator_key), meta.get("transform"))
        value = _transform_fred(rows, transform)
        if value is None:
            return None
        return {
            "value": value,
            "previous_value": _fred_value_at(rows, 1, transform),
            "previous_date": str(rows[1].get("date") or "") if len(rows) > 1 else None,
            "date": str(rows[0].get("date") or ""),
            "source": f"FRED:{series}",
            "raw": rows[0],
        }
    except Exception:
        return None


def _freshness(date_value: str | None) -> str:
    if not date_value:
        return "missing"
    try:
        year = int(str(date_value)[:4])
    except Exception:
        return "missing"
    current_year = datetime.now(timezone.utc).year
    if year >= current_year - 1:
        return "fresh"
    if year >= current_year - 3:
        return "watch"
    return "stale"


def _bias(indicator_key: str, value: float | None) -> str:
    if value is None:
        return "missing"
    meta = INDICATOR_META[indicator_key]
    good_low, good_high = meta.get("good", (None, None))
    bad_low, bad_high = meta.get("bad", (None, None))
    if good_low is not None and good_high is not None and good_low <= value <= good_high:
        return "bullish"
    if good_low is not None and good_high is None and value >= good_low:
        return "bullish"
    if good_low is None and good_high is not None and value <= good_high:
        return "bullish"
    if bad_low is not None and value >= bad_low:
        return "bearish"
    if bad_high is not None and value <= bad_high:
        return "bearish"
    return "neutral"


def _indicator_row(indicator_key: str, data: dict[str, Any] | None) -> dict[str, Any]:
    meta = INDICATOR_META[indicator_key]
    value = _to_float((data or {}).get("value"))
    previous_value = _to_float((data or {}).get("previous_value"))
    fresh = _freshness((data or {}).get("date"))
    source = (data or {}).get("source") or "Needs provider"
    date_value = (data or {}).get("date")
    try:
        year = int(str(date_value)[:4]) if date_value else None
    except Exception:
        year = None
    if year is not None and year < datetime.now(timezone.utc).year - 5:
        return {
            "key": indicator_key,
            "label": meta["label"],
            "value": None,
            "unit": meta["unit"],
            "date": None,
            "source": f"{source} stale {date_value}",
            "freshness": "missing",
            "bias": "missing",
        }
    bias = _bias(indicator_key, value)
    if fresh == "stale" and bias == "bullish":
        bias = "neutral"
    if value is None:
        bias = "missing"
    delta = (value - previous_value) if value is not None and previous_value is not None else None
    trend = "flat"
    if delta is None:
        trend = "unknown"
    elif abs(delta) >= 0.01:
        trend = "up" if delta > 0 else "down"
    return {
        "key": indicator_key,
        "label": meta["label"],
        "value": round(value, 2) if value is not None else None,
        "previous_value": round(previous_value, 2) if previous_value is not None else None,
        "previous_date": (data or {}).get("previous_date"),
        "delta": round(delta, 2) if delta is not None else None,
        "trend": trend,
        "unit": (data or {}).get("unit") or meta["unit"],
        "date": date_value,
        "source": source,
        "freshness": fresh,
        "bias": bias,
        "proxy": bool((data or {}).get("proxy")),
    }


def _latest_date(rows: list[dict[str, Any]]) -> str | None:
    dates = sorted([str(row.get("date")) for row in rows if row.get("date")], reverse=True)
    return dates[0] if dates else None


def _row_value(rows_by_key: dict[str, dict[str, Any]], key: str) -> float | None:
    return _to_float((rows_by_key.get(key) or {}).get("value"))


def _case_proxy_row(indicator_key: str, rows_by_key: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    source_rows = [row for row in rows_by_key.values() if row.get("value") is not None and row.get("freshness") != "stale"]
    if not source_rows:
        return None
    gdp = _row_value(rows_by_key, "gdp_growth")
    industrial = _row_value(rows_by_key, "industrial_production")
    retail = _row_value(rows_by_key, "retail_sales")
    unemployment = _row_value(rows_by_key, "unemployment")
    cpi = _row_value(rows_by_key, "cpi")
    ppi = _row_value(rows_by_key, "ppi")
    current_account = _row_value(rows_by_key, "current_account")
    debt = _row_value(rows_by_key, "debt_to_gdp")
    bond = _row_value(rows_by_key, "bond_yield")
    date = _latest_date(source_rows)
    value = None
    unit = INDICATOR_META[indicator_key]["unit"]
    source = "Case macro model proxy from official rows"
    if indicator_key == "pmi":
        parts = [v for v in (gdp, industrial, retail) if v is not None]
        if parts:
            value = max(35.0, min(65.0, 50.0 + (sum(parts) / len(parts))))
    elif indicator_key == "wage_growth":
        if gdp is not None and cpi is not None:
            value = max(-5.0, min(12.0, (gdp * 0.45) + cpi))
        elif gdp is not None:
            value = max(-5.0, min(12.0, gdp * 0.65))
    elif indicator_key == "job_creation":
        unit = "score"
        if unemployment is not None:
            value = max(-250.0, min(250.0, (5.0 - unemployment) * 60.0))
        elif gdp is not None:
            value = max(-250.0, min(250.0, gdp * 25.0))
    elif indicator_key == "ppi":
        if cpi is not None:
            value = cpi
            source = "Case macro model proxy from CPI"
    elif indicator_key == "policy_rate":
        if bond is not None:
            value = bond
            source = "Case macro model proxy from sovereign yield"
        elif cpi is not None:
            value = max(0.0, cpi + 1.0)
            source = "Case macro model proxy from inflation"
    elif indicator_key == "current_account":
        if current_account is not None:
            value = current_account
        elif gdp is not None and retail is not None:
            value = max(-8.0, min(8.0, (gdp - retail) * 0.4))
    elif indicator_key == "debt_to_gdp":
        if debt is not None:
            value = debt
        elif current_account is not None:
            value = max(25.0, min(180.0, 90.0 - (current_account * 5.0)))
            source = "Case macro model proxy from current account balance"
        elif gdp is not None:
            value = max(25.0, min(180.0, 95.0 - (gdp * 4.0)))
    elif indicator_key == "bond_yield":
        if bond is not None:
            value = bond
        elif cpi is not None and gdp is not None:
            value = max(0.0, min(15.0, cpi + (gdp * 0.25) + 1.0))
            source = "Case macro model proxy from inflation and growth"
    elif indicator_key == "industrial_production" and gdp is not None:
        value = gdp
        source = "Case macro model proxy from GDP growth"
    elif indicator_key == "retail_sales" and gdp is not None:
        value = max(-10.0, min(15.0, gdp * 0.85))
        source = "Case macro model proxy from GDP growth"
    elif indicator_key == "unemployment" and gdp is not None:
        value = max(2.0, min(12.0, 6.0 - (gdp * 0.25)))
        source = "Case macro model proxy from GDP growth"
    elif indicator_key == "cpi" and gdp is not None:
        value = max(-3.0, min(12.0, gdp * 0.35))
        source = "Case macro model proxy from GDP growth"
    if value is None:
        return None
    return _indicator_row(indicator_key, {
        "value": value,
        "date": date,
        "source": source,
        "unit": unit,
        "proxy": True,
    })


async def _build_indicator(region: dict[str, Any], indicator_key: str) -> dict[str, Any]:
    meta = INDICATOR_META[indicator_key]
    fred = await _fred_indicator(region["key"], indicator_key, meta)
    if fred:
        return _indicator_row(indicator_key, fred)
    wb_code = meta.get("wb")
    wb_country = region.get("wb")
    if wb_code and wb_country and wb_country != "TWN":
        wb = await _world_bank_latest(wb_country, wb_code)
        if wb:
            return _indicator_row(indicator_key, wb)
    proxy = await _world_bank_proxy(region, indicator_key)
    if proxy:
        return _indicator_row(indicator_key, proxy)
    return _indicator_row(indicator_key, None)


def _region_signal(indicators: list[dict[str, Any]]) -> dict[str, Any]:
    score = 50
    fresh_count = 0
    missing_count = 0
    stale_count = 0
    proxy_count = 0
    for row in indicators:
        if row["freshness"] == "fresh":
            fresh_count += 1
        if row["freshness"] == "stale":
            stale_count += 1
        if row.get("proxy"):
            proxy_count += 1
        if row["bias"] == "missing":
            missing_count += 1
            continue
        if row["bias"] == "bullish":
            score += 2 if row.get("proxy") else (7 if row["freshness"] == "fresh" else 3)
        elif row["bias"] == "bearish":
            score -= 3 if row.get("proxy") else (8 if row["freshness"] == "fresh" else 4)
    if missing_count > 5:
        score -= 4
    if proxy_count >= 6:
        score -= 6
    score = max(0, min(100, score))
    if score >= 58:
        label, icon, color = "BULLISH", "UP", "#4ade80"
    elif score <= 42:
        label, icon, color = "BEARISH", "DOWN", "#f87171"
    else:
        label, icon, color = "NEUTRAL", "FLAT", "#fbbf24"
    return {
        "label": label,
        "icon": icon,
        "color": color,
        "score": score,
        "reason": f"{fresh_count} fresh, {stale_count} stale, {missing_count} missing, {proxy_count} proxy indicators. Score uses growth, labor, inflation, policy, external balance, debt, and bond-yield signals.",
    }


async def _build_region(region: dict[str, Any]) -> dict[str, Any]:
    rows = await asyncio.gather(*[_build_indicator(region, key) for group in CATEGORIES for key in group["indicators"]])
    by_key = {row["key"]: row for row in rows}
    for key, row in list(by_key.items()):
        if row.get("bias") == "missing":
            proxy_row = _case_proxy_row(key, by_key)
            if proxy_row:
                by_key[key] = proxy_row
    rows = [by_key[key] for group in CATEGORIES for key in group["indicators"]]
    categories = [
        {
            "key": group["key"],
            "label": group["label"],
            "indicators": [by_key[key] for key in group["indicators"]],
        }
        for group in CATEGORIES
    ]
    signal = _region_signal(rows)
    coverage = {
        "fresh": sum(1 for row in rows if row["freshness"] == "fresh"),
        "watch": sum(1 for row in rows if row["freshness"] == "watch"),
        "stale": sum(1 for row in rows if row["freshness"] == "stale"),
        "missing": sum(1 for row in rows if row["bias"] == "missing"),
        "total": len(rows),
    }
    return {**region, "categories": categories, "signal": signal, "coverage": coverage}


async def overview() -> dict[str, Any]:
    regions = await asyncio.gather(*[_build_region(region) for region in REGIONS])
    return {
        "ok": True,
        "generated_at": _now_iso(),
        "providers": {
            "fred": bool(os.environ.get("FRED_API_KEY", "").strip()),
            "world_bank": True,
            "london_strategic_edge": bool(os.environ.get("LSE_API_KEY", "").strip()),
        },
        "regions": regions,
        "indicator_contract": CATEGORIES,
        "staleness_policy": "Fresh is current year or prior year; watch is 2-3 years old; older rows are stale and do not create bullish signals.",
    }
