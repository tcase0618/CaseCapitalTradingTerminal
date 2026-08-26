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

from .db import log_activity


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _row_count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


async def run_full_terminal_scan(triggered_by: str = "full_terminal") -> dict[str, Any]:
    started = _now()
    stage_times: dict[str, float] = {}

    async def timed(label: str, awaitable) -> Any:
        t0 = _now()
        try:
            return await awaitable
        finally:
            stage_times[label] = round((_now() - t0).total_seconds(), 2)

    await log_activity(f"Full terminal scan started ({triggered_by})", "info")
    from . import candidate_ledger, lottery, options_desk, pharma, portfolio_manager, scanner, strategy_screeners

    core_task = asyncio.create_task(timed("core_scan", scanner.run_scan(triggered_by=triggered_by, auto_execute=False)))
    lottery_task = asyncio.create_task(timed("lottery_scan", lottery.run_dedicated_lottery_scan(triggered_by=triggered_by)))
    pharma_task = asyncio.create_task(timed("pharma_scan", pharma.run_pharma_scan(triggered_by=triggered_by)))
    shock_task = asyncio.create_task(timed("pharma_shock_scan", pharma.run_catalyst_shock_scan(triggered_by=triggered_by, force_refresh=True)))

    scan = await core_task
    family_results = await asyncio.gather(lottery_task, pharma_task, shock_task, return_exceptions=True)
    lottery_result = family_results[0] if not isinstance(family_results[0], Exception) else {"ok": False, "error": str(family_results[0])}
    pharma_result = family_results[1] if not isinstance(family_results[1], Exception) else {"ok": False, "error": str(family_results[1])}
    pharma_shock_result = family_results[2] if not isinstance(family_results[2], Exception) else {"ok": False, "error": str(family_results[2])}

    strategy_payload = await timed("strategy_screeners", strategy_screeners.run_all(scan=scan, persist=True))
    # Keep the exact specialist output attached to this cycle. Reporting and
    # execution must consume this result rather than recomputing it later.
    scan["strategy_payload"] = strategy_payload
    ledger_payload = await timed("candidate_ledger", candidate_ledger.build_from_scan(scan=scan, include_external=True, persist=True))
    options_payload = await timed("options_desk_candidates", options_desk.build_candidates(limit=100, persist=True))
    pm_payload = await timed("portfolio_manager", portfolio_manager.latest_portfolio_plan())

    equity_execution: dict[str, Any] = {"skipped": True, "reason": "ENABLE_TRADE_EXECUTION is off"}
    if os.environ.get("ENABLE_TRADE_EXECUTION", "false").strip().lower() in {"1", "true", "yes", "on"}:
        from . import trade_floor
        if pm_payload.get("scan_finished_at") != scan.get("finished_at"):
            equity_execution = {"skipped": True, "reason": "pm_scan_mismatch", "executed": [], "rejected": []}
        else:
            equity_execution = await timed(
                "equity_execution",
                trade_floor.evaluate_and_execute(
                    scan.get("results") or [],
                    pm_rows=pm_payload.get("recommendations") or [],
                    pm_scan_finished_at=pm_payload.get("scan_finished_at"),
                ),
            )

    options_execution: dict[str, Any] = {"skipped": True, "reason": "ENABLE_OPTIONS_EXECUTION is off"}
    if options_desk.options_execution_enabled():
        options_execution = await timed("options_execution", options_desk.auto_execute_latest())

    telegram_result: dict[str, Any] = {"skipped": True, "reason": "telegram_env_missing"}
    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        from . import telegram_events
        scan["telegram_report_variant"] = "full_terminal"
        scan["execution_summary"] = {
            "equity_executed": len(equity_execution.get("executed") or []),
            "equity_rejected": len(equity_execution.get("rejected") or []),
            "equity_rejected_sample": (equity_execution.get("rejected") or [])[:8],
            "options_ready": options_execution.get("ready"),
            "options_submitted": _row_count(options_execution.get("submitted")),
            "options_skipped": _row_count(options_execution.get("skipped")),
            "options_submitted_rows": options_execution.get("submitted") or [],
            "options_skipped_sample": (options_execution.get("skipped") if isinstance(options_execution.get("skipped"), list) else [])[:8],
        }
        telegram_result = await timed("telegram_dispatch", telegram_events.dispatch_scan_report(scan))

    finished = _now()
    duration = round((finished - started).total_seconds(), 2)
    summary = {
        "core_results": len(scan.get("results") or []),
        "lottery_candidates": lottery_result.get("count") if isinstance(lottery_result, dict) else None,
        "strategy_candidates": (strategy_payload.get("summary") or {}).get("total"),
        "pm_routable": (strategy_payload.get("summary") or {}).get("pm_routable"),
        "ledger_candidates": (ledger_payload.get("summary") or {}).get("total") or len(ledger_payload.get("candidates") or []),
        "options_candidates": len(options_payload.get("candidates") or []),
        "pm_actions": (pm_payload.get("summary") or {}),
        "equity_executed": len(equity_execution.get("executed") or []),
        "equity_rejected": len(equity_execution.get("rejected") or []),
        "options_submitted": _row_count(options_execution.get("submitted")),
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
        "portfolio_manager": pm_payload.get("summary") or {},
        "equity_execution": equity_execution,
        "options_execution": options_execution,
        "telegram": telegram_result,
    }
