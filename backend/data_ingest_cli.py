"""Case Capital data ingestion CLI.

This is a non-execution command surface for pulling free/provider-backed data
into immutable-ish raw snapshots. It does not place orders, resize positions,
or alter PM/trading state.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import typer
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from services.db import get_db, log_activity, stamped  # noqa: E402

app = typer.Typer(
    help=(
        "Case Capital free-data ingestion and raw snapshot CLI. "
        "Use --no-persist for dry runs."
    )
)

FOREX_FACTORY_FEEDS = {
    "thisweek": "https://nfs.faireconomy.media/ff_calendar_thisweek.xml",
}

DEFAULT_FRED_SERIES = [
    "FEDFUNDS",
    "DGS2",
    "DGS10",
    "T10Y2Y",
    "CPIAUCSL",
    "UNRATE",
    "PAYEMS",
    "RSAFS",
    "INDPRO",
    "GDP",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(k): _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [_json_safe(v) for v in value]
        return str(value)


async def _persist_snapshot(
    *,
    source_key: str,
    dataset: str,
    ok: bool,
    payload: dict[str, Any],
    request: dict[str, Any] | None = None,
    provider: str | None = None,
    source_timestamp: str | None = None,
    dataset_version: str = "raw-v1",
) -> dict[str, Any]:
    received_at = _now_iso()
    doc = stamped({
        "source_key": source_key,
        "provider": provider or source_key,
        "dataset": dataset,
        "ok": bool(ok),
        "request": request or {},
        "source_timestamp": source_timestamp,
        "received_timestamp": received_at,
        "knowledge_timestamp": received_at,
        "revision_timestamp": received_at,
        "dataset_version": dataset_version,
        "payload": _json_safe(payload),
    })
    db = get_db()
    res = await db.raw_data_snapshots.insert_one(doc)
    await log_activity(
        f"Data ingest snapshot stored: {source_key}/{dataset}",
        "success" if ok else "warn",
        {"snapshot_id": str(res.inserted_id), "ok": ok, "dataset": dataset},
    )
    return {"snapshot_id": str(res.inserted_id), "received_timestamp": received_at}


def _event_text(event: ET.Element, field: str) -> str | None:
    child = event.find(field)
    if child is None or child.text is None:
        return None
    text = child.text.strip()
    return text or None


def _parse_forex_factory_xml(xml_text: str, *, feed_name: str, min_impact: set[str] | None = None) -> list[dict[str, Any]]:
    impact_rank = {"low": 1, "medium": 2, "high": 3}
    min_rank = 0
    if min_impact:
        min_rank = min((impact_rank.get(x.lower(), 0) for x in min_impact), default=0)
    root = ET.fromstring(xml_text)
    rows: list[dict[str, Any]] = []
    for event in root.findall(".//event"):
        impact = (_event_text(event, "impact") or "").strip()
        if min_rank and impact_rank.get(impact.lower(), 0) < min_rank:
            continue
        row = {
            "title": _event_text(event, "title"),
            "country": _event_text(event, "country"),
            "date": _event_text(event, "date"),
            "time": _event_text(event, "time"),
            "impact": impact or None,
            "forecast": _event_text(event, "forecast"),
            "previous": _event_text(event, "previous"),
            "actual": _event_text(event, "actual"),
            "url": _event_text(event, "url"),
            "feed": feed_name,
        }
        if row["title"] and row["country"] and row["date"]:
            rows.append(row)
    return rows


async def _fetch_json(url: str, *, params: dict[str, Any] | None = None, timeout: float = 20.0) -> tuple[int, Any, str]:
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "CaseCapitalTerminal/1.0 data-ingest"}) as client:
        response = await client.get(url, params=params)
        text = response.text
        try:
            return response.status_code, response.json(), text
        except Exception:
            return response.status_code, None, text


async def _fetch_text(url: str, *, timeout: float = 20.0, retries: int = 2) -> tuple[int, str]:
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": "CaseCapitalTerminal/1.0 data-ingest"}) as client:
        last_response: httpx.Response | None = None
        for attempt in range(max(1, retries + 1)):
            response = await client.get(url)
            last_response = response
            if response.status_code != 429 or attempt >= retries:
                return response.status_code, response.text
            await asyncio.sleep(2.0 + attempt)
        return (last_response.status_code, last_response.text) if last_response else (0, "")


def _compact_row(row: Any) -> Any:
    if not isinstance(row, dict):
        return row
    wanted = [
        "ok",
        "ticker",
        "company_name",
        "source",
        "quality",
        "series_id",
        "date",
        "value",
        "title",
        "country",
        "time",
        "impact",
        "forecast",
        "previous",
        "actual",
        "drug",
        "indication",
        "pdufa_date",
        "type",
        "data_quality",
        "source_confidence",
        "status_code",
        "reason",
    ]
    out = {k: row.get(k) for k in wanted if row.get(k) is not None}
    if "sources" in row and isinstance(row.get("sources"), list):
        out["sources"] = [
            {
                "key": src.get("key"),
                "quality": src.get("quality"),
                "ok": src.get("ok"),
            }
            for src in row["sources"][:8]
            if isinstance(src, dict)
        ]
    if "key_ratios_meta" in row and isinstance(row.get("key_ratios_meta"), dict):
        meta = row["key_ratios_meta"]
        out["ratio_period"] = {
            "period_end": meta.get("period_end"),
            "filed": meta.get("filed"),
            "form": meta.get("form"),
            "source": meta.get("source"),
        }
    return out or {"keys": sorted(str(k) for k in row.keys())[:12]}


def _compact_result(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    compact = {
        "ok": value.get("ok"),
        "source": value.get("source"),
        "fetched_at": value.get("fetched_at"),
        "count": value.get("count"),
        "status_code": value.get("status_code"),
        "snapshot": value.get("snapshot"),
    }
    if "feeds" in value:
        compact["feeds"] = value.get("feeds")
    if "providers" in value:
        compact["providers"] = value.get("providers")
    rows = value.get("rows")
    if isinstance(rows, list):
        compact["sample_rows"] = [_compact_row(row) for row in rows[:5]]
    if "results" in value and isinstance(value.get("results"), dict):
        compact["results"] = {k: _compact_result(v) for k, v in value["results"].items()}
    return {k: v for k, v in compact.items() if v is not None}


def _emit(payload: dict[str, Any], *, raw: bool = False) -> None:
    typer.echo(json.dumps(_json_safe(payload if raw else _compact_result(payload)), indent=2))


@app.command("providers")
def providers() -> None:
    """Show the free/provider-backed datasets this CLI can pull."""
    _emit({
        "ok": True,
        "providers": [
            {"key": "alpaca", "coverage": "broker truth, positions, orders, fills, stock/options market data", "runtime": "existing terminal adapters"},
            {"key": "forex_factory", "coverage": "weekly global economic calendar via Fair Economy XML feed", "runtime": "data_ingest_cli forex-factory"},
            {"key": "fred", "coverage": "macro series and release dates", "runtime": "data_ingest_cli fred-series / macro-calendar"},
            {"key": "fda_calendar", "coverage": "public FDA/PDUFA calendar rows imported into pharma calendar", "runtime": "data_ingest_cli fda-calendar"},
            {"key": "sec_edgar", "coverage": "company lookup, XBRL company facts, filings context", "runtime": "data_ingest_cli ticker / sec-ticker"},
            {"key": "clinicaltrials", "coverage": "trial records", "runtime": "existing free_data/pharma adapters"},
            {"key": "openfda", "coverage": "drug/device safety and recall summaries", "runtime": "existing free_data/pharma adapters"},
            {"key": "usaspending", "coverage": "prime contract awards and agency references", "runtime": "data_ingest_cli usaspending-probe"},
            {"key": "london_strategic_edge", "coverage": "configured premium/free key-backed context if available", "runtime": "existing adapter"},
        ],
    })


async def _forex_factory_async(
    weeks: list[str],
    impact: str,
    persist: bool,
) -> dict[str, Any]:
    wanted_impact = {x.strip().lower() for x in impact.split(",") if x.strip()}
    rows: list[dict[str, Any]] = []
    feeds: list[dict[str, Any]] = []
    for week in weeks:
        key = week.strip().lower()
        url = FOREX_FACTORY_FEEDS.get(key, key if key.startswith("http") else "")
        if not url:
            feeds.append({"week": week, "ok": False, "reason": "unknown_week_or_url"})
            continue
        status, text = await _fetch_text(url, timeout=20.0)
        if status != 200:
            feeds.append({"week": week, "url": url, "ok": False, "status_code": status, "reason": text[:180]})
            continue
        try:
            parsed = _parse_forex_factory_xml(text, feed_name=key, min_impact=wanted_impact or None)
        except Exception as exc:
            feeds.append({"week": week, "url": url, "ok": False, "status_code": status, "reason": str(exc)[:180]})
            continue
        rows.extend(parsed)
        feeds.append({"week": week, "url": url, "ok": True, "rows": len(parsed)})
    payload = {
        "ok": any(f.get("ok") for f in feeds),
        "source": "ForexFactory/FairEconomy XML",
        "fetched_at": _now_iso(),
        "feeds": feeds,
        "filters": {"impact": sorted(wanted_impact)},
        "rows": rows,
        "count": len(rows),
    }
    if persist:
        payload["snapshot"] = await _persist_snapshot(
            source_key="forex_factory",
            provider="Fair Economy / Forex Factory",
            dataset="economic_calendar",
            ok=payload["ok"],
            request={"weeks": weeks, "impact": impact},
            payload=payload,
            source_timestamp=payload["fetched_at"],
        )
    return payload


@app.command("forex-factory")
def forex_factory(
    weeks: str = typer.Option("thisweek", help="Comma-separated feed names or XML URLs."),
    impact: str = typer.Option("medium,high", help="Minimum/allowed impact levels to keep."),
    persist: bool = typer.Option(True, help="Persist raw snapshot into Mongo raw_data_snapshots."),
    raw_output: bool = typer.Option(False, help="Print full payload instead of summary."),
) -> None:
    """Pull ForexFactory/FairEconomy economic calendar XML.

    Example:
        python data_ingest_cli.py forex-factory --impact high --no-persist
    """
    payload = asyncio.run(_forex_factory_async([w for w in weeks.split(",") if w.strip()], impact, persist))
    _emit(payload, raw=raw_output)


async def _fred_series_async(series: list[str], persist: bool) -> dict[str, Any]:
    from services import free_data

    results = await asyncio.gather(*(free_data.fred_latest(s) for s in series), return_exceptions=True)
    rows = []
    for sid, result in zip(series, results):
        if isinstance(result, Exception):
            rows.append({"ok": False, "series_id": sid, "quality": "down", "reason": str(result)[:180]})
        else:
            rows.append(result)
    payload = {
        "ok": any(r.get("ok") for r in rows),
        "source": "FRED",
        "fetched_at": _now_iso(),
        "rows": rows,
        "count": len(rows),
    }
    if persist:
        payload["snapshot"] = await _persist_snapshot(
            source_key="fred",
            provider="FRED",
            dataset="latest_macro_series",
            ok=payload["ok"],
            request={"series": series},
            payload=payload,
            source_timestamp=payload["fetched_at"],
        )
    return payload


@app.command("fred-series")
def fred_series(
    series: list[str] = typer.Argument(None, help="FRED series IDs. Defaults to core macro set."),
    persist: bool = typer.Option(True, help="Persist raw snapshot into Mongo raw_data_snapshots."),
    raw_output: bool = typer.Option(False, help="Print full payload instead of summary."),
) -> None:
    """Pull latest observations for selected FRED series.

    Example:
        python data_ingest_cli.py fred-series FEDFUNDS DGS10 --no-persist
    """
    payload = asyncio.run(_fred_series_async(series or DEFAULT_FRED_SERIES, persist))
    _emit(payload, raw=raw_output)


async def _macro_calendar_async(days: int, persist: bool) -> dict[str, Any]:
    from services import macro_pulse

    rows = await macro_pulse.upcoming_events(days_ahead=days, force=True)
    status = await macro_pulse.source_status()
    payload = {
        "ok": True,
        "source": "ForexFactory/FairEconomy USD macro calendar",
        "source_status": status,
        "fetched_at": _now_iso(),
        "days_ahead": days,
        "rows": rows,
        "count": len(rows),
    }
    if persist:
        payload["snapshot"] = await _persist_snapshot(
            source_key="forex_factory_macro_calendar",
            provider="ForexFactory/FairEconomy XML",
            dataset="macro_calendar",
            ok=True,
            request={"days_ahead": days},
            payload=payload,
            source_timestamp=payload["fetched_at"],
        )
    return payload


async def _fda_calendar_async(persist: bool, allow_fallback: bool) -> dict[str, Any]:
    from services import pharma

    result = await pharma.import_fda_calendar(
        persist=persist,
        allow_fallback=allow_fallback,
        triggered_by="data_ingest_cli",
    )
    payload = {
        "ok": result.get("ok"),
        "source": "Public FDA/PDUFA calendars",
        "fetched_at": result.get("fetched_at") or _now_iso(),
        "count": result.get("count"),
        "imported": result.get("imported"),
        "persisted": result.get("persisted"),
        "blocked": result.get("blocked"),
        "reason": result.get("reason"),
        "source_counts": result.get("source_counts"),
        "quality_counts": result.get("quality_counts"),
        "fallback_used": result.get("fallback_used"),
        "source_errors": result.get("source_errors"),
        "rows": result.get("rows") or [],
    }
    if persist:
        payload["snapshot"] = await _persist_snapshot(
            source_key="fda_calendar",
            provider="Public FDA/PDUFA calendar sources",
            dataset="pharma_pdufa_calendar",
            ok=bool(payload["ok"]) and not bool(payload.get("blocked")),
            request={"allow_fallback": allow_fallback},
            payload=payload,
            source_timestamp=payload["fetched_at"],
        )
    return payload


@app.command("fda-calendar")
def fda_calendar(
    persist: bool = typer.Option(True, help="Import rows into pharma_pdufa plus cache and store raw snapshot."),
    allow_fallback: bool = typer.Option(False, help="Allow curated seed fallback rows to be imported if live sources fail."),
    raw_output: bool = typer.Option(False, help="Print full payload instead of summary."),
) -> None:
    """Refresh the pharma FDA/PDUFA calendar from public calendar sources.

    Example:
        python data_ingest_cli.py fda-calendar --no-persist
        python data_ingest_cli.py fda-calendar
    """
    _emit(asyncio.run(_fda_calendar_async(persist, allow_fallback)), raw=raw_output)


@app.command("macro-calendar")
def macro_calendar(
    days: int = typer.Option(30, min=1, max=120),
    persist: bool = typer.Option(True, help="Persist raw snapshot into Mongo raw_data_snapshots."),
    raw_output: bool = typer.Option(False, help="Print full payload instead of summary."),
) -> None:
    """Refresh the terminal macro calendar and store a raw snapshot.

    Example:
        python data_ingest_cli.py macro-calendar --days 45 --no-persist
    """
    _emit(asyncio.run(_macro_calendar_async(days, persist)), raw=raw_output)


async def _ticker_async(tickers: list[str], persist: bool) -> dict[str, Any]:
    from services import free_data

    clean = [str(t).upper().strip().lstrip("$") for t in tickers if str(t).strip()]
    results = await asyncio.gather(*(free_data.ticker_free_data(t) for t in clean), return_exceptions=True)
    rows = []
    for ticker, result in zip(clean, results):
        if isinstance(result, Exception):
            rows.append({"ok": False, "ticker": ticker, "quality": "down", "reason": str(result)[:180]})
        else:
            rows.append(result)
    payload = {
        "ok": any(r.get("ok") for r in rows),
        "source": "SEC/ClinicalTrials/openFDA/AlphaVantage/LSE adapters",
        "fetched_at": _now_iso(),
        "tickers": clean,
        "rows": rows,
        "count": len(rows),
    }
    if persist:
        payload["snapshot"] = await _persist_snapshot(
            source_key="ticker_free_data",
            provider="Case Capital adapters",
            dataset="ticker_context",
            ok=payload["ok"],
            request={"tickers": clean},
            payload=payload,
            source_timestamp=payload["fetched_at"],
        )
    return payload


@app.command("ticker")
def ticker(
    tickers: list[str] = typer.Argument(..., help="Ticker symbols to enrich."),
    persist: bool = typer.Option(True, help="Persist raw snapshot into Mongo raw_data_snapshots."),
    raw_output: bool = typer.Option(False, help="Print full payload instead of summary."),
) -> None:
    """Pull free context for ticker(s): SEC, trials, FDA, Alpha Vantage, LSE if configured.

    Example:
        python data_ingest_cli.py ticker LDOS APLD --no-persist
    """
    _emit(asyncio.run(_ticker_async(tickers, persist)), raw=raw_output)


async def _sec_ticker_async(tickers: list[str], persist: bool) -> dict[str, Any]:
    from services import free_data

    clean = [str(t).upper().strip().lstrip("$") for t in tickers if str(t).strip()]
    rows = []
    for ticker in clean:
        lookup = await free_data.sec_company_lookup(ticker)
        facts = await free_data.sec_companyfacts(lookup.get("cik")) if lookup.get("ok") and lookup.get("cik") else {
            "ok": False,
            "quality": "no_match",
            "reason": "ticker lookup did not resolve CIK",
        }
        rows.append({"ticker": ticker, "lookup": lookup, "companyfacts": facts})
    payload = {
        "ok": any((r.get("lookup") or {}).get("ok") for r in rows),
        "source": "SEC EDGAR APIs",
        "fetched_at": _now_iso(),
        "tickers": clean,
        "rows": rows,
        "count": len(rows),
    }
    if persist:
        payload["snapshot"] = await _persist_snapshot(
            source_key="sec_edgar",
            provider="SEC EDGAR",
            dataset="ticker_companyfacts",
            ok=payload["ok"],
            request={"tickers": clean},
            payload=payload,
            source_timestamp=payload["fetched_at"],
        )
    return payload


@app.command("sec-ticker")
def sec_ticker(
    tickers: list[str] = typer.Argument(..., help="Ticker symbols to pull from SEC EDGAR."),
    persist: bool = typer.Option(True, help="Persist raw snapshot into Mongo raw_data_snapshots."),
    raw_output: bool = typer.Option(False, help="Print full payload instead of summary."),
) -> None:
    """Pull SEC ticker lookup + companyfacts for ticker(s).

    Example:
        python data_ingest_cli.py sec-ticker LDOS --no-persist
    """
    _emit(asyncio.run(_sec_ticker_async(tickers, persist)), raw=raw_output)


async def _usaspending_probe_async(persist: bool) -> dict[str, Any]:
    status, data, text = await _fetch_json("https://api.usaspending.gov/api/v2/references/toptier_agencies/", timeout=20.0)
    payload = {
        "ok": status == 200 and isinstance(data, dict),
        "source": "USAspending.gov",
        "fetched_at": _now_iso(),
        "status_code": status,
        "data": data if isinstance(data, dict) else None,
        "reason": None if status == 200 else text[:180],
    }
    if persist:
        payload["snapshot"] = await _persist_snapshot(
            source_key="usaspending",
            provider="USAspending.gov",
            dataset="toptier_agencies_probe",
            ok=payload["ok"],
            request={},
            payload=payload,
            source_timestamp=payload["fetched_at"],
        )
    return payload


@app.command("usaspending-probe")
def usaspending_probe(
    persist: bool = typer.Option(True, help="Persist raw snapshot into Mongo raw_data_snapshots."),
    raw_output: bool = typer.Option(False, help="Print full payload instead of summary."),
) -> None:
    """Probe USAspending agency reference data and persist the response.

    Example:
        python data_ingest_cli.py usaspending-probe --no-persist
    """
    _emit(asyncio.run(_usaspending_probe_async(persist)), raw=raw_output)


async def _all_async(tickers: list[str], fred_series_ids: list[str], persist: bool) -> dict[str, Any]:
    tasks = {
        "forex_factory": _forex_factory_async(["thisweek"], "medium,high", persist),
        "fred_series": _fred_series_async(fred_series_ids or DEFAULT_FRED_SERIES, persist),
        "macro_calendar": _macro_calendar_async(45, persist),
        "usaspending": _usaspending_probe_async(persist),
    }
    if tickers:
        tasks["ticker"] = _ticker_async(tickers, persist)
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    payload: dict[str, Any] = {"ok": True, "fetched_at": _now_iso(), "results": {}}
    for key, result in zip(tasks.keys(), results):
        if isinstance(result, Exception):
            payload["results"][key] = {"ok": False, "reason": str(result)[:180]}
            payload["ok"] = False
        else:
            payload["results"][key] = result
            if not result.get("ok"):
                payload["ok"] = False
    return payload


@app.command("all")
def all_sources(
    tickers: list[str] = typer.Option(None, "--ticker", "-t", help="Optional ticker(s) to enrich."),
    fred_series_ids: list[str] = typer.Option(None, "--fred", help="Optional FRED series IDs."),
    persist: bool = typer.Option(True, help="Persist raw snapshots into Mongo raw_data_snapshots."),
    raw_output: bool = typer.Option(False, help="Print full payload instead of summary."),
) -> None:
    """Run the main free-data pull set in one command.

    Example:
        python data_ingest_cli.py all -t LDOS -t APLD --no-persist
    """
    _emit(asyncio.run(_all_async(tickers or [], fred_series_ids or DEFAULT_FRED_SERIES, persist)), raw=raw_output)


if __name__ == "__main__":
    app()
