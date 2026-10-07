"""Read-only bridge to THE ACCOUNTANT's research API.

The Accountant owns its SEC/XBRL store.  The terminal retains only a compact
research snapshot used for display and PM evidence review; this adapter never
calls Accountant mutation endpoints and cannot affect execution authority.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from .db import get_db

PROVIDER = "case_capital_accountant"
RESEARCH_ONLY = True
DECISION_AUTHORITY = "NONE"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _enabled() -> bool:
    return os.environ.get("ACCOUNTANT_RESEARCH_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def _base_url() -> str:
    return os.environ.get("ACCOUNTANT_API_BASE_URL", "").strip().rstrip("/")


def _timeout() -> float:
    try:
        return max(1.0, min(20.0, float(os.environ.get("ACCOUNTANT_API_TIMEOUT_SECONDS", "6"))))
    except (TypeError, ValueError):
        return 6.0


def _cache_ttl() -> int:
    try:
        return max(60, min(7 * 24 * 60 * 60, int(os.environ.get("ACCOUNTANT_RESEARCH_CACHE_TTL_SECONDS", "21600"))))
    except (TypeError, ValueError):
        return 21600


def _scan_limit() -> int:
    try:
        return max(1, min(100, int(os.environ.get("ACCOUNTANT_RESEARCH_SCAN_MAX_TICKERS", "36"))))
    except (TypeError, ValueError):
        return 36


def _symbol(value: Any) -> str:
    return "".join(ch for ch in str(value or "").upper().strip() if ch.isalnum() or ch in {".", "-"})


def _headers() -> dict[str, str]:
    token = os.environ.get("ACCOUNTANT_API_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _fresh(snapshot: dict[str, Any]) -> bool:
    fetched = _parse_time(snapshot.get("fetched_at"))
    return bool(fetched and (datetime.now(timezone.utc) - fetched).total_seconds() <= _cache_ttl())


def status() -> dict[str, Any]:
    configured = bool(_base_url())
    return {
        "ok": bool(_enabled() and configured),
        "enabled": _enabled(),
        "configured": configured,
        "provider": PROVIDER,
        "base_url": _base_url() or None,
        "research_only": RESEARCH_ONLY,
        "decision_authority": DECISION_AUTHORITY,
        "wired_to_pm_evidence": True,
        "wired_to_execution": False,
        "cache_ttl_seconds": _cache_ttl(),
        "scan_max_tickers": _scan_limit(),
    }


def _compact_packet(ticker: str, integration: dict[str, Any], packet: dict[str, Any] | None) -> dict[str, Any]:
    packet = packet or {}
    report = packet.get("report") if isinstance(packet.get("report"), dict) else {}
    report_card = packet.get("report_card") if isinstance(packet.get("report_card"), dict) else {}
    controls = packet.get("research_controls") if isinstance(packet.get("research_controls"), dict) else {}
    integrity = packet.get("source_integrity") if isinstance(packet.get("source_integrity"), dict) else {}
    return {
        "ticker": ticker,
        "provider": PROVIDER,
        "research_only": RESEARCH_ONLY,
        "decision_authority": DECISION_AUTHORITY,
        "fetched_at": _now(),
        "integration": {
            key: integration.get(key)
            for key in (
                "ready_for_readonly_integration", "pipeline_stage", "report_available",
                "report_card_available", "canonical_facts_count", "statement_snapshots_count",
                "latest_report_date", "latest_report_card_filed_date", "latest_report_updated_at",
                "stance", "future_bucket", "data_quality_tier",
            )
        },
        "report": {
            key: report.get(key)
            for key in (
                "as_of_date", "stance", "bullish_score", "bearish_score", "composite_score",
                "data_quality_tier", "pipeline_stage", "latest_filing_date", "highlights", "updated_at",
            )
        } if report else None,
        "report_card": {
            key: report_card.get(key)
            for key in ("filing_type", "period_of_report", "filed_date", "accepted_at", "source_url", "final_verdict")
        } if report_card else None,
        "research_controls": {
            key: controls.get(key) for key in ("research_only", "execution_allowed", "warnings")
        } if controls else {},
        "source_integrity": {
            key: integrity.get(key) for key in ("ok", "quality", "warnings", "generated_at")
        } if integrity else {},
        "provenance": {
            "packet_version": packet.get("packet_version"),
            "packet_id": packet.get("packet_id"),
            "accountant_generated_at": packet.get("generated_at") or integration.get("generated_at"),
            "accountant_base_url": _base_url(),
        },
        "limitations": list(packet.get("limitations") or [])[:12],
    }


async def _get_json(client: httpx.AsyncClient, path: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        response = await client.get(f"{_base_url()}{path}")
        if response.status_code == 404:
            return None, "not_found"
        if response.status_code >= 400:
            return None, f"http_{response.status_code}"
        payload = response.json()
        return (payload if isinstance(payload, dict) else None), None
    except (httpx.HTTPError, ValueError) as exc:
        return None, exc.__class__.__name__.lower()


async def ticker_research(ticker: str, *, force_refresh: bool = False) -> dict[str, Any]:
    """Return a compact Accountant research snapshot, using terminal cache first."""
    symbol = _symbol(ticker)
    base = {
        "ok": False,
        "ticker": symbol,
        "provider": PROVIDER,
        "research_only": RESEARCH_ONLY,
        "decision_authority": DECISION_AUTHORITY,
    }
    if not symbol:
        return {**base, "reason": "missing_symbol"}
    config = status()
    if not config["enabled"]:
        return {**base, "reason": "accountant_adapter_disabled"}
    if not config["configured"]:
        return {**base, "reason": "missing_accountant_api_base_url"}

    db = get_db()
    cached = await db.accountant_research_snapshots.find_one({"ticker": symbol}, {"_id": 0}) or {}
    if cached and not force_refresh and _fresh(cached):
        return {**cached, "ok": True, "cache_state": "fresh"}

    async with httpx.AsyncClient(timeout=_timeout(), headers=_headers(), follow_redirects=True) as client:
        integration, integration_error = await _get_json(client, f"/api/integration/accountant/{symbol}")
        if integration is None:
            fallback = {**base, "reason": f"accountant_integration_{integration_error or 'unavailable'}"}
            if cached:
                return {**cached, "ok": True, "cache_state": "stale_fallback", "refresh_reason": fallback["reason"]}
            return fallback
        packet: dict[str, Any] | None = None
        packet_error = None
        if integration.get("report_available") or integration.get("report_card_available"):
            packet, packet_error = await _get_json(client, f"/api/research-packets/{symbol}")
        snapshot = _compact_packet(symbol, integration, packet)
        snapshot["ok"] = True
        snapshot["cache_state"] = "refreshed"
        if packet_error:
            snapshot["packet_reason"] = packet_error
        await db.accountant_research_snapshots.update_one(
            {"ticker": symbol},
            {"$set": snapshot, "$setOnInsert": {"created_at": _now()}},
            upsert=True,
        )
        return snapshot


def _priority(row: dict[str, Any]) -> tuple[float, float, str]:
    return (
        -float(row.get("case_score") or (row.get("strategy_case") or {}).get("case_score") or 0),
        -float(row.get("strategy_confidence") or (row.get("strategy_case") or {}).get("confidence") or 0),
        _symbol(row.get("ticker")),
    )


async def enrich_scan_candidates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Attach read-only Accountant evidence to a bounded PM candidate set.

    Enrichment failures are isolated from scans.  The fields are deliberately
    namespaced so current PM scoring and execution code cannot consume them as
    a hidden gate or trading instruction.
    """
    config = status()
    eligible = [row for row in rows if isinstance(row, dict) and _symbol(row.get("ticker")) and row.get("pm_routable") and not row.get("read_only")]
    if not config["ok"]:
        return {"ok": False, "reason": "accountant_not_configured", "requested": 0, "enriched": 0, "status": config}
    selected: list[str] = []
    seen: set[str] = set()
    for row in sorted(eligible, key=_priority):
        ticker = _symbol(row.get("ticker"))
        if ticker and ticker not in seen:
            seen.add(ticker)
            selected.append(ticker)
        if len(selected) >= _scan_limit():
            break
    semaphore = asyncio.Semaphore(4)

    async def load(symbol: str) -> tuple[str, dict[str, Any]]:
        async with semaphore:
            return symbol, await ticker_research(symbol)

    results = await asyncio.gather(*(load(symbol) for symbol in selected), return_exceptions=True)
    snapshots: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    for item in results:
        if isinstance(item, Exception):
            continue
        symbol, payload = item
        if payload.get("ok"):
            snapshots[symbol] = payload
        else:
            errors[symbol] = str(payload.get("reason") or "unavailable")
    for row in rows:
        snapshot = snapshots.get(_symbol(row.get("ticker")))
        if snapshot:
            row["accountant_research"] = snapshot
    return {
        "ok": True,
        "requested": len(selected),
        "enriched": len(snapshots),
        "unavailable": len(errors),
        "tickers": sorted(snapshots),
        "errors": errors,
        "research_only": RESEARCH_ONLY,
        "decision_authority": DECISION_AUTHORITY,
    }
