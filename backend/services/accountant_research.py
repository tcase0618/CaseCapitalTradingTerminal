"""Read-only bridge to THE ACCOUNTANT's research API.

The Accountant owns its SEC/XBRL store.  The terminal retains only a compact
research snapshot used for display and PM evidence review; this adapter never
calls Accountant mutation endpoints and cannot affect execution authority.
"""
from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timezone
from typing import Any

import httpx

from .db import get_db

PROVIDER = "case_capital_accountant"
RESEARCH_ONLY = True
DECISION_AUTHORITY = "NONE"
PACKET_VERSION = "ACCOUNTANT_RESEARCH_PACKET_V2"
RESEARCH_BUDGET_SECONDS = 15.0


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


def _max_age_days() -> int:
    try:
        return max(1, min(365, int(os.environ.get("ACCOUNTANT_RESEARCH_MAX_AGE_DAYS", "7"))))
    except (TypeError, ValueError):
        return 7


def _symbol(value: Any) -> str:
    return "".join(ch for ch in str(value or "").upper().strip() if ch.isalnum() or ch in {".", "-"})


def _headers() -> dict[str, str]:
    token = os.environ.get("ACCOUNTANT_API_TOKEN", "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo and parsed.utcoffset() is not None else None
    except (TypeError, ValueError):
        return None


def _fresh(snapshot: dict[str, Any]) -> bool:
    fetched = _parse_time(snapshot.get("fetched_at"))
    return bool(fetched and 0 <= (datetime.now(timezone.utc) - fetched).total_seconds() <= _cache_ttl())


def _acceptance(status: str, reasons: list[str], packet: dict[str, Any] | None = None) -> dict[str, Any]:
    supplied = (packet or {}).get("acceptance")
    supplied = supplied if isinstance(supplied, dict) else {}
    return {
        "status": status,
        "accepted": status == "VALIDATED",
        "reasons": reasons,
        "source_as_of": supplied.get("source_as_of") if isinstance(supplied.get("source_as_of"), str) else None,
        "report_as_of": supplied.get("report_as_of") if isinstance(supplied.get("report_as_of"), str) else None,
    }


def _validate_packet(ticker: str, integration: dict[str, Any], packet: dict[str, Any]) -> list[str]:
    reasons = []
    acceptance = packet.get("acceptance")
    acceptance = acceptance if isinstance(acceptance, dict) else {}
    mode = packet.get("operating_mode")
    mode = mode if isinstance(mode, dict) else {}
    integrity = packet.get("source_integrity")
    integrity = integrity if isinstance(integrity, dict) else {}
    if packet.get("packet_version") != PACKET_VERSION:
        reasons.append("unsupported_packet_version")
    if integration.get("ticker") != ticker or packet.get("ticker") != ticker:
        reasons.append("ticker_mismatch")
    if not (acceptance.get("status") == "VALIDATED" and acceptance.get("accepted") is True
            and isinstance(acceptance.get("reasons"), list)
            and acceptance["reasons"] == []):
        reasons.append("producer_not_validated")
    if packet.get("research_only") is not True or packet.get("execution_allowed") is not False:
        reasons.append("invalid_research_authority")
    if mode.get("terminal_handoff_allowed") is not True:
        reasons.append("terminal_handoff_not_allowed")
    controls = packet.get("research_controls")
    for authority in (mode, controls):
        if isinstance(authority, dict) and (
            ("execution_allowed" in authority and authority["execution_allowed"] is not False)
            or ("research_only" in authority and authority["research_only"] is not True)
        ):
            reasons.append("conflicting_research_authority")
    if integrity.get("source_grade") not in ("STRONG", "WATCH"):
        reasons.append("insufficient_source_grade")
    now = datetime.now(timezone.utc)
    timestamps = {
        "integration_generated_at": integration.get("generated_at"),
        "packet_generated_at": packet.get("generated_at"),
        "source_as_of": acceptance.get("source_as_of"),
        "report_as_of": acceptance.get("report_as_of"),
    }
    evidence_present = False
    for name in ("report", "report_card"):
        evidence = packet.get(name)
        if isinstance(evidence, dict) and evidence:
            evidence_present = True
            if "ticker" in evidence and evidence["ticker"] != ticker:
                reasons.append(f"{name}_ticker_mismatch")
            if "updated_at" in evidence:
                timestamps[f"{name}_updated_at"] = evidence["updated_at"]
        elif evidence is not None:
            reasons.append(f"invalid_{name}")
    if not evidence_present:
        reasons.append("missing_research_evidence")
    for name, value in timestamps.items():
        parsed = _parse_time(value)
        # Producer as-of fields have calendar-day precision, not generation time.
        # Only these fields accept dates; actual event timestamps stay aware.
        if parsed is None and name in {"source_as_of", "report_as_of"} and isinstance(value, str):
            try:
                day = date.fromisoformat(value)
                parsed = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
            except ValueError:
                pass
        if parsed is None:
            reasons.append(f"{name}_invalid")
        else:
            age = (now - parsed).total_seconds()
            if age < 0:
                reasons.append(f"{name}_future")
            elif age > _max_age_days() * 86400:
                reasons.append(f"{name}_stale")
    for evidence in (integration, packet, packet.get("report"), packet.get("report_card")):
        if isinstance(evidence, dict) and "INSUFFICIENT" in str(evidence.get("data_quality_tier", "")).upper():
            reasons.append("insufficient_data_quality")
            break
    return reasons


def status() -> dict[str, Any]:
    configured = bool(_base_url())
    return {
        "ok": bool(_enabled() and configured),
        "enabled": _enabled(),
        "configured": configured,
        "provider": PROVIDER,
        "base_url": _base_url() or None,
        "research_only": RESEARCH_ONLY,
        "execution_allowed": False,
        "decision_authority": DECISION_AUTHORITY,
        "wired_to_pm_evidence": True,
        "wired_to_execution": False,
        "cache_ttl_seconds": _cache_ttl(),
        "max_age_days": _max_age_days(),
        "scan_max_tickers": _scan_limit(),
        "research_budget_seconds": RESEARCH_BUDGET_SECONDS,
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
        "execution_allowed": False,
        "acceptance": _acceptance("VALIDATED", [], packet),
        "operating_mode": packet.get("operating_mode"),
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
            key: controls.get(key) for key in (
                "version", "approval_state", "qa_status", "qa_checks", "qa_failures",
                "hard_vetoes", "execution_authority", "research_only", "execution_allowed", "warnings",
            )
        } if controls else {},
        "source_integrity": {
            key: integrity.get(key) for key in ("source_grade", "ok", "quality", "warnings", "generated_at")
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
    """Bound cache access, both HTTP requests and persistence by one deadline."""
    try:
        return await asyncio.wait_for(
            _ticker_research(ticker, force_refresh=force_refresh), timeout=RESEARCH_BUDGET_SECONDS,
        )
    except asyncio.TimeoutError:
        return {
            "ok": False, "ticker": _symbol(ticker), "provider": PROVIDER,
            "reason": "accountant_research_timeout", "cache_state": "rejected",
            "research_only": RESEARCH_ONLY, "execution_allowed": False,
            "decision_authority": DECISION_AUTHORITY,
            "acceptance": _acceptance("UNAVAILABLE", ["accountant_research_timeout"]),
        }


async def _ticker_research(ticker: str, *, force_refresh: bool = False) -> dict[str, Any]:
    """Validate current producer evidence; cached evidence is diagnostic only.

    force_refresh remains API-compatible, but every request checks readiness and
    the packet so cache age cannot hide revoked handoff or authentication failures.
    """
    symbol = _symbol(ticker)
    base = {
        "ok": False,
        "ticker": symbol,
        "provider": PROVIDER,
        "research_only": RESEARCH_ONLY,
        "execution_allowed": False,
        "acceptance": _acceptance("UNAVAILABLE", ["not_loaded"]),
        "decision_authority": DECISION_AUTHORITY,
    }
    if not symbol:
        return {**base, "reason": "missing_symbol", "acceptance": _acceptance("UNAVAILABLE", ["missing_symbol"])}
    config = status()
    if not config["enabled"]:
        return {**base, "reason": "accountant_adapter_disabled", "acceptance": _acceptance("UNAVAILABLE", ["accountant_adapter_disabled"])}
    if not config["configured"]:
        return {**base, "reason": "missing_accountant_api_base_url", "acceptance": _acceptance("UNAVAILABLE", ["missing_accountant_api_base_url"])}

    db = get_db()
    cached = await db.accountant_research_snapshots.find_one({"ticker": symbol}, {"_id": 0}) or {}
    def reject(reason: str, *, packet: dict[str, Any] | None = None, reasons: list[str] | None = None) -> dict[str, Any]:
        result = {
            **base, "reason": reason,
            "acceptance": _acceptance("UNVALIDATED" if packet is not None else "UNAVAILABLE", reasons or [reason], packet),
            "cache_state": "rejected",
        }
        if cached:
            # Never expose a cached success or acceptance through diagnostics.
            result["stale_diagnostics"] = {
                **cached, "ok": False, "research_only": True, "execution_allowed": False,
                "decision_authority": DECISION_AUTHORITY,
                "acceptance": _acceptance("UNAVAILABLE", [reason]),
            }
        return result

    async with httpx.AsyncClient(timeout=_timeout(), headers=_headers(), follow_redirects=True) as client:
        integration, integration_error = await _get_json(client, f"/api/integration/accountant/{symbol}")
        if integration is None:
            return reject(f"accountant_integration_{integration_error or 'unavailable'}")
        if integration.get("ready_for_readonly_integration") is not True:
            return reject("accountant_not_ready")
        packet, packet_error = await _get_json(client, f"/api/research-packets/{symbol}")
        if packet is None:
            return reject(f"accountant_packet_{packet_error or 'unavailable'}")
        reasons = _validate_packet(symbol, integration, packet)
        if reasons:
            return reject(reasons[0], packet=packet, reasons=reasons)
        snapshot = _compact_packet(symbol, integration, packet)
        snapshot["ok"] = True
        snapshot["cache_state"] = "refreshed"
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
    for row in rows:
        if isinstance(row, dict):
            row.pop("accountant_research", None)
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

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*(load(symbol) for symbol in selected), return_exceptions=True),
            timeout=RESEARCH_BUDGET_SECONDS,
        )
    except asyncio.TimeoutError:
        # gather cancellation drains both active loads and semaphore waiters.
        # Do not attach partial/cached evidence when the batch missed its budget.
        return {
            "ok": False, "reason": "accountant_research_timeout",
            "requested": len(selected), "enriched": 0, "unavailable": len(selected),
            "tickers": [], "errors": {symbol: "accountant_research_timeout" for symbol in selected},
            "research_only": RESEARCH_ONLY, "decision_authority": DECISION_AUTHORITY,
        }
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
        if not isinstance(row, dict):
            continue
        snapshot = snapshots.get(_symbol(row.get("ticker")))
        if snapshot and row.get("pm_routable") and not row.get("read_only"):
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
