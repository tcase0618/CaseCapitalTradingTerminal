"""Scanner: fetch all signals (market + gov + congress) -> aggregate ->
pre-filter (2+) -> compute risk + targets + squeeze + time_target in Python ->
single batched Claude call (only thesis/conviction/horizon/stop_loss). 24h cache."""
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from . import claude_service, congress, conviction, dark_horse, earnings_engine, \
    learning_engine, lottery, macro_pulse, options_engine, pnl_tracker, pricer, \
    risk_target, squeeze as squeeze_mod, ticker_hygiene, time_target, usaspending, x_factor
from .db import get_db, log_activity, stamped
from .scrapers import collect_all_signals

logger = logging.getLogger(__name__)


def _execution_enabled() -> bool:
    return os.environ.get("ENABLE_TRADE_EXECUTION", "false").strip().lower() in {"1", "true", "yes", "on"}


def _scan_signature(rows: list[dict[str, Any]]) -> str:
    """Stable fingerprint for detecting repeated scan evidence."""
    compact = []
    for row in rows:
        compact.append({
            "ticker": row.get("ticker"),
            "signals": sorted(str(s) for s in (row.get("signals") or [])),
            "signal_score": row.get("signal_score"),
            "trade_score": row.get("trade_score"),
            "price": row.get("price"),
            "entry_low": row.get("entry_low"),
            "entry_high": row.get("entry_high"),
            "stop_loss": row.get("stop_loss"),
            "target_blended": row.get("target_blended") or (row.get("targets") or {}).get("target_blended"),
            "sector": row.get("sector"),
        })
    payload = json.dumps(sorted(compact, key=lambda r: str(r.get("ticker") or "")), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
        fund = fundamentals.get(ticker) or {}
        company_identity = (
            x.get("company")
            or fund.get("name")
            or (x.get("gov_summary") or {}).get("prime")
            or (x.get("insider_summary") or {}).get("company")
        )
        if len(str(ticker or "")) == 1 and not company_identity:
            continue
        # Finalize CONCENTRATION_WIN: requires mkt cap < $2B
        if x.get("concentration_provisional"):
            mc = fund.get("market_cap")
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
    live_price_meta: dict[str, dict[str, Any]] = {}
    if candidates:
        try:
            live_price_meta = await pricer.batch_live_price_meta([c["ticker"] for c in candidates])
        except Exception as e:
            logger.warning("scanner live price overlay failed: %s", e)
            live_price_meta = {}

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
        fund = dict(fundamentals.get(ticker, {}) or {})
        price_meta = live_price_meta.get(ticker) or {}
        if price_meta.get("price"):
            fund["price"] = float(price_meta["price"])
        if price_meta:
            fund["price_source"] = price_meta.get("source")
            fund["price_timestamp"] = price_meta.get("provider_ts")
            fund["price_age_seconds"] = price_meta.get("age_seconds")
            fund["price_fresh"] = price_meta.get("fresh")
            fund["premarket_confirmed"] = price_meta.get("premarket_confirmed")
            fund["price_warning"] = price_meta.get("warning")
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
        c["price_meta"] = price_meta
        c["price_source"] = price_meta.get("source") if price_meta else None
        c["price_timestamp"] = price_meta.get("provider_ts") if price_meta else None
        c["price_age_seconds"] = price_meta.get("age_seconds") if price_meta else None
        c["price_fresh"] = price_meta.get("fresh") if price_meta else False
        c["premarket_confirmed"] = price_meta.get("premarket_confirmed") if price_meta else False
        c["price_warning"] = price_meta.get("warning") if price_meta else "no_live_price_meta"
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
    claude_disabled = os.environ.get("DISABLE_CLAUDE_ANALYSIS", "").strip().lower() in {"1", "true", "yes", "on"}
    fresh_calls = 0 if claude_disabled else (1 if (len(analyses) - cache_hits) > 0 else 0)  # 1 batched call total

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
            "company_name": c.get("company") or c["fundamentals"].get("name"),
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
            "price_source": c.get("price_source"),
            "price_timestamp": c.get("price_timestamp"),
            "price_age_seconds": c.get("price_age_seconds"),
            "price_fresh": c.get("price_fresh"),
            "premarket_confirmed": c.get("premarket_confirmed"),
            "price_warning": c.get("price_warning"),
            "price_meta": c.get("price_meta") or {},
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
    hygiene = ticker_hygiene.filter_rows(final)
    final = hygiene["rows"]
    await ticker_hygiene.record_rejections("scanner_final", hygiene["rejected"])
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
        "ticker_hygiene": {
            "rejected_count": hygiene["rejected_count"],
            "rejected": hygiene["rejected"][:50],
        },
        "claude_calls_made": fresh_calls,
        "claude_cache_hits": cache_hits,
        "results": final,
        "budget_surges": gov.get("budget_surges", []),
    })

    # ─────────── AXIOM v3.2 — Post-scan intelligence pipeline ───────────
    v32 = await _run_v32_pipeline(final)
    try:
        from . import trade_floor as _tf_regime
        regime_snapshot = await asyncio.wait_for(_tf_regime.regime_status(), timeout=8.0)
    except Exception:
        regime_snapshot = {"status": "unknown", "reason": "regime_unavailable"}
    for r in final:
        r["regime"] = regime_snapshot.get("status")
        r["regime_playbook"] = regime_snapshot.get("playbook")
    scan_doc.update({
        "v32": v32,
        "regime": regime_snapshot,
        "max_conviction": v32.get("max_conviction") or {},
        "narrative_locks": v32.get("narrative_locks") or [],
        "dark_horse_alerts": v32.get("dark_horse") or [],
        "x_factor_alerts": v32.get("x_factor") or [],
        "lottery_picks": v32.get("lottery") or [],
        "macro_pulse": v32.get("macro") or {},
        "earnings_week": v32.get("earnings_summary") or {},
    })

    # v5.0 — attach Trade Score (computed from Trade Floor Engine weights)
    # MUST happen BEFORE inserting scan_doc so DB rows carry trade_score.
    try:
        from . import trade_floor_learning as _tfle
        for r in final:
            sigs = r.get("signals") or {}
            r["trade_score"] = await _tfle.get_trade_score(sigs)
    except Exception as e:
        logger.warning("trade_score attach failed: %s", e)

    signature = _scan_signature(final)
    previous = await db.scan_results.find_one(
        {},
        {"_id": 0, "finished_at": 1, "triggered_by": 1, "scan_signature": 1, "freshness": 1},
        sort=[("finished_at", -1)],
    )
    previous_signature = (previous or {}).get("scan_signature") or ((previous or {}).get("freshness") or {}).get("signature")
    duplicate = bool(previous_signature and previous_signature == signature)
    price_rows = [r for r in final if r.get("price")]
    fresh_price_rows = [r for r in price_rows if r.get("price_fresh")]
    premarket_rows = [r for r in price_rows if r.get("premarket_confirmed")]
    stale_price_rows = [r for r in price_rows if r.get("price_warning")]
    scan_doc["scan_signature"] = signature
    scan_doc["freshness"] = {
        "status": "DUPLICATE_SIGNATURE" if duplicate else "FRESH_SIGNATURE",
        "signature": signature,
        "duplicate_of": (previous or {}).get("finished_at") if duplicate else None,
        "previous_triggered_by": (previous or {}).get("triggered_by") if duplicate else None,
        "blocks_trading": False,
        "price_rows": len(price_rows),
        "fresh_price_rows": len(fresh_price_rows),
        "premarket_confirmed_rows": len(premarket_rows),
        "stale_price_rows": len(stale_price_rows),
        "detail": (
            "Fresh scan completed but evidence fingerprint matched the previous scan; treat as unchanged source evidence, not an old scan record."
            if duplicate else
            "Fresh scan completed with a new evidence fingerprint."
        ),
    }
    try:
        from . import candidate_ledger
        ledger = await candidate_ledger.build_from_scan(scan_doc, include_external=False, persist=False)
        scan_doc["candidate_ledger"] = {
            "cycle_id": ledger.get("cycle_id"),
            "summary": ledger.get("summary") or {},
            "candidates": ledger.get("candidates") or [],
        }
    except Exception as e:
        logger.warning("candidate ledger attach failed: %s", e)
        scan_doc["candidate_ledger"] = {
            "cycle_id": None,
            "summary": {"total": len(final), "error": str(e)},
            "candidates": [],
        }
    await db.scan_results.insert_one(dict(scan_doc))
    try:
        from . import postgres_store
        await postgres_store.mirror_document("scan_results", scan_doc)
    except Exception:
        pass
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
    # AXIOM Pharma — fully isolated parallel pipeline. Errors here NEVER
    # affect the main scan record.
    try:
        from . import pharma as _pharma
        asyncio.create_task(_pharma.run_pharma_scan(triggered_by="main_scan"))
    except Exception as e:
        logger.warning("Pharma parallel scan dispatch failed: %s", e)

    # v5.0 — SEC EDGAR poll + Trade Floor execution in background
    try:
        from . import sec_filings as _sec
        asyncio.create_task(_sec.poll_edgar_filings())
    except Exception as e:
        logger.warning("SEC poll dispatch failed: %s", e)
    if _execution_enabled():
        try:
            from . import trade_floor as _tf
            asyncio.create_task(_tf.evaluate_and_execute(final))
        except Exception as e:
            logger.warning("Trade Floor dispatch failed: %s", e)
    else:
        await log_activity("Scan execution dispatch skipped; ENABLE_TRADE_EXECUTION is off", "info")
    try:
        from . import options_desk as _od
        if _od.options_execution_enabled():
            async def _options_post_scan_execute() -> None:
                try:
                    result = await _od.refresh_and_auto_execute_latest()
                    await log_activity(
                        f"Options Desk post-scan auto-execute: {len(result.get('submitted', []))} submitted, "
                        f"{len(result.get('skipped', []))} skipped",
                        "success" if result.get("submitted") else "info",
                        {
                            "summary": result.get("summary"),
                            "submitted": result.get("submitted"),
                            "skipped": (result.get("skipped") or [])[:10],
                        },
                    )
                except Exception as exc:
                    logger.warning("Options Desk post-scan auto-execute failed: %s", exc)
                    await log_activity(f"Options Desk post-scan auto-execute failed: {exc}", "warning")
            asyncio.create_task(_options_post_scan_execute())
        else:
            await log_activity("Options Desk auto-execute skipped; ENABLE_OPTIONS_EXECUTION is off", "info")
    except Exception as e:
        logger.warning("Options Desk auto-execute status log failed: %s", e)
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
    try:
        from . import postgres_store
        await postgres_store.upsert_snapshot("bot_state", "state", {
            "_id": "state",
            "last_scan_at": finished.isoformat(),
            "last_scan_summary": {
                "pre_filter_passed": pre_filter_count,
                "results_count": len(final),
                "claude_calls_made": fresh_calls,
                "claude_cache_hits": cache_hits,
            },
        }, source="scanner")
    except Exception:
        pass
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
    # X-Factor: seed baselines for any new tickers BEFORE evaluating so the
    # multipliers have history to compare against
    await x_factor.seed_baseline(tickers)
    dh_ctx_task = _build_dh_context()
    xf_task = x_factor.batch_evaluate(tickers)
    xf_discovery_task = x_factor.discovery_candidates(set(tickers))
    macro_task = macro_pulse.upcoming_events()
    scan_set = set(tickers)
    earnings_task = earnings_engine.current_week_with_probability(scan_tickers=scan_set)

    dh_ctx, xf_alerts, xf_discoveries, macro_events, earnings_week = await asyncio.gather(
        dh_ctx_task, xf_task, xf_discovery_task, macro_task, earnings_task,
        return_exceptions=True,
    )
    if isinstance(dh_ctx, Exception):
        dh_ctx = {}
    if isinstance(xf_alerts, Exception):
        xf_alerts = []
    if isinstance(xf_discoveries, Exception):
        xf_discoveries = []
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
    earnings_by_ticker: dict[str, dict[str, Any]] = {}
    for day_rows in (earnings_week.get("by_day") or {}).values():
        for r in day_rows:
            earnings_tickers.add(r["ticker"])
            earnings_by_ticker[r["ticker"]] = r
    for r in final:
        t = r["ticker"]
        if t in dh_by_ticker:
            r["dark_horse"] = dh_by_ticker[t]
        if t in xf_by_ticker:
            r["x_factor"] = xf_by_ticker[t]
        if t in earnings_tickers:
            r["earnings_this_week"] = True
            er = earnings_by_ticker.get(t) or {}
            r["earnings_row"] = {
                "earnings_date": er.get("earnings_date"),
                "am_pm": er.get("am_pm"),
                "beat_probability_pct": er.get("beat_probability_pct"),
                "earnings_setup_rating": er.get("earnings_setup_rating"),
                "post_earnings_reaction": er.get("post_earnings_reaction"),
                "earnings_call_tone": er.get("earnings_call_tone"),
            }
            pead = er.get("pead") or {}
            if pead.get("active"):
                r["pead"] = pead
                signal_name = pead.get("signal") or f"PEAD_{str(pead.get('direction') or 'CONFIRMED').upper()}"
                if signal_name not in (r.get("signals") or []):
                    r.setdefault("signals", []).append(signal_name)

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
        "x_factor_discoveries": xf_discoveries,
        "lottery": lottery_picks,
        "macro": {"events": macro_events,
                    "imminent": [e for e in macro_events if e.get("is_imminent")]},
        "earnings_summary": {
            "total": earnings_week.get("total", 0),
            "week_of": earnings_week.get("week_of"),
            "by_day_counts": {d: len(rows) for d, rows in (earnings_week.get("by_day") or {}).items()},
            "pead_confirmed": sum(
                1
                for day_rows in (earnings_week.get("by_day") or {}).values()
                for row in day_rows
                if (row.get("pead") or {}).get("active")
            ),
        },
        "narrative_locks": conv["narrative_locks"],
        "max_conviction": {"top3": conv["top3"]},
    }
