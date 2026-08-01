"""Credible free-data source adapters.

These adapters expose official or low-cost sources as optional intelligence.
They are deliberately isolated from PM execution logic so provider gaps cannot
block scans, ticker pages, or trade management.
"""
from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx


SEC_HEADERS = {"User-Agent": "Case Cap Terminal research@casecap.local"}
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
FRED_BASE = "https://api.stlouisfed.org/fred"
CLINICALTRIALS_URL = "https://clinicaltrials.gov/api/v2/studies"
OPENFDA_EVENT_URL = "https://api.fda.gov/drug/event.json"
OPENFDA_ENFORCEMENT_URL = "https://api.fda.gov/drug/enforcement.json"
ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"

_SEC_TICKER_CACHE: dict[str, dict[str, Any]] | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_ticker(ticker: str) -> str:
    return re.sub(r"[^A-Z0-9.\-]", "", str(ticker or "").upper())


def _clean_search_term(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace('"', "").strip())


def _quality(ok: bool, *, no_match: bool = False, optional: bool = False) -> str:
    if ok:
        return "live"
    if no_match:
        return "no_match"
    if optional:
        return "optional"
    return "down"


def catalog() -> list[dict[str, Any]]:
    return [
        {
            "key": "sec_edgar",
            "name": "SEC EDGAR",
            "official": True,
            "configured": True,
            "cost": "free",
            "use": "Filings, ticker-to-CIK lookup, company facts, insider forms, official fundamentals.",
        },
        {
            "key": "fred",
            "name": "FRED",
            "official": True,
            "configured": bool(os.environ.get("FRED_API_KEY")),
            "cost": "free API key",
            "use": "Rates, inflation, labor, credit spreads, macro regime.",
        },
        {
            "key": "clinicaltrials",
            "name": "ClinicalTrials.gov",
            "official": True,
            "configured": True,
            "cost": "free",
            "use": "Trial counts, phases, and recruiting status for biotech or medical-device tickers.",
        },
        {
            "key": "openfda",
            "name": "openFDA",
            "official": True,
            "configured": True,
            "cost": "free",
            "use": "Drug adverse-event and enforcement signals when a manufacturer match exists.",
        },
        {
            "key": "usaspending",
            "name": "USAspending.gov",
            "official": True,
            "configured": True,
            "cost": "free",
            "use": "Federal award and contract context for defense, industrial, and services names.",
        },
        {
            "key": "alpha_vantage",
            "name": "Alpha Vantage",
            "official": False,
            "configured": bool(os.environ.get("ALPHA_VANTAGE_API_KEY")),
            "cost": "free tier",
            "use": "Backup overview fields, analyst target, valuation ratios, and technical indicators.",
        },
        {
            "key": "nasdaq_earnings_html",
            "name": "Nasdaq Earnings Calendar",
            "official": False,
            "configured": True,
            "cost": "free public HTML",
            "use": "Earnings date confirmation with a source label and scrape-health checks.",
        },
        {
            "key": "openinsider_html",
            "name": "OpenInsider",
            "official": False,
            "configured": True,
            "cost": "free public HTML",
            "use": "Insider open-market buy and cluster checks.",
        },
        {
            "key": "finviz_html",
            "name": "Finviz",
            "official": False,
            "configured": True,
            "cost": "free public HTML",
            "use": "Short float, float, sector, and quick profile fallback fields.",
        },
    ]


async def _http_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 12.0,
) -> tuple[int, Any, str]:
    async with httpx.AsyncClient(timeout=timeout, headers=headers or {}, follow_redirects=True) as client:
        r = await client.get(url, params=params)
        text = r.text
        try:
            data = r.json()
        except Exception:
            data = None
        return r.status_code, data, text


async def _sec_ticker_map() -> dict[str, dict[str, Any]]:
    global _SEC_TICKER_CACHE
    if _SEC_TICKER_CACHE is not None:
        return _SEC_TICKER_CACHE
    status, data, _text = await _http_json(SEC_TICKERS_URL, headers=SEC_HEADERS, timeout=15.0)
    if status != 200 or not isinstance(data, dict):
        _SEC_TICKER_CACHE = {}
        return _SEC_TICKER_CACHE
    fields = data.get("fields") or []
    rows = data.get("data") or []
    lookup: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = dict(zip(fields, row))
        ticker = _clean_ticker(item.get("ticker", ""))
        if not ticker:
            continue
        lookup[ticker] = {
            "ticker": ticker,
            "cik": str(item.get("cik") or "").zfill(10),
            "company_name": item.get("name"),
            "exchange": item.get("exchange"),
        }
    _SEC_TICKER_CACHE = lookup
    return lookup


async def sec_company_lookup(ticker: str) -> dict[str, Any]:
    t = _clean_ticker(ticker)
    if not t:
        return {"ok": False, "quality": "down", "reason": "missing_ticker"}
    try:
        item = (await _sec_ticker_map()).get(t)
        if not item:
            return {"ok": False, "quality": "no_match", "ticker": t, "reason": "ticker_not_found"}
        return {"ok": True, "quality": "live", "source": "SEC company tickers", **item}
    except Exception as exc:
        return {"ok": False, "quality": "down", "ticker": t, "reason": str(exc)[:180]}


def _latest_unit_value(unit_blob: dict[str, Any] | None) -> dict[str, Any] | None:
    if not unit_blob:
        return None
    candidates: list[dict[str, Any]] = []
    for unit_rows in unit_blob.values():
        if isinstance(unit_rows, list):
            candidates.extend([r for r in unit_rows if r.get("val") is not None])
    if not candidates:
        return None
    candidates.sort(key=lambda r: (r.get("fy") or 0, r.get("end") or "", r.get("filed") or ""), reverse=True)
    row = candidates[0]
    return {
        "value": row.get("val"),
        "unit": next((unit for unit, rows in unit_blob.items() if row in rows), None),
        "period_end": row.get("end"),
        "filed": row.get("filed"),
        "form": row.get("form"),
        "fiscal_year": row.get("fy"),
        "fiscal_period": row.get("fp"),
    }


def _unit_values(unit_blob: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not unit_blob:
        return []
    values: list[dict[str, Any]] = []
    for unit, unit_rows in unit_blob.items():
        if not isinstance(unit_rows, list):
            continue
        for row in unit_rows:
            if row.get("val") is not None:
                values.append({
                    "value": row.get("val"),
                    "unit": unit,
                    "period_end": row.get("end"),
                    "filed": row.get("filed"),
                    "form": row.get("form"),
                    "fiscal_year": row.get("fy"),
                    "fiscal_period": row.get("fp"),
                })
    values.sort(key=lambda r: (r.get("fiscal_year") or 0, r.get("period_end") or "", r.get("filed") or ""), reverse=True)
    return values


def _find_fact(facts: dict[str, Any], taxonomy: str, tags: list[str]) -> dict[str, Any] | None:
    tax = facts.get(taxonomy) or {}
    for tag in tags:
        node = tax.get(tag) or {}
        latest = _latest_unit_value(node.get("units"))
        if latest:
            return {"tag": tag, "taxonomy": taxonomy, **latest}
    return None


def _metric(facts: dict[str, Any], label: str, taxonomy: str, tags: list[str]) -> dict[str, Any] | None:
    fact = _find_fact(facts, taxonomy, tags)
    if not fact:
        return None
    return {"label": label, **fact}


def _fact_history(facts: dict[str, Any], taxonomy: str, tags: list[str]) -> list[dict[str, Any]]:
    tax = facts.get(taxonomy) or {}
    for tag in tags:
        node = tax.get(tag) or {}
        values = _unit_values(node.get("units"))
        if values:
            return [{"tag": tag, "taxonomy": taxonomy, **row} for row in values]
    return []


def _metric_value(metrics: dict[str, Any], key: str) -> float | None:
    try:
        value = metrics.get(key, {}).get("value")
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return round(numerator / denominator, 2)


def _safe_subtract(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return round(left - right, 2)


def _ratio_status(key: str, value: float | None) -> str:
    if value is None:
        return "missing"
    if key == "current_ratio":
        if 1.5 <= value <= 2.5:
            return "good"
        if value < 1.0 or value > 3.0:
            return "bad"
        return "watch"
    if key == "quick_ratio":
        if value > 1.0:
            return "good"
        if value < 1.0:
            return "bad"
        return "watch"
    if key == "working_capital":
        return "good" if value > 0 else "bad"
    if key == "debt_to_equity":
        if 1.0 <= value <= 1.5:
            return "good"
        if value > 2.0:
            return "bad"
        return "watch"
    if key == "interest_coverage":
        if value > 3.0:
            return "good"
        if value < 1.5:
            return "bad"
        return "watch"
    if key == "roe":
        pct = value * 100
        if 15 <= pct <= 20:
            return "good"
        if pct < 10:
            return "bad"
        return "watch"
    if key == "net_profit_margin":
        pct = value * 100
        if pct >= 10:
            return "good"
        if pct < 5:
            return "bad"
        return "watch"
    if key == "gross_profit_margin":
        pct = value * 100
        if pct > 50:
            return "good"
        if pct < 20:
            return "bad"
        return "watch"
    if key == "operating_margin":
        pct = value * 100
        if pct > 15:
            return "good"
        if pct < 5:
            return "bad"
        return "watch"
    if key == "ebitda_margin":
        pct = value * 100
        if pct > 15:
            return "good"
        if pct < 10:
            return "bad"
        return "watch"
    if key == "free_cash_flow_margin":
        pct = value * 100
        if pct > 8:
            return "good"
        if pct < 0:
            return "bad"
        return "watch"
    if key == "revenue_growth_rate":
        pct = value * 100
        if pct > 10:
            return "good"
        if pct <= 0:
            return "bad"
        return "watch"
    if key == "inventory_turnover":
        if 5 <= value <= 10:
            return "good"
        if value < 2 or value > 15:
            return "bad"
        return "watch"
    if key == "roa":
        pct = value * 100
        if pct > 5:
            return "good"
        if pct < 2:
            return "bad"
        return "watch"
    if key == "operating_cash_flow":
        return "good" if value > 0 else "bad"
    if key == "cash_conversion":
        if value >= 1.0:
            return "good"
        if value < 0.75:
            return "bad"
        return "watch"
    if key == "debt_to_assets":
        if value < 0.5:
            return "good"
        if value > 0.7:
            return "bad"
        return "watch"
    if key == "pe_ratio":
        if 15 <= value <= 25:
            return "good"
        if value > 30 or value < 10:
            return "bad"
        return "watch"
    return "missing"


def _ratio_row(key: str, label: str, value: float | None, unit: str = "x") -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "value": value,
        "unit": unit,
        "status": _ratio_status(key, value),
    }


def _ratio_meta(sec_facts: dict[str, Any], alpha: dict[str, Any]) -> dict[str, Any]:
    metrics = sec_facts.get("metrics") or {}
    anchor = (
        metrics.get("revenue")
        or metrics.get("net_income")
        or metrics.get("assets")
        or {}
    )
    return {
        "source": "SEC EDGAR companyfacts",
        "market_source": "Alpha Vantage OVERVIEW" if alpha.get("ok") else None,
        "fetched_at": sec_facts.get("fetched_at"),
        "period_end": anchor.get("period_end"),
        "filed": anchor.get("filed"),
        "form": anchor.get("form"),
        "fiscal_year": anchor.get("fiscal_year"),
        "fiscal_period": anchor.get("fiscal_period"),
        "updates_on": "Next SEC companyfacts refresh after each 10-Q or 10-K filing.",
    }


def _flatten_lse_rows(lse_payload: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for bucket in ("fundamentals", "financial_reports", "company_profiles"):
        rows = lse_payload.get(bucket) if isinstance(lse_payload, dict) else None
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict):
                    merged.update({str(k).lower(): v for k, v in row.items() if v not in (None, "")})
    return merged


def _lse_pick(flat: dict[str, Any], *names: str) -> float | None:
    lowered = [n.lower() for n in names]
    for key, value in flat.items():
        key_l = str(key).lower()
        if any(key_l == name or name in key_l for name in lowered):
            parsed = _to_float(value)
            if parsed is not None:
                return parsed
    return None


def _lse_pct(flat: dict[str, Any], *names: str) -> float | None:
    value = _lse_pick(flat, *names)
    if value is None:
        return None
    return value / 100.0 if abs(value) > 1 else value


def _lse_ratio_rows(lse_payload: dict[str, Any]) -> dict[str, Any]:
    flat = _flatten_lse_rows(lse_payload)
    if not flat:
        return {}
    return {
        "current_ratio": _ratio_row("current_ratio", "Current Ratio", _lse_pick(flat, "current_ratio")),
        "quick_ratio": _ratio_row("quick_ratio", "Quick Ratio", _lse_pick(flat, "quick_ratio")),
        "debt_to_equity": _ratio_row("debt_to_equity", "Debt-to-Equity", _lse_pick(flat, "debt_to_equity", "debt_equity")),
        "gross_profit_margin": _ratio_row("gross_profit_margin", "Gross Profit Margin", _lse_pct(flat, "gross_margin", "gross_profit_margin"), unit="pct"),
        "operating_margin": _ratio_row("operating_margin", "Operating Margin", _lse_pct(flat, "operating_margin"), unit="pct"),
        "ebitda_margin": _ratio_row("ebitda_margin", "EBITDA Margin", _lse_pct(flat, "ebitda_margin"), unit="pct"),
        "net_profit_margin": _ratio_row("net_profit_margin", "Net Profit Margin", _lse_pct(flat, "net_margin", "net_profit_margin"), unit="pct"),
        "revenue_growth_rate": _ratio_row("revenue_growth_rate", "Revenue Growth Rate", _lse_pct(flat, "revenue_growth", "revenue_growth_rate"), unit="pct"),
        "roe": _ratio_row("roe", "Return on Equity", _lse_pct(flat, "roe", "return_on_equity"), unit="pct"),
        "roa": _ratio_row("roa", "Return on Assets", _lse_pct(flat, "roa", "return_on_assets"), unit="pct"),
        "pe_ratio": _ratio_row("pe_ratio", "Price-to-Earnings", _lse_pick(flat, "pe_ratio", "price_earnings", "trailing_pe")),
    }


def _compute_sec_ratios(metrics: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    revenue = _metric_value(metrics, "revenue")
    net_income = _metric_value(metrics, "net_income")
    current_assets = _metric_value(metrics, "current_assets")
    current_liabilities = _metric_value(metrics, "current_liabilities")
    cash = _metric_value(metrics, "cash")
    marketable_securities = _metric_value(metrics, "marketable_securities")
    accounts_receivable = _metric_value(metrics, "accounts_receivable")
    inventory = _metric_value(metrics, "inventory")
    equity = _metric_value(metrics, "equity")
    liabilities = _metric_value(metrics, "liabilities")
    interest_expense = _metric_value(metrics, "interest_expense")
    operating_income = _metric_value(metrics, "operating_income")
    cost_of_revenue = _metric_value(metrics, "cost_of_revenue")
    gross_profit = _metric_value(metrics, "gross_profit")
    if gross_profit is None and revenue is not None and cost_of_revenue is not None:
        gross_profit = revenue - cost_of_revenue
    ebitda = _metric_value(metrics, "ebitda")
    if ebitda is None:
        depreciation_amortization = _metric_value(metrics, "depreciation_amortization")
        if operating_income is not None and depreciation_amortization is not None:
            ebitda = operating_income + depreciation_amortization
    operating_cash_flow = _metric_value(metrics, "operating_cash_flow")
    capital_expenditures = _metric_value(metrics, "capital_expenditures")
    free_cash_flow = None
    if operating_cash_flow is not None and capital_expenditures is not None:
        free_cash_flow = operating_cash_flow - abs(capital_expenditures)
    quick_asset_values = [cash, marketable_securities, accounts_receivable]
    quick_assets = None if all(v is None for v in quick_asset_values) else sum(v or 0 for v in quick_asset_values)
    inventory_history = _fact_history(facts, "us-gaap", [
        "InventoryNet",
        "InventoryFinishedGoodsNetOfReserves",
    ])
    average_inventory = inventory
    if inventory is not None and len(inventory_history) > 1:
        for row in inventory_history:
            try:
                prior_inventory = float(row.get("value"))
            except Exception:
                continue
            if row.get("period_end") != metrics.get("inventory", {}).get("period_end"):
                average_inventory = (inventory + prior_inventory) / 2.0
                break

    revenue_history = _fact_history(facts, "us-gaap", [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ])
    prior_revenue = None
    if revenue_history:
        current_period_end = metrics.get("revenue", {}).get("period_end")
        current_period = metrics.get("revenue", {}).get("fiscal_period")
        current_year = metrics.get("revenue", {}).get("fiscal_year")
        for row in revenue_history:
            if row.get("period_end") == current_period_end:
                continue
            if (
                current_period
                and current_year
                and row.get("fiscal_period") == current_period
                and row.get("fiscal_year") == current_year - 1
            ):
                prior_revenue = float(row.get("value"))
                break
        if prior_revenue is None:
            for row in revenue_history:
                if row.get("period_end") != current_period_end:
                    prior_revenue = float(row.get("value"))
                    break

    return {
        "current_ratio": _ratio_row(
            "current_ratio",
            "Current Ratio",
            _safe_ratio(current_assets, current_liabilities),
        ),
        "quick_ratio": _ratio_row(
            "quick_ratio",
            "Quick Ratio",
            _safe_ratio(quick_assets, current_liabilities),
        ),
        "working_capital": _ratio_row(
            "working_capital",
            "Working Capital",
            _safe_subtract(current_assets, current_liabilities),
            unit="USD",
        ),
        "debt_to_equity": _ratio_row(
            "debt_to_equity",
            "Debt-to-Equity",
            _safe_ratio(liabilities, equity),
        ),
        "interest_coverage": _ratio_row(
            "interest_coverage",
            "Interest Coverage",
            _safe_ratio(abs(operating_income) if operating_income is not None else None,
                        abs(interest_expense) if interest_expense is not None else None),
        ),
        "gross_profit_margin": _ratio_row(
            "gross_profit_margin",
            "Gross Profit Margin",
            _safe_ratio(gross_profit, revenue),
            unit="pct",
        ),
        "operating_margin": _ratio_row(
            "operating_margin",
            "Operating Margin",
            _safe_ratio(operating_income, revenue),
            unit="pct",
        ),
        "ebitda_margin": _ratio_row(
            "ebitda_margin",
            "EBITDA Margin",
            _safe_ratio(ebitda, revenue),
            unit="pct",
        ),
        "net_profit_margin": _ratio_row(
            "net_profit_margin",
            "Net Profit Margin",
            _safe_ratio(net_income, revenue),
            unit="pct",
        ),
        "revenue_growth_rate": _ratio_row(
            "revenue_growth_rate",
            "Revenue Growth Rate",
            _safe_ratio(None if revenue is None or prior_revenue is None else revenue - prior_revenue, prior_revenue),
            unit="pct",
        ),
        "free_cash_flow_margin": _ratio_row(
            "free_cash_flow_margin",
            "Free Cash Flow Margin",
            _safe_ratio(free_cash_flow, revenue),
            unit="pct",
        ),
        "roe": _ratio_row(
            "roe",
            "Return on Equity",
            _safe_ratio(net_income, equity),
            unit="pct",
        ),
        "roa": _ratio_row(
            "roa",
            "Return on Assets",
            _safe_ratio(net_income, _metric_value(metrics, "assets")),
            unit="pct",
        ),
        "inventory_turnover": _ratio_row(
            "inventory_turnover",
            "Inventory Turnover",
            _safe_ratio(cost_of_revenue, average_inventory),
        ),
        "operating_cash_flow": _ratio_row(
            "operating_cash_flow",
            "Operating Cash Flow",
            operating_cash_flow,
            unit="USD",
        ),
        "cash_conversion": _ratio_row(
            "cash_conversion",
            "Cash Conversion",
            _safe_ratio(operating_cash_flow, net_income),
        ),
        "debt_to_assets": _ratio_row(
            "debt_to_assets",
            "Debt-to-Assets",
            _safe_ratio(liabilities, _metric_value(metrics, "assets")),
        ),
    }


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, "", "None", "N/A", "-"):
            return None
        return float(value)
    except Exception:
        return None


def _summarize_companyfacts(data: dict[str, Any], cik: str) -> dict[str, Any]:
    facts = data.get("facts") or {}
    metrics = {
        "revenue": _metric(facts, "Revenue", "us-gaap", [
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
        ]),
        "net_income": _metric(facts, "Net income", "us-gaap", ["NetIncomeLoss"]),
        "assets": _metric(facts, "Assets", "us-gaap", ["Assets"]),
        "liabilities": _metric(facts, "Liabilities", "us-gaap", ["Liabilities"]),
        "current_assets": _metric(facts, "Current assets", "us-gaap", ["AssetsCurrent"]),
        "current_liabilities": _metric(facts, "Current liabilities", "us-gaap", ["LiabilitiesCurrent"]),
        "cash": _metric(facts, "Cash", "us-gaap", ["CashAndCashEquivalentsAtCarryingValue"]),
        "marketable_securities": _metric(facts, "Marketable securities", "us-gaap", [
            "MarketableSecuritiesCurrent",
            "ShortTermInvestments",
            "AvailableForSaleSecuritiesCurrent",
        ]),
        "accounts_receivable": _metric(facts, "Accounts receivable", "us-gaap", [
            "AccountsReceivableNetCurrent",
            "AccountsAndNotesReceivableNet",
        ]),
        "inventory": _metric(facts, "Inventory", "us-gaap", [
            "InventoryNet",
            "InventoryFinishedGoodsNetOfReserves",
        ]),
        "debt": _metric(facts, "Debt", "us-gaap", [
            "LongTermDebtAndFinanceLeaseObligationsCurrentAndNoncurrent",
            "LongTermDebtAndFinanceLeaseObligations",
            "LongTermDebtCurrent",
            "LongTermDebtNoncurrent",
        ]),
        "interest_expense": _metric(facts, "Interest expense", "us-gaap", [
            "InterestExpenseNonOperating",
            "InterestExpense",
        ]),
        "operating_income": _metric(facts, "Operating income", "us-gaap", [
            "OperatingIncomeLoss",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        ]),
        "gross_profit": _metric(facts, "Gross profit", "us-gaap", ["GrossProfit"]),
        "ebitda": _metric(facts, "EBITDA", "us-gaap", [
            "EarningsBeforeInterestTaxesDepreciationAmortization",
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterestPlusDepreciationDepletionAndAmortization",
        ]),
        "depreciation_amortization": _metric(facts, "Depreciation and amortization", "us-gaap", [
            "DepreciationDepletionAndAmortization",
            "DepreciationDepletionAndAmortizationExpense",
            "DepreciationAndAmortization",
        ]),
        "cost_of_revenue": _metric(facts, "Cost of revenue", "us-gaap", [
            "CostOfRevenue",
            "CostOfGoodsAndServicesSold",
            "CostOfGoodsSold",
        ]),
        "operating_cash_flow": _metric(facts, "Operating cash flow", "us-gaap", [
            "NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        ]),
        "capital_expenditures": _metric(facts, "Capital expenditures", "us-gaap", [
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsToAcquireProductiveAssets",
            "CapitalExpendituresIncurredButNotYetPaid",
        ]),
        "equity": _metric(facts, "Stockholders equity", "us-gaap", [
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        ]),
        "shares": _metric(facts, "Common shares outstanding", "dei", ["EntityCommonStockSharesOutstanding"]),
        "eps_basic": _metric(facts, "EPS basic", "us-gaap", ["EarningsPerShareBasic"]),
    }
    compact_metrics = {k: v for k, v in metrics.items() if v}
    keys = sorted((facts.get("us-gaap") or {}).keys())
    return {
        "ok": True,
        "quality": "live",
        "source": "SEC EDGAR companyfacts",
        "fetched_at": _now_iso(),
        "cik": str(cik).zfill(10),
        "entity_name": data.get("entityName"),
        "fact_count": len(keys),
        "metrics": compact_metrics,
        "ratios": _compute_sec_ratios(compact_metrics, facts),
        "sample_facts": keys[:40],
    }


async def sec_companyfacts(cik: str) -> dict[str, Any]:
    clean = str(cik or "").strip().lstrip("0")
    if not clean.isdigit():
        return {"ok": False, "quality": "down", "reason": "invalid_cik"}
    padded = clean.zfill(10)
    try:
        status, data, text = await _http_json(
            SEC_FACTS_URL.format(cik=padded),
            headers=SEC_HEADERS,
            timeout=15.0,
        )
        if status != 200 or not isinstance(data, dict):
            return {"ok": False, "quality": "down", "status_code": status, "reason": text[:180]}
        return _summarize_companyfacts(data, padded)
    except Exception as exc:
        return {"ok": False, "quality": "down", "reason": str(exc)[:180]}


async def clinical_trials_summary(company_name: str | None, ticker: str | None = None) -> dict[str, Any]:
    term = _clean_search_term(company_name) or _clean_ticker(ticker or "")
    if not term:
        return {"ok": False, "quality": "no_match", "reason": "missing_search_term"}
    try:
        status, data, text = await _http_json(
            CLINICALTRIALS_URL,
            params={"query.term": term, "pageSize": 25, "format": "json"},
            timeout=12.0,
        )
        if status != 200 or not isinstance(data, dict):
            return {"ok": False, "quality": "down", "status_code": status, "reason": text[:180]}
        studies = data.get("studies") or []
        if not studies:
            return {"ok": False, "quality": "no_match", "query": term, "count": 0}
        statuses: dict[str, int] = {}
        phases: dict[str, int] = {}
        examples: list[dict[str, Any]] = []
        for study in studies:
            protocol = study.get("protocolSection") or {}
            ident = protocol.get("identificationModule") or {}
            status_mod = protocol.get("statusModule") or {}
            design = protocol.get("designModule") or {}
            status_label = status_mod.get("overallStatus") or "UNKNOWN"
            statuses[status_label] = statuses.get(status_label, 0) + 1
            for phase in design.get("phases") or ["UNKNOWN"]:
                phases[phase] = phases.get(phase, 0) + 1
            if len(examples) < 3:
                examples.append({
                    "nct_id": ident.get("nctId"),
                    "title": ident.get("briefTitle"),
                    "status": status_label,
                    "phases": design.get("phases") or [],
                })
        return {
            "ok": True,
            "quality": "live",
            "source": "ClinicalTrials.gov",
            "fetched_at": _now_iso(),
            "query": term,
            "returned_count": len(studies),
            "statuses": statuses,
            "phases": phases,
            "examples": examples,
        }
    except Exception as exc:
        return {"ok": False, "quality": "down", "query": term, "reason": str(exc)[:180]}


async def openfda_summary(company_name: str | None) -> dict[str, Any]:
    term = _clean_search_term(company_name)
    if not term:
        return {"ok": False, "quality": "no_match", "reason": "missing_company_name"}
    search = f'openfda.manufacturer_name:"{term}"'
    try:
        event_status, event_data, event_text = await _http_json(
            OPENFDA_EVENT_URL,
            params={"search": search, "count": "patient.reaction.reactionmeddrapt.exact", "limit": 5},
            timeout=12.0,
        )
        enforcement_status, enforcement_data, _enforcement_text = await _http_json(
            OPENFDA_ENFORCEMENT_URL,
            params={"search": search, "count": "classification.exact", "limit": 5},
            timeout=12.0,
        )
        adverse = []
        if event_status == 200 and isinstance(event_data, dict):
            adverse = [
                {"term": row.get("term"), "count": row.get("count")}
                for row in (event_data.get("results") or [])[:5]
            ]
        recalls = []
        if enforcement_status == 200 and isinstance(enforcement_data, dict):
            recalls = [
                {"classification": row.get("term"), "count": row.get("count")}
                for row in (enforcement_data.get("results") or [])[:5]
            ]
        if adverse or recalls:
            return {
                "ok": True,
                "quality": "live",
                "source": "openFDA",
                "fetched_at": _now_iso(),
                "query": term,
                "adverse_events_top": adverse,
                "recalls_by_class": recalls,
            }
        no_match = event_status == 404 or enforcement_status == 404
        return {
            "ok": False,
            "quality": _quality(False, no_match=no_match),
            "query": term,
            "reason": "no manufacturer match" if no_match else event_text[:180],
        }
    except Exception as exc:
        return {"ok": False, "quality": "down", "query": term, "reason": str(exc)[:180]}


async def alpha_vantage_snapshot(ticker: str) -> dict[str, Any]:
    key = os.environ.get("ALPHA_VANTAGE_API_KEY", "").strip()
    t = _clean_ticker(ticker)
    if not key:
        return {"ok": False, "quality": "optional", "ticker": t, "reason": "ALPHA_VANTAGE_API_KEY not configured"}
    if not t:
        return {"ok": False, "quality": "down", "reason": "missing_ticker"}
    try:
        status, data, text = await _http_json(
            ALPHA_VANTAGE_URL,
            params={"function": "OVERVIEW", "symbol": t, "apikey": key},
            timeout=12.0,
        )
        if status != 200 or not isinstance(data, dict):
            return {"ok": False, "quality": "down", "ticker": t, "status_code": status, "reason": text[:180]}
        note = data.get("Note") or data.get("Information")
        if note:
            return {"ok": False, "quality": "fallback", "ticker": t, "reason": str(note)[:180]}
        if not data or data.get("Symbol") in (None, ""):
            return {"ok": False, "quality": "no_match", "ticker": t, "reason": "empty overview"}
        fields = [
            "Symbol", "Name", "Sector", "Industry", "MarketCapitalization", "PERatio",
            "ProfitMargin", "QuarterlyEarningsGrowthYOY", "QuarterlyRevenueGrowthYOY",
            "AnalystTargetPrice", "Beta", "52WeekHigh", "52WeekLow", "DividendYield",
            "Description",
        ]
        return {
            "ok": True,
            "quality": "live",
            "source": "Alpha Vantage OVERVIEW",
            "fetched_at": _now_iso(),
            "ticker": t,
            "overview": {field: data.get(field) for field in fields if data.get(field) not in (None, "", "None")},
        }
    except Exception as exc:
        return {"ok": False, "quality": "down", "ticker": t, "reason": str(exc)[:180]}


async def fred_latest(series_id: str) -> dict[str, Any]:
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        return {"ok": False, "quality": "optional", "reason": "FRED_API_KEY not configured"}
    sid = str(series_id or "").strip().upper()
    if not sid:
        return {"ok": False, "quality": "down", "reason": "missing_series_id"}
    try:
        status, data, text = await _http_json(
            f"{FRED_BASE}/series/observations",
            params={"series_id": sid, "api_key": key, "file_type": "json", "sort_order": "desc", "limit": 1},
            timeout=12.0,
        )
        if status != 200 or not isinstance(data, dict):
            return {"ok": False, "quality": "down", "status_code": status, "reason": text[:180]}
        obs = (data.get("observations") or [None])[0] or {}
        return {
            "ok": True,
            "quality": "live",
            "source": "FRED",
            "series_id": sid,
            "date": obs.get("date"),
            "value": obs.get("value"),
            "fetched_at": _now_iso(),
        }
    except Exception as exc:
        return {"ok": False, "quality": "down", "reason": str(exc)[:180]}


async def ticker_free_data(ticker: str, company_name: str | None = None) -> dict[str, Any]:
    t = _clean_ticker(ticker)
    started = _now_iso()
    lookup = await sec_company_lookup(t)
    resolved_name = company_name or lookup.get("company_name") or t

    tasks = [
        sec_companyfacts(lookup.get("cik")) if lookup.get("ok") and lookup.get("cik") else asyncio.sleep(0, result={
            "ok": False,
            "quality": "no_match",
            "reason": "SEC ticker lookup did not resolve a CIK",
        }),
        clinical_trials_summary(resolved_name, t),
        openfda_summary(resolved_name),
        alpha_vantage_snapshot(t),
    ]
    sec_facts, trials, fda, alpha = await asyncio.gather(*tasks, return_exceptions=True)
    try:
        from . import london_strategic_edge as lse_svc
        lse = await lse_svc.ticker_context(t) if lse_svc.configured() else {
            "ok": False,
            "quality": "optional",
            "reason": "LSE_API_KEY not configured",
        }
    except Exception as exc:
        lse = {"ok": False, "quality": "down", "source_key": "london_strategic_edge", "reason": str(exc)[:180]}

    def normalize(value: Any, source_key: str) -> dict[str, Any]:
        if isinstance(value, Exception):
            return {"ok": False, "quality": "down", "source_key": source_key, "reason": str(value)[:180]}
        if isinstance(value, dict):
            return value
        return {"ok": False, "quality": "down", "source_key": source_key, "reason": "unexpected_response"}

    payload = {
        "ok": True,
        "ticker": t,
        "company_name": resolved_name,
        "fetched_at": started,
        "sec": {
            "lookup": lookup,
            "companyfacts": normalize(sec_facts, "sec_companyfacts"),
        },
        "clinical_trials": normalize(trials, "clinicaltrials"),
        "openfda": normalize(fda, "openfda"),
        "alpha_vantage": normalize(alpha, "alpha_vantage"),
        "london_strategic_edge": normalize(lse, "london_strategic_edge"),
    }
    payload["sources"] = [
        {"key": "london_strategic_edge", "quality": "live" if payload["london_strategic_edge"].get("provider") else payload["london_strategic_edge"].get("quality"), "ok": bool(payload["london_strategic_edge"].get("provider"))},
        {"key": "sec_edgar", "quality": payload["sec"]["companyfacts"].get("quality"), "ok": payload["sec"]["companyfacts"].get("ok")},
        {"key": "clinicaltrials", "quality": payload["clinical_trials"].get("quality"), "ok": payload["clinical_trials"].get("ok")},
        {"key": "openfda", "quality": payload["openfda"].get("quality"), "ok": payload["openfda"].get("ok")},
        {"key": "alpha_vantage", "quality": payload["alpha_vantage"].get("quality"), "ok": payload["alpha_vantage"].get("ok")},
    ]
    ratios = dict((payload["sec"]["companyfacts"].get("ratios") or {}))
    lse_ratios = {
        key: value for key, value in _lse_ratio_rows(payload["london_strategic_edge"]).items()
        if value.get("value") is not None
    }
    ratios.update(lse_ratios)
    pe_ratio = _to_float((payload["alpha_vantage"].get("overview") or {}).get("PERatio"))
    if "pe_ratio" not in lse_ratios:
        ratios["pe_ratio"] = _ratio_row("pe_ratio", "Price-to-Earnings", pe_ratio)
    payload["key_ratios"] = ratios
    payload["key_ratios_meta"] = _ratio_meta(payload["sec"]["companyfacts"], payload["alpha_vantage"])
    if payload["london_strategic_edge"].get("provider"):
        payload["key_ratios_meta"]["source"] = "London Strategic Edge + SEC EDGAR companyfacts"
        payload["key_ratios_meta"]["market_source"] = "London Strategic Edge primary, Alpha Vantage fallback"
    return payload
