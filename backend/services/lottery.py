"""Lottery League v2.

The League is a fenced moonshot research book. It does not borrow the old
options-lottery probability model and it never calls yfinance. Candidates are
scored from observable low-float runner evidence, stored in ll_* collections,
and graded with a synthetic haircut so paper fills cannot look cleaner than
the data deserves.
"""
from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from .db import get_db, log_activity, stamped

logger = logging.getLogger(__name__)

LL_RUBRIC_VERSION = "lottery-league-v2.0-moonshot-desk"
FINVIZ_URL = (
    "https://finviz.com/screener.ashx?v=111"
    "&f=sh_price_1to20,sh_relvol_o2,sh_short_o15"
    "&o=-volume"
)
ENTRY_HAIRCUT_PCT = 1.0
EXIT_HAIRCUT_PCT = 1.5
ROUND_TRIP_HAIRCUT_PCT = ENTRY_HAIRCUT_PCT + EXIT_HAIRCUT_PCT
TICKET_NOTIONAL = 10.0
MAX_DAILY_TICKETS = 2
MAX_OPEN_TICKETS = 6
LEAGUE_CAP_PCT = 0.05


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> str:
    return _now().date().isoformat()


def _clean_ticker(value: Any) -> str:
    text = re.sub(r"[^A-Za-z.]", "", str(value or "").upper()).replace(".", "-")
    return text[:8]


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, str):
            value = value.replace("$", "").replace(",", "").replace("%", "").strip()
            if value in {"", "-", "N/A"}:
                return default
        return float(value)
    except Exception:
        return default


def _pct(value: Any) -> float | None:
    try:
        if value is None:
            return None
        if isinstance(value, str):
            value = value.replace("%", "").replace("+", "").replace(",", "").strip()
        return float(value)
    except Exception:
        return None


def _money(value: float | None) -> str:
    if value is None:
        return "-"
    return f"${value:,.2f}"


def tier_for(score: float) -> str:
    if score >= 80:
        return "JACKPOT"
    if score >= 65:
        return "HOT"
    if score >= 50:
        return "WATCH"
    return "REJECT"


def _component_score(value: float, tiers: list[tuple[float, float]]) -> float:
    for threshold, score in tiers:
        if value >= threshold:
            return score
    return 0.0


async def _latest_regime() -> dict[str, Any]:
    try:
        from . import trade_floor

        return await trade_floor.regime_status()
    except Exception as exc:
        return {
            "ok": False,
            "status": "unknown",
            "weather": "unknown",
            "halt_new_entries": True,
            "reason": str(exc),
        }


async def _account_equity() -> float:
    try:
        from . import trade_floor

        account = await trade_floor.get_account()
        return _num(account.get("equity") or account.get("portfolio_value"), 0)
    except Exception:
        return 0.0


async def _halted_symbols() -> set[str]:
    try:
        from . import trading_halts

        payload = await trading_halts.fetch_halts()
        return {str(h.get("symbol") or "").upper() for h in payload.get("halts", []) if h.get("active")}
    except Exception:
        return set()


async def _dilution_flag(ticker: str) -> dict[str, Any]:
    db = get_db()
    cutoff = (_now() - timedelta(days=90)).isoformat()
    forms = ["S-1", "S-3", "424B1", "424B2", "424B3", "424B4", "424B5", "424B7", "424B8", "FWP"]
    rows = await db.sec_filings.find(
        {
            "ticker": ticker,
            "$or": [{"form": {"$in": forms}}, {"title": {"$regex": "ATM|offering|prospectus", "$options": "i"}}],
            "created_at": {"$gte": cutoff},
        },
        {"_id": 0, "form": 1, "filing_date": 1, "created_at": 1, "title": 1, "url": 1},
    ).sort("created_at", -1).to_list(5)
    return {
        "active": bool(rows),
        "penalty": 25 if rows else 0,
        "forms": rows,
        "label": "DILUTION WATCH" if rows else "CLEAR",
    }


def _catalyst_score(row: dict[str, Any]) -> tuple[float, list[str]]:
    signals = {str(s).upper() for s in (row.get("signals") or [])}
    chips: list[str] = []
    score = 0.0
    if row.get("pharma") or "PHARMA_PDUFA" in signals or "PDUFA" in signals:
        score = max(score, 25.0)
        chips.append("PHARMA/FDA")
    if row.get("contract") or row.get("contracts") or "CONTRACT_SURGE" in signals:
        score = max(score, 20.0)
        chips.append("CONTRACT")
    if row.get("pead") or row.get("earnings_this_week") or "UPCOMING_EARNINGS" in signals:
        score = max(score, 15.0)
        chips.append("EARNINGS")
    if row.get("x_factor") or "X_FACTOR" in signals or "DARK_HORSE" in signals:
        score = max(score, 10.0)
        chips.append("ATTENTION")
    return score, chips


def _score_candidate(row: dict[str, Any], halted: set[str]) -> dict[str, Any]:
    ticker = _clean_ticker(row.get("ticker"))
    price = _num(row.get("price") or row.get("last") or row.get("current_price"), 0)
    change_pct = _pct(row.get("change_pct") or row.get("day_change_pct") or row.get("change")) or 0.0
    rel_vol = _num(row.get("relative_volume") or row.get("rel_volume") or row.get("rvol"), 0)
    volume = _num(row.get("volume"), 0)
    float_proxy = _num(row.get("float") or row.get("float_shares") or row.get("shares_outstanding"), 0)
    high = _num(row.get("day_high") or row.get("high"), 0)
    low = _num(row.get("day_low") or row.get("low"), 0)
    close_position = ((price - low) / (high - low)) if price and high > low else None

    gap_surge = _component_score(change_pct, [(40, 20), (25, 17), (15, 13), (10, 9), (5, 4)])
    rvol_score = _component_score(rel_vol, [(15, 15), (10, 12), (5, 9), (2, 4)])
    if rel_vol <= 0 and volume >= 2_000_000:
        rvol_score = 5

    float_score = 0.0
    float_label = "UNKNOWN"
    if float_proxy:
        if float_proxy < 10_000_000:
            float_score, float_label = 15, "<=10M SO-basis"
        elif float_proxy < 30_000_000:
            float_score, float_label = 10, "<=30M SO-basis"
        elif float_proxy < 60_000_000:
            float_score, float_label = 5, "<=60M SO-basis"
        else:
            float_label = ">60M SO-basis"
    elif price and price <= 20:
        float_score, float_label = 3, "UNKNOWN FLOAT"

    rotation = (volume / float_proxy) if volume and float_proxy else 0.0
    rotation_score = _component_score(rotation, [(1.5, 15), (0.8, 11), (0.4, 6), (0.2, 3)])
    catalyst_score, catalyst_chips = _catalyst_score(row)
    structure_score = 0.0
    if close_position is not None:
        structure_score = _component_score(close_position, [(0.85, 10), (0.70, 7), (0.55, 4)])

    spread_pct = _num(row.get("spread_pct") or row.get("spread"), 0)
    penalties: list[dict[str, Any]] = []
    if spread_pct > 1:
        penalties.append({"key": "spread", "label": "Spread >1%", "points": 10})
    if ticker in halted:
        penalties.append({"key": "halt", "label": "Active halt", "points": 15})
    if row.get("failed_breakout_today"):
        penalties.append({"key": "failed_breakout", "label": "Failed breakout", "points": 5})
    if row.get("reverse_split_30d"):
        penalties.append({"key": "reverse_split", "label": "Reverse split <30d", "points": 10})

    components = {
        "gap_surge": round(gap_surge, 1),
        "rvol": round(rvol_score, 1),
        "float_tier": round(float_score, 1),
        "rotation": round(rotation_score, 1),
        "catalyst": round(catalyst_score, 1),
        "structure": round(structure_score, 1),
    }
    raw = sum(components.values())
    penalty_total = sum(p["points"] for p in penalties)
    score = max(0.0, min(100.0, raw - penalty_total))
    triggers = []
    if change_pct >= 10:
        triggers.append("GAP/SURGE")
    if rel_vol >= 5:
        triggers.append("RVOL")
    if rotation >= 0.8:
        triggers.append("ROTATION")
    triggers.extend(catalyst_chips)

    return {
        "ticker": ticker,
        "company": row.get("company") or row.get("name") or ticker,
        "sector": row.get("sector") or "-",
        "price": round(price, 4) if price else None,
        "change_pct": round(change_pct, 2),
        "volume": int(volume) if volume else None,
        "relative_volume": round(rel_vol, 2) if rel_vol else None,
        "float_proxy": int(float_proxy) if float_proxy else None,
        "float_confidence": float_label,
        "rotation": round(rotation, 2) if rotation else None,
        "spread_pct": round(spread_pct, 2) if spread_pct else None,
        "quote_age_seconds": row.get("quote_age_seconds"),
        "score": round(score, 1),
        "tier": tier_for(score),
        "components": components,
        "penalties": penalties,
        "triggers": list(dict.fromkeys(triggers)),
        "source": row.get("source") or "scanner_or_lottery_universe",
        "rubric_version": LL_RUBRIC_VERSION,
        "eligible": score >= 60 and ticker not in halted,
    }


async def _finviz_candidates() -> list[dict[str, Any]]:
    headers = {
        "User-Agent": "Mozilla/5.0 CaseCapitalTerminal/1.0",
        "Accept": "text/html,application/xhtml+xml",
    }
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return []

    try:
        async with httpx.AsyncClient(timeout=18.0, headers=headers, follow_redirects=True) as client:
            response = await client.get(FINVIZ_URL)
        if response.status_code != 200:
            return []
        soup = BeautifulSoup(response.text, "html.parser")
        rows: list[dict[str, Any]] = []
        for table_row in soup.select("table.screener_table tr, table#screener-table tr"):
            cells = [cell.get_text(" ", strip=True) for cell in table_row.find_all("td")]
            if len(cells) < 9 or not cells[1].isalpha():
                continue
            rows.append({
                "ticker": cells[1].upper(),
                "company": cells[2] if len(cells) > 2 else "",
                "sector": cells[3] if len(cells) > 3 else "",
                "price": _num(cells[8] if len(cells) > 8 else None, 0),
                "change_pct": _pct(cells[9] if len(cells) > 9 else None),
                "volume": _num(cells[10] if len(cells) > 10 else None, 0),
                "source": "finviz_low_float_screen",
            })
        return rows[:80]
    except Exception as exc:
        logger.warning("Lottery League Finviz universe failed: %s", exc)
        return []


async def _latest_scan_rows() -> list[dict[str, Any]]:
    db = get_db()
    scan = await db.scan_results.find_one({}, {"_id": 0, "results": 1}, sort=[("finished_at", -1)])
    rows = []
    for row in (scan or {}).get("results") or []:
        rows.append({**row, "source": row.get("source") or "latest_terminal_scan"})
    return rows


async def _enrich_candidate(candidate: dict[str, Any], halted: set[str]) -> dict[str, Any]:
    scored = _score_candidate(candidate, halted)
    dilution = await _dilution_flag(scored["ticker"])
    scored["dilution"] = dilution
    if dilution.get("active"):
        scored["penalties"].append({"key": "dilution", "label": "Offering/ATM shelf", "points": 25})
        scored["score"] = max(0, round(scored["score"] - 25, 1))
        scored["tier"] = tier_for(scored["score"])
        scored["eligible"] = scored["score"] >= 60 and scored["ticker"] not in halted
    if scored["ticker"] in halted:
        scored["halt_status"] = "HALTED"
    else:
        scored["halt_status"] = "CLEAR"
    return scored


async def run_dedicated_lottery_scan(triggered_by: str = "operator") -> dict[str, Any]:
    """Run the League universe scan and persist an ll_scans snapshot."""
    db = get_db()
    halted = await _halted_symbols()
    finviz = await _finviz_candidates()
    by_ticker: dict[str, dict[str, Any]] = {}
    for row in finviz:
        ticker = _clean_ticker(row.get("ticker"))
        if not ticker:
            continue
        existing = by_ticker.get(ticker) or {}
        by_ticker[ticker] = {**existing, **row, "ticker": ticker}

    candidates = []
    for row in list(by_ticker.values())[:120]:
        candidates.append(await _enrich_candidate(row, halted))
    candidates.sort(key=lambda r: (r.get("score") or 0), reverse=True)

    regime = await _latest_regime()
    doc = stamped({
        "scan_id": f"ll-{_now().strftime('%Y%m%d%H%M%S')}",
        "date": _today(),
        "scanned_at": _now().isoformat(),
        "triggered_by": triggered_by,
        "rubric_version": LL_RUBRIC_VERSION,
        "source_counts": {"finviz_low_float_screen": len(finviz), "latest_scan": 0, "deduped": len(candidates)},
        "regime": regime,
        "candidates": candidates,
    })
    await db.ll_scans.insert_one(doc)
    await db.ll_scans.update_one({"_id": "current"}, {"$set": {k: v for k, v in doc.items() if k != "_id"}}, upsert=True)
    await log_activity(f"Lottery League scan: {len(candidates)} candidates", "info")
    return {"ok": True, "count": len(candidates), "candidates": candidates, "scan": {k: v for k, v in doc.items() if k != "_id"}}


async def latest_dedicated_lottery() -> list[dict[str, Any]]:
    db = get_db()
    doc = await db.ll_scans.find_one({"_id": "current"}, {"_id": 0})
    if doc:
        return doc.get("candidates") or []
    legacy = await db.lottery_dedicated_scan.find_one({"_id": "current"}, {"_id": 0})
    return legacy.get("candidates", []) if legacy else []


async def evaluate_for_scan(scan_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Scanner compatibility hook. It tags rows with League-style score only."""
    halted = await _halted_symbols()
    picks = [_score_candidate({**row, "source": "scan_bridge"}, halted) for row in (scan_results or [])]
    picks = [p for p in picks if p["score"] >= 50]
    picks.sort(key=lambda x: -x["score"])
    return picks


async def log_picks(picks: list[dict[str, Any]]) -> int:
    """Persist scanner bridge observations without creating tickets."""
    if not picks:
        return 0
    db = get_db()
    doc = stamped({
        "date": _today(),
        "rubric_version": LL_RUBRIC_VERSION,
        "source": "scanner_bridge",
        "picks": picks,
    })
    await db.ll_scanner_bridge.update_one({"date": _today()}, {"$set": doc}, upsert=True)
    return len(picks)


async def _tickets(active_only: bool = False) -> list[dict[str, Any]]:
    db = get_db()
    query = {"status": {"$in": ["OPEN", "HALTED"]}} if active_only else {}
    return await db.ll_tickets.find(query, {"_id": 0}).sort("opened_at", -1).to_list(500)


def _ticket_multiple(ticket: dict[str, Any]) -> float | None:
    entry = _num(ticket.get("entry_price"), 0)
    current = _num(ticket.get("current_price") or ticket.get("exit_price"), 0)
    if not entry or not current:
        return None
    return round(current / entry, 3)


async def issue_ticket(ticker: str, entry_price: float, variant: str = "V1_DAY2_CONTINUATION",
                       score: float | None = None, reason: str = "operator") -> dict[str, Any]:
    """Create a fenced paper ticket. This does not send an equity/options order."""
    db = get_db()
    t = _clean_ticker(ticker)
    if not t or entry_price <= 0:
        return {"ok": False, "reason": "invalid_ticket"}
    regime = await _latest_regime()
    status = str(regime.get("status") or regime.get("weather") or "").lower()
    if status in {"red", "doomsday"} or regime.get("halt_new_entries"):
        return {"ok": False, "reason": f"league_disabled_by_regime:{status or 'unknown'}", "regime": regime}
    open_count = await db.ll_tickets.count_documents({"status": {"$in": ["OPEN", "HALTED"]}})
    today_count = await db.ll_tickets.count_documents({"date": _today()})
    if open_count >= MAX_OPEN_TICKETS:
        return {"ok": False, "reason": "max_open_tickets", "open": open_count}
    daily_limit = 1 if status == "downtrend" else MAX_DAILY_TICKETS
    if today_count >= daily_limit:
        return {"ok": False, "reason": "daily_ticket_budget_used", "today": today_count, "limit": daily_limit}
    duplicate = await db.ll_tickets.find_one({"ticker": t, "date": _today()}, {"_id": 1})
    if duplicate:
        return {"ok": False, "reason": "no_reentry_same_ticker_same_day"}
    latest_scan = await db.ll_scans.find_one({"_id": "current"}, {"_id": 0, "candidates": 1})
    candidate = next(
        (c for c in (latest_scan or {}).get("candidates", []) if _clean_ticker(c.get("ticker")) == t),
        {},
    )

    stop_price = round(max(entry_price * 0.60, entry_price - (entry_price * 0.40)), 4)
    doc = stamped({
        "ticket_id": f"llt-{t}-{_now().strftime('%Y%m%d%H%M%S')}",
        "book": "lottery",
        "ticker": t,
        "date": _today(),
        "variant": variant,
        "status": "OPEN",
        "entry_price": round(float(entry_price), 4),
        "entry_fill_price": round(float(entry_price), 4),
        "current_price": round(float(entry_price), 4),
        "peak_price": round(float(entry_price), 4),
        "trough_price": round(float(entry_price), 4),
        "ticket_notional": TICKET_NOTIONAL,
        "score": score if score is not None else candidate.get("score"),
        "entry_snapshot": {
            "score": score if score is not None else candidate.get("score"),
            "tier": candidate.get("tier"),
            "components": candidate.get("components") or {},
            "penalties": candidate.get("penalties") or [],
            "triggers": candidate.get("triggers") or [],
            "float_proxy": candidate.get("float_proxy"),
            "float_confidence": candidate.get("float_confidence"),
            "relative_volume": candidate.get("relative_volume"),
            "rotation": candidate.get("rotation"),
            "change_pct": candidate.get("change_pct"),
            "quote_age_seconds": candidate.get("quote_age_seconds"),
            "dilution": candidate.get("dilution") or {},
            "halt_status": candidate.get("halt_status"),
            "source": candidate.get("source"),
            "captured_at": _now().isoformat(),
        },
        "triggers": candidate.get("triggers") or [],
        "float_proxy": candidate.get("float_proxy"),
        "float_confidence": candidate.get("float_confidence"),
        "quote_age_seconds": candidate.get("quote_age_seconds"),
        "regime": regime,
        "ladder": [
            {"level": 0.30, "fraction": 1 / 3, "status": "WAITING"},
            {"level": 1.00, "fraction": 1 / 3, "status": "WAITING"},
            {"level": "trail_20pct", "fraction": 1 / 3, "status": "WAITING"},
        ],
        "stop_price": stop_price,
        "time_stop_date": (_now().date() + timedelta(days=7)).isoformat(),
        "opened_at": _now().isoformat(),
        "opened_by": reason,
        "haircut": {"entry_pct": ENTRY_HAIRCUT_PCT, "exit_pct": EXIT_HAIRCUT_PCT},
    })
    await db.ll_tickets.insert_one(doc)
    await log_activity(f"Lottery League ticket opened: {t} @ ${entry_price}", "info")
    doc.pop("_id", None)
    return {"ok": True, "ticket": doc}


async def refresh_settlements() -> dict[str, Any]:
    """Refresh open ticket marks and detect stale stop/time states."""
    from . import pricer

    db = get_db()
    tickets = await _tickets(active_only=True)
    updated = 0
    stop_hits = 0
    for ticket in tickets:
        ticker = ticket.get("ticker")
        price = await pricer.get_latest_close(ticker)
        if price is None:
            continue
        entry = _num(ticket.get("entry_price"), 0)
        peak = max(_num(ticket.get("peak_price"), entry), price)
        trough = min(x for x in [_num(ticket.get("trough_price"), entry), price, entry] if x > 0)
        update: dict[str, Any] = {
            "current_price": round(price, 4),
            "peak_price": round(peak, 4),
            "trough_price": round(trough, 4),
            "mark_updated_at": _now().isoformat(),
        }
        if entry and price <= _num(ticket.get("stop_price"), 0):
            update["risk_state"] = "STOP_TRIGGERED_REVIEW"
            stop_hits += 1
        if str(ticket.get("time_stop_date") or "") <= _today():
            update["risk_state"] = "TIME_STOP_REVIEW"
        await db.ll_tickets.update_one({"ticket_id": ticket["ticket_id"]}, {"$set": update})
        updated += 1
    return {"ok": True, "updated": updated, "open": len(tickets), "stop_reviews": stop_hits}


async def settle_ticket(ticket_id: str, exit_price: float, reason: str = "operator_settle") -> dict[str, Any]:
    db = get_db()
    ticket = await db.ll_tickets.find_one({"ticket_id": ticket_id}, {"_id": 0})
    if not ticket:
        return {"ok": False, "reason": "ticket_not_found"}
    entry = _num(ticket.get("entry_price"), 0)
    raw_pct = ((exit_price - entry) / entry * 100) if entry else 0
    haircut_pct = raw_pct - ROUND_TRIP_HAIRCUT_PCT
    update = {
        "status": "CLOSED",
        "exit_price": round(float(exit_price), 4),
        "exit_fill_price": round(float(exit_price), 4),
        "closed_at": _now().isoformat(),
        "exit_reason": reason,
        "raw_return_pct": round(raw_pct, 2),
        "haircut_return_pct": round(haircut_pct, 2),
        "realized_multiple": round(exit_price / entry, 3) if entry else None,
    }
    await db.ll_tickets.update_one({"ticket_id": ticket_id}, {"$set": update})
    try:
        from . import lottery_grader
        await lottery_grader.grade_closed_tickets()
    except Exception as exc:
        logger.warning("lottery grade on settle failed: %s", exc)
    return {"ok": True, "ticket_id": ticket_id, **update}


def _aggregate_grades(rows: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [r for r in rows if r.get("status") == "CLOSED" or r.get("closed_at")]
    open_rows = [r for r in rows if r.get("status") in {"OPEN", "HALTED"}]
    returns = [_num(r.get("haircut_return_pct"), 0) for r in closed]
    raw_returns = [_num(r.get("raw_return_pct"), 0) for r in closed]
    wins_30 = [r for r in returns if r >= 30]
    wins_100 = [r for r in returns if r >= 100]
    wins_300 = [r for r in returns if r >= 300]
    total = sum(returns)
    sorted_abs = sorted([abs(r) for r in returns], reverse=True)
    top1 = (sorted_abs[0] / sum(sorted_abs) * 100) if sorted_abs and sum(sorted_abs) else None
    top5 = (sum(sorted_abs[:5]) / sum(sorted_abs) * 100) if sorted_abs and sum(sorted_abs) else None
    ev = (total / len(closed)) if closed else None
    kill = "GATHERING"
    if len(closed) >= 60 and (ev or 0) <= 0:
        kill = "RETIRE_VARIANT"
    elif len(closed) >= 60:
        kill = "VALIDATED_POSITIVE"
    return {
        "total_tickets": len(rows),
        "open": len(open_rows),
        "closed": len(closed),
        "ev_per_ticket_pct_haircut": round(ev, 2) if ev is not None else None,
        "ev_per_ticket_pct_raw": round(sum(raw_returns) / len(raw_returns), 2) if raw_returns else None,
        "hit_rate_30": round(len(wins_30) / len(closed), 3) if closed else None,
        "hit_rate_100": round(len(wins_100) / len(closed), 3) if closed else None,
        "hit_rate_300": round(len(wins_300) / len(closed), 3) if closed else None,
        "median_ticket_pct": round(sorted(returns)[len(returns) // 2], 2) if returns else None,
        "top1_concentration_pct": round(top1, 1) if top1 is not None else None,
        "top5_concentration_pct": round(top5, 1) if top5 is not None else None,
        "kill_status": kill,
        "n_to_variant_decision": max(0, 60 - len(closed)),
        "haircut": {"entry_pct": ENTRY_HAIRCUT_PCT, "exit_pct": EXIT_HAIRCUT_PCT, "round_trip_pct": ROUND_TRIP_HAIRCUT_PCT},
    }


async def track_record() -> dict[str, Any]:
    rows = await _tickets(active_only=False)
    return _aggregate_grades(rows)


async def recent_picks(days: int = 14, tier: str | None = None) -> list[dict[str, Any]]:
    cutoff = (_now() - timedelta(days=days)).date().isoformat()
    rows = await _tickets(active_only=False)
    out = [r for r in rows if str(r.get("date") or "") >= cutoff]
    if tier:
        out = [r for r in out if r.get("tier") == tier]
    return out[:200]


async def board() -> dict[str, Any]:
    db = get_db()
    scan = await db.ll_scans.find_one({"_id": "current"}, {"_id": 0}) or {}
    tickets = await _tickets(active_only=False)
    active = [t for t in tickets if t.get("status") in {"OPEN", "HALTED"}]
    account_equity = await _account_equity()
    cap = account_equity * LEAGUE_CAP_PCT if account_equity else None
    deployed = sum(_num(t.get("ticket_notional"), TICKET_NOTIONAL) for t in active)
    regime = scan.get("regime") or await _latest_regime()
    status = str(regime.get("status") or regime.get("weather") or "unknown").lower()
    daily_limit = 1 if status == "downtrend" else MAX_DAILY_TICKETS
    if status in {"red", "doomsday"} or regime.get("halt_new_entries"):
        gate = {"status": "DISABLED", "reason": f"Regime {status} manages exits only", "color": "red"}
    else:
        gate = {"status": "ISSUANCE_OPEN", "reason": f"{daily_limit} ticket/day budget", "color": "green"}
    try:
        from . import lottery_grader
        truth = await lottery_grader.truth_board(limit=300)
    except Exception as exc:
        truth = {"ok": False, "reason": exc.__class__.__name__}
    return {
        "ok": True,
        "rubric_version": LL_RUBRIC_VERSION,
        "generated_at": _now().isoformat(),
        "scan": scan,
        "candidates": scan.get("candidates") or [],
        "tickets": active,
        "all_tickets": tickets[:300],
        "jackpot_board": _aggregate_grades(tickets),
        "book": {
            "ticket_notional": TICKET_NOTIONAL,
            "max_daily_tickets": daily_limit,
            "max_open_tickets": MAX_OPEN_TICKETS,
            "league_cap_pct": LEAGUE_CAP_PCT,
            "account_equity": account_equity or None,
            "cap_dollars": round(cap, 2) if cap is not None else None,
            "deployed_dollars": round(deployed, 2),
            "cap_usage_pct": round(deployed / cap * 100, 1) if cap else None,
        },
        "gate": gate,
        "truth_board": truth,
        "honesty": {
            "negative_skew_truth": "Lottery-profile stocks underperform on average; this book tests whether catalyst, float rotation, and structure lift the right tail enough to overcome the base rate.",
            "kill_criteria": "EV <= 0 after 60 graded tickets retires a variant; EV <= 0 after 150 League tickets retires the League.",
            "paper_fill_haircut": f"Headline grades subtract {ROUND_TRIP_HAIRCUT_PCT}% round trip: +{ENTRY_HAIRCUT_PCT}% entry and -{EXIT_HAIRCUT_PCT}% exit.",
            "data_limits": "Free float is a proxy unless verified from SEC/companyfacts; IEX/free intraday data is not Level 2 tape.",
        },
    }


async def league_candidates() -> dict[str, Any]:
    db = get_db()
    scan = await db.ll_scans.find_one({"_id": "current"}, {"_id": 0}) or {}
    return {"ok": True, "scan": scan, "candidates": scan.get("candidates") or []}


async def league_tickets(active_only: bool = False) -> dict[str, Any]:
    return {"ok": True, "tickets": await _tickets(active_only=active_only)}


async def add_manual_play(ticker: str, entry_price: float, lottery_score: int | None = None,
                          risk_amount: float | None = None) -> dict[str, Any]:
    """Legacy endpoint now creates a League ticket."""
    return await issue_ticket(ticker, entry_price, score=lottery_score, reason="manual_legacy_endpoint")


async def settle_manual_play(ticker: str, exit_price: float, play_date: str) -> dict[str, Any]:
    tickets = await _tickets(active_only=True)
    match = next((t for t in tickets if t.get("ticker") == _clean_ticker(ticker) and t.get("date") == play_date), None)
    if not match:
        return {"ok": False, "reason": "ticket_not_found"}
    return await settle_ticket(match["ticket_id"], exit_price, reason="manual_legacy_settle")


async def update_manual_peak_marks(refresh: bool = True) -> int:
    result = await refresh_settlements() if refresh else {"updated": 0}
    return int(result.get("updated") or 0)


async def list_manual_plays(active_only: bool = False) -> list[dict[str, Any]]:
    return await _tickets(active_only=active_only)


async def lottery_manual_track_record() -> dict[str, Any]:
    record = await track_record()
    rows = await _tickets(active_only=False)
    record["history"] = [r for r in rows if r.get("status") == "CLOSED" or r.get("closed_at")]
    return record


async def manual_settle_track_pick(ticker: str, exit_ask: float, play_date: str) -> dict[str, Any]:
    return await settle_manual_play(ticker, exit_ask, play_date)


async def delete_track_pick(ticker: str, play_date: str) -> dict[str, Any]:
    db = get_db()
    res = await db.ll_tickets.delete_one({"ticker": _clean_ticker(ticker), "date": play_date})
    return {"ok": bool(res.deleted_count), "deleted": res.deleted_count}
