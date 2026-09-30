"""Full terminal scan orchestration.

This is the single path for operator-triggered and scheduled full-cycle scans.
It refreshes discovery, specialist screeners, PM state, options candidates, and
Telegram from the same cycle so scheduled runs do not lag manual Launch Control.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

from .db import get_db, log_activity

_FULL_SCAN_LOCK = asyncio.Lock()

_STALE_PUBLIC_ENTRY_REASONS = {
    "public_execution_quote_stale_or_unverifiable",
    "public_final_quote_stale_or_unverifiable",
    "public_final_quote_unavailable",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _row_count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _reason_counts(rows: Any) -> dict[str, int]:
    if not isinstance(rows, list):
        return {}
    counts: dict[str, int] = {}
    for row in rows:
        reason = str((row or {}).get("reason") or "unknown") if isinstance(row, dict) else "unknown"
        counts[reason] = counts.get(reason, 0) + 1
    return counts


def _is_stale_public_entry_rejection(reason: Any) -> bool:
    """Whether an execution rejection merits a later fresh-market retry.

    This deliberately excludes PM, stop, buying-power, halt, and duplicate
    rejections. A retry queue must never become a second path around those
    decisions or controls.
    """
    value = str(reason or "").strip().lower()
    return value in _STALE_PUBLIC_ENTRY_REASONS or (
        value.startswith("public_execution_quote_") and ("stale" in value or "unverifiable" in value)
    )


async def _persist_stale_execution_retries(
    *,
    pm_rows: list[dict[str, Any]],
    equity_execution: dict[str, Any],
    cycle_id: str,
    observed_at: str | None,
) -> dict[str, Any]:
    """Persist only fresh-quote retry candidates for the 10:30 full rescan."""
    rejected = equity_execution.get("rejected") or []
    stale = [row for row in rejected if isinstance(row, dict) and _is_stale_public_entry_rejection(row.get("reason"))]
    if not stale:
        return {"queued": 0, "tickers": []}
    approved = {
        str(row.get("ticker") or "").upper(): row
        for row in pm_rows
        if str(row.get("action") or "").upper() in {"STARTER", "ACCUMULATE"}
    }
    today = (observed_at or _now().isoformat())[:10]
    db = get_db()
    queued: list[str] = []
    for rejection in stale:
        ticker = str(rejection.get("ticker") or "").upper()
        pm_row = approved.get(ticker)
        if not ticker or not pm_row:
            continue
        source_scan = str(pm_row.get("source_scan") or pm_row.get("screener_id") or "CORE")
        retry_key = f"{today}:{ticker}:{source_scan}"
        await db.stale_execution_retries.update_one(
            {"retry_key": retry_key},
            {"$set": {
                "retry_key": retry_key,
                "ticker": ticker,
                "source_scan": source_scan,
                "scanner_family": pm_row.get("scanner_family"),
                "source_cycle_id": cycle_id,
                "source_observed_at": observed_at,
                "pm_action": pm_row.get("action"),
                "pm_score": pm_row.get("pm_score"),
                "allocation_usd": pm_row.get("allocation_usd"),
                "last_rejection_reason": rejection.get("reason"),
                "last_quote_age_seconds": rejection.get("age_seconds"),
                "status": "PENDING_1030_FULL_RESCAN",
                "updated_at": _now().isoformat(),
            }, "$setOnInsert": {"created_at": _now().isoformat()}},
            upsert=True,
        )
        queued.append(ticker)
    return {"queued": len(queued), "tickers": sorted(set(queued))}


async def _resolve_stale_execution_retries(
    *,
    pm_rows: list[dict[str, Any]],
    equity_execution: dict[str, Any],
    cycle_id: str,
) -> dict[str, Any]:
    """Record the outcome of the 10:30 *new* PM cycle for queued tickers."""
    db = get_db()
    pending = await db.stale_execution_retries.find(
        {"status": "PENDING_1030_FULL_RESCAN"}, {"_id": 0}
    ).to_list(500)
    if not pending:
        return {"reviewed": 0, "retried": 0, "executed": 0}
    current = {str(row.get("ticker") or "").upper(): row for row in pm_rows}
    executed = {str(row.get("ticker") or "").upper() for row in equity_execution.get("executed") or []}
    rejected = {str(row.get("ticker") or "").upper(): row for row in equity_execution.get("rejected") or [] if isinstance(row, dict)}
    retried = completed = 0
    for item in pending:
        ticker = str(item.get("ticker") or "").upper()
        pm_row = current.get(ticker)
        if not pm_row:
            status = "NOT_REDISCOVERED_1030"
        elif str(pm_row.get("action") or "").upper() not in {"STARTER", "ACCUMULATE"}:
            status = "NOT_REAPPROVED_1030"
        else:
            retried += 1
            if ticker in executed:
                status = "EXECUTED_1030"
                completed += 1
            else:
                failure = rejected.get(ticker) or {}
                status = "STALE_AGAIN_1030" if _is_stale_public_entry_rejection(failure.get("reason")) else "REJECTED_1030"
        await db.stale_execution_retries.update_one(
            {"retry_key": item.get("retry_key")},
            {"$set": {"status": status, "retry_cycle_id": cycle_id, "resolved_at": _now().isoformat()}},
        )
    return {"reviewed": len(pending), "retried": retried, "executed": completed}


async def _persist_execution_funnel(
    funnel,
    *,
    cycle_id: str,
    pm_recommendations: list[dict[str, Any]],
    equity_execution: dict[str, Any],
    options_payload: dict[str, Any],
    options_execution: dict[str, Any],
    observed_at: str | None,
) -> dict[str, Any]:
    payload = funnel.build_cycle(
        cycle_id=cycle_id,
        pm_recommendations=pm_recommendations,
        equity_execution=equity_execution,
        options_payload=options_payload,
        options_execution=options_execution,
        observed_at=observed_at,
    )
    await funnel.persist(payload)
    return payload


async def _run_full_terminal_scan(triggered_by: str = "full_terminal") -> dict[str, Any]:
    started = _now()
    stage_times: dict[str, float] = {}

    async def timed(label: str, awaitable) -> Any:
        t0 = _now()
        try:
            return await awaitable
        finally:
            stage_times[label] = round((_now() - t0).total_seconds(), 2)

    await log_activity(f"Full terminal scan started ({triggered_by})", "info")
    from . import candidate_ledger, lottery, options_desk, pharma, pm_brain, portfolio_manager, scanner, strategy_contracts, strategy_screeners

    core_task = asyncio.create_task(
        timed(
            "core_scan",
            scanner.run_scan(triggered_by=triggered_by, auto_execute=False, run_sidecars=False),
        )
    )
    lottery_task = asyncio.create_task(timed("lottery_scan", lottery.run_dedicated_lottery_scan(triggered_by=triggered_by)))
    # The full-cycle report owns scheduled Telegram delivery.  The underlying
    # scanners still persist and return their findings, but must not fan out
    # separate alert messages for the same cycle.
    pharma_task = asyncio.create_task(timed("pharma_scan", pharma.run_pharma_scan(triggered_by=triggered_by, notify=False)))
    shock_task = asyncio.create_task(timed("pharma_shock_scan", pharma.run_catalyst_shock_scan(triggered_by=triggered_by, force_refresh=True, notify=False)))

    scan = await core_task
    # Core scans predate full-cycle orchestration and may not carry an id. A
    # full cycle must always have one so execution idempotency and replay point
    # to the same immutable PM packet.
    scan["cycle_id"] = scan.get("cycle_id") or f"{triggered_by}:{scan.get('finished_at') or started.isoformat()}"
    family_results = await asyncio.gather(lottery_task, pharma_task, shock_task, return_exceptions=True)
    lottery_result = family_results[0] if not isinstance(family_results[0], Exception) else {"ok": False, "error": str(family_results[0])}
    pharma_result = family_results[1] if not isinstance(family_results[1], Exception) else {"ok": False, "error": str(family_results[1])}
    pharma_shock_result = family_results[2] if not isinstance(family_results[2], Exception) else {"ok": False, "error": str(family_results[2])}

    strategy_payload = await timed(
        "strategy_screeners",
        strategy_screeners.run_all(scan=scan, persist=True, lottery_result=lottery_result),
    )
    try:
        from . import pnl_tracker
        await pnl_tracker.record_scan_picks(
            {
                "results": strategy_payload.get("candidates") or [],
                "cycle_id": scan.get("cycle_id"),
                "scan_finished_at": scan.get("finished_at"),
            },
            include_first_seen=False,
        )
    except Exception as exc:
        await log_activity(f"Strategy performance ledger failed: {exc.__class__.__name__}", "warning")
    # Keep the exact specialist output attached to this cycle. Reporting and
    # execution must consume this result rather than recomputing it later.
    scan["strategy_payload"] = strategy_payload
    strategy_pm_rows = [
        row for row in strategy_payload.get("candidates") or []
        if row.get("pm_routable") and not row.get("read_only")
    ]
    lottery_strategy_rows = [
        row for row in strategy_payload.get("candidates") or []
        if str((row.get("strategy_scanner") or {}).get("family") or "").upper() == "LOTTERY"
    ]
    lottery_tickers = {
        str(row.get("ticker") or "").upper()
        for row in lottery_strategy_rows
        if row.get("ticker")
    }
    lottery_summary = {
        "raw_candidates": len(lottery_result.get("candidates") or []),
        "qualified_rows": len(lottery_strategy_rows),
        "qualified_tickers": len(lottery_tickers),
        "pm_actions": {},
        "pm_approved": 0,
        "execution_note": "PM-approved lottery rows still require live execution gates and broker checks",
    }
    scan["lottery_result"] = lottery_result
    scan["pharma_result"] = pharma_result
    scan["pharma_shock_result"] = pharma_shock_result
    pm_payload = await timed(
        "portfolio_manager",
        portfolio_manager.latest_portfolio_plan(
            scan=scan,
            strategy_payload={
                **strategy_payload,
                "rows": [
                    row for row in strategy_payload.get("candidates") or []
                    if row.get("pm_routable") and not row.get("read_only")
                ],
            },
            lottery_result=lottery_result,
        ),
    )
    # PM memory is written from this frozen decision packet before execution.
    # It records the rationale of both traded and untraded decisions without
    # granting new execution authority.
    try:
        pm_brain_result = await timed(
            "pm_brain_memory",
            pm_brain.record_pm_cycle(
                pm_payload,
                cycle_id=str(scan["cycle_id"]),
                observed_at=pm_payload.get("generated_at") or scan.get("finished_at"),
            ),
        )
    except Exception as exc:
        pm_brain_result = {"ok": False, "reason": f"pm_memory_persist_failed:{exc.__class__.__name__}"}
        await log_activity("PM brain memory persistence failed", "warning", pm_brain_result)
    pm_payload["pm_brain"] = pm_brain_result
    for pm in pm_payload.get("recommendations") or []:
        ticker = str(pm.get("ticker") or "").upper()
        if ticker not in lottery_tickers:
            continue
        action = str(pm.get("action") or "MISSING").upper()
        lottery_summary["pm_actions"][action] = lottery_summary["pm_actions"].get(action, 0) + 1
        if action in {"ACCUMULATE", "STARTER"}:
            lottery_summary["pm_approved"] += 1
    # The Options Desk consumes this cycle's authoritative PM recommendations.
    # It selects and validates contracts, but must not recompute PM routing.
    options_payload = await timed(
        "options_desk_candidates",
        options_desk.build_candidates(
            limit=100,
            persist=True,
            scan=scan,
            pm_recommendations=pm_payload.get("recommendations") or [],
        ),
    )
    # This is a read model of the frozen cycle outputs. Passing the already
    # computed packets prevents the ledger from re-running scanners, calendars,
    # and PM routing with potentially different data mid-cycle.
    ledger_payload = await timed(
        "candidate_ledger",
        candidate_ledger.build_from_scan(
            scan=scan,
            include_external=True,
            persist=True,
            strategy_payload=strategy_payload,
            options_payload=options_payload,
            pharma_payload=pharma_result,
            pm_payload=pm_payload,
            include_earnings=False,
        ),
    )
    # This compares target, lifecycle, and selected option evidence without
    # changing the PM decision or either broker execution path.
    strategy_contract_payload = await timed(
        "strategy_contracts_shadow",
        strategy_contracts.build_shadow_contracts(
            pm_payload.get("recommendations") or [],
            options_payload=options_payload,
            cycle_id=str(scan["cycle_id"]),
            observed_at=pm_payload.get("generated_at") or scan.get("finished_at"),
            persist=True,
        ),
    )
    scan["options_payload"] = options_payload
    scan["strategy_contracts_shadow"] = strategy_contract_payload
    scan["pm_payload"] = pm_payload
    scan["lottery_summary"] = lottery_summary

    equity_execution: dict[str, Any] = {"skipped": True, "reason": "ENABLE_TRADE_EXECUTION is off"}
    execution_freshness: dict[str, Any] = {"skipped": True, "reason": "public_equity_execution_not_enabled"}
    if os.environ.get("ENABLE_TRADE_EXECUTION", "false").strip().lower() in {"1", "true", "yes", "on"}:
        from . import public_execution
        if pm_payload.get("scan_finished_at") != scan.get("finished_at"):
            equity_execution = {"skipped": True, "reason": "pm_scan_mismatch", "executed": [], "rejected": []}
        else:
            if public_execution.enabled():
                execution_freshness = await timed(
                    "execution_freshness",
                    public_execution.refresh_execution_freshness(pm_payload.get("recommendations") or []),
                )
                equity_execution = await timed("public_equity_execution", public_execution.execute_pm_equity(pm_payload.get("recommendations") or [], cycle_id=scan.get("cycle_id")))
            else:
                # Public is the sole equity broker. Alpaca is intentionally
                # isolated to the options desk and must never become an equity
                # execution fallback when Public is unavailable or disabled.
                equity_execution = {
                    "skipped": True,
                    "reason": "public_equity_execution_not_enabled_no_alpaca_fallback",
                    "executed": [],
                    "rejected": [],
                }

    options_execution: dict[str, Any] = {"skipped": True, "reason": "ENABLE_OPTIONS_EXECUTION is off"}
    if options_desk.options_execution_enabled():
        options_execution = await timed(
            "options_execution",
            options_desk.auto_execute_latest(candidate_set=options_payload),
        )

    stale_retry_queue = await timed(
        "stale_execution_retry_queue",
        _persist_stale_execution_retries(
            pm_rows=pm_payload.get("recommendations") or [],
            equity_execution=equity_execution,
            cycle_id=str(scan["cycle_id"]),
            observed_at=scan.get("finished_at"),
        ),
    )
    stale_retry_resolution = {"reviewed": 0, "retried": 0, "executed": 0}
    if triggered_by == "stale_retry_scan_1030":
        stale_retry_resolution = await timed(
            "stale_execution_retry_resolution",
            _resolve_stale_execution_retries(
                pm_rows=pm_payload.get("recommendations") or [],
                equity_execution=equity_execution,
                cycle_id=str(scan["cycle_id"]),
            ),
        )

    from . import execution_funnel
    execution_funnel_payload = await timed(
        "execution_funnel",
        _persist_execution_funnel(
            execution_funnel,
            cycle_id=str(scan["cycle_id"]),
            pm_recommendations=pm_payload.get("recommendations") or [],
            equity_execution=equity_execution,
            options_payload=options_payload,
            options_execution=options_execution,
            observed_at=scan.get("finished_at"),
        ),
    )

    scan["execution_summary"] = {
        "equity_status": "SKIPPED" if equity_execution.get("skipped") else "ATTEMPTED",
        "equity_skip_reason": equity_execution.get("reason") if equity_execution.get("skipped") else None,
        "equity_executed": len(equity_execution.get("executed") or []),
        "equity_queued": len(equity_execution.get("queued") or []),
        "equity_rejected": len(equity_execution.get("rejected") or []),
        "equity_rejection_reason_counts": equity_execution.get("rejection_reason_counts") or _reason_counts(equity_execution.get("rejected")),
        "equity_rejected_sample": (equity_execution.get("rejected") or [])[:8],
        "equity_freshness": execution_freshness,
        "equity_submitted_rows": equity_execution.get("executed") or [],
        "stale_execution_retry_queue": stale_retry_queue,
        "stale_execution_retry_resolution": stale_retry_resolution,
        "options_ready": options_execution.get("ready"),
        "options_submitted": _row_count(options_execution.get("submitted")),
        "options_skipped": _row_count(options_execution.get("skipped")),
        "options_submitted_rows": options_execution.get("submitted") or [],
        "options_skipped_sample": (options_execution.get("skipped") if isinstance(options_execution.get("skipped"), list) else [])[:8],
        "funnel": execution_funnel_payload.get("summary") or {},
    }
    telegram_result: dict[str, Any] = {"skipped": True, "reason": "telegram_env_missing"}
    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        from . import telegram_events
        scan["telegram_report_variant"] = "full_terminal"
        telegram_result = await timed("telegram_dispatch", telegram_events.dispatch_scan_report(scan))

    # scanner.run_scan persists its core document before the specialist/PM
    # stages exist. Update that same document so the latest scan is a complete,
    # replayable decision record rather than a misleading core-only snapshot.
    try:
        await get_db().scan_results.update_one(
            {"finished_at": scan.get("finished_at")},
            {"$set": {
                "full_cycle_finished_at": _now().isoformat(),
                "strategy_payload": strategy_payload,
                "lottery_result": lottery_result,
                "lottery_summary": lottery_summary,
                "pharma_result": pharma_result,
                "pharma_shock_result": pharma_shock_result,
                "options_payload": options_payload,
                "strategy_contracts_shadow": strategy_contract_payload,
                "pm_payload": pm_payload,
                "execution_summary": scan.get("execution_summary"),
                "execution_funnel": execution_funnel_payload,
                "execution_freshness": execution_freshness,
                "stale_execution_retry_queue": stale_retry_queue,
                "stale_execution_retry_resolution": stale_retry_resolution,
                "telegram_report_variant": scan.get("telegram_report_variant"),
            }},
        )
    except Exception as exc:
        await log_activity(f"Full terminal cycle persistence failed: {exc.__class__.__name__}", "warning")

    finished = _now()
    duration = round((finished - started).total_seconds(), 2)
    summary = {
        "core_results": len(scan.get("results") or []),
        "lottery_candidates": lottery_result.get("count") if isinstance(lottery_result, dict) else None,
        "strategy_candidates": (strategy_payload.get("summary") or {}).get("total"),
        "pm_routable": (strategy_payload.get("summary") or {}).get("pm_routable"),
        "ledger_candidates": (ledger_payload.get("summary") or {}).get("total") or len(ledger_payload.get("candidates") or []),
        "options_candidates": len(options_payload.get("candidates") or []),
        "strategy_shadow_contracts": (strategy_contract_payload.get("summary") or {}).get("total"),
        "pm_actions": (pm_payload.get("summary") or {}),
        "equity_executed": len(equity_execution.get("executed") or []),
        "equity_rejected": len(equity_execution.get("rejected") or []),
        "stale_execution_retry_queued": stale_retry_queue.get("queued", 0),
        "stale_execution_retry_executed": stale_retry_resolution.get("executed", 0),
        "options_submitted": _row_count(options_execution.get("submitted")),
        "execution_funnel": execution_funnel_payload.get("summary") or {},
        "options_skipped": _row_count(options_execution.get("skipped")),
        "pharma_rows": len(pharma_result.get("results") or []) if isinstance(pharma_result, dict) else None,
        "pharma_shocks": pharma_shock_result.get("candidate_count") if isinstance(pharma_shock_result, dict) else None,
    }
    await log_activity("Full terminal scan completed", "info", {"duration_sec": duration, "stage_times": stage_times, "summary": summary})
    return {
        "ok": True,
        "triggered_by": triggered_by,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_sec": duration,
        "stage_times": stage_times,
        "scan_finished_at": scan.get("finished_at"),
        "summary": summary,
        "scan": {
            "results": scan.get("results") or [],
            "pre_filter_passed": scan.get("pre_filter_passed"),
            "raw_counts": scan.get("raw_counts") or {},
            "freshness": scan.get("freshness") or {},
        },
        "lottery": lottery_result,
        "strategy_screeners": strategy_payload.get("summary") or {},
        "candidate_ledger": ledger_payload.get("summary") or {},
        "options_desk": options_payload.get("summary") or {},
        "strategy_contracts": strategy_contract_payload.get("summary") or {},
        "portfolio_manager": pm_payload.get("summary") or {},
        "equity_execution": equity_execution,
        "execution_freshness": execution_freshness,
        "stale_execution_retry_queue": stale_retry_queue,
        "stale_execution_retry_resolution": stale_retry_resolution,
        "options_execution": options_execution,
        "execution_funnel": execution_funnel_payload,
        "telegram": telegram_result,
    }


async def run_full_terminal_scan(triggered_by: str = "full_terminal") -> dict[str, Any]:
    """Run one full cycle at a time; concurrent manual/scheduled calls skip."""
    if _FULL_SCAN_LOCK.locked():
        return {"ok": False, "skipped": True, "reason": "full_terminal_scan_already_running", "triggered_by": triggered_by}
    async with _FULL_SCAN_LOCK:
        return await _run_full_terminal_scan(triggered_by=triggered_by)
