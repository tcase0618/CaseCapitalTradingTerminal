"""Shared execution gate and kill-switch state.

This is intentionally thin and fast. It does not decide trade quality; it only
prevents execution when the terminal is globally unsafe, misconfigured, or fed
obviously invalid symbols.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
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
    try:
        from . import data_truth
        return await asyncio.wait_for(
            data_truth.overview(force_refresh=force_refresh, persist=False),
            timeout=12.0,
        )
    except Exception as exc:
        return {
            "ok": False,
            "truth_grade": "F",
            "decision": "BLOCK",
            "error": str(exc)[:220],
        }


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
    truth_decision = str(truth.get("decision") or "UNKNOWN").upper()
    truth_grade = str(truth.get("truth_grade") or "UNKNOWN").upper()
    scoped_qc = ((truth.get("qc") or {}).get("scoped_blockers") or {})
    if scope in {"equity", "options"}:
        scoped_blockers = scoped_qc.get(scope, [])
    else:
        scoped_blockers = scoped_qc.get("system", [])
    for row in scoped_blockers:
        key = row.get("key") or row.get("label") or "quality"
        blockers.append(f"qc:{key}")

    if truth_decision == "BLOCK" and scope == "system":
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
            await get_db().execution_gate_checks.insert_one(stamped(payload))
        except Exception:
            pass
    return payload


async def overview(force_refresh: bool = False) -> dict[str, Any]:
    return await check(scope="system", force_refresh=force_refresh, record=False)
