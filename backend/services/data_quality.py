"""Terminal-wide data quality control.

The QC layer does not replace source-specific services. It gives the terminal
one fast place to ask: what data is fresh enough to trust, what is display-only,
and what should block trading until refreshed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from .db import get_db, log_activity, stamped


CRITICAL_MAX_AGE_MINUTES = {
    "live_positions": 15,
    "options_risk": 15,
    "latest_scan": 480,
    "system_health": 5,
}

REMEDIATION_TIMEOUT_SECONDS = 24.0
ATTEMPT_TIMEOUT_SECONDS = 8.0
INTEGRATION_CACHE_TTL_SECONDS = 180.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _market_day_now_et() -> bool:
    """Fast local market-day guard for QC.

    Scheduler still uses Alpaca calendar for the actual scan decision. QC should
    not hard-block the terminal on weekends because the stock scan is correctly
    skipped then, while positions/options/news can continue refreshing.
    """
    try:
        from zoneinfo import ZoneInfo

        now_et = datetime.now(ZoneInfo("America/New_York"))
        return now_et.weekday() < 5
    except Exception:
        return True


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _age_minutes(value: Any) -> float | None:
    dt = _parse_dt(value)
    if not dt:
        return None
    return max(0.0, (_now() - dt).total_seconds() / 60.0)


def _status_from_age(age: float | None, max_age: float) -> tuple[str, bool]:
    if age is None:
        return "MISSING", False
    if age <= max_age:
        return "LIVE", True
    if age <= max_age * 2:
        return "STALE", False
    return "DOWN", False


def _score_row(status: str, critical: bool, warnings: int = 0) -> int:
    base = {
        "LIVE": 100,
        "WARN": 78,
        "FALLBACK": 62,
        "STALE": 45,
        "MISSING": 20,
        "DOWN": 0,
    }.get(status, 40)
    if critical and status in {"STALE", "MISSING", "DOWN"}:
        base = min(base, 25)
    return max(0, min(100, base - warnings * 6))


def _qc_row(
    key: str,
    label: str,
    status: str,
    *,
    critical: bool,
    source: str,
    fetched_at: Any = None,
    age_minutes: float | None = None,
    detail: str = "",
    warnings: list[str] | None = None,
    refresh: str | None = None,
    blocks_trading: bool | None = None,
    auto_fix: str | None = None,
    execution_scopes: list[str] | None = None,
) -> dict[str, Any]:
    warnings = warnings or []
    blocks = bool(blocks_trading) if blocks_trading is not None else bool(critical and status in {"STALE", "MISSING", "DOWN"})
    return {
        "key": key,
        "label": label,
        "status": status,
        "score": _score_row(status, critical, len(warnings)),
        "critical": critical,
        "blocks_trading": blocks,
        "execution_scopes": execution_scopes or (["system"] if blocks else []),
        "source": source,
        "fetched_at": fetched_at,
        "age_minutes": round(age_minutes, 2) if age_minutes is not None else None,
        "detail": detail,
        "warnings": warnings,
        "refresh_endpoint": refresh,
        "auto_fix": auto_fix or _default_auto_fix(key, status, critical),
    }


def _default_auto_fix(key: str, status: str, critical: bool) -> str:
    if status == "LIVE":
        return "none_needed"
    if key in {"live_positions", "options_risk"}:
        return "instant_repull"
    if key == "latest_scan":
        return "bounded_scan_refresh"
    if critical and status in {"STALE", "MISSING", "DOWN"}:
        return "critical_probe"
    if status == "FALLBACK":
        return "provider_or_config_limited"
    if status == "WARN":
        return "probe_and_explain"
    return "manual_review"


async def _latest_scan_row() -> dict[str, Any]:
    db = get_db()
    state = await db.bot_state.find_one({"_id": "state"}, {"_id": 0}) or {}
    scan = await db.scan_results.find_one({}, {"_id": 0, "finished_at": 1, "results": 1, "pre_filter_passed": 1}, sort=[("finished_at", -1)])
    finished = state.get("last_scan_at") or (scan or {}).get("finished_at")
    age = _age_minutes(finished)
    status, _ = _status_from_age(age, CRITICAL_MAX_AGE_MINUTES["latest_scan"])
    count = len((scan or {}).get("results") or [])
    market_day = _market_day_now_et()
    blocks = status in {"MISSING", "DOWN"} or (market_day and status == "STALE")
    warnings = []
    detail = f"{count} latest rows"
    if not market_day and status in {"STALE", "DOWN"}:
        status = "WARN"
        warnings.append("stock scan stale while market is closed; scheduler should refresh on next market day")
        detail = f"{count} latest rows; market closed so stale stock scan is not execution-critical"
    return _qc_row(
        "latest_scan",
        "Latest Scanner Evidence",
        status,
        critical=True,
        source="scan_results",
        fetched_at=finished,
        age_minutes=age,
        detail=detail,
        warnings=warnings,
        refresh="/api/scan/run",
        blocks_trading=blocks,
        execution_scopes=["equity", "options"] if blocks else [],
    )


async def _live_positions_row() -> tuple[dict[str, Any], dict[str, Any]]:
    db = get_db()
    latest = await db.bot_state.find_one({"_id": "live_position_snapshot_latest"}, {"_id": 0}) or {}
    fetched = latest.get("snapshot_at") or latest.get("created_at")
    age = _age_minutes(fetched)
    status, _ = _status_from_age(age, CRITICAL_MAX_AGE_MINUTES["live_positions"])
    totals = latest.get("totals") or {}
    detail = f"{totals.get('positions', 0)} positions / {totals.get('open_orders', 0)} open orders"
    row = _qc_row(
        "live_positions",
        "Live Position Authority",
        status,
        critical=True,
        source="alpaca_positions:equities+options",
        fetched_at=fetched,
        age_minutes=age,
        detail=detail,
        refresh="/api/position_monitor/refresh",
        execution_scopes=["equity", "options"] if status in {"STALE", "MISSING", "DOWN"} else [],
    )
    return row, latest


async def _options_risk_row() -> dict[str, Any]:
    db = get_db()
    risk = await db.options_desk_risk_checks.find_one({}, {"_id": 0}, sort=[("checked_at", -1)]) or {}
    fetched = risk.get("checked_at") or risk.get("created_at")
    age = _age_minutes(fetched)
    status, _ = _status_from_age(age, CRITICAL_MAX_AGE_MINUTES["options_risk"])
    checks = risk.get("checks") or []
    conflicts = [c for c in checks if c.get("data_conflict")]
    hard_stops = [c for c in checks if c.get("hard_stop_triggered")]
    warnings = []
    if conflicts:
        warnings.append(f"{len(conflicts)} option position/snapshot conflict")
    if hard_stops:
        warnings.append(f"{len(hard_stops)} option hard-stop condition")
    if status == "LIVE" and conflicts:
        status = "WARN"
    return _qc_row(
        "options_risk",
        "Options Risk Marks",
        status,
        critical=True,
        source="alpaca_positions_first",
        fetched_at=fetched,
        age_minutes=age,
        detail=f"{len(checks)} option positions checked",
        warnings=warnings,
        refresh="/api/options_desk/risk/check",
        blocks_trading=status in {"STALE", "MISSING", "DOWN"},
        execution_scopes=["options"] if status in {"STALE", "MISSING", "DOWN"} else [],
    )


async def _integration_rows(force_probe: bool = False) -> list[dict[str, Any]]:
    from . import integration_status as integration_svc

    db = get_db()
    if not force_probe:
        cached = await db.bot_state.find_one({"_id": "data_quality_integrations_cache"}, {"_id": 0}) or {}
        cache_age = _age_minutes(cached.get("generated_at"))
        if cached.get("rows") and cache_age is not None and cache_age * 60.0 <= INTEGRATION_CACHE_TTL_SECONDS:
            return cached.get("rows") or []

    rows = []
    try:
        integrations = await asyncio.wait_for(integration_svc.integration_status(), timeout=12.0)
    except Exception as exc:
        cached = await db.bot_state.find_one({"_id": "data_quality_integrations_cache"}, {"_id": 0}) or {}
        if cached.get("rows"):
            cached_rows = cached.get("rows") or []
            for row in cached_rows:
                row.setdefault("warnings", [])
                row["warnings"] = list(row.get("warnings") or []) + [f"integration probe timed out; using cached QC rows: {str(exc)[:120]}"]
            return cached_rows
        return [_qc_row(
            "integrations",
            "Integration Probe",
            "DOWN",
            critical=True,
            source="integration_status",
            detail=str(exc)[:160],
            warnings=["integration status probe failed"],
        )]
    for item in integrations:
        quality = str(item.get("quality") or "").upper()
        if quality == "LIVE":
            status = "LIVE"
        elif quality == "FALLBACK":
            status = "FALLBACK"
        elif quality == "OPTIONAL":
            status = "WARN"
        elif quality == "UNCHECKED":
            status = "WARN"
        else:
            status = "DOWN"
        critical = item.get("key") in {"alpaca", "price_path", "edgar"}
        blocks = critical and status == "DOWN"
        scopes = []
        if blocks and item.get("key") in {"alpaca", "price_path"}:
            scopes = ["equity"]
        elif blocks:
            scopes = ["equity", "options"]
        rows.append(_qc_row(
            f"integration:{item.get('key')}",
            item.get("name") or item.get("key"),
            status,
            critical=critical,
            source=item.get("key") or "integration",
            fetched_at=item.get("last"),
            age_minutes=_age_minutes(item.get("last")),
            detail=str(item.get("detail") or item.get("reason") or "")[:180],
            warnings=[item.get("reason")] if item.get("reason") else [],
            blocks_trading=blocks,
            execution_scopes=scopes,
        ))
    await db.bot_state.update_one(
        {"_id": "data_quality_integrations_cache"},
        {"$set": stamped({"generated_at": _now_iso(), "rows": rows})},
        upsert=True,
    )
    return rows


async def _latest_remediation() -> dict[str, Any] | None:
    db = get_db()
    return await db.data_quality_events.find_one(
        {"event_type": "quality_remediation"},
        {"_id": 0},
        sort=[("created_at", -1)],
    )


def _needs_remediation(row: dict[str, Any]) -> bool:
    status = row.get("status")
    return bool(status in {"WARN", "FALLBACK", "STALE", "MISSING", "DOWN"} or row.get("warnings"))


async def _bounded_attempt(coro: Any, timeout: float = ATTEMPT_TIMEOUT_SECONDS) -> Any:
    return await asyncio.wait_for(coro, timeout=timeout)


def _attempt_base(row: dict[str, Any], action: str) -> dict[str, Any]:
    return {
        "key": row.get("key"),
        "label": row.get("label"),
        "before_status": row.get("status"),
        "before_score": row.get("score"),
        "critical": bool(row.get("critical")),
        "blocks_trading_before": bool(row.get("blocks_trading")),
        "action": action,
        "started_at": _now_iso(),
        "outcome": "pending",
        "detail": "",
        "trading_impact": "none",
    }


def _finish_attempt(
    attempt: dict[str, Any],
    outcome: str,
    detail: str,
    *,
    trading_impact: str = "none",
    payload: Any = None,
) -> dict[str, Any]:
    attempt["finished_at"] = _now_iso()
    attempt["outcome"] = outcome
    attempt["detail"] = str(detail or "")[:500]
    attempt["trading_impact"] = trading_impact
    if payload is not None:
        attempt["payload"] = payload
    return attempt


async def _attempt_remediation(row: dict[str, Any]) -> dict[str, Any]:
    key = str(row.get("key") or "")
    source_key = key.split("integration:", 1)[1] if key.startswith("integration:") else key
    attempt = _attempt_base(row, "probe")

    try:
        if key == "live_positions":
            attempt["action"] = "repull_equity_and_option_positions"
            from . import scheduler

            payload = await _bounded_attempt(
                scheduler.persist_live_position_snapshot(triggered_by="quality_auto_remediation")
            )
            totals = (payload or {}).get("totals") or {}
            return _finish_attempt(
                attempt,
                "repulled",
                f"{totals.get('positions', 0)} positions / {totals.get('open_orders', 0)} open orders refreshed",
                trading_impact="critical_cache_refreshed",
                payload={"snapshot_at": (payload or {}).get("snapshot_at"), "totals": totals},
            )

        if key == "options_risk":
            attempt["action"] = "recheck_options_positions_from_alpaca"
            from . import options_desk

            payload = await _bounded_attempt(options_desk.monitor_open_positions(enforce_hard_stop=False))
            checks = (payload or {}).get("checks") or []
            conflicts = [c for c in checks if c.get("data_conflict")]
            hard_stops = [c for c in checks if c.get("hard_stop_triggered")]
            if hard_stops:
                return _finish_attempt(
                    attempt,
                    "risk_condition_confirmed",
                    f"{len(hard_stops)} option hard-stop condition(s) confirmed; this is risk state, not stale data",
                    trading_impact="risk_monitor_updated",
                )
            if conflicts:
                return _finish_attempt(
                    attempt,
                    "provider_conflict_confirmed",
                    f"{len(conflicts)} option position/snapshot conflict(s); Alpaca position mark remains authority",
                    trading_impact="risk_monitor_updated",
                )
            return _finish_attempt(
                attempt,
                "rechecked_clean",
                f"{len(checks)} option position(s) checked from Alpaca position authority",
                trading_impact="risk_monitor_updated",
            )

        if key == "latest_scan":
            attempt["action"] = "refresh_latest_scanner_evidence"
            from . import scanner

            payload = await _bounded_attempt(scanner.run_scan(triggered_by="quality_auto_remediation"), timeout=18.0)
            return _finish_attempt(
                attempt,
                "refreshed",
                f"{len((payload or {}).get('results') or [])} scan rows produced",
                trading_impact="scanner_cache_refreshed",
                payload={"finished_at": (payload or {}).get("finished_at"), "results": len((payload or {}).get("results") or [])},
            )

        if source_key == "alpaca":
            attempt["action"] = "probe_equity_alpaca_account"
            from . import trade_floor

            acct = await _bounded_attempt(trade_floor.get_account())
            return _finish_attempt(
                attempt,
                "live" if acct else "still_down",
                "Equity Alpaca account probe succeeded" if acct else "Equity Alpaca account probe returned no account",
                trading_impact="critical_provider_checked",
                payload={"equity": (acct or {}).get("equity"), "cash": (acct or {}).get("cash")} if acct else None,
            )

        if source_key == "price_path":
            attempt["action"] = "probe_configured_price_path"
            from . import pricer

            label = pricer.source_label()
            return _finish_attempt(
                attempt,
                "live" if label.startswith("alpaca") else "fallback_confirmed",
                f"Configured price path is {label}",
                trading_impact="critical_provider_checked" if label.startswith("alpaca") else "display_or_secondary_fallback",
            )

        if source_key == "london_strategic_edge":
            attempt["action"] = "probe_lse_primary_provider"
            from . import london_strategic_edge as lse_svc

            payload = await _bounded_attempt(lse_svc.health_probe())
            return _finish_attempt(
                attempt,
                "live" if payload.get("ok") else "still_down",
                payload.get("reason") or "London Strategic Edge provider probe succeeded",
                trading_impact="primary_market_provider_checked",
                payload=payload,
            )

        if source_key == "edgar":
            attempt["action"] = "poll_sec_edgar"
            from . import sec_filings

            payload = await _bounded_attempt(sec_filings.poll_edgar_filings())
            return _finish_attempt(
                attempt,
                "refreshed" if payload.get("ok", True) else "still_down",
                f"EDGAR poll completed with {payload.get('count', payload.get('saved', 0))} item(s)",
                trading_impact="research_cache_refreshed",
                payload=payload,
            )

        if source_key in {"clinicaltrials", "openfda", "fda_pdufa"}:
            attempt["action"] = "refresh_pharma_pipeline"
            from . import pharma

            payload = await _bounded_attempt(pharma.run_pharma_scan(triggered_by="quality_auto_remediation"), timeout=18.0)
            return _finish_attempt(
                attempt,
                "refreshed" if payload.get("ok", True) else "fallback_confirmed",
                f"Pharma pipeline refreshed; {len(payload.get('results') or payload.get('rows') or [])} row(s) evaluated",
                trading_impact="display_research_refreshed",
                payload={"ok": payload.get("ok"), "count": len(payload.get("results") or payload.get("rows") or [])},
            )

        if source_key == "barchart":
            attempt["action"] = "repull_barchart_unusual_options"
            from . import x_factor

            syms = await _bounded_attempt(x_factor.barchart_unusual_set())
            return _finish_attempt(attempt, "refreshed", f"{len(syms or [])} unusual-options tickers pulled", trading_impact="x_factor_cache_refreshed")

        if source_key == "stocktwits":
            attempt["action"] = "probe_stocktwits_symbol_feed"
            from . import x_factor

            payload = await _bounded_attempt(x_factor.fetch_stocktwits("SPY"))
            return _finish_attempt(
                attempt,
                "live" if payload else "still_down",
                "StockTwits SPY probe returned data" if payload else "StockTwits SPY probe returned no data",
                trading_impact="x_factor_probe",
            )

        if source_key == "google_trends":
            attempt["action"] = "probe_google_trends"
            from . import x_factor

            payload = await _bounded_attempt(x_factor.fetch_google_trends("SPY"))
            return _finish_attempt(
                attempt,
                "live" if payload else "unchecked_confirmed",
                "Google Trends returned SPY context" if payload else "Google Trends remains scan-runtime checked only",
                trading_impact="x_factor_probe",
            )

        if source_key in {"reddit", "yahoo_news", "nih_cdc"}:
            outcome = "fallback_confirmed"
            reason = {
                "reddit": "Public Reddit mode is intentionally rate-limited until OAuth keys are configured",
                "yahoo_news": "Yahoo public endpoints can fall back to yfinance/news mirrors",
                "nih_cdc": "NIH/CDC prevalence is currently a static curated research dataset",
            }.get(source_key, "Fallback confirmed")
            return _finish_attempt(attempt, outcome, reason, trading_impact="display_only_fallback")

        if source_key == "finnhub":
            return _finish_attempt(
                attempt,
                "needs_configuration",
                "FINNHUB_API_KEY is optional and not configured or did not probe cleanly",
                trading_impact="none_optional_source",
            )

        if source_key == "telegram":
            attempt["action"] = "probe_telegram_bot"
            from . import integration_status as integration_svc

            rows = await _bounded_attempt(integration_svc.integration_status())
            tg = next((r for r in rows if r.get("key") == "telegram"), None)
            return _finish_attempt(
                attempt,
                "live" if tg and tg.get("ok") else "still_down",
                (tg or {}).get("reason") or "Telegram bot probe completed",
                trading_impact="alerting_checked",
            )

        if source_key == "usaspending":
            attempt["action"] = "probe_usaspending"
            from . import integration_status as integration_svc

            rows = await _bounded_attempt(integration_svc.integration_status())
            row2 = next((r for r in rows if r.get("key") == "usaspending"), None)
            return _finish_attempt(
                attempt,
                "live" if row2 and row2.get("ok") else "still_down",
                (row2 or {}).get("reason") or "USAspending probe completed",
                trading_impact="research_provider_checked",
            )

        return _finish_attempt(attempt, "no_automatic_fix", "No source-specific automatic repair is registered for this check")
    except asyncio.TimeoutError:
        return _finish_attempt(attempt, "timeout", f"{attempt.get('action')} exceeded {ATTEMPT_TIMEOUT_SECONDS}s budget")
    except Exception as exc:
        return _finish_attempt(attempt, "error", str(exc)[:500])


async def overview(force_refresh: bool = False, record_event: bool = True) -> dict[str, Any]:
    if force_refresh:
        from . import options_desk, scheduler

        await scheduler.persist_live_position_snapshot(triggered_by="quality_force_refresh")
        await options_desk.monitor_open_positions(enforce_hard_stop=False)

    rows: list[dict[str, Any]] = []
    live_row, latest_positions = await _live_positions_row()
    rows.append(live_row)
    rows.append(await _options_risk_row())
    rows.append(await _latest_scan_row())
    rows.extend(await _integration_rows(force_probe=force_refresh))

    critical = [r for r in rows if r.get("critical")]
    blockers = [r for r in critical if r.get("blocks_trading")]
    scoped_blockers = {
        "system": [],
        "equity": [],
        "options": [],
    }
    for row in blockers:
        scopes = row.get("execution_scopes") or ["system"]
        if "all" in scopes:
            scopes = ["system", "equity", "options"]
        for scope in scopes:
            if scope in scoped_blockers:
                scoped_blockers[scope].append(row)
    fallback = [r for r in rows if r.get("status") == "FALLBACK"]
    warnings = [r for r in rows if r.get("status") == "WARN" or r.get("warnings")]
    avg_score = round(sum(r.get("score", 0) for r in rows) / max(1, len(rows)), 1)
    critical_score = round(sum(r.get("score", 0) for r in critical) / max(1, len(critical)), 1)
    if scoped_blockers["system"]:
        gate_decision = "BLOCK"
    elif blockers:
        gate_decision = "SCOPED_BLOCK"
    else:
        gate_decision = "ALLOW"
    trading_gate = {
        "decision": gate_decision,
        "can_repull_fast": True,
        "max_gate_delay_ms": 1500,
        "policy": "Use fresh cached authority first; repull only stale critical sources; never wait on display-only feeds.",
        "blockers": blockers,
        "scoped_blockers": {k: v[:12] for k, v in scoped_blockers.items()},
    }
    payload = {
        "ok": not blockers,
        "generated_at": _now_iso(),
        "force_refreshed": force_refresh,
        "score": avg_score,
        "critical_score": critical_score,
        "trading_gate": trading_gate,
        "summary": {
            "total_checks": len(rows),
            "critical_checks": len(critical),
            "blockers": len(blockers),
            "warnings": len(warnings),
            "fallbacks": len(fallback),
            "live": sum(1 for r in rows if r.get("status") == "LIVE"),
            "down": sum(1 for r in rows if r.get("status") == "DOWN"),
        },
        "latest_positions": {
            "snapshot_at": latest_positions.get("snapshot_at"),
            "totals": latest_positions.get("totals") or {},
            "equities": {
                "position_count": (latest_positions.get("equities") or {}).get("position_count", 0),
                "unrealized_pl": (latest_positions.get("equities") or {}).get("unrealized_pl"),
            },
            "options": {
                "position_count": (latest_positions.get("options") or {}).get("position_count", 0),
                "unrealized_pl": (latest_positions.get("options") or {}).get("unrealized_pl"),
            },
        },
        "checks": rows,
    }
    latest_remediation = await _latest_remediation()
    if latest_remediation:
        attempts = latest_remediation.get("attempts") or []
        payload["remediation"] = {
            "last_run_at": latest_remediation.get("created_at") or latest_remediation.get("generated_at"),
            "attempts_count": len(attempts),
            "fixed_count": sum(1 for a in attempts if a.get("outcome") in {"live", "refreshed", "repulled", "rechecked_clean"}),
            "pending_count": sum(1 for a in attempts if a.get("outcome") in {"fallback_confirmed", "provider_conflict_confirmed", "needs_configuration", "still_down", "timeout", "error"}),
            "attempts": attempts[:20],
        }
    if record_event:
        db = get_db()
        await db.data_quality_events.insert_one(stamped({
            "event_type": "quality_overview",
            "generated_at": payload["generated_at"],
            "force_refreshed": force_refresh,
            "score": avg_score,
            "critical_score": critical_score,
            "trading_gate": trading_gate,
            "summary": payload["summary"],
        }))
    return payload


async def remediate(limit: int = 16) -> dict[str, Any]:
    """Try to repair degraded data without running any trade execution path."""
    started = _now_iso()
    before = await overview(force_refresh=False, record_event=False)
    candidates = [r for r in before.get("checks", []) if _needs_remediation(r)]
    candidates.sort(key=lambda r: (not r.get("critical"), not r.get("blocks_trading"), r.get("score", 0)))
    selected = candidates[: max(1, min(int(limit or 16), 32))]

    async def _run_all() -> list[dict[str, Any]]:
        tasks = [_attempt_remediation(row) for row in selected]
        if not tasks:
            return []
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out = []
        for row, result in zip(selected, results):
            if isinstance(result, Exception):
                out.append(_finish_attempt(_attempt_base(row, "probe"), "error", str(result)[:500]))
            else:
                out.append(result)
        return out

    try:
        attempts = await asyncio.wait_for(_run_all(), timeout=REMEDIATION_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        attempts = [
            _finish_attempt(
                _attempt_base(row, "probe"),
                "timeout",
                f"Remediation batch exceeded {REMEDIATION_TIMEOUT_SECONDS}s budget",
            )
            for row in selected
        ]

    after = await overview(force_refresh=False, record_event=False)
    after_by_key = {r.get("key"): r for r in after.get("checks", [])}
    for attempt in attempts:
        after_row = after_by_key.get(attempt.get("key")) or {}
        attempt["after_status"] = after_row.get("status")
        attempt["after_score"] = after_row.get("score")
        attempt["blocks_trading_after"] = bool(after_row.get("blocks_trading"))
        if attempt.get("blocks_trading_before") and not attempt.get("blocks_trading_after"):
            attempt["trading_impact"] = "blocker_cleared"

    summary = {
        "attempted": len(attempts),
        "fixed": sum(1 for a in attempts if a.get("outcome") in {"live", "refreshed", "repulled", "rechecked_clean"}),
        "confirmed_fallback": sum(1 for a in attempts if a.get("outcome") in {"fallback_confirmed", "provider_conflict_confirmed", "unchecked_confirmed"}),
        "needs_configuration": sum(1 for a in attempts if a.get("outcome") == "needs_configuration"),
        "still_down": sum(1 for a in attempts if a.get("outcome") in {"still_down", "timeout", "error"}),
        "blockers_before": (before.get("summary") or {}).get("blockers", 0),
        "blockers_after": (after.get("summary") or {}).get("blockers", 0),
        "warnings_before": (before.get("summary") or {}).get("warnings", 0),
        "warnings_after": (after.get("summary") or {}).get("warnings", 0),
        "fallbacks_before": (before.get("summary") or {}).get("fallbacks", 0),
        "fallbacks_after": (after.get("summary") or {}).get("fallbacks", 0),
    }
    event = stamped({
        "event_type": "quality_remediation",
        "generated_at": started,
        "finished_at": _now_iso(),
        "summary": summary,
        "attempts": attempts,
        "trading_gate_before": before.get("trading_gate"),
        "trading_gate_after": after.get("trading_gate"),
    })
    db = get_db()
    await db.data_quality_events.insert_one(event)
    await log_activity("Quality remediation completed", meta=summary)
    try:
        from . import telegram_events
        await telegram_events.dispatch_qc_report(force_refresh=False, send_if_clean=False)
    except Exception:
        pass
    fresh = await overview(force_refresh=False, record_event=False)
    fresh["remediation"] = {
        "last_run_at": event.get("created_at") or event.get("generated_at"),
        "attempts_count": len(attempts),
        "fixed_count": summary["fixed"],
        "pending_count": summary["confirmed_fallback"] + summary["needs_configuration"] + summary["still_down"],
        "attempts": attempts,
        "summary": summary,
    }
    return {"ok": True, "started_at": started, "summary": summary, "attempts": attempts, "overview": fresh}


async def events(limit: int = 100) -> dict[str, Any]:
    db = get_db()
    limit = max(1, min(int(limit or 100), 500))
    rows = await db.data_quality_events.find({}, {"_id": 0}).sort("created_at", -1).to_list(limit)
    return {"ok": True, "count": len(rows), "events": rows}
