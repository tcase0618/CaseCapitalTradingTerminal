"""Read-only research-source routes.

These sources may enrich research and company context, but none of the routes
in this module can create, alter, or authorize an order.
"""
from __future__ import annotations

from fastapi import APIRouter

from services import risk_target


router = APIRouter(tags=["research"])


@router.get("/data/free/catalog")
async def free_data_catalog():
    from services import free_data

    return {"sources": free_data.catalog()}


@router.get("/data/free/sec/companyfacts/{cik}")
async def free_data_sec_companyfacts(cik: str):
    from services import free_data

    return await free_data.sec_companyfacts(cik)


@router.get("/data/free/ticker/{ticker}")
async def free_data_ticker(ticker: str):
    from services import free_data

    normalized = ticker.upper()
    company_name = None
    try:
        fundamentals = await risk_target.fetch_fundamentals(normalized)
        company_name = (fundamentals or {}).get("name")
    except Exception:
        company_name = None
    return await free_data.ticker_free_data(normalized, company_name=company_name)


@router.get("/data/free/fred/latest/{series_id}")
async def free_data_fred_latest(series_id: str):
    from services import free_data

    return await free_data.fred_latest(series_id)


@router.get("/data/finance-toolkit/status")
async def finance_toolkit_status():
    from services import finance_toolkit_source

    return finance_toolkit_source.status()


@router.get("/data/finance-toolkit/profile/{ticker}")
async def finance_toolkit_profile(ticker: str):
    from services import finance_toolkit_source

    return await finance_toolkit_source.company_profile(ticker)


@router.get("/data/finance-toolkit/research/{ticker}")
async def finance_toolkit_research(ticker: str, sections: str | None = None):
    from services import finance_toolkit_source

    return await finance_toolkit_source.research_bundle(ticker, sections=sections)


@router.get("/research/accountant/status")
async def accountant_research_status():
    from services import accountant_research

    return accountant_research.status()


@router.get("/research/accountant/{ticker}")
async def accountant_research_ticker(ticker: str, force_refresh: bool = False):
    from services import accountant_research

    return await accountant_research.ticker_research(ticker, force_refresh=force_refresh)
