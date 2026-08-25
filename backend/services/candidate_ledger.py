"""Unified candidate ledger for multi-scan routing.

Each scan family can keep its own logic and UI, but the PM and Case Court need
one deduped docket per cycle. This module builds that docket from the latest
core scan plus already-running specialist outputs. It does not execute trades.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from .db import get_db, stamped


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ticker(value: Any) -> str:
    return str(value or "").replace("$", "").strip().upper()


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _rows(payload: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if not isinstance(payload, dict):
        return []
    for key in keys or ("results", "candidates", "events", "plays", "rows"):
        value = payload.get(key)
        if isinstance(value, list):
            return [r for r in value if isinstance(r, dict)]
    return []


def _candidate_id(cycle_id: str, ticker: str) -> str:
    return "cand-" + hashlib.sha1(f"{cycle_id}|{ticker}".encode("utf-8")).hexdigest()[:12]


def _cycle_id(scan: dict[str, Any]) -> str:
    basis = "|".join([
        str(scan.get("finished_at") or scan.get("started_at") or _now().isoformat()),
        str(scan.get("scan_signature") or ""),
        str(scan.get("triggered_by") or ""),
    ])
    return "cyc-" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:14]


def _signals(row: dict[str, Any]) -> list[str]:
    sigs = row.get("signals") or []
    if isinstance(sigs, dict):
        return sorted(str(k) for k, v in sigs.items() if v)
    return sorted(str(s) for s in sigs)


def _quality(row: dict[str, Any], source: str) -> dict[str, Any]:
    age = row.get("price_age_seconds") or row.get("quote_age_seconds")
    price_ok = bool(row.get("price") or row.get("current_price") or row.get("last"))
    fresh = row.get("price_fresh")
    if fresh is None and age is not None:
        fresh = _num(age, 999999) <= 900
    warnings = []
    if not price_ok:
        warnings.append("missing_price")
    if fresh is False:
        warnings.append("stale_price")
    if source in {"options", "pharma", "lottery"} and not row:
        warnings.append("empty_specialist_row")
    score = 100
    if not price_ok:
        score -= 25
    if fresh is False:
        score -= 20
    if row.get("data_quality") in {"fallback_calendar", "fallback", "degraded"}:
        score -= 15
        warnings.append(str(row.get("data_quality")))
    return {
        "score": max(0, min(100, score)),
        "price_ok": price_ok,
        "fresh": bool(fresh) if fresh is not None else None,
        "warnings": list(dict.fromkeys(warnings)),
    }


def _new_entry(cycle_id: str, ticker: str) -> dict[str, Any]:
    return {
        "candidate_id": _candidate_id(cycle_id, ticker),
        "cycle_id": cycle_id,
        "ticker": ticker,
        "sources": [],
        "strategy_tags": [],
        "scores": {},
        "rows": {},
        "quality": {},
        "pm": {},
        "case_court": {"status": "PENDING"},
        "final_route": "PM_REVIEW",
    }


def _merge_source(entry: dict[str, Any], source: str, row: dict[str, Any], tags: list[str] | None = None) -> None:
    if source not in entry["sources"]:
        entry["sources"].append(source)
    entry["rows"][source] = row
    entry["quality"][source] = _quality(row, source)
    for tag in tags or []:
        if tag and tag not in entry["strategy_tags"]:
            entry["strategy_tags"].append(tag)
    score = (
        row.get("pm_score")
        or row.get("score")
        or row.get("signal_score")
        or row.get("binary_event_score")
        or row.get("lottery_score")
    )
    if score is not None:
        entry["scores"][source] = round(_num(score), 2)


def _route_tags_for_core(row: dict[str, Any]) -> list[str]:
    tags = ["CORE"]
    opts = row.get("options") or {}
    if opts and opts.get("strategy") not in {None, "", "AVOID_OPTIONS"}:
        tags.append("OPTIONS")
    if row.get("lottery_score") or row.get("lottery_tier"):
        tags.append("LOTTERY")
    if row.get("earnings_this_week") or row.get("earnings_summary"):
        tags.append("EARNINGS")
    sigs = " ".join(_signals(row)).upper()
    if "PDUFA" in sigs or "PHARMA" in sigs:
        tags.append("PHARMA")
    return tags


async def build_from_scan(scan: dict[str, Any] | None = None, *, include_external: bool = True, persist: bool = False) -> dict[str, Any]:
    db = get_db()
    if scan is None:
        scan = await db.scan_results.find_one({}, {"_id": 0}, sort=[("finished_at", -1)])
    scan = scan or {}
    cycle_id = _cycle_id(scan)
    by_ticker: dict[str, dict[str, Any]] = {}

    def ensure(ticker: str) -> dict[str, Any]:
        t = _ticker(ticker)
        if t not in by_ticker:
            by_ticker[t] = _new_entry(cycle_id, t)
        return by_ticker[t]

    for row in scan.get("results") or []:
        ticker = _ticker(row.get("ticker"))
        if not ticker:
            continue
        entry = ensure(ticker)
        _merge_source(entry, "core_scan", row, _route_tags_for_core(row))

    for row in scan.get("lottery_picks") or []:
        ticker = _ticker(row.get("ticker"))
        if not ticker:
            continue
        entry = ensure(ticker)
        _merge_source(entry, "lottery", row, ["LOTTERY", row.get("tier"), *[str(x) for x in row.get("triggers") or []]])

    v32 = scan.get("v32") or {}
    for row in v32.get("x_factor_discoveries") or []:
        ticker = _ticker(row.get("ticker"))
        if ticker:
            _merge_source(ensure(ticker), "discovery", row, ["DISCOVERY", "X_FACTOR"])
    for row in v32.get("x_factor") or []:
        ticker = _ticker(row.get("ticker"))
        if ticker:
            _merge_source(ensure(ticker), "x_factor", row, ["X_FACTOR"])
    for row in v32.get("dark_horse") or []:
        ticker = _ticker(row.get("ticker"))
        if ticker:
            _merge_source(ensure(ticker), "dark_horse", row, ["DARK_HORSE"])

    if include_external:
        try:
            from . import options_desk

            options = await options_desk.candidates()
            for row in _rows(options, "candidates"):
                ticker = _ticker(row.get("ticker") or row.get("underlying"))
                if ticker:
                    route = str(row.get("route") or "OPTION").upper()
                    _merge_source(ensure(ticker), "options", row, ["OPTIONS", route, str(row.get("strategy") or "")])
        except Exception as exc:
            pass

        try:
            from . import pharma

            pdufa = await pharma.get_pdufa_within_days(days=90)
            for row in _rows(pdufa, "results"):
                ticker = _ticker(row.get("ticker"))
                if ticker:
                    _merge_source(ensure(ticker), "pharma", row, ["PHARMA", "FDA_CALENDAR", str(row.get("tier") or "")])
        except Exception:
            pass

        try:
            from . import earnings_engine

            earnings = await earnings_engine.current_week_cached(scan_tickers=set(by_ticker.keys()))
            for day_rows in (earnings.get("by_day") or {}).values():
                for row in day_rows:
                    ticker = _ticker(row.get("ticker"))
                    if ticker:
                        _merge_source(ensure(ticker), "earnings", row, ["EARNINGS"])
        except Exception:
            pass

        try:
            from . import portfolio_manager

            pm = await portfolio_manager.latest_portfolio_plan()
            for row in _rows(pm, "recommendations"):
                ticker = _ticker(row.get("ticker"))
                if ticker:
                    entry = ensure(ticker)
                    entry["pm"] = row
                    _merge_source(entry, "pm", row, ["EQUITY_PM", str(row.get("option_view") or "")])
        except Exception:
            pass

    candidates = [v for k, v in sorted(by_ticker.items()) if k]
    for entry in candidates:
        source_set = set(entry.get("sources") or [])
        if "options" in source_set and "core_scan" in source_set:
            entry["final_route"] = "BOTH_PM_REVIEW"
        elif "options" in source_set:
            entry["final_route"] = "OPTIONS_PM_REVIEW"
        elif "pharma" in source_set:
            entry["final_route"] = "PHARMA_PM_REVIEW"
        elif "lottery" in source_set:
            entry["final_route"] = "LOTTERY_PM_REVIEW"
        elif "core_scan" in source_set:
            entry["final_route"] = "EQUITY_PM_REVIEW"
        max_quality = min((q.get("score", 0) for q in (entry.get("quality") or {}).values()), default=0)
        entry["candidate_quality_score"] = max_quality

    summary = {
        "total": len(candidates),
        "core": sum(1 for c in candidates if "core_scan" in c.get("sources", [])),
        "options": sum(1 for c in candidates if "options" in c.get("sources", [])),
        "pharma": sum(1 for c in candidates if "pharma" in c.get("sources", [])),
        "lottery": sum(1 for c in candidates if "lottery" in c.get("sources", [])),
        "earnings": sum(1 for c in candidates if "earnings" in c.get("sources", [])),
        "discovery": sum(1 for c in candidates if "discovery" in c.get("sources", [])),
        "pm_attached": sum(1 for c in candidates if c.get("pm")),
    }
    doc = stamped({
        "ok": True,
        "cycle_id": cycle_id,
        "scan_finished_at": scan.get("finished_at"),
        "generated_at": _now().isoformat(),
        "summary": summary,
        "candidates": candidates,
    })
    if persist:
        await db.candidate_ledgers.update_one({"cycle_id": cycle_id}, {"$set": doc}, upsert=True)
        await db.candidate_ledgers.update_one({"_id": "latest"}, {"$set": {k: v for k, v in doc.items() if k != "_id"}}, upsert=True)
    doc.pop("_id", None)
    return doc


async def latest(*, rebuild: bool = True) -> dict[str, Any]:
    if rebuild:
        return await build_from_scan(include_external=True, persist=True)
    db = get_db()
    doc = await db.candidate_ledgers.find_one({"_id": "latest"}, {"_id": 0})
    if doc:
        return doc
    return await build_from_scan(include_external=True, persist=True)
