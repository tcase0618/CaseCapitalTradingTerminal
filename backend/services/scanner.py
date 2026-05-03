"""Scanner: fetch all signals (market + gov + congress) -> aggregate ->
pre-filter (2+) -> compute risk + targets + squeeze + time_target in Python ->
single batched Claude call (only thesis/conviction/horizon/stop_loss). 24h cache."""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from . import claude_service, congress, risk_target, squeeze as squeeze_mod, \
    time_target, usaspending
from .db import get_db, log_activity
from .scrapers import collect_all_signals

logger = logging.getLogger(__name__)


def _aggregate_market_signals(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_ticker: dict[str, dict[str, Any]] = {}

    for c in raw.get("insider_clusters", []):
        t = c["ticker"]
        x = by_ticker.setdefault(t, {"ticker": t, "signals": [], "company": c.get("company")})
        x["signals"].append("insider_cluster_buy")
        x["insider_summary"] = {
            "insider_count": c.get("insider_count"),
            "buy_count": c.get("buy_count"),
            "total_value_usd": c.get("total_value_usd"),
            "latest_filing": c.get("latest_filing"),
        }

    for s in raw.get("high_short_interest", []):
        t = s["ticker"]
        x = by_ticker.setdefault(t, {"ticker": t, "signals": []})
        if "high_short_interest" not in x["signals"]:
            x["signals"].append("high_short_interest")
        x["short_summary"] = {"short_float_pct": s.get("short_float_pct")}

    for e in raw.get("upcoming_earnings", []):
        t = e["ticker"]
        x = by_ticker.setdefault(t, {"ticker": t, "signals": []})
        if "upcoming_earnings" not in x["signals"]:
            x["signals"].append("upcoming_earnings")
        x["earnings_summary"] = {"earnings_date": e.get("earnings_date")}

    return by_ticker


def _merge_gov_signals(by_ticker: dict[str, dict[str, Any]],
                        gov: dict[str, Any]) -> dict[str, dict[str, Any]]:
    for ticker, info in gov.get("by_ticker", {}).items():
        x = by_ticker.setdefault(ticker, {"ticker": ticker, "signals": []})
        for s in info.get("signals", []):
            if s not in x["signals"] and s != "CONCENTRATION_WIN_PROVISIONAL":
                x["signals"].append(s)
        x["gov_summary"] = info.get("gov_summary", {})
        x["contracts"] = info.get("contracts", [])
        x["concentration_provisional"] = "CONCENTRATION_WIN_PROVISIONAL" in info.get("signals", [])
    return by_ticker


def _finalize_signals_and_filter(by_ticker: dict[str, dict[str, Any]],
                                  fundamentals: dict[str, dict]) -> list[dict[str, Any]]:
    """Apply 2+ signal pre-filter + finalize CONCENTRATION_WIN with mkt-cap."""
    out: list[dict[str, Any]] = []
    for ticker, x in by_ticker.items():
        # Finalize CONCENTRATION_WIN: requires mkt cap < $2B
        if x.get("concentration_provisional"):
            mc = (fundamentals.get(ticker) or {}).get("market_cap")
            if mc and mc < 2_000_000_000:
                if "CONCENTRATION_WIN" not in x["signals"]:
                    x["signals"].append("CONCENTRATION_WIN")
        x.pop("concentration_provisional", None)
        unique = list(dict.fromkeys(x["signals"]))
        x["signals"] = unique
        if len(unique) >= 2:
            out.append(x)
    out.sort(key=lambda v: len(v["signals"]), reverse=True)
    return out


def _short_pct(x: dict) -> float | None:
    s = (x.get("short_summary") or {}).get("short_float_pct")
    if isinstance(s, (int, float)):
        return float(s)
    return None


def _insider_buys(x: dict) -> int:
    return int((x.get("insider_summary") or {}).get("buy_count") or 0)


def _merge_congress_signals(by_ticker: dict[str, dict[str, Any]],
                              cong: dict[str, Any]) -> dict[str, dict[str, Any]]:
    for ticker, info in cong.get("by_ticker", {}).items():
        x = by_ticker.setdefault(ticker, {"ticker": ticker, "signals": []})
        if "CONGRESSIONAL_BUY" not in x["signals"]:
            x["signals"].append("CONGRESSIONAL_BUY")
        x["congress_summary"] = {
            "buyer_count": len(info["buys"]),
            "any_committee_match": info["any_match"],
            "max_weight": info["max_weight"],
            "buyers": [b["name"] for b in info["buys"][:3]],
        }
    return by_ticker


async def run_scan(triggered_by: str = "manual") -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    await log_activity(f"Scan started ({triggered_by})", "info")

    raw, gov, cong = await asyncio.gather(
        collect_all_signals(),
        usaspending.detect_gov_signals(),
        congress.detect_congress_signals(),
    )

    by_ticker = _aggregate_market_signals(raw)
    by_ticker = _merge_gov_signals(by_ticker, gov)
    by_ticker = _merge_congress_signals(by_ticker, cong)

    # Determine which tickers need fundamentals: those with 2+ signals OR
    # those that have concentration_provisional (need mkt-cap to finalize).
    needs_fund: set[str] = set()
    for v in by_ticker.values():
        if len(set(v["signals"])) >= 2:
            needs_fund.add(v["ticker"])
        elif v.get("concentration_provisional"):
            needs_fund.add(v["ticker"])

    # yfinance is rate-sensitive under high concurrency — limit to 2 at a time
    sem = asyncio.Semaphore(2)
    async def _bounded(tk):
        async with sem:
            return await risk_target.fetch_fundamentals(tk)
    fundamentals: dict[str, dict] = {}
    keys = sorted(needs_fund)
    if keys:
        vals = await asyncio.gather(*[_bounded(k) for k in keys], return_exceptions=True)
        for k, v in zip(keys, vals):
            if isinstance(v, Exception):
                fundamentals[k] = {}
            else:
                fundamentals[k] = v or {}

    # Apply 2+ signal pre-filter (after CONCENTRATION_WIN finalization)
    candidates = _finalize_signals_and_filter(by_ticker, fundamentals)
    pre_filter_count = len(candidates)

    await log_activity(
        f"Aggregated: {len(raw['insider_clusters'])} insider, "
        f"{len(raw['high_short_interest'])} short, "
        f"{len(raw['upcoming_earnings'])} earnings, "
        f"{len(gov.get('by_ticker', {}))} gov-public -> {pre_filter_count} candidates 2+ signals",
        "info",
    )

    # Compute risk + targets + squeeze + time_target in pure Python (zero Claude tokens)
    enriched: list[dict[str, Any]] = []
    for c in candidates:
        ticker = c["ticker"]
        fund = fundamentals.get(ticker, {}) or {}
        gov_summary = c.get("gov_summary") or {}
        short_pct_val = _short_pct(c)

        # Persist short observation for squeeze rate-of-change tracking
        await squeeze_mod.record_short_observation(ticker, short_pct_val)

        risk = risk_target.compute_risk(
            fund, c["signals"], gov_summary,
            short_pct=short_pct_val, insider_buys=_insider_buys(c),
        )
        targets = risk_target.compute_targets(fund, c["signals"], gov_summary)
        sq = await squeeze_mod.compute_squeeze(ticker, short_pct_val, fund)
        tt = time_target.compute_time_target(c["signals"], "")

        c["fundamentals"] = fund
        c["risk"] = risk
        c["targets"] = targets
        c["squeeze"] = sq
        c["time_target"] = tt
        c["price"] = fund.get("price")
        c["market_cap"] = fund.get("market_cap")
        c["sector"] = fund.get("sector")
        c["beta"] = fund.get("beta")
        c["rev_ttm"] = fund.get("trailing_revenue")
        c["short_pct"] = short_pct_val
        c["insider_buys"] = _insider_buys(c)
        c["risk_score"] = risk["score"]
        c["target_low"] = targets.get("target_low")
        c["target_high"] = targets.get("target_high")
        c["target_blended"] = targets.get("target_blended")
        c["squeeze_score"] = sq.get("score")
        c["contracts_brief"] = [
            {"agency": ct.get("agency"), "amount": ct.get("amount")}
            for ct in (c.get("contracts") or [])[:2]
        ]
        enriched.append(c)

    # Single batched Claude call (only thesis/conviction/horizon/stop_loss/entry/score)
    analyses = await claude_service.analyze_batch(enriched)
    cache_hits = sum(1 for a in analyses if a.get("cached"))
    fresh_calls = (1 if (len(analyses) - cache_hits) > 0 else 0)  # 1 batched call total

    # Merge Claude output into enriched dicts (keyed by ticker)
    by_t = {a["ticker"]: a for a in analyses}
    final: list[dict[str, Any]] = []
    fy_active = time_target.fiscal_year_multiplier_active()
    _ = fy_active  # used for scan_doc below
    for c in enriched:
        a = by_t.get(c["ticker"])
        if not a:
            continue
        # Apply FY seasonality multiplier on signal_score (gov signals only)
        score = a.get("signal_score") or 0
        score, fy_applied = time_target.apply_fy_multiplier(c["signals"], score)
        # Re-compute time target now that we have catalyst
        tt = time_target.compute_time_target(c["signals"], a.get("catalyst_date", ""))
        # Stop loss: Claude or computed fallback
        stop_loss = a.get("stop_loss") or risk_target.compute_stop_loss(c["fundamentals"], c["risk"])
        final.append({
            "ticker": c["ticker"],
            "signals": c["signals"],
            "signal_score": score,
            "fy_multiplier_applied": fy_applied,
            "thesis": a.get("thesis", ""),
            "entry_low": a.get("entry_low"),
            "entry_high": a.get("entry_high"),
            "catalyst_date": a.get("catalyst_date", ""),
            "conviction": a.get("conviction", "medium"),
            "time_horizon": a.get("time_horizon", "medium"),
            "time_target": tt,
            "stop_loss": stop_loss,
            "cached": a.get("cached", False),
            "price": c["price"],
            "market_cap": c["market_cap"],
            "sector": c["sector"],
            "risk": c["risk"],
            "targets": c["targets"],
            "squeeze": c["squeeze"],
            "contracts": c.get("contracts") or [],
            "gov_summary": c.get("gov_summary") or {},
            "congress_summary": c.get("congress_summary"),
            "insider_summary": c.get("insider_summary"),
            "short_summary": c.get("short_summary"),
            "earnings_summary": c.get("earnings_summary"),
        })
    final.sort(key=lambda x: (x.get("signal_score", 0), x.get("targets", {}).get("upside_blended") or 0), reverse=True)

    finished = datetime.now(timezone.utc)
    db = get_db()
    scan_doc = {
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_sec": round((finished - started).total_seconds(), 2),
        "triggered_by": triggered_by,
        "raw_counts": {
            "insider_clusters": len(raw["insider_clusters"]),
            "high_short_interest": len(raw["high_short_interest"]),
            "upcoming_earnings": len(raw["upcoming_earnings"]),
            "gov_public_tickers": len(gov.get("by_ticker", {})),
        },
        "pre_filter_passed": pre_filter_count,
        "claude_calls_made": fresh_calls,
        "claude_cache_hits": cache_hits,
        "results": final,
        "budget_surges": gov.get("budget_surges", []),
    }
    await db.scan_results.insert_one(dict(scan_doc))
    await db.bot_state.update_one(
        {"_id": "state"},
        {"$set": {
            "last_scan_at": finished.isoformat(),
            "last_scan_summary": {
                "pre_filter_passed": pre_filter_count,
                "results_count": len(final),
                "claude_calls_made": fresh_calls,
                "claude_cache_hits": cache_hits,
            },
        }},
        upsert=True,
    )
    await log_activity(
        f"Scan complete: {len(final)} analyses ({fresh_calls} batched Claude call, "
        f"{cache_hits} cached) in {scan_doc['duration_sec']}s",
        "success",
    )
    scan_doc.pop("_id", None)
    return scan_doc


async def latest_scan() -> dict[str, Any] | None:
    db = get_db()
    return await db.scan_results.find_one({}, {"_id": 0}, sort=[("finished_at", -1)])


async def run_gov_scan_only(triggered_by: str = "manual") -> dict[str, Any]:
    """For /scan_gov — gov contracts only, separate from full scan."""
    started = datetime.now(timezone.utc)
    gov = await usaspending.detect_gov_signals()
    by_ticker = gov.get("by_ticker", {})
    # Compute risk/targets for each public-company ticker
    out = []
    if by_ticker:
        keys = list(by_ticker.keys())
        funds = await asyncio.gather(*[risk_target.fetch_fundamentals(t) for t in keys])
        for ticker, fund in zip(keys, funds):
            info = by_ticker[ticker]
            signals = [s for s in info.get("signals", []) if s != "CONCENTRATION_WIN_PROVISIONAL"]
            # finalize concentration
            if "CONCENTRATION_WIN_PROVISIONAL" in info.get("signals", []):
                mc = (fund or {}).get("market_cap")
                if mc and mc < 2_000_000_000:
                    signals.append("CONCENTRATION_WIN")
            risk = risk_target.compute_risk(fund or {}, signals, info.get("gov_summary"),
                                              short_pct=None, insider_buys=0)
            targets = risk_target.compute_targets(fund or {}, signals, info.get("gov_summary"))
            out.append({
                "ticker": ticker, "signals": signals,
                "price": (fund or {}).get("price"),
                "market_cap": (fund or {}).get("market_cap"),
                "risk": risk, "targets": targets,
                "contracts": info.get("contracts", []),
                "gov_summary": info.get("gov_summary", {}),
            })
    out.sort(key=lambda x: x["risk"]["score"], reverse=False)
    return {
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "results": out,
        "budget_surges": gov.get("budget_surges", []),
        "triggered_by": triggered_by,
    }
