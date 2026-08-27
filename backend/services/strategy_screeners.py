"""Strategy-specific scanner fan-out around the unchanged Core Scan.

The core scanner remains the broad discovery engine. This module turns existing
specialist data into a shared PM-compatible candidate contract so each strategy
can have its own scanner lane without adding order authority here.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from .db import get_db, stamped
from . import strategy_ideology

SCREENER_VERSION = "strategy-screeners-v1.1-options-badges"
OPTIONS_FINVIZ_STRATEGY_SCREENS = {
    "options_momentum_underlying_finviz": {
        "url": "https://finviz.com/screener.ashx?v=111&f=sh_opt_option,sh_price_o5,sh_avgvol_o500,sh_relvol_o1.5,ta_perf_1wup&o=-relativevolume",
        "lane": "TACTICAL_MOMENTUM_CALL",
        "screener_id": "options_tactical_momentum_call",
        "signals": ["OPTION_MOMENTUM", "RVOL_CONFIRM"],
        "base_score": 58,
    },
    "options_breakout_underlying_finviz": {
        "url": "https://finviz.com/screener.ashx?v=111&f=sh_opt_option,sh_price_o5,sh_avgvol_o500,ta_highlow20d_nh&o=-volume",
        "lane": "BREAKOUT_CALL",
        "screener_id": "options_breakout_call",
        "signals": ["OPTION_BREAKOUT", "NEW_HIGH_CONFIRM"],
        "base_score": 57,
    },
    "options_liquid_largecap_leaps_finviz": {
        "url": "https://finviz.com/screener.ashx?v=111&f=cap_midover,sh_opt_option,sh_price_o10,sh_avgvol_o500,ta_sma200_pa&o=-marketcap",
        "lane": "LEAPS_TREND",
        "screener_id": "options_leaps_trend",
        "signals": ["LEAPS_CANDIDATE", "LIQUID_UNDERLYING"],
        "base_score": 55,
    },
}


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
    learned_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ticker = _ticker(row.get("ticker") or row.get("underlying") or row.get("symbol"))
    px = _num(price if price is not None else row.get("price") or row.get("last") or row.get("current_price"), 0)
    score = max(0.0, min(100.0, float(score or 0)))
    target = _num(row.get("target_blended") or (row.get("targets") or {}).get("target_blended"), 0)
    if px > 0 and target <= px:
        if family == "LOTTERY":
            # Lottery candidates need a defined-risk plan that can clear the
            # PM starter floor. The old 22% target / 28% stop produced RR
            # 0.79, so every otherwise-valid Lottery proposal became WATCH.
            target = round(px * 1.40, 4)
        elif family == "OPTIONS":
            target = round(px * 1.20, 4)
        else:
            target = round(px * 1.14, 4)
    stop = _num(row.get("stop_loss") or (row.get("risk") or {}).get("stop_loss"), 0)
    if px > 0 and stop <= 0:
        if family == "LOTTERY":
            stop = round(px * 0.80, 4)
        elif family == "OPTIONS":
            stop = round(px * 0.92, 4)
        else:
            stop = round(px * 0.88, 4)
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
    if family == "LOTTERY":
        strategy_case = strategy_ideology.apply_lottery_learning(
            strategy_case,
            native_score=score,
            row=row,
            learned_config=learned_config,
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
            "badges": _strategy_badges(strategy_case, row),
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


def _strategy_badges(strategy_case: dict[str, Any], row: dict[str, Any]) -> list[dict[str, Any]]:
    badges = [
        {"label": str(strategy_case.get("risk_shape") or "RISK").replace("_", " ").upper(), "tone": "risk"},
        {"label": f"QC {strategy_case.get('data_quality', 0)}", "tone": "data"},
        {"label": f"VOL {strategy_case.get('volume_intensity_score', 0)}", "tone": "volume"},
    ]
    learned = strategy_case.get("learning_adjustment") or {}
    for badge in learned.get("badges") or []:
        badges.append({"label": str(badge).replace("_", " ").upper(), "tone": "learning"})
    opts = row.get("options") or {}
    if opts.get("data_quality"):
        badges.append({"label": str(opts.get("data_quality")).replace("_", " ").upper(), "tone": "options"})
    return badges[:7]


async def _lottery_learned_config() -> dict[str, Any] | None:
    try:
        return await get_db().ll_learned_config.find_one({"_id": "current"}, {"_id": 0})
    except Exception:
        return None


def _lottery_family_rows(rows: list[dict[str, Any]], learned_config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    from .lottery import lottery_signal_groups, lottery_strategy_fits
    for row in rows:
        ticker = _ticker(row.get("ticker"))
        if not ticker:
            continue
        score = _num(row.get("score"), 0)
        comps = row.get("components") or {}
        triggers = {str(x).upper() for x in (row.get("triggers") or [])}
        dilution = row.get("dilution") or {}
        evidence_score = max(
            _num(comps.get("gap_surge"), 0),
            _num(comps.get("rvol"), 0),
            _num(comps.get("rotation"), 0),
            _num(comps.get("catalyst"), 0),
            _num(comps.get("short_interest"), 0),
        )
        signal_groups = row.get("signal_groups") or lottery_signal_groups(row)
        signal_count = len(signal_groups)
        strategy_fits = row.get("strategy_fits") or lottery_strategy_fits(row, signal_groups)
        has_confluence = signal_count >= 2
        eligible = has_confluence and (bool(row.get("eligible")) or (score >= 35 and evidence_score > 0))
        if not eligible:
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
                learned_config=learned_config,
            ))
            continue
        lanes = [lane for lane in strategy_fits if lane in {"DAY2_CONTINUATION", "SUPERNOVA", "RED_GREEN", "CATALYST_RUNNER", "SERIAL_RUNNER"}]
        if not lanes:
            lanes = ["SIGNAL_CONFLUENCE"]
        for lane in lanes:
            screener_id = f"lottery_{lane.lower()}"
            built = _base_row(row=row, screener_id=screener_id, family="LOTTERY", lane=lane, score=score, learned_config=learned_config)
            built["signal_groups"] = signal_groups
            built["independent_signal_count"] = signal_count
            built["signal_band"] = f"{signal_count} SIGNALS" if signal_count < 5 else "5+ SIGNALS"
            built["signal_gate"] = "PASS_2_PLUS"
            built["strategy_fits"] = strategy_fits
            out.append(built)
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
                learned_config=learned_config,
            ))
    return out


async def _independent_options_rows(limit: int = 55) -> list[dict[str, Any]]:
    """Build option strategy candidates from option-specific discovery sources.

    This intentionally does not read the Core Scan. Core can still reach PM on
    its own, but the Options scanner has to discover its own names.
    """
    by_ticker: dict[str, dict[str, Any]] = {}

    def add(
        ticker: Any,
        *,
        source: str,
        lane: str,
        score: float,
        signals: list[str] | None = None,
        row: dict[str, Any] | None = None,
        screener_id: str | None = None,
    ) -> None:
        t = _ticker(ticker)
        if not t:
            return
        existing = by_ticker.get(t) or {"ticker": t, "signals": [], "sources": [], "option_lanes": [], "option_screeners": []}
        merged_signals = list(dict.fromkeys([*(existing.get("signals") or []), *(signals or []), source, lane]))
        by_ticker[t] = {
            **existing,
            **(row or {}),
            "ticker": t,
            "source": source,
            "sources": list(dict.fromkeys([*(existing.get("sources") or []), source])),
            "signals": merged_signals,
            "option_lanes": list(dict.fromkeys([*(existing.get("option_lanes") or []), lane])),
            "option_screeners": list(dict.fromkeys([*(existing.get("option_screeners") or []), screener_id or f"options_{lane.lower()}"])),
            "score": max(_num(existing.get("score"), 0), float(score or 0)),
        }

    try:
        from . import lottery

        async def fetch_option_screen(source: str, spec: dict[str, Any]) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
            rows = await lottery._fetch_finviz_url(spec["url"], source, limit=70)
            return source, spec, rows

        tasks = [fetch_option_screen(source, spec) for source, spec in OPTIONS_FINVIZ_STRATEGY_SCREENS.items()]
        for result in await asyncio.gather(*tasks, return_exceptions=True):
            if isinstance(result, Exception):
                continue
            source, spec, rows = result
            for row in rows:
                change = abs(_num(row.get("change_pct") or row.get("change"), 0))
                rel_vol = _num(row.get("relative_volume") or row.get("rel_volume"), 0)
                volume = _num(row.get("volume"), 0)
                score = min(82, float(spec["base_score"]) + min(10, change * 0.45) + min(8, rel_vol * 1.2) + (4 if volume >= 1_000_000 else 0))
                add(
                    row.get("ticker"),
                    source=source,
                    lane=spec["lane"],
                    score=score,
                    signals=spec["signals"],
                    row=row,
                    screener_id=spec["screener_id"],
                )
    except Exception:
        pass

    try:
        from . import scrapers

        high_short = await scrapers.fetch_finviz_high_short_interest(min_pct=10.0, limit=35)
        for row in high_short:
            short_pct = _num(row.get("short_float_pct"), 10)
            add(
                row.get("ticker"),
                source="options_finviz_high_short",
                lane="SQUEEZE_CALL",
                score=min(78, 54 + short_pct * 0.7),
                signals=["HIGH_SHORT_INTEREST", "OPTION_SQUEEZE"],
                row=row,
                screener_id="options_squeeze_call",
            )
    except Exception:
        pass

    try:
        from . import x_factor

        trending = sorted(await x_factor.yahoo_trending_set())
        for ticker in trending[:25]:
            add(
                ticker,
                source="options_yahoo_trending",
                lane="TACTICAL_MOMENTUM_CALL",
                score=56,
                signals=["YAHOO_TRENDING", "ATTENTION"],
                screener_id="options_tactical_momentum_call",
            )
    except Exception:
        pass

    try:
        from . import pharma

        payload = await pharma.get_pdufa_within_days(days=90)
        rows = payload if isinstance(payload, list) else payload.get("results") or []
        for row in rows[:25]:
            add(
                row.get("ticker"),
                source="options_pharma_calendar",
                lane="EVENT_DEFINED_RISK",
                score=max(58, _num(row.get("binary_event_score") or row.get("score") or row.get("materiality_score"), 58)),
                signals=["PHARMA", "FDA_CALENDAR", "EVENT_DEFINED_RISK"],
                row=row,
                screener_id="options_event_defined_risk",
            )
    except Exception:
        pass

    rows = list(by_ticker.values())[:limit]
    try:
        from . import pricer

        prices = await pricer.batch_live_price_meta([r["ticker"] for r in rows], concurrency=8)
        for row in rows:
            meta = prices.get(row["ticker"]) or {}
            if meta.get("price") is not None:
                row["price"] = meta.get("price")
                row["quote_age_seconds"] = meta.get("age_seconds")
                row["price_source"] = meta.get("source")
    except Exception:
        pass

    out: list[dict[str, Any]] = []
    for row in rows:
        ticker = _ticker(row.get("ticker"))
        if not ticker:
            continue
        lanes = row.get("option_lanes") or ["TACTICAL_MOMENTUM_CALL"]
        lane = "EVENT_DEFINED_RISK" if "EVENT_DEFINED_RISK" in lanes else str(lanes[0])
        score = _num(row.get("score"), 0)
        screeners = row.get("option_screeners") or [f"options_{lane.lower()}"]
        preferred = (
            "options_event_defined_risk" if lane == "EVENT_DEFINED_RISK" else
            "options_leaps_trend" if lane == "LEAPS_TREND" else
            "options_breakout_call" if lane == "BREAKOUT_CALL" else
            "options_squeeze_call" if lane == "SQUEEZE_CALL" else
            "options_tactical_momentum_call"
        )
        screener_id = preferred if preferred in screeners else str(screeners[0])
        strategy = (
            "LONG_CALL_EVENT_SCOUT" if lane == "EVENT_DEFINED_RISK" else
            "ITM_LEAPS_SCOUT" if lane == "LEAPS_TREND" else
            "CALL_BREAKOUT_SCOUT" if lane == "BREAKOUT_CALL" else
            "LONG_CALL_SCOUT"
        )
        built = _base_row(row=row, screener_id=screener_id, family="OPTIONS", lane=lane, score=max(score, 50))
        built["options"] = {
            "strategy": strategy,
            "direction": "BULL",
            "options_intent": True,
            "preferred_route": "OPTION",
            "strategy_reason": "Independent options screener candidate; contract still must clear Alpaca chain and liquidity checks.",
            "iv_rank": row.get("iv_rank", 50),
            "iv_label": row.get("iv_label", "UNKNOWN"),
            "screener_lanes": lanes,
            "screener_sources": row.get("sources") or [],
            "data_provider": "SCREENER_ONLY",
            "data_quality": "NEEDS_CHAIN_REFRESH",
        }
        built["strategy_scanner"]["badges"] = _strategy_badges(built["strategy_case"], {"options": built["options"], **row})
        out.append(built)
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
            out.append(_base_row(
                row=row,
                screener_id="pharma_core_overlap",
                family="PHARMA",
                lane="CORE_PHARMA_SIGNAL",
                score=max(_num(row.get("signal_score"), 0) * 10, 55),
                pm_routable=False,
                read_only=True,
                notes=["Core pharma overlap is evidence-only; pharma PM routing comes from the FDA/catalyst screener."],
            ))
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
    lottery_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    db = get_db()
    if scan is None:
        scan = await db.scan_results.find_one({}, {"_id": 0}, sort=[("finished_at", -1)])
    scan = scan or {}
    scan_rows = scan.get("results") or []
    lottery_rows: list[dict[str, Any]] = []
    # Prefer the result produced by this cycle. Re-reading the mutable
    # `current` document here allowed a concurrent scheduler run to replace a
    # fresh Lottery result before PM routing consumed it.
    attached_lottery = lottery_result
    if attached_lottery is None and isinstance(scan.get("lottery_result"), dict):
        attached_lottery = scan.get("lottery_result")
    if isinstance(attached_lottery, dict):
        lottery_rows = attached_lottery.get("candidates") or []
    else:
        try:
            from . import lottery

            lottery_rows = await lottery.latest_dedicated_lottery()
        except Exception:
            lottery_rows = []

    rows: list[dict[str, Any]] = []
    learned_config = await _lottery_learned_config()
    rows.extend(_lottery_family_rows(lottery_rows, learned_config=learned_config))
    if include_options_native:
        rows.extend(await _independent_options_rows())
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
        stored_payload = {k: v for k, v in payload.items() if k != "_id"}
        await db.strategy_screeners.update_one({"_id": "latest"}, {"$set": stored_payload}, upsert=True)
        history_key = f"strategy:{scan.get('finished_at') or payload['generated_at']}"
        await db.strategy_screeners_history.update_one(
            {"_id": history_key},
            {"$set": {**stored_payload, "history_key": history_key}},
            upsert=True,
        )
    payload.pop("_id", None)
    return payload


async def pm_rows(
    scan: dict[str, Any] | None = None,
    *,
    persist: bool = True,
    lottery_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = await run_all(
        scan=scan,
        persist=persist,
        include_options_native=True,
        lottery_result=lottery_result,
    )
    rows = [r for r in payload.get("candidates") or [] if r.get("pm_routable") and not r.get("read_only")]
    payload["rows"] = rows
    payload["summary"] = {**(payload.get("summary") or {}), "pm_rows": len(rows)}
    return payload
