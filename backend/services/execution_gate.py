"""Shared execution gate and kill-switch state.

This is intentionally thin and fast. It does not decide trade quality; it only
prevents execution when the terminal is globally unsafe, misconfigured, or fed
obviously invalid symbols.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import get_db, stamped
from .ticker_hygiene import validate_ticker


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _csv_set(name: str) -> set[str]:
    return {x.strip().upper() for x in os.environ.get(name, "").split(",") if x.strip()}


def kill_switch_state() -> dict[str, Any]:
    return {
        "global": _env_bool("GLOBAL_EXECUTION_KILL"),
        "equity": _env_bool("EQUITY_EXECUTION_KILL"),
        "options": _env_bool("OPTIONS_EXECUTION_KILL"),
        "qc_strict": _env_bool("QC_EXECUTION_KILL"),
        "ticker_kill_list": sorted(_csv_set("TICKER_KILL_LIST")),
        "sector_kill_list": sorted(_csv_set("SECTOR_KILL_LIST")),
    }


async def _truth_snapshot(force_refresh: bool = False) -> dict[str, Any]:
    if not force_refresh:
        cached = await _cached_truth_snapshot(allow_stale=False)
        if cached:
            cached.setdefault("gate_source", "fresh_cached_truth")
            return cached
    try:
        from . import data_truth
        return await asyncio.wait_for(
            data_truth.overview(force_refresh=force_refresh, persist=True),
            timeout=float(os.environ.get("EXECUTION_GATE_TRUTH_TIMEOUT_SECONDS", "18.0") or 18.0),
        )
    except Exception as exc:
        cached = await _cached_truth_snapshot(allow_stale=False)
        if not cached and force_refresh:
            cached = await _cached_truth_snapshot(
                max_age_seconds=int(os.environ.get("EXECUTION_GATE_REFRESH_FALLBACK_SECONDS", "1800") or 1800),
                allow_stale=False,
            )
        if cached:
            cached.setdefault("warnings", [])
            cached["gate_source"] = "cached_truth_after_refresh_error"
            cached["refresh_error"] = exc.__class__.__name__
            cached["refresh_error_detail"] = str(exc)[:220]
            return cached
        return {
            "ok": False,
            "truth_grade": "F",
            "decision": "BLOCK",
            "error": str(exc)[:220],
            "error_type": exc.__class__.__name__,
        }


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


async def _revalidate_stale_execution_authority(
    truth: dict[str, Any],
    scope: str,
    scoped_blockers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Recheck the two execution-critical providers when QC is stale.

    A recently-run QC cache is not provider evidence. Previously, an old
    cached Alpaca/price-path row could block an otherwise healthy paper
    account for days. The order path now performs a bounded live recheck and
    removes only those two blockers when both authorities answer cleanly.
    Options still require their own valid contract checks in options_desk.
    """
    if scope not in {"equity", "options"}:
        return scoped_blockers
    critical = {"integration:alpaca", "integration:price_path"}
    if not any((row.get("key") or row.get("label")) in critical for row in scoped_blockers):
        return scoped_blockers
    try:
        from . import pricer, trade_floor

        account = await asyncio.wait_for(trade_floor.get_account(), timeout=6.0)
        price_source = pricer.execution_source_label()
        if not account or not str(price_source).lower().startswith("alpaca"):
            return scoped_blockers
        truth["execution_authority_revalidated_at"] = _now()
        truth["execution_authority_revalidation"] = {
            "alpaca_account": True,
            "price_path": price_source,
            "scope": scope,
        }
        return [
            row for row in scoped_blockers
            if (row.get("key") or row.get("label")) not in critical
        ]
    except Exception as exc:
        truth["execution_authority_revalidation_error"] = exc.__class__.__name__
        return scoped_blockers
async def _cached_truth_snapshot(max_age_seconds: int | None = None, allow_stale: bool = False) -> dict[str, Any] | None:
    ttl = max_age_seconds
    if ttl is None:
        ttl = int(os.environ.get("EXECUTION_GATE_OVERVIEW_CACHE_SECONDS", "300") or 300)
    if ttl <= 0:
        return None
    try:
        cached = await get_db().bot_state.find_one({"_id": "data_truth_latest"}, {"_id": 0})
    except Exception:
        return None
    if not cached:
        return None
    generated = _parse_dt(cached.get("generated_at"))
    if not generated:
        return None
    stale = datetime.now(timezone.utc) - generated > timedelta(seconds=ttl)
    if stale and not allow_stale:
        return None
    cached["cache"] = {
        "source": "bot_state.data_truth_latest",
        "max_age_seconds": ttl,
        "age_seconds": round((datetime.now(timezone.utc) - generated).total_seconds(), 2),
        "stale": stale,
    }
    return cached


async def check(
    scope: str = "system",
    ticker: str | None = None,
    sector: str | None = None,
    *,
    truth: dict[str, Any] | None = None,
    force_refresh: bool = False,
    record: bool = True,
) -> dict[str, Any]:
    scope = (scope or "system").strip().lower()
    blockers: list[str] = []
    warnings: list[str] = []
    kills = kill_switch_state()

    if kills["global"]:
        blockers.append("global_execution_kill_enabled")
    if scope == "equity" and kills["equity"]:
        blockers.append("equity_execution_kill_enabled")
    if scope == "options" and kills["options"]:
        blockers.append("options_execution_kill_enabled")

    normalized_ticker = None
    if ticker:
        ticker_check = validate_ticker(ticker)
        normalized_ticker = ticker_check.get("ticker")
        if not ticker_check["ok"]:
            blockers.append(f"ticker_hygiene:{ticker_check['reason']}")
        elif normalized_ticker in set(kills["ticker_kill_list"]):
            blockers.append("ticker_kill_list_match")

    normalized_sector = str(sector or "").strip().upper()
    if normalized_sector and normalized_sector in set(kills["sector_kill_list"]):
        blockers.append("sector_kill_list_match")

    truth = truth if truth is not None else await _truth_snapshot(force_refresh=force_refresh)
    # Cached truth after a failed refresh is diagnostic evidence only, never
    # sufficient authorization for a new order.
    if truth.get("refresh_error"):
        blockers.append("truth_refresh_failed")
    truth_decision = str(truth.get("decision") or "UNKNOWN").upper()
    truth_grade = str(truth.get("truth_grade") or "UNKNOWN").upper()
    scoped_qc = ((truth.get("qc") or {}).get("scoped_blockers") or {})
    if scope in {"equity", "options"}:
        scoped_blockers = scoped_qc.get(scope, [])
    else:
        scoped_blockers = scoped_qc.get("system", [])
    scoped_blockers = await _revalidate_stale_execution_authority(truth, scope, scoped_blockers)
    for row in scoped_blockers:
        key = row.get("key") or row.get("label") or "quality"
        blockers.append(f"qc:{key}")

    if truth_decision == "BLOCK":
        blockers.append(f"data_truth_block:{truth_grade or truth_decision}")
    elif _env_bool("BLOCK_ON_LOW_TRUTH_GRADE") and truth_grade in {"D", "F"}:
        blockers.append(f"low_truth_grade_strict_block:{truth_grade}")
    elif kills["qc_strict"] and truth_decision != "PASS":
        blockers.append(f"qc_strict_requires_pass:{truth_decision}")
    elif truth_decision == "WATCH" or truth_grade in {"C", "D", "F"}:
        warnings.append("data_truth_watch")

    execution = truth.get("execution") or {}
    if scope == "equity":
        if not execution.get("equity_execution_enabled"):
            blockers.append("equity_execution_disabled")
        if not execution.get("equity_paper") and not _env_bool("ALLOW_LIVE_EQUITY_EXECUTION"):
            blockers.append("equity_account_not_paper")
    if scope == "options":
        if not execution.get("options_execution_enabled"):
            blockers.append("options_execution_disabled")
        if not execution.get("options_paper") and not _env_bool("ALLOW_LIVE_OPTIONS_EXECUTION"):
            blockers.append("options_account_not_paper")

    payload = {
        "ok": not blockers,
        "generated_at": _now(),
        "scope": scope,
        "ticker": normalized_ticker,
        "sector": normalized_sector or None,
        "decision": "BLOCK" if blockers else "WATCH" if warnings else "PASS",
        "blockers": sorted(set(blockers)),
        "warnings": sorted(set(warnings)),
        "kill_switches": kills,
        "truth_grade": truth_grade,
        "truth_decision": truth_decision,
        "truth": truth,
    }
    if record:
        try:
            doc = stamped(payload)
            await get_db().execution_gate_checks.insert_one(doc)
            try:
                from . import postgres_store
                await postgres_store.mirror_document("execution_gate_checks", doc)
            except Exception:
                pass
        except Exception:
            pass
    return payload


async def overview(force_refresh: bool = False) -> dict[str, Any]:
    # UI/header calls must be snappy. Order paths still call check() directly
    # and revalidate against fresh truth before submitting anything.
    truth = None
    if not force_refresh:
        truth = await _cached_truth_snapshot(allow_stale=False)
        if not truth:
            truth = await _cached_truth_snapshot(
                max_age_seconds=int(os.environ.get("EXECUTION_GATE_UI_STALE_OK_SECONDS", "1800") or 1800),
                allow_stale=True,
            )
            if truth:
                truth.setdefault("warnings", [])
                warning = "execution gate UI using stale cached truth; order paths still revalidate live"
                if warning not in truth["warnings"]:
                    truth["warnings"].append(warning)
    return await check(scope="system", force_refresh=force_refresh, truth=truth, record=False)
