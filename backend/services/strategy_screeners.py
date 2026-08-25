"""Strategy-specific scanner fan-out around the unchanged Core Scan.

The core scanner remains the broad discovery engine. This module turns existing
specialist data into a shared PM-compatible candidate contract so each strategy
can have its own scanner lane without adding order authority here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .db import get_db, stamped
from . import strategy_ideology

SCREENER_VERSION = "strategy-screeners-v1.0"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ticker(value: Any) -> str:
    return str(value or "").replace("$", "").strip().upper()


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        if isinstance(value, str):
            value = value.replace("$", "").replace(",", "").replace("%", "").strip()
        return float(value)
    except Exception:
        return default


def _signals(row: dict[str, Any]) -> list[str]:
    sigs = row.get("signals") or []
    if isinstance(sigs, dict):
        return sorted(str(k) for k, v in sigs.items() if v)
    if isinstance(sigs, list):
        return [str(s) for s in sigs if s]
    return []


def _text_blob(row: dict[str, Any]) -> str:
    parts = [
        row.get("ticker"),
        row.get("company"),
        row.get("source"),
        row.get("tier"),
        row.get("form"),
        row.get("title"),
        row.get("summary"),
        " ".join(_signals(row)),
        " ".join(str(x) for x in (row.get("triggers") or [])),
    ]
    return " ".join(str(p or "") for p in parts).upper()


def _base_row(
    *,
    row: dict[str, Any],
    screener_id: str,
    family: str,
    lane: str,
    score: float,
    price: float | None = None,
    pm_routable: bool = True,
    read_only: bool = False,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    ticker = _ticker(row.get("ticker") or row.get("underlying") or row.get("symbol"))
    px = _num(price if price is not None else row.get("price") or row.get("last") or row.get("current_price"), 0)
    score = max(0.0, min(100.0, float(score or 0)))
    target = _num(row.get("target_blended") or (row.get("targets") or {}).get("target_blended"), 0)
    if px > 0 and target <= px:
        target = round(px * (1.22 if family == "LOTTERY" else 1.14), 4)
    stop = _num(row.get("stop_loss") or (row.get("risk") or {}).get("stop_loss"), 0)
    if px > 0 and stop <= 0:
        stop = round(px * (0.72 if family == "LOTTERY" else 0.88), 4)
    raw_signals = _signals(row)
    signals = list(dict.fromkeys([
        "STRATEGY_SCANNER",
        family,
        screener_id,
        lane,
        *raw_signals[:8],
    ]))
    strategy_case = strategy_ideology.case_score(
        strategy_id=screener_id,
        native_score=score,
        row={**row, "price": px or row.get("price")},
        family=family,
        lane=lane,
    )
    return {
        "ticker": ticker,
        "company": row.get("company") or row.get("name") or ticker,
        "sector": row.get("sector") or row.get("industry") or "Unknown",
        "price": round(px, 4) if px > 0 else None,
        "signal_score": round(score / 10.0, 2),
        "trade_score": round(score / 2.5, 2),
        "learning_score": row.get("learning_score") or 0,
        "signals": signals,
        "targets": {"target_blended": target} if target > 0 else {},
        "stop_loss": stop if stop > 0 else None,
        "risk": {
            "score": _num((row.get("risk") or {}).get("score"), 35.0 if family != "LOTTERY" else 55.0),
            "stop_loss": stop if stop > 0 else None,
            "level": row.get("risk_level") or ("HIGH" if family == "LOTTERY" else "MED"),
        },
        "strategy_scanner": {
            "version": SCREENER_VERSION,
            "screener_id": screener_id,
            "family": family,
            "lane": lane,
            "native_score": round(score, 1),
            "case_score": strategy_case["case_score"],
            "confidence": strategy_case["confidence"],
            "pm_routable": bool(pm_routable),
            "read_only": bool(read_only),
            "notes": notes or [],
        },
        "strategy_case": strategy_case,
        "case_score": strategy_case["case_score"],
        "strategy_confidence": strategy_case["confidence"],
        "source_scan": screener_id,
        "scanner_family": family,
        "pm_routable": bool(pm_routable),
        "read_only": bool(read_only),
        "raw_source": row,
    }


def _lottery_family_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        ticker = _ticker(row.get("ticker"))
        if not ticker:
            continue
        score = _num(row.get("score"), 0)
        comps = row.get("components") or {}
        triggers = {str(x).upper() for x in (row.get("triggers") or [])}
        dilution = row.get("dilution") or {}
        if score >= 50 or triggers:
            out.append(_base_row(row=row, screener_id="lottery_day2_continuation", family="LOTTERY", lane="DAY2_CONTINUATION", score=score))
        if _num(comps.get("structure"), 0) >= 4 or _num(row.get("change_pct"), 0) >= 8:
            out.append(_base_row(row=row, screener_id="lottery_red_green", family="LOTTERY", lane="RED_GREEN", score=max(score, 52)))
        if _num(comps.get("rvol"), 0) >= 9 or _num(comps.get("rotation"), 0) >= 6 or "RVOL" in triggers or "ROTATION" in triggers:
            out.append(_base_row(row=row, screener_id="lottery_supernova", family="LOTTERY", lane="SUPERNOVA", score=max(score, 56)))
        if _num(comps.get("catalyst"), 0) > 0 or {"PHARMA/FDA", "CONTRACT", "EARNINGS", "ATTENTION"} & triggers:
            out.append(_base_row(row=row, screener_id="lottery_catalyst_runner", family="LOTTERY", lane="CATALYST_RUNNER", score=max(score, 55)))
        if "RUNNER" in _text_blob(row) or row.get("prior_runner_events") or _num(row.get("relative_volume"), 0) >= 8:
            out.append(_base_row(row=row, screener_id="lottery_serial_runner", family="LOTTERY", lane="SERIAL_RUNNER", score=max(score, 54)))
        if dilution.get("active") or any(str((p or {}).get("key") or "").lower() == "dilution" for p in (row.get("penalties") or [])):
            out.append(_base_row(
                row=row,
                screener_id="lottery_dilution_read",
                family="LOTTERY",
                lane="DILUTION_READ",
                score=max(score, 45),
                pm_routable=False,
                read_only=True,
                notes=["Dilution scan is read-only evidence in this rollout."],
            ))
    return out


def _core_options_rows(scan_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in scan_rows:
        ticker = _ticker(row.get("ticker"))
        if not ticker:
            continue
        opts = row.get("options") or {}
        text = _text_blob(row)
        optionish = bool(opts and opts.get("strategy") not in {None, "", "AVOID_OPTIONS"}) or "OPTION" in text or "CHEAP_IV" in text
        if not optionish:
            continue
        score = max(_num(row.get("signal_score"), 0) * 10, _num(row.get("trade_score"), 0) * 2.5, _num(opts.get("score"), 0))
        out.append(_base_row(row=row, screener_id="options_native", family="OPTIONS", lane=str(opts.get("strategy") or "PM_OPTION_REVIEW"), score=max(score, 50)))
    return out


async def _pharma_rows(scan_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    scan_tickers = {_ticker(r.get("ticker")) for r in scan_rows if _ticker(r.get("ticker"))}
    try:
        from . import pharma

        payload = await pharma.get_pdufa_within_days(days=90)
        rows = payload if isinstance(payload, list) else payload.get("results") or []
    except Exception:
        rows = []
    for row in rows:
        ticker = _ticker(row.get("ticker"))
        if not ticker:
            continue
        score = _num(row.get("binary_event_score") or row.get("score") or row.get("materiality_score"), 55)
        notes = ["outside_core_scan"] if scan_tickers and ticker not in scan_tickers else []
        out.append(_base_row(row=row, screener_id="pharma_calendar", family="PHARMA", lane=str(row.get("event_type") or "FDA_CALENDAR"), score=score, notes=notes))
    for row in scan_rows:
        if "PHARMA" in _text_blob(row) or "PDUFA" in _text_blob(row) or row.get("pharma"):
            out.append(_base_row(row=row, screener_id="pharma_core_overlap", family="PHARMA", lane="CORE_PHARMA_SIGNAL", score=max(_num(row.get("signal_score"), 0) * 10, 55)))
    return out


async def _earnings_rows(scan_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    scan_tickers = {_ticker(r.get("ticker")) for r in scan_rows if _ticker(r.get("ticker"))}
    try:
        from . import earnings_engine

        payload = await earnings_engine.current_week_cached(scan_tickers=scan_tickers)
        for day_rows in (payload.get("by_day") or {}).values():
            for row in day_rows or []:
                ticker = _ticker(row.get("ticker"))
                if ticker:
                    out.append(_base_row(
                        row=row,
                        screener_id="earnings_calendar",
                        family="EARNINGS",
                        lane=str(row.get("timing") or "EARNINGS"),
                        score=_num(row.get("score"), 52),
                        pm_routable=False,
                        read_only=True,
                        notes=["Earnings scanner is research-only and tracked outside PM routing."],
                    ))
    except Exception:
        pass
    for row in scan_rows:
        if row.get("earnings_this_week") or row.get("earnings_summary"):
            out.append(_base_row(
                row=row,
                screener_id="earnings_core_overlap",
                family="EARNINGS",
                lane="CORE_EARNINGS_SIGNAL",
                score=max(_num(row.get("signal_score"), 0) * 10, 52),
                pm_routable=False,
                read_only=True,
                notes=["Earnings overlap is research-only and does not alter PM routing."],
            ))
    return out


def _sec_bias(row: dict[str, Any]) -> str:
    explanation = row.get("explanation") or row.get("assessment") or {}
    bias = str(row.get("bias") or explanation.get("bias") or "").upper()
    text = _text_blob(row)
    if bias:
        return bias
    bearish_terms = ("OFFERING", "424B", "S-1", "S-3", "GOING CONCERN", "DELIST", "DEFAULT", "BANKRUPTCY", "RESTATEMENT")
    if any(term in text for term in bearish_terms):
        return "BEARISH"
    bullish_terms = ("13D", "ACTIVIST", "FORM 4", "INSIDER PURCHASE", "SC 13G")
    if any(term in text for term in bullish_terms):
        return "BULLISH"
    return "NEUTRAL"


async def _sec_rows() -> list[dict[str, Any]]:
    try:
        from . import sec_filings

        rows = await sec_filings.recent_filings(days=7)
    except Exception:
        rows = []
    out: list[dict[str, Any]] = []
    for row in rows[:120]:
        ticker = _ticker(row.get("ticker"))
        if not ticker:
            continue
        bias = _sec_bias(row)
        bearish = bias == "BEARISH"
        out.append(_base_row(
            row=row,
            screener_id="sec_filings",
            family="SEC",
            lane=f"{bias}_FILING",
            score=_num(row.get("narrative_lock_score") or row.get("significance"), 45),
            pm_routable=False,
            read_only=True,
            notes=[
                "SEC scanner is research-only and tracked outside PM routing.",
                *(
                    ["SEC bearish is read-only and cannot veto or block PM routing."]
                    if bearish
                    else []
                ),
            ],
        ))
    return out


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        ticker = _ticker(row.get("ticker"))
        screener_id = str((row.get("strategy_scanner") or {}).get("screener_id") or row.get("source_scan") or "")
        if not ticker or not screener_id:
            continue
        key = (ticker, screener_id)
        existing = by_key.get(key)
        if not existing or _num((row.get("strategy_scanner") or {}).get("native_score"), 0) > _num((existing.get("strategy_scanner") or {}).get("native_score"), 0):
            by_key[key] = row
    return sorted(by_key.values(), key=lambda r: (_ticker(r.get("ticker")), str(r.get("source_scan") or "")))


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_family: dict[str, int] = {}
    by_pm_family: dict[str, int] = {}
    by_read_only_family: dict[str, int] = {}
    by_screener: dict[str, int] = {}
    pm_routable = 0
    read_only = 0
    sec_read_only = 0
    sec_bearish_read_only = 0
    for row in rows:
        scanner = row.get("strategy_scanner") or {}
        family = str(scanner.get("family") or "UNKNOWN")
        screener_id = str(scanner.get("screener_id") or "unknown")
        by_family[family] = by_family.get(family, 0) + 1
        by_screener[screener_id] = by_screener.get(screener_id, 0) + 1
        if row.get("pm_routable"):
            pm_routable += 1
            by_pm_family[family] = by_pm_family.get(family, 0) + 1
        if row.get("read_only"):
            read_only += 1
            by_read_only_family[family] = by_read_only_family.get(family, 0) + 1
        if family == "SEC" and row.get("read_only"):
            sec_read_only += 1
            lane = str(scanner.get("lane") or "")
            if lane.startswith("BEARISH"):
                sec_bearish_read_only += 1
    return {
        "total": len(rows),
        "pm_routable": pm_routable,
        "read_only": read_only,
        "sec_read_only": sec_read_only,
        "sec_bearish_read_only": sec_bearish_read_only,
        "by_family": by_family,
        "by_pm_family": by_pm_family,
        "by_read_only_family": by_read_only_family,
        "by_screener": by_screener,
        "case_court_active_routing": False,
        "sec_bearish_veto_enabled": False,
    }


async def run_all(
    scan: dict[str, Any] | None = None,
    *,
    persist: bool = False,
    include_options_native: bool = True,
) -> dict[str, Any]:
    db = get_db()
    if scan is None:
        scan = await db.scan_results.find_one({}, {"_id": 0}, sort=[("finished_at", -1)])
    scan = scan or {}
    scan_rows = scan.get("results") or []
    lottery_rows: list[dict[str, Any]] = []
    try:
        from . import lottery

        lottery_rows = await lottery.latest_dedicated_lottery()
    except Exception:
        lottery_rows = []

    rows: list[dict[str, Any]] = []
    rows.extend(_lottery_family_rows(lottery_rows))
    if include_options_native:
        rows.extend(_core_options_rows(scan_rows))
    rows.extend(await _pharma_rows(scan_rows))
    rows.extend(await _earnings_rows(scan_rows))
    rows.extend(await _sec_rows())
    rows = _dedupe(rows)
    payload = stamped({
        "ok": True,
        "version": SCREENER_VERSION,
        "generated_at": _now().isoformat(),
        "scan_finished_at": scan.get("finished_at"),
        "summary": _summary(rows),
        "candidates": rows,
    })
    if persist:
        await db.strategy_screeners.update_one({"_id": "latest"}, {"$set": {k: v for k, v in payload.items() if k != "_id"}}, upsert=True)
    payload.pop("_id", None)
    return payload


async def pm_rows(scan: dict[str, Any] | None = None, *, persist: bool = True) -> dict[str, Any]:
    payload = await run_all(scan=scan, persist=persist, include_options_native=True)
    rows = [r for r in payload.get("candidates") or [] if r.get("pm_routable") and not r.get("read_only")]
    payload["rows"] = rows
    payload["summary"] = {**(payload.get("summary") or {}), "pm_rows": len(rows)}
    return payload
