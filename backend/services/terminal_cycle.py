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

    core_task = asyncio.create_task(timed("core_scan", scanner.run_scan(triggered_by=triggered_by)))
    lottery_task = asyncio.create_task(timed("lottery_scan", lottery.run_dedicated_lottery_scan(triggered_by=triggered_by)))
    pharma_task = asyncio.create_task(timed("pharma_scan", pharma.run_pharma_scan(triggered_by=triggered_by)))
    shock_task = asyncio.create_task(timed("pharma_shock_scan", pharma.run_catalyst_shock_scan(triggered_by=triggered_by, force_refresh=True)))

    scan = await core_task
    family_results = await asyncio.gather(lottery_task, pharma_task, shock_task, return_exceptions=True)
    lottery_result = family_results[0] if not isinstance(family_results[0], Exception) else {"ok": False, "error": str(family_results[0])}
    pharma_result = family_results[1] if not isinstance(family_results[1], Exception) else {"ok": False, "error": str(family_results[1])}
    pharma_shock_result = family_results[2] if not isinstance(family_results[2], Exception) else {"ok": False, "error": str(family_results[2])}

    strategy_payload = await timed("strategy_screeners", strategy_screeners.run_all(scan=scan, persist=True))
    ledger_payload = await timed("candidate_ledger", candidate_ledger.build_from_scan(scan=scan, include_external=True, persist=True))
    options_payload = await timed("options_desk_candidates", options_desk.build_candidates(limit=100, persist=True))
    pm_payload = await timed("portfolio_manager", portfolio_manager.latest_portfolio_plan())

    telegram_result: dict[str, Any] = {"skipped": True, "reason": "telegram_env_missing"}
    if os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        from . import telegram_events
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
        "telegram": telegram_result,
    }
