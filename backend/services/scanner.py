"""Scanner: fetch all signals (market + gov + congress) -> aggregate ->
pre-filter (2+) -> compute risk + targets + squeeze + time_target in Python ->
single batched Claude call (only thesis/conviction/horizon/stop_loss). 24h cache."""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from . import claude_service, congress, conviction, dark_horse, earnings_engine, \
    learning_engine, lottery, macro_pulse, options_engine, pnl_tracker, pricer, \
    risk_target, squeeze as squeeze_mod, time_target, usaspending, x_factor
from .db import get_db, log_activity, stamped
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


_STRATEGY_DEFAULT_NAMES = {
    "LONG_CALL": "Long Call",
    "BULL_CALL_SPREAD": "Bull Call Spread",
    "BEAR_PUT_SPREAD": "Bear Put Spread",
    "LOTTERY_CALL": "Lottery Call",
    "AVOID_OPTIONS": "Avoid Options",
}


def _strategy_default_name(strat: str | None) -> str:
    return _STRATEGY_DEFAULT_NAMES.get(strat or "", "Long Call")


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

    # Universe size = total distinct tickers swept across all sources before
    # the 2+ signal pre-filter. This is what the dashboard shows as the
    # "PRE-FILTER UNIVERSE" tile so the user sees coverage breadth.
    universe_size = len(by_ticker)

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

    # Options intelligence — bounded concurrency (yfinance is rate-sensitive)
    opt_sem = asyncio.Semaphore(2)
    async def _bounded_opts(stock):
        async with opt_sem:
            try:
                return await options_engine.analyze_ticker(stock)
            except Exception as e:
                logger.warning("options analyze failed for %s: %s", stock.get("ticker"), e)
                return None

    if enriched:
        opts_results = await asyncio.gather(*[_bounded_opts(s) for s in enriched])
        for stock, opts in zip(enriched, opts_results):
            stock["options"] = opts
            # UNUSUAL_FLOW signal injection (+2 pts), CALL_SWEEP (+3 pts)
            if opts and opts.get("flow"):
                f = opts["flow"]
                if (f.get("unusual_calls") or f.get("unusual_puts")) and "UNUSUAL_FLOW" not in stock["signals"]:
                    stock["signals"].append("UNUSUAL_FLOW")
                if f.get("call_sweep") and "CALL_SWEEP" not in stock["signals"]:
                    stock["signals"].append("CALL_SWEEP")

    # Single batched Claude call (only thesis/conviction/horizon/stop_loss/entry/score)
    analyses = await claude_service.analyze_batch(enriched)
    cache_hits = sum(1 for a in analyses if a.get("cached"))
    fresh_calls = (1 if (len(analyses) - cache_hits) > 0 else 0)  # 1 batched call total

    # Merge Claude output into enriched dicts (keyed by ticker)
    by_t = {a["ticker"]: a for a in analyses}
    final: list[dict[str, Any]] = []
    fy_active = time_target.fiscal_year_multiplier_active()
    _ = fy_active  # used for scan_doc below
    # Load live learning weights for this scan
    live_weights = await learning_engine.get_weights()

    for c in enriched:
        a = by_t.get(c["ticker"])
        if not a:
            continue
        # Apply FY seasonality multiplier on signal_score (gov signals only)
        score = a.get("signal_score") or 0
        score, fy_applied = time_target.apply_fy_multiplier(c["signals"], score)
        # +2 for UNUSUAL_FLOW, +3 for CALL_SWEEP (additive bonuses)
        if "UNUSUAL_FLOW" in c["signals"]:
            score = min(10, score + 2)
        if "CALL_SWEEP" in c["signals"]:
            score = min(10, score + 3)
        # AXIOM Learning Engine — additive points per signal from live weights
        # Capped at 10 so existing dashboards keep the 0-10 scale; the raw
        # learning_score is also surfaced separately for the Learning UI.
        learning_score = sum(live_weights.get(s, 0) for s in c["signals"])
        if c.get("congress_summary") and (c["congress_summary"].get("any_committee_match")):
            learning_score += live_weights.get("committee_match_bonus", 0)
        if (c.get("squeeze") or {}).get("score", 0) >= 65:
            learning_score += live_weights.get("squeeze_bonus", 0)
        # Re-compute time target now that we have catalyst
        tt = time_target.compute_time_target(c["signals"], a.get("catalyst_date", ""))
        # Stop loss: Claude or computed fallback
        stop_loss = a.get("stop_loss") or risk_target.compute_stop_loss(c["fundamentals"], c["risk"])
        opts = c.get("options")
        # Inject Claude-provided strategy_name + one-liner into options block
        if opts:
            opts = {**opts}
            opts["strategy_name"] = a.get("options_strategy_name") or _strategy_default_name(opts.get("strategy"))
            opts["one_liner"] = a.get("options_one_liner") or opts.get("strategy_reason", "")
            opts["hold_stock_instead"] = bool(a.get("hold_stock_instead")) or opts.get("strategy") == "AVOID_OPTIONS"
        final.append({
            "ticker": c["ticker"],
            "signals": c["signals"],
            "signal_score": score,
            "learning_score": round(learning_score, 1),
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
            "options": opts,
        })
    final.sort(key=lambda x: (x.get("signal_score", 0), x.get("targets", {}).get("upside_blended") or 0), reverse=True)

    finished = datetime.now(timezone.utc)
    db = get_db()
    scan_doc = stamped({
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
        "universe_size": universe_size,
        "pre_filter_passed": pre_filter_count,
        "claude_calls_made": fresh_calls,
        "claude_cache_hits": cache_hits,
        "results": final,
        "budget_surges": gov.get("budget_surges", []),
    })

    # ─────────── AXIOM v3.2 — Post-scan intelligence pipeline ───────────
    v32 = await _run_v32_pipeline(final)
    scan_doc.update({
        "v32": v32,
        "max_conviction": v32.get("max_conviction") or {},
        "narrative_locks": v32.get("narrative_locks") or [],
        "dark_horse_alerts": v32.get("dark_horse") or [],
        "x_factor_alerts": v32.get("x_factor") or [],
        "lottery_picks": v32.get("lottery") or [],
        "macro_pulse": v32.get("macro") or {},
        "earnings_week": v32.get("earnings_summary") or {},
    })

    await db.scan_results.insert_one(dict(scan_doc))
    # Record P&L tracking rows (one per ticker, signal & options)
    try:
        await pnl_tracker.record_scan_picks(scan_doc)
    except Exception as e:
        logger.warning("P&L recording failed: %s", e)
    # Refresh combo stats with the latest live data so the Learning page
    # reflects every scan immediately
    try:
        await learning_engine.refresh_combo_stats_live()
    except Exception as e:
        logger.warning("Live combo stats refresh failed: %s", e)
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



async def _run_v32_pipeline(final: list[dict[str, Any]]) -> dict[str, Any]:
    """Run all 7 AXIOM v3.2 post-scan modules on the scan results.
    Returns a dict with keys: dark_horse, x_factor, lottery, macro,
    earnings_summary, narrative_locks, max_conviction."""
    if not final:
        return {}
    tickers = [r["ticker"] for r in final if r.get("ticker")]
    if not tickers:
        return {}

    # 1) Dark Horse — needs OHLC + ADV context per ticker
    async def _build_dh_context():
        ctx: dict[str, dict] = {}
        # Pull recent history for ADV + last close/prev close
        hist = await pricer.batch_history(tickers, days=35)
        for t in tickers:
            series = hist.get(t) or {}
            if not series:
                continue
            dates = sorted(series.keys())
            if len(dates) < 5:
                continue
            closes = [series[d] for d in dates]
            close = closes[-1]
            prev_close = closes[-2] if len(closes) > 1 else None
            ctx[t] = {
                "close": close,
                "prev_close": prev_close,
                "vwap_proxy": close,  # fall back to close if no intraday
                "avg_volume_30d": None,  # filled below
            }
        # Volume ADV via yfinance (one batch)
        try:
            import yfinance as yf
            def _vol():
                data = yf.download(tickers=" ".join(tickers), period="35d",
                                    interval="1d", progress=False, threads=True,
                                    group_by="ticker", auto_adjust=False)
                if data is None or len(data) == 0:
                    return {}
                out = {}
                if len(tickers) == 1:
                    try:
                        out[tickers[0]] = float(data["Volume"].dropna().tail(30).mean())
                    except Exception:
                        pass
                    return out
                for t in tickers:
                    try:
                        out[t] = float(data[t]["Volume"].dropna().tail(30).mean())
                    except Exception:
                        continue
                return out
            vol_map = await asyncio.get_event_loop().run_in_executor(None, _vol)
            for t, v in vol_map.items():
                if t in ctx:
                    ctx[t]["avg_volume_30d"] = v
        except Exception as e:
            logger.warning("ADV fetch failed: %s", e)
        return ctx

    # 2) Run independent modules in parallel
    dh_ctx_task = _build_dh_context()
    xf_task = x_factor.batch_evaluate(tickers)
    macro_task = macro_pulse.upcoming_events()
    scan_set = set(tickers)
    earnings_task = earnings_engine.current_week_with_probability(scan_tickers=scan_set)

    dh_ctx, xf_alerts, macro_events, earnings_week = await asyncio.gather(
        dh_ctx_task, xf_task, macro_task, earnings_task,
        return_exceptions=True,
    )
    if isinstance(dh_ctx, Exception):
        dh_ctx = {}
    if isinstance(xf_alerts, Exception):
        xf_alerts = []
    if isinstance(macro_events, Exception):
        macro_events = []
    if isinstance(earnings_week, Exception):
        earnings_week = {"by_day": {}}

    # Dark Horse needs context; eval now
    try:
        dh_alerts = await dark_horse.batch_evaluate(tickers, dh_ctx)
    except Exception as e:
        logger.warning("Dark Horse batch failed: %s", e)
        dh_alerts = []

    # Build lookup maps
    dh_by_ticker = {a["ticker"]: a for a in dh_alerts}
    xf_by_ticker = {a["ticker"]: a for a in xf_alerts}

    # Stitch alerts back into the scan result objects so the dashboard
    # can render badges inline
    earnings_tickers = set()
    for day_rows in (earnings_week.get("by_day") or {}).values():
        for r in day_rows:
            earnings_tickers.add(r["ticker"])
    for r in final:
        t = r["ticker"]
        if t in dh_by_ticker:
            r["dark_horse"] = dh_by_ticker[t]
        if t in xf_by_ticker:
            r["x_factor"] = xf_by_ticker[t]
        if t in earnings_tickers:
            r["earnings_this_week"] = True

    # 3) Lottery scoring (now that dark_horse is on results)
    lottery_picks = await lottery.evaluate_for_scan(final)
    await lottery.log_picks(lottery_picks)
    lottery_by_ticker = {p["ticker"]: p["score"] for p in lottery_picks}
    # Stitch lottery score into final results
    for r in final:
        t = r["ticker"]
        if t in lottery_by_ticker:
            r["lottery_score"] = lottery_by_ticker[t]
            r["lottery_tier"] = next((p["tier"] for p in lottery_picks if p["ticker"] == t), None)

    # 4) Conviction + Narrative Lock
    conv = await conviction.compute_for_scan(
        final, dh_by_ticker, xf_by_ticker, earnings_tickers, lottery_by_ticker,
    )
    # Stitch
    nl_set = {p["ticker"] for p in conv["narrative_locks"]}
    top3_set = {p["ticker"] for p in conv["top3"]}
    for r in final:
        if r["ticker"] in nl_set:
            r["narrative_lock"] = True
            # Per spec: Narrative Lock auto-elevates lottery tier to JACKPOT
            r["lottery_tier"] = "JACKPOT"
            # Add 20 to axiom_score
            if r.get("signal_score") is not None:
                r["signal_score_pre_nl"] = r["signal_score"]
                r["signal_score"] = r["signal_score"] + 20
        if r["ticker"] in top3_set:
            r["max_conviction"] = True

    return {
        "dark_horse": dh_alerts,
        "x_factor": xf_alerts,
        "lottery": lottery_picks,
        "macro": {"events": macro_events,
                    "imminent": [e for e in macro_events if e.get("is_imminent")]},
        "earnings_summary": {
            "total": earnings_week.get("total", 0),
            "week_of": earnings_week.get("week_of"),
            "by_day_counts": {d: len(rows) for d, rows in (earnings_week.get("by_day") or {}).items()},
        },
        "narrative_locks": conv["narrative_locks"],
        "max_conviction": {"top3": conv["top3"]},
    }
