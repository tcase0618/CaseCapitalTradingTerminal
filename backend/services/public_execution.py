"""Public equity execution path, kept behind an explicit live feature flag."""
from __future__ import annotations

import logging
import os
import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from . import execution_safety, public_api, safety
from .db import get_db, log_activity, stamped

logger = logging.getLogger(__name__)
BROKER_BASE = "public"
ALLOCATIONS = (2.0, 4.0, 6.0)
ET = ZoneInfo("America/New_York")
PUBLIC_MIN_FRACTIONAL_NOTIONAL_USD = 5.0
PUBLIC_PHASE1_CLOSE_PCT = 0.40
PUBLIC_PHASE2_CLOSE_PCT = 0.30
PUBLIC_PHASE2_MULTIPLIER = 1.50
PUBLIC_PHASE3_TRAIL_PCT = 0.50
PUBLIC_DEFAULT_MAX_EQUITY_SPREAD_BPS = 300.0

OUTCOME_THESIS_WIN = "THESIS_WIN"
OUTCOME_THESIS_LOSS = "THESIS_LOSS"
OUTCOME_STOP_LOSS = "STOP_LOSS"
OUTCOME_RATCHET_EXIT = "RATCHET_EXIT"
OUTCOME_EXECUTION_ANOMALY = "EXECUTION_ANOMALY"
OUTCOME_BROKER_RECONCILIATION_EXIT = "BROKER_RECONCILIATION_EXIT"
OUTCOME_OPERATOR_EXIT = "OPERATOR_EXIT"


def enabled() -> bool:
    cfg = public_api.config()
    return bool(cfg.enabled and cfg.account_id and cfg.access_token and cfg.live_equity_enabled and not cfg.research_only)


def monitored_exit_override_enabled() -> bool:
    """Allow entries only when every unprotected fill has known broker routing limits.

    This does not describe a broker-side stop as present.  It permits the
    operator to use the terminal's 24/5 five-minute protective-exit monitor
    when Public rejects stop orders solely due to its configured routing.
    Unknown, malformed, or failed protection records remain fail-closed.
    """
    return os.getenv("PUBLIC_ALLOW_MONITORED_EXIT_ONLY", "false").strip().lower() in {"1", "true", "yes", "on"}


def broker_bracket_protection_enabled() -> bool:
    """Whether the separately validated Public bracket path may transmit.

    Public brackets are not interchangeable with the terminal's 24/5
    fractional workflow: the broker requires a whole-share CORE-session order.
    Keep this opt-in until a read-only preflight confirms the account's exact
    routing capability.
    """
    return os.getenv("PUBLIC_BRACKET_PROTECTION_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def _monitored_exit_max_age_seconds() -> int:
    try:
        return max(60, min(600, int(os.getenv("PUBLIC_MONITORED_EXIT_MAX_AGE_SECONDS", "180"))))
    except (TypeError, ValueError):
        return 180


def _timestamp_age_seconds(value: Any, *, now: datetime | None = None) -> int | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0, int(((now or datetime.now(timezone.utc)) - parsed.astimezone(timezone.utc)).total_seconds()))
    except (TypeError, ValueError):
        return None


def _verified_monitored_exit(row: dict[str, Any], *, now: datetime | None = None) -> tuple[bool, int | None]:
    """Return whether the terminal, rather than Public, is actively protecting a fill.

    Public's current routing rejects conditional stops.  A position is only
    considered terminal-covered when its ratchet is enabled, its active stop
    is positive, and the one-minute monitor has checked it recently.  A local
    stop number by itself is never sufficient.
    """
    stop = _num(row.get("pm_active_stop") or row.get("current_stop"))
    plan = row.get("pm_ratchet_plan") or {}
    age = _timestamp_age_seconds(row.get("pm_last_ratchet_check"), now=now)
    active = bool(plan.get("enabled")) and str(row.get("protection_state") or "").upper() == "MONITORED_EXIT_ONLY"
    return bool(active and stop > 0 and age is not None and age <= _monitored_exit_max_age_seconds()), age


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _allocation(row: dict[str, Any]) -> float:
    value = _num(row.get("allocation_usd"))
    return min(ALLOCATIONS, key=lambda item: abs(item - value)) if value > 0 else 0.0


def _symbol(row: dict[str, Any]) -> str:
    instrument = row.get("instrument") or {}
    return str(row.get("symbol") or row.get("ticker") or instrument.get("symbol") or "").upper().strip()


def _strategy_attribution(row: dict[str, Any]) -> dict[str, Any]:
    scanner = row.get("strategy_scanner") or {}
    source_scan = row.get("source_scan")
    views = [view for view in (row.get("strategy_views") or []) if isinstance(view, dict)]
    view_lanes = [str(view.get("lane")) for view in views if view.get("lane")]
    view_screeners = [str(view.get("screener_id")) for view in views if view.get("screener_id")]
    lanes = row.get("strategy_lanes") or scanner.get("lanes") or view_lanes
    lottery_views = [view for view in views if str(view.get("family") or "").upper() == "LOTTERY"]
    lottery_view = lottery_views[0] if lottery_views else None
    primary_scanner = lottery_view or scanner
    primary_family = str(primary_scanner.get("family") or row.get("scanner_family") or "").upper() or None
    primary_id = primary_scanner.get("screener_id") or primary_scanner.get("id") or primary_scanner.get("name")
    fallback_id = source_scan or "CORE"
    if not primary_family:
        primary_family = "CORE" if fallback_id == "CORE" else None
    primary_lane = lottery_view.get("lane") if lottery_view else None
    if lottery_view and primary_lane and primary_lane not in lanes:
        lanes = [primary_lane, *lanes]
    return {
        "strategy_id": primary_id or row.get("strategy_id") or source_scan or (view_screeners[0] if view_screeners else fallback_id),
        "screener_id": primary_id or row.get("screener_id") or source_scan or (view_screeners[0] if view_screeners else fallback_id),
        "scanner_family": primary_family,
        "strategy_is_lottery": bool(lottery_view) or primary_family == "LOTTERY",
        "strategy_lanes": list(dict.fromkeys(str(lane) for lane in lanes)) if isinstance(lanes, (list, tuple)) else ([str(lanes)] if lanes else []),
    }


def _utc_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _minimum_thesis_hold_seconds() -> int:
    """Minimum observed lifecycle before an exit can teach entry selection."""
    try:
        return max(60, min(3_600, int(os.getenv("PUBLIC_MIN_THESIS_HOLD_SECONDS", "300"))))
    except (TypeError, ValueError):
        return 300


def _observed_hold_seconds(trade: dict[str, Any]) -> int | None:
    entry = _utc_datetime(trade.get("filled_at") or trade.get("submitted_at"))
    close = _utc_datetime(trade.get("closed_at"))
    if not entry or not close:
        return None
    return max(0, int((close - entry).total_seconds()))


def _classify_trade_outcome(trade: dict[str, Any]) -> tuple[str | None, str | None, bool, bool]:
    """Classify a close before any strategy-learning eligibility is assigned.

    A broker-history match alone is not sufficient evidence for an entry model:
    an immediate disappearance, an unmatched broker reconciliation, or a
    manual/operator exit says something about operations, not the thesis.
    """
    if str(trade.get("status") or "").upper() != "CLOSED":
        return None, "position_open", False, False
    reason = str(trade.get("close_reason") or "").lower()
    verified = bool(trade.get("broker_exit_verified"))
    realized = trade.get("realized_pl_pct")
    try:
        realized_pct = float(realized) if realized is not None else None
    except (TypeError, ValueError):
        realized_pct = None
    hold_seconds = _observed_hold_seconds(trade)
    unconfirmed = bool(trade.get("broker_exit_unconfirmed")) or "position_absent" in reason or "unconfirmed" in reason
    if unconfirmed or (hold_seconds is not None and hold_seconds < _minimum_thesis_hold_seconds()):
        return OUTCOME_EXECUTION_ANOMALY, "unconfirmed_or_too_short_to_measure_thesis", False, False
    if hold_seconds is None:
        return OUTCOME_BROKER_RECONCILIATION_EXIT, "entry_or_close_time_unavailable", False, False
    if not verified or realized_pct is None:
        return OUTCOME_BROKER_RECONCILIATION_EXIT, "broker_exit_not_verified", False, False
    if "operator" in reason or "manual" in reason or "to_cash" in reason:
        return OUTCOME_OPERATOR_EXIT, "operator_directed_exit", False, True
    if "ratchet" in reason or "phase" in reason or "trail" in reason:
        return OUTCOME_RATCHET_EXIT, "ratchet_or_phase_exit", False, True
    if "protective" in reason or "stop" in reason or "emergency" in reason:
        return OUTCOME_STOP_LOSS, "protective_exit", False, True
    return (
        OUTCOME_THESIS_WIN if realized_pct > 0 else OUTCOME_THESIS_LOSS,
        None,
        True,
        True,
    )


def _day2_entry_gate(row: dict[str, Any]) -> tuple[bool, str | None]:
    """Keep Day-2 visible while it earns an independently verified sample."""
    attribution = _strategy_attribution(row)
    lanes = {str(value).upper() for value in attribution.get("strategy_lanes") or []}
    is_day2 = "DAY2_CONTINUATION" in lanes or str(attribution.get("strategy_id") or "").lower() == "lottery_day2_continuation"
    if not is_day2:
        return True, None
    if os.getenv("LOTTERY_DAY2_LIVE_AUTOMATION_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return False, "lottery_day2_shadow_until_edge_proven"
    groups = {str(value).upper() for value in row.get("signal_groups") or []}
    required = {"MOMENTUM", "VOLUME"}
    confirmations = {"ROTATION", "CATALYST", "SHORT", "ATTENTION", "STRUCTURE"}
    if not required.issubset(groups) or not groups.intersection(confirmations):
        return False, "lottery_day2_requires_distinct_confirmation"
    try:
        min_score = max(55.0, min(95.0, float(os.getenv("LOTTERY_DAY2_MIN_PM_SCORE", "60"))))
    except (TypeError, ValueError):
        min_score = 60.0
    if _num(row.get("pm_score")) < min_score:
        return False, "lottery_day2_pm_score_below_minimum"
    return True, None


def _day2_observation(row: dict[str, Any], attribution: dict[str, Any]) -> dict[str, Any] | None:
    """Persist Day-2 structure without filling missing microstructure with guesses."""
    lanes = {str(value).upper() for value in attribution.get("strategy_lanes") or []}
    if "DAY2_CONTINUATION" not in lanes:
        return None
    source = row.get("day2_features") or row.get("intraday_structure") or {}
    if not isinstance(source, dict):
        source = {}
    values = {
        "premarket_change_pct": source.get("premarket_change_pct", row.get("premarket_change_pct")),
        "gap_retention_pct": source.get("gap_retention_pct", row.get("gap_retention_pct")),
        "float_turnover": source.get("float_turnover", row.get("float_turnover")),
        "vwap": source.get("vwap", row.get("vwap")),
        "first_hour_structure": source.get("first_hour_structure", row.get("first_hour_structure")),
    }
    missing = [key for key, value in values.items() if value is None]
    return {
        **values,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "quality": "COMPLETE" if not missing else "PARTIAL" if len(missing) < len(values) else "UNAVAILABLE",
        "missing_fields": missing,
    }


def _entry_decision_packet(
    row: dict[str, Any],
    *,
    attribution: dict[str, Any],
    quote: dict[str, Any],
    limit_price: float,
    stop_price: float,
    structural_target: float,
    ratchet_plan: dict[str, Any],
) -> dict[str, Any]:
    """Freeze the evidence and PM contract available at order submission."""
    return {
        "schema_version": "public-equity-entry-v2",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "strategy": {
            "strategy_id": attribution.get("strategy_id"),
            "screener_id": attribution.get("screener_id"),
            "scanner_family": attribution.get("scanner_family"),
            "lanes": list(attribution.get("strategy_lanes") or []),
            "source_scan": row.get("source_scan"),
        },
        "evidence": {
            "signals": list(row.get("signals") or []),
            "signal_groups": list(row.get("signal_groups") or []),
            "independent_signal_count": row.get("independent_signal_count"),
            "strategy_fits": list(row.get("strategy_fits") or []),
            "triggers": list(row.get("triggers") or []),
            "provenance": dict(row.get("provenance") or {}),
        },
        "pm": {
            "action": str(row.get("action") or "").upper(),
            "score": row.get("pm_score"),
            "score_breakdown": dict(row.get("score_breakdown") or {}),
            "case_score": row.get("case_score"),
            "strategy_confidence": row.get("strategy_confidence"),
            "allocation_usd": row.get("allocation_usd"),
            "reasons": list(row.get("reasons") or []),
            "cautions": list(row.get("cautions") or []),
        },
        "market": {
            "regime": row.get("regime"),
            "regime_playbook": row.get("regime_playbook"),
            "sector": row.get("sector"),
            "entry_quote": dict(quote or {}),
            "entry_limit_price": limit_price,
        },
        "lifecycle": {
            "target": structural_target or None,
            "target_source": row.get("target_source"),
            "target_is_proxy": bool(row.get("target_is_proxy")),
            "stop": stop_price,
            "ratchet_plan": dict(ratchet_plan or {}),
            "plan": dict(row.get("lifecycle_plan") or {}),
            "holding_window_days": row.get("horizon_days") or row.get("hold_window_days"),
        },
        "day2_observation": _day2_observation(row, attribution),
    }


def _public_learning_record(trade: dict[str, Any]) -> dict[str, Any]:
    """Normalize one Public trade into an outcome-ledger row.

    Only a broker-history-verified closed trade is eligible for PM learning.
    Open positions and closes without a matched broker fill remain observable,
    but cannot be silently treated as evidence for a strategy.
    """
    attribution = trade.get("strategy_attribution") or _strategy_attribution(trade)
    realized_pct = trade.get("realized_pl_pct")
    try:
        realized_pct = float(realized_pct) if realized_pct is not None else None
    except (TypeError, ValueError):
        realized_pct = None
    closed = str(trade.get("status") or "").upper() == "CLOSED"
    verified = bool(trade.get("broker_exit_verified")) and realized_pct is not None
    strategy_id = attribution.get("strategy_id") or trade.get("strategy_id") or "UNATTRIBUTED"
    legacy_or_unattributed = strategy_id in {"UNATTRIBUTED", "LEGACY_UNATTRIBUTED"}
    outcome_class, outcome_reason, entry_learning_eligible, exit_learning_eligible = _classify_trade_outcome(trade)
    eligible = bool(verified and not legacy_or_unattributed and entry_learning_eligible)
    return {
        "learning_id": f"public-learning:{trade.get('client_order_id')}",
        "client_order_id": trade.get("client_order_id"),
        "broker_base": BROKER_BASE,
        "ticker": _symbol(trade),
        "strategy_id": strategy_id,
        "screener_id": attribution.get("screener_id") or trade.get("screener_id") or strategy_id,
        "scanner_family": attribution.get("scanner_family") or trade.get("scanner_family"),
        "strategy_lanes": attribution.get("strategy_lanes") or trade.get("strategy_lanes") or [],
        "pm_score": _num(trade.get("pm_score")) or None,
        "case_score": _num(trade.get("case_score")) or None,
        "strategy_confidence": _num(trade.get("strategy_confidence")) or None,
        "entry_price": _num(trade.get("filled_avg_price") or trade.get("limit_price")) or None,
        "entry_at": trade.get("filled_at") or trade.get("submitted_at"),
        "close_at": trade.get("closed_at"),
        "hold_seconds": _observed_hold_seconds(trade),
        "status": str(trade.get("status") or "UNKNOWN").upper(),
        "realized_pnl": _num(trade.get("realized_pnl")) if verified else None,
        "realized_pct": realized_pct if verified else None,
        "outcome_verified": verified,
        "outcome_class": outcome_class,
        "outcome_reason": outcome_reason,
        "entry_learning_eligible": eligible,
        "exit_learning_eligible": bool(verified and not legacy_or_unattributed and exit_learning_eligible),
        "learning_eligible": eligible,
        "learning_exclusion_reason": None if eligible else (
            "strategy_unattributed_or_legacy" if legacy_or_unattributed else outcome_reason or ("broker_exit_not_verified" if closed else "position_open")
        ),
        "entry_decision": dict(trade.get("entry_decision") or {}),
        "source": "public_broker_history_reconciliation",
    }


async def sync_public_learning_ledger() -> dict[str, int]:
    """Persist Public execution outcomes without altering strategy weights."""
    db = get_db()
    rows = await db.tf_trades.find({"broker_base": BROKER_BASE}, {"_id": 0}).to_list(5_000)
    written = eligible = verified = 0
    for trade in rows:
        client_order_id = str(trade.get("client_order_id") or "")
        if not client_order_id:
            continue
        record = _public_learning_record(trade)
        await db.public_trade_learning.update_one(
            {"learning_id": record["learning_id"]},
            {"$set": stamped({**record, "updated_at": datetime.now(timezone.utc).isoformat()})},
            upsert=True,
        )
        written += 1
        verified += 1 if record["outcome_verified"] else 0
        eligible += 1 if record["learning_eligible"] else 0
    return {"written": written, "verified_outcomes": verified, "learning_eligible": eligible}


async def label_legacy_unattributed_positions() -> int:
    """Quarantine pre-attribution Public holdings from strategy learning.

    This preserves the broker position and its P&L while making clear that it
    cannot be evidence for Core, Lottery, or any other strategy.
    """
    db = get_db()
    result = await db.tf_trades.update_many(
        {
            "broker_base": BROKER_BASE,
            "$or": [
                {"strategy_id": None},
                {"strategy_id": {"$exists": False}},
                {"strategy_id": ""},
            ],
        },
        {"$set": {
            "strategy_id": "LEGACY_UNATTRIBUTED",
            "screener_id": "LEGACY_UNATTRIBUTED",
            "strategy_attribution": {
                "strategy_id": "LEGACY_UNATTRIBUTED",
                "screener_id": "LEGACY_UNATTRIBUTED",
                "scanner_family": "LEGACY",
                "strategy_lanes": [],
                "strategy_is_lottery": False,
            },
            "learning_exclusion_reason": "strategy_unattributed_or_legacy",
            "legacy_attribution_labeled_at": datetime.now(timezone.utc).isoformat(),
        }},
    )
    return int(getattr(result, "modified_count", 0) or 0)


def _positions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("positions") or payload.get("holdings") or []
    return rows if isinstance(rows, list) else []


def _orders_by_id(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index Public's portfolio order feed for read-path reconciliation.

    Some Public accounts return UUID order identifiers from placement that the
    single-order endpoint cannot parse.  Portfolio v2 returns those same
    orders authoritatively, so use it as a read-only fallback rather than
    declaring a working broker order unobservable.
    """
    rows = payload.get("orders") or []
    if not isinstance(rows, list):
        return {}
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        order_id = row.get("orderId") or row.get("id")
        if order_id:
            indexed[str(order_id)] = row
    return indexed


async def _broker_orders_with_search(
    client: public_api.PublicAPIClient,
    portfolio_snapshot: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Merge Public order-search truth over the portfolio convenience feed.

    Portfolio orders are useful but may be incomplete. Public's documented
    order-search response includes terminal status, fills, rejects, and legs;
    when available it takes precedence. The legacy client fallback keeps old
    tests and staged deployments observable without inventing broker state.
    """
    combined = _orders_by_id(portfolio_snapshot)
    search = getattr(client, "search_orders", None)
    if not callable(search):
        return combined, {"source": "portfolio_only", "search_ok": False, "reason": "client_search_orders_unavailable"}
    now = datetime.now(timezone.utc)
    try:
        payload = await search(
            created_after=(now - timedelta(days=30)).isoformat().replace("+00:00", "Z"),
            created_before=now.isoformat().replace("+00:00", "Z"),
        )
        searched = _orders_by_id(payload)
        combined.update(searched)
        return combined, {
            "source": "public_order_search+portfolio",
            "search_ok": True,
            "search_order_count": len(searched),
            "portfolio_order_count": len(_orders_by_id(portfolio_snapshot)),
        }
    except Exception as exc:
        logger.warning("Public order search unavailable; falling back to portfolio/order-V2: %s", exc.__class__.__name__)
        return combined, {
            "source": "portfolio_only",
            "search_ok": False,
            "reason": f"public_order_search_failed:{exc.__class__.__name__}",
        }


async def _get_order_with_portfolio_fallback(
    client: public_api.PublicAPIClient,
    order_id: str,
    portfolio_orders: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    try:
        return await client.get_order(order_id)
    except Exception:
        fallback = portfolio_orders.get(str(order_id))
        if fallback is not None:
            return fallback
        raise


def _qty(row: dict[str, Any]) -> float:
    # Trade-floor rows store the broker-confirmed holding in qty_remaining;
    # Public portfolio rows normally expose quantity/shares instead.
    return _num(row.get("qty_remaining") or row.get("quantity") or row.get("qty") or row.get("shares"))


def _quote_price(row: dict[str, Any]) -> float:
    bid = _num(row.get("bid") or row.get("bidPrice"))
    ask = _num(row.get("ask") or row.get("askPrice"))
    if bid > 0 and ask >= bid:
        return round((bid + ask) / 2.0, 4)
    return _num(row.get("last") or row.get("lastPrice") or ask or bid)


def _quote_sides(row: dict[str, Any]) -> tuple[float, float]:
    return (
        _num(row.get("bid") or row.get("bidPrice")),
        _num(row.get("ask") or row.get("askPrice")),
    )


def _preflight_economics(payload: dict[str, Any] | None) -> dict[str, float | None]:
    """Normalize Public's non-mutating order economics for audit and research.

    Values are evidence from the broker preflight, not a forecast of a fill.
    Unknown fields stay ``None`` so downstream research cannot mistake missing
    cost data for a zero-cost trade.
    """
    data = payload if isinstance(payload, dict) else {}
    regulatory = data.get("regulatoryFees") or data.get("regulatory_fees") or {}
    regulatory_total = None
    if isinstance(regulatory, dict):
        values = [_num(value, -1.0) for value in regulatory.values()]
        known = [value for value in values if value >= 0]
        regulatory_total = round(sum(known), 6) if known else None
    def known(*keys: str) -> float | None:
        for key in keys:
            value = _num(data.get(key), -1.0)
            if value >= 0:
                return value
        return None
    commission = known("estimatedCommission", "estimated_commission")
    execution_fee = known("estimatedExecutionFee", "estimated_execution_fee")
    explicit_total = known("estimatedFees", "estimated_fees")
    components = [value for value in (commission, execution_fee, regulatory_total) if value is not None]
    estimated_total = explicit_total if explicit_total is not None else (round(sum(components), 6) if components else None)
    return {
        "order_value": known("orderValue", "order_value"),
        "estimated_cost": known("estimatedCost", "estimated_cost"),
        "buying_power_requirement": known("buyingPowerRequirement", "buying_power_requirement"),
        "estimated_quantity": known("estimatedQuantity", "estimated_quantity"),
        "estimated_commission": commission,
        "estimated_execution_fee": execution_fee,
        "estimated_regulatory_fees": regulatory_total,
        "estimated_total_fees": estimated_total,
        "price_increment": known("priceIncrement", "price_increment"),
    }


def _max_equity_spread_bps() -> float:
    try:
        return max(25.0, min(2_000.0, float(os.getenv("PUBLIC_MAX_EQUITY_SPREAD_BPS", str(PUBLIC_DEFAULT_MAX_EQUITY_SPREAD_BPS)))))
    except (TypeError, ValueError):
        return PUBLIC_DEFAULT_MAX_EQUITY_SPREAD_BPS


def _execution_quote(row: dict[str, Any], *, side: str, emergency: bool = False) -> tuple[dict[str, Any] | None, str | None]:
    """Validate a two-sided Public quote and derive a non-chasing limit.

    A last trade is useful for display but cannot price a new order. Normal
    orders rest at midpoint, preserving price improvement. An emergency stop
    exit uses the current bid so the terminal does not promise a protective
    sale at a price the market is not bidding.
    """
    bid, ask = _quote_sides(row)
    if bid <= 0 or ask <= 0 or ask < bid:
        return None, "public_execution_quote_requires_valid_bid_ask"
    mid = (bid + ask) / 2.0
    spread_bps = ((ask - bid) / mid) * 10_000 if mid > 0 else None
    if spread_bps is None or spread_bps > _max_equity_spread_bps():
        return None, "public_execution_quote_spread_too_wide"
    normal_side = str(side or "").upper()
    raw_limit = bid if emergency and normal_side == "SELL" else mid
    precision = 4 if raw_limit < 1 else 2
    limit = round(raw_limit, precision)
    if limit <= 0:
        return None, "public_execution_quote_limit_invalid"
    return {
        "bid": bid,
        "ask": ask,
        "mid": round(mid, precision),
        "spread_bps": round(spread_bps, 2),
        "limit_price": limit,
        "quote_time": _execution_quote_timestamp(row),
    }, None


def _exit_quote_retry_config() -> tuple[int, float]:
    """Bound rapid refreshes for an exit that cannot safely price yet."""
    attempts = max(1, min(30, int(_num(os.getenv("PUBLIC_EXIT_QUOTE_REFRESH_ATTEMPTS", "30"), 30))))
    interval = max(0.0, min(5.0, _num(os.getenv("PUBLIC_EXIT_QUOTE_RETRY_SECONDS", "2"), 2.0)))
    return attempts, interval


async def _refresh_exit_quotes(
    client: public_api.PublicAPIClient,
    symbols: list[str],
    *,
    emergency: bool = False,
    max_attempts: int | None = None,
) -> dict[str, Any]:
    """Rapidly retry stale exit quotes without accepting stale fallback data.

    A single request is made for every unresolved symbol on each attempt. This
    keeps a burst of stop exits bounded to one provider call per retry rather
    than one call per position. Only Public's fresh two-sided quote can release
    an exit for submission.
    """
    attempts, interval = _exit_quote_retry_config()
    if max_attempts is not None:
        attempts = max(1, min(attempts, int(max_attempts)))
    pending = {str(symbol or "").upper().strip() for symbol in symbols if str(symbol or "").strip()}
    resolved: dict[str, dict[str, Any]] = {}
    unresolved: dict[str, dict[str, Any]] = {}
    used_attempts = 0
    for attempt in range(1, attempts + 1):
        if not pending:
            break
        used_attempts = attempt
        try:
            rows = _quotes(await client.quotes(sorted(pending)))
            by_symbol = {_symbol(row): row for row in rows}
        except Exception as exc:
            for symbol in pending:
                unresolved[symbol] = {"reason": f"public_exit_quote_refresh_failed:{exc.__class__.__name__}", "attempt": attempt}
            by_symbol = {}
        for symbol in list(pending):
            row = by_symbol.get(symbol) or {}
            quote, reason = _execution_quote(row, side="SELL", emergency=emergency)
            fresh, age = safety.quote_is_fresh({"ts": _execution_quote_timestamp(row)})
            if fresh and quote:
                resolved[symbol] = {"quote": row, "execution_quote": quote, "age_seconds": age, "attempt": attempt}
                pending.discard(symbol)
            else:
                unresolved[symbol] = {
                    "reason": reason or "public_exit_quote_stale_or_unverifiable",
                    "age_seconds": age,
                    "attempt": attempt,
                }
        if pending and attempt < attempts and interval:
            await asyncio.sleep(interval)
    return {
        "attempts": used_attempts,
        "configured_attempts": attempts,
        "resolved": resolved,
        "unresolved": {symbol: unresolved.get(symbol) or {"reason": "public_exit_quote_unavailable"} for symbol in pending},
    }


async def _record_exit_quote_unavailable(
    trade: dict[str, Any],
    *,
    ticker: str,
    reason: str,
    quote_refresh: dict[str, Any],
) -> None:
    """Persist and alert a failed exit price discovery without selling blind."""
    details = {
        "reason": reason,
        "attempts": quote_refresh.get("attempts"),
        "configured_attempts": quote_refresh.get("configured_attempts"),
        "unresolved": (quote_refresh.get("unresolved") or {}).get(ticker),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    await get_db().tf_trades.update_one(
        {"client_order_id": trade.get("client_order_id"), "broker_base": BROKER_BASE},
        {"$set": {"emergency_exit_status": "PENDING_QUOTE_UNAVAILABLE", "emergency_exit_quote_refresh": details}},
    )
    try:
        from . import telegram_events

        await telegram_events.emit_event(
            "public_exit_quote_unavailable",
            severity="watch",
            scope="execution",
            ticker=ticker,
            title="Public exit quote unavailable",
            summary=(
                f"Exit pricing for {ticker} remained unavailable after "
                f"{details['attempts']} rapid Public quote checks; no blind sell was submitted."
            ),
            details=details,
            priority="critical",
        )
    except Exception:
        logger.exception("Failed to emit Public exit quote alert for %s", ticker)


def _quote_timestamp(row: dict[str, Any]) -> Any:
    return row.get("quoteTime") or row.get("quote_time") or row.get("timestamp") or row.get("updatedAt")


def _execution_quote_timestamp(row: dict[str, Any]) -> Any:
    """Return the timestamp that actually supports a two-sided limit quote.

    ``quoteTime`` can be a fresh last trade while bid/ask are stale. Where
    Public supplies bid/ask timestamps, execution must use the older side.
    Legacy providers without side timestamps retain the generic fallback.
    """
    direct = row.get("executableQuoteTime") or row.get("executable_quote_time")
    if direct:
        return direct
    bid_time = row.get("bidTimestamp") or row.get("bid_timestamp")
    ask_time = row.get("askTimestamp") or row.get("ask_timestamp")
    if bid_time or ask_time:
        if not (bid_time and ask_time):
            return None
        try:
            bid_dt = datetime.fromisoformat(str(bid_time).replace("Z", "+00:00"))
            ask_dt = datetime.fromisoformat(str(ask_time).replace("Z", "+00:00"))
            bid_dt = bid_dt if bid_dt.tzinfo else bid_dt.replace(tzinfo=timezone.utc)
            ask_dt = ask_dt if ask_dt.tzinfo else ask_dt.replace(tzinfo=timezone.utc)
            return bid_time if bid_dt <= ask_dt else ask_time
        except (TypeError, ValueError):
            return None
    return _quote_timestamp(row)


def _stop_price(row: dict[str, Any]) -> float:
    """Accept the PM contract's ``stop`` field and legacy ``stop_price``."""
    return _num(row.get("stop") or row.get("stop_price"))


def _lottery_proxy_target_can_execute(row: dict[str, Any]) -> bool:
    """Authorize a Lottery proxy target only when risk is independently real.

    Strategy lanes may estimate upside for research, but that estimate is not
    an executable price target.  A PM-approved Lottery order may therefore
    proceed without a structural target only when it has its own validated
    stop and an enabled ratchet plan to govern the exit.
    """
    attribution = _strategy_attribution(row)
    ratchet = row.get("ratchet_plan") or {}
    return bool(attribution.get("strategy_is_lottery") and ratchet.get("enabled"))


def _routing_rejects_stop(trade: dict[str, Any]) -> bool:
    """Recognize Public's documented routing rejection without retry storms."""
    error = str(trade.get("protective_order_error") or "").lower()
    return "not allowed on lit exchanges" in error or '"code":145' in error.replace(" ", "")


def _broker_protection_capability(order_shape: dict[str, Any], stop_price: float) -> dict[str, Any]:
    """Describe the protection Public can *actually* attach to an entry.

    A broker bracket/OTO stop is eligible only for a whole-share, CORE-session
    entry. Fractional and 24/5 positions must remain explicitly classified as
    terminal-monitored; they must never be displayed as broker-protected.
    """
    session = str(order_shape.get("session") or "").upper()
    quantity = _num(order_shape.get("quantity"))
    whole_share = quantity >= 1 and abs(quantity - round(quantity)) < 1e-8
    eligible = bool(session == "CORE" and whole_share and stop_price > 0)
    if not eligible:
        reason = "public_bracket_requires_core_whole_share_stop"
    elif not broker_bracket_protection_enabled():
        reason = "public_bracket_feature_not_enabled"
    else:
        reason = None
    return {
        "eligible": eligible,
        "enabled": bool(eligible and broker_bracket_protection_enabled()),
        "mode": "BROKER_OTO_STOP" if eligible and broker_bracket_protection_enabled() else "MONITORED_EXIT_ONLY",
        "reason": reason,
        "session": session,
        "whole_share_quantity": quantity if whole_share else None,
    }


def _public_position_mark(raw: dict[str, Any]) -> tuple[float, float, Any]:
    """Normalize Public's documented Portfolio v2 price and provider gain field.

    ``instrumentGain.gainPercentage`` is retained as a provider observation,
    not treated as lifetime position P&L. The PM uses cost-basis return below.
    """
    last = raw.get("lastPrice") or raw.get("last_price") or {}
    gain = raw.get("instrumentGain") or raw.get("instrument_gain") or {}
    price = _num(last.get("lastPrice") or last.get("value") if isinstance(last, dict) else last)
    pnl = _num(gain.get("gainPercentage") or gain.get("percentage") if isinstance(gain, dict) else None)
    timestamp = (last.get("timestamp") or last.get("updatedAt")) if isinstance(last, dict) else None
    return price, pnl, timestamp


def _public_position_cost_basis(raw: dict[str, Any]) -> tuple[float, float]:
    """Return broker-reported unit and total cost basis, never a guess."""
    basis = raw.get("costBasis") or raw.get("cost_basis") or {}
    if not isinstance(basis, dict):
        return 0.0, 0.0
    return (
        _num(basis.get("unitCost") or basis.get("unit_cost")),
        _num(basis.get("totalCost") or basis.get("total_cost")),
    )


def _public_position_unrealized_pct(raw: dict[str, Any], total_cost: float | None = None) -> float | None:
    """Calculate lifetime position return from broker cost and market value."""
    _, broker_total_cost = _public_position_cost_basis(raw)
    cost = broker_total_cost if total_cost is None else total_cost
    market_value = _num(raw.get("currentValue") or raw.get("current_value"), -1.0)
    if cost <= 0 or market_value < 0:
        return None
    return round((market_value - cost) / cost * 100.0, 4)


def _public_position_opened_at(raw: dict[str, Any]) -> str | None:
    value = raw.get("openedAt") or raw.get("opened_at")
    return str(value) if value else None


def _import_client_order_id(ticker: str, opened_at: str | None) -> str:
    # An imported broker holding has no terminal order id.  This stable id
    # makes it visible and idempotent without pretending it was terminal-made.
    stamp = (opened_at or "unknown").replace(" ", "T").replace(":", "-")[:32]
    return f"public-import-{ticker.lower()}-{stamp}"


def _history_transactions(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("transactions") or payload.get("history") or []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _history_timestamp(row: dict[str, Any]) -> datetime | None:
    value = row.get("timestamp") or row.get("createdAt") or row.get("date")
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _matching_broker_sell(
    transactions: list[dict[str, Any]],
    *,
    ticker: str,
    quantity: float,
    after: datetime | None,
) -> dict[str, Any] | None:
    """Return only an exact, post-entry broker sell; never infer a partial exit."""
    for row in transactions:
        if str(row.get("type") or "").upper() != "TRADE":
            continue
        if str(row.get("side") or "").upper() != "SELL":
            continue
        if _symbol(row) != ticker:
            continue
        sold = abs(_num(row.get("quantity")))
        when = _history_timestamp(row)
        if quantity <= 0 or abs(sold - quantity) > max(1e-5, quantity * 0.001):
            continue
        if after and (not when or when < after):
            continue
        proceeds = _num(row.get("netAmount"))
        if proceeds > 0:
            return row
    return None


async def _reconcile_closed_broker_history(
    client: public_api.PublicAPIClient,
) -> dict[str, Any]:
    """Backfill only exact Public sell fills into closed terminal records.

    Terminal state used to mark positions closed merely because a broker
    holding disappeared.  This pass upgrades those records only when Public's
    activity history provides an exact quantity match, preventing fabricated
    realized P&L from entering learning.
    """
    db = get_db()
    rows = await db.tf_trades.find(
        {"broker_base": BROKER_BASE, "status": "CLOSED"}, {"_id": 0},
    ).to_list(500)
    candidates = [row for row in rows if _num(row.get("realized_pnl")) == 0 and not row.get("broker_exit_verified")]
    if not candidates:
        return {"checked": 0, "verified": 0, "unmatched": 0}
    # Some legacy rows populated filled_at only when a later reconciliation
    # observed the position. Submitted_at is the safe lower bound for an exact
    # broker-history match; a sell before it can never be attributed here.
    starts = [_parse_history_start(row.get("submitted_at") or row.get("filled_at")) for row in candidates]
    starts = [value for value in starts if value]
    start = min(starts).isoformat().replace("+00:00", "Z") if starts else "2020-01-01T00:00:00Z"
    try:
        payload = await client.history(start=start, end=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), page_size=500)
    except Exception as exc:
        return {"checked": len(candidates), "verified": 0, "unmatched": len(candidates), "error": exc.__class__.__name__}
    transactions = _history_transactions(payload)
    verified = 0
    for row in candidates:
        entry_time = _parse_history_start(row.get("submitted_at") or row.get("filled_at"))
        quantity = _qty(row)
        if quantity <= 0:
            quantity = _num(row.get("qty_total"))
        sell = _matching_broker_sell(transactions, ticker=_symbol(row), quantity=quantity, after=entry_time)
        if not sell:
            continue
        proceeds = _num(sell.get("netAmount"))
        entry_cost = _num(row.get("notional")) or (_num(row.get("filled_avg_price")) * quantity)
        if proceeds <= 0 or entry_cost <= 0:
            continue
        realized = round(proceeds - entry_cost, 4)
        await db.tf_trades.update_one(
            {"client_order_id": row.get("client_order_id"), "broker_base": BROKER_BASE},
            {"$set": {
                "broker_exit_verified": True,
                "broker_exit_transaction_id": sell.get("id"),
                "broker_exit_price": round(proceeds / quantity, 6),
                "broker_exit_proceeds": proceeds,
                "realized_pnl": realized,
                "realized_pl_pct": round(realized / entry_cost * 100, 4),
                "realized_pnl_source": "public_history_exact_quantity_match",
                "last_synced_at": datetime.now(timezone.utc).isoformat(),
            }},
        )
        verified += 1
    return {"checked": len(candidates), "verified": verified, "unmatched": len(candidates) - verified}


async def _import_unattributed_closed_broker_history(
    client: public_api.PublicAPIClient,
) -> dict[str, Any]:
    """Import complete broker round-trips with no terminal ledger record.

    This repairs account-level performance reconciliation while retaining the
    fact that the strategy is unknown.  It never assigns the trade to a scan,
    lane, or PM decision, so learning cannot mistake an external/legacy trade
    for evidence about a terminal strategy.
    """
    db = get_db()
    rows = await db.tf_trades.find({"broker_base": BROKER_BASE}, {"_id": 0, "ticker": 1}).to_list(1000)
    ledger_tickers = {_symbol(row) for row in rows}
    try:
        payload = await client.history(
            start="2020-01-01T00:00:00Z",
            end=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            page_size=500,
        )
    except Exception as exc:
        return {"imported": 0, "error": exc.__class__.__name__}
    transactions = sorted(_history_transactions(payload), key=lambda row: _history_timestamp(row) or datetime.min.replace(tzinfo=timezone.utc))
    open_buys: dict[str, list[dict[str, Any]]] = {}
    imported: list[str] = []
    for row in transactions:
        if str(row.get("type") or "").upper() != "TRADE":
            continue
        ticker = _symbol(row)
        quantity = abs(_num(row.get("quantity")))
        amount = _num(row.get("netAmount"))
        if not ticker or quantity <= 0 or amount == 0:
            continue
        side = str(row.get("side") or "").upper()
        if side == "BUY":
            open_buys.setdefault(ticker, []).append(row)
            continue
        if side != "SELL" or ticker in ledger_tickers:
            continue
        candidates = open_buys.get(ticker) or []
        buy = next((candidate for candidate in candidates if abs(abs(_num(candidate.get("quantity"))) - quantity) <= max(1e-5, quantity * 0.001)), None)
        if not buy:
            continue
        buy_amount = abs(_num(buy.get("netAmount")))
        if buy_amount <= 0 or amount <= 0:
            continue
        client_order_id = f"public-history-{str(buy.get('id') or ticker).lower()}"
        await db.tf_trades.insert_one(stamped({
            "client_order_id": client_order_id,
            "broker_base": BROKER_BASE,
            "ticker": ticker,
            "instrument": "EQUITY",
            "status": "CLOSED",
            "fill_status": "EXIT_FILLED",
            "qty_total": quantity,
            "qty_remaining": 0.0,
            "filled_avg_price": round(buy_amount / quantity, 6),
            "filled_at": str(buy.get("timestamp") or ""),
            "submitted_at": str(buy.get("timestamp") or ""),
            "closed_at": str(row.get("timestamp") or ""),
            "notional": buy_amount,
            "allocation_usd": buy_amount,
            "broker_imported": True,
            "management_state": "BROKER_HISTORY_UNATTRIBUTED",
            "strategy_id": None,
            "screener_id": None,
            "scanner_family": None,
            "strategy_lanes": [],
            "realized_pnl": round(amount - buy_amount, 4),
            "realized_pl_pct": round((amount - buy_amount) / buy_amount * 100, 4),
            "realized_pnl_source": "public_history_unattributed_round_trip",
            "broker_exit_verified": True,
            "broker_entry_transaction_id": buy.get("id"),
            "broker_exit_transaction_id": row.get("id"),
            "broker_exit_proceeds": amount,
            "close_reason": "public_history_import",
        }))
        ledger_tickers.add(ticker)
        imported.append(ticker)
    return {"imported": len(imported), "tickers": imported}


def _parse_history_start(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


async def _import_unmanaged_broker_positions(
    positions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Create explicit ledger rows for broker holdings the terminal missed.

    We intentionally do not manufacture a strategy, stop, or target for a
    holding that predates the terminal's durable order record.  Importing it
    exposes the position to the PM and protection coverage report; it remains
    non-executable for automated stop management until a real plan exists.
    """
    db = get_db()
    open_rows = await db.tf_trades.find(
        {"broker_base": BROKER_BASE, "status": "OPEN"},
        {"_id": 0, "ticker": 1},
    ).to_list(500)
    known = {_symbol(row) for row in open_rows}
    imported: list[str] = []
    for ticker, raw in positions.items():
        if not ticker or ticker in known:
            continue
        quantity = _qty(raw)
        if quantity <= 0:
            continue
        unit_cost, total_cost = _public_position_cost_basis(raw)
        opened_at = _public_position_opened_at(raw)
        current_price, provider_gain_pct, price_timestamp = _public_position_mark(raw)
        unrealized_pct = _public_position_unrealized_pct(raw, total_cost)
        await db.tf_trades.insert_one(stamped({
            "client_order_id": _import_client_order_id(ticker, opened_at),
            "broker_base": BROKER_BASE,
            "ticker": ticker,
            "instrument": "EQUITY",
            "status": "OPEN",
            "fill_status": "FILLED",
            "qty_total": quantity,
            "qty_remaining": quantity,
            "filled_avg_price": unit_cost,
            "filled_at": opened_at,
            "submitted_at": opened_at or datetime.now(timezone.utc).isoformat(),
            "notional": total_cost,
            "allocation_usd": total_cost,
            "broker_imported": True,
            "management_state": "REQUIRES_STRATEGY_RECONCILIATION",
            "strategy_id": None,
            "screener_id": None,
            "scanner_family": None,
            "strategy_lanes": [],
            "current_stop": 0.0,
            "pm_active_stop": 0.0,
            "protection_state": "UNMANAGED_NO_STOP",
            "protective_order_status": "NOT_ATTEMPTED_NO_VERIFIED_PLAN",
            "protection_note": "Imported from broker portfolio without a durable terminal entry plan; no stop or strategy was invented.",
            "broker_mark_price": current_price,
            "broker_mark_timestamp": price_timestamp,
            "broker_unrealized_pct": unrealized_pct,
            "broker_provider_gain_pct": provider_gain_pct,
            "last_synced_at": datetime.now(timezone.utc).isoformat(),
        }))
        imported.append(ticker)
    return {"imported": imported, "count": len(imported)}


def _public_session_now(now: datetime | None = None) -> str:
    """Return Public's legal equity session for the current ET clock."""
    now_et = (now or datetime.now(timezone.utc)).astimezone(ET)
    if now_et.weekday() < 5 and (now_et.hour, now_et.minute) >= (9, 30) and (now_et.hour, now_et.minute) < (16, 0):
        return "CORE"
    return "TWENTY_FOUR_HOURS"


def _entry_order_shape(amount: float, price: float, *, now: datetime | None = None) -> tuple[dict[str, float | str] | None, str | None]:
    """Build only broker-valid equity order shapes for the current session."""
    session = _public_session_now(now) if now is not None else _public_session_now()
    if session == "CORE":
        if amount < PUBLIC_MIN_FRACTIONAL_NOTIONAL_USD:
            return None, "public_core_fractional_minimum_5_usd"
        return {"amount": amount, "session": session}, None
    quantity = int(amount // price) if price > 0 else 0
    if quantity < 1:
        return None, "public_24h_requires_whole_share_within_allocation"
    return {"quantity": float(quantity), "session": session}, None


def _exit_order_shape(quantity: float, *, now: datetime | None = None) -> tuple[dict[str, float | str] | None, str | None]:
    """Build the largest broker-valid sell for the active Public session.

    Core trading accepts the broker's fractional position quantity. Public's
    24-hour session accepts whole shares only, so an overnight exit can reduce
    a fractional holding but cannot claim to flatten its fractional residual.
    The next core-session PM pass can close that residual using its updated
    broker quantity.
    """
    session = _public_session_now(now) if now is not None else _public_session_now()
    if quantity <= 0:
        return None, "public_exit_quantity_invalid"
    if session == "CORE":
        return {"quantity": float(quantity), "session": session}, None
    whole_shares = int(quantity)
    if whole_shares < 1:
        return None, "public_24h_exit_requires_whole_share"
    residual = round(float(quantity) - whole_shares, 8)
    reason = "public_24h_fractional_residual_core_session_required" if residual > 0 else None
    return {"quantity": float(whole_shares), "session": session}, reason


def _protective_order_terms(now: datetime | None = None) -> dict[str, Any]:
    """Build a broker-valid stop order for the current Public session.

    Public permits extended and 24-hour equity sessions only with DAY time in
    force.  GTD can persist for core-session stops, but pairing it with a
    24-hour session is rejected by the broker.  The monitor detects terminal
    DAY-order states and re-arms an eligible stop on later cycles.
    """
    session = _public_session_now(now)
    if session == "CORE":
        return {
            "time_in_force": "GTD",
            "expiration_time": (now or datetime.now(timezone.utc)) + timedelta(days=30),
            "session": "CORE",
        }
    return {"time_in_force": "DAY", "expiration_time": None, "session": session}


def _public_phase_plan(entry: float, target: float, *, no_capped_tp: bool, proxy_target: bool) -> dict[str, Any]:
    """Persist an explicit Public-compatible partial-exit plan.

    Lottery proxy targets and no-cap ratchet plans deliberately remain runner
    positions. A phase plan is created only for a PM structural target that is
    above the actual entry price.
    """
    if no_capped_tp or proxy_target or entry <= 0 or target <= entry:
        return {"enabled": False, "reason": "no_verified_capped_target"}
    phase2 = entry + PUBLIC_PHASE2_MULTIPLIER * (target - entry)
    return {
        "enabled": True,
        "phase1_target": round(target, 4),
        "phase2_target": round(phase2, 4),
        "phase1_close_pct": PUBLIC_PHASE1_CLOSE_PCT,
        "phase2_close_pct": PUBLIC_PHASE2_CLOSE_PCT,
        "phase3_trail_pct": PUBLIC_PHASE3_TRAIL_PCT,
        "source": "pm_structural_target",
    }


def _phase_plan_for_trade(trade: dict[str, Any]) -> dict[str, Any]:
    """Use a persisted plan, or migrate a verified PM target once.

    This intentionally cannot invent a target for account-protection or
    no-cap runner positions. Their ratchet-only exit policy remains intact.
    """
    existing = trade.get("public_phase_plan") or {}
    if existing.get("enabled"):
        return existing
    ratchet = trade.get("pm_ratchet_plan") or {}
    entry = _num(trade.get("filled_avg_price") or trade.get("entry_price_ref"))
    target = _num(
        trade.get("phase1_target")
        or trade.get("structural_target")
        or ratchet.get("initial_target_price")
    )
    return _public_phase_plan(
        entry,
        target,
        no_capped_tp=bool(ratchet.get("no_capped_tp")),
        proxy_target=str(trade.get("execution_target_mode") or "").upper() == "RATCHET_ONLY_PROXY_TARGET",
    )


def _phase_limit_price(mark: float) -> float:
    """A sellable limit based on the fresh mark, respecting Public ticks."""
    raw = mark * 0.995
    return round(raw, 4 if raw < 1 else 2)


def _numeric_field(payload: Any, names: set[str]) -> float | None:
    """Find a positive account value across Public's changing response shapes."""
    normalized = {name.replace("_", "").lower() for name in names}
    if isinstance(payload, dict):
        priority = ("buyingpower", "availablecash", "cash")
        for wanted in priority:
            for key, value in payload.items():
                if wanted in normalized and str(key).replace("_", "").lower() == wanted:
                    number = _num(value, -1.0)
                    if number >= 0:
                        return number
        for key, value in payload.items():
            if str(key).replace("_", "").lower() in normalized:
                number = _num(value, -1.0)
                if number >= 0:
                    return number
        for value in payload.values():
            found = _numeric_field(value, names)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _numeric_field(value, names)
            if found is not None:
                return found
    return None


def _cash_buying_power(payload: dict[str, Any]) -> float | None:
    """Read Public's typed cash buying-power object without guessing."""
    buying = payload.get("buyingPower") or payload.get("buying_power")
    if isinstance(buying, dict):
        for key in ("cashOnlyBuyingPower", "cash_only_buying_power", "availableCash", "available_cash"):
            value = _num(buying.get(key), -1.0)
            if value >= 0:
                return value
    return None


async def portfolio_state() -> dict[str, Any]:
    """Return the live Public equity book in a PM-safe normalized shape.

    Portfolio construction must use the same broker book that will receive an
    equity order. This read-only adapter intentionally exposes cash buying
    power separately from total account value.
    """
    if not enabled():
        return {"ok": False, "reason": "public_live_equity_disabled", "broker": BROKER_BASE}
    try:
        async with public_api.PublicAPIClient(use_sdk=False) as client:
            payload = await client.portfolio()
    except Exception as exc:
        return {"ok": False, "reason": f"public_portfolio_unavailable:{exc.__class__.__name__}", "broker": BROKER_BASE}

    positions: list[dict[str, Any]] = []
    for raw in _positions(payload):
        ticker = _symbol(raw)
        if not ticker:
            continue
        current_price, provider_gain_pct, price_timestamp = _public_position_mark(raw)
        _, total_cost = _public_position_cost_basis(raw)
        unrealized_pct = _public_position_unrealized_pct(raw, total_cost)
        positions.append({
            "ticker": ticker,
            "quantity": _qty(raw),
            "market_value": _num(raw.get("currentValue") or raw.get("current_value")),
            "cost_basis": total_cost,
            "unrealized_pl": round(_num(raw.get("currentValue") or raw.get("current_value")) - total_cost, 4),
            "current_price": current_price,
            "unrealized_pct": unrealized_pct,
            "provider_gain_pct": provider_gain_pct,
            "return_units": "percent",
            "price_timestamp": price_timestamp,
            "broker_base": BROKER_BASE,
        })
    open_orders: list[dict[str, Any]] = []
    for order in payload.get("orders") or []:
        if not isinstance(order, dict):
            continue
        status = str(order.get("status") or order.get("orderStatus") or "UNKNOWN").upper()
        if status in {"FILLED", "CANCELLED", "CANCELED", "REJECTED", "EXPIRED", "FAILED"}:
            continue
        open_orders.append({
            "id": order.get("orderId") or order.get("id"),
            "symbol": _symbol(order),
            "side": order.get("side"),
            "type": order.get("orderType") or order.get("type"),
            "qty": order.get("quantity") or order.get("filledQuantity"),
            "notional": order.get("amount") or order.get("orderValue"),
            "limit_price": order.get("limitPrice") or order.get("limit_price"),
            "status": status,
            "submitted_at": order.get("createdAt") or order.get("submittedAt"),
        })
    total_value = _num(payload.get("totalAccountValue") or payload.get("total_account_value"))
    cash = _cash_buying_power(payload)
    if total_value <= 0:
        total_value = (cash or 0.0) + sum(_num(position.get("market_value")) for position in positions)
    return {
        "ok": bool(total_value > 0 and cash is not None),
        "reason": None if total_value > 0 and cash is not None else "public_portfolio_incomplete",
        "broker": BROKER_BASE,
        "equity": round(total_value, 2),
        "cash_buying_power": round(cash, 2) if cash is not None else None,
        "positions": positions,
        "position_count": len(positions),
        "open_orders": open_orders,
        "open_order_count": len(open_orders),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def _account_allows_buys(accounts_payload: dict[str, Any], account_id: str) -> tuple[bool, str | None]:
    """Reject restricted/close-only Public accounts before new orders."""
    accounts = accounts_payload.get("accounts") or []
    if isinstance(accounts, dict):
        accounts = [accounts]
    account = next((x for x in accounts if str(x.get("accountId") or x.get("account_id")) == account_id), None)
    if not isinstance(account, dict):
        return False, "public_account_not_found"
    permission = str(account.get("tradePermissions") or account.get("trade_permissions") or "").upper()
    if permission in {"RESTRICTED_CLOSE_ONLY", "CLOSE_ONLY", "LIQUIDATION_ONLY"}:
        return False, f"public_account_{permission.lower()}"
    return True, None


def _quotes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("quotes") or payload.get("results") or payload.get("data") or []
    if isinstance(rows, dict):
        return list(rows.values())
    return rows if isinstance(rows, list) else []


async def refresh_execution_freshness(pm_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Refresh execution-grade marks immediately before the equity order stage.

    This is diagnostic and deliberately does *not* authorize an order. The
    execution path re-reads every accepted quote again just before submission.
    Keeping this separate makes stale-provider behavior visible in the cycle
    record instead of looking like a scanner delay.
    """
    approved = [
        row
        for row in pm_rows
        if str(row.get("action") or "").upper() in {"ACCUMULATE", "STARTER"}
        and _allocation(row) > 0
        and _symbol(row)
    ]
    tickers = sorted({_symbol(row) for row in approved})
    attempts = max(1, min(3, int(_num(os.environ.get("EXECUTION_FRESHNESS_ATTEMPTS"), 2))))
    retry_seconds = max(0.0, min(5.0, _num(os.environ.get("EXECUTION_FRESHNESS_RETRY_SECONDS"), 1.0)))
    rows: dict[str, dict[str, Any]] = {
        ticker: {"ticker": ticker, "fresh": False, "price": None, "age_seconds": None, "source": "unavailable"}
        for ticker in tickers
    }
    pending = set(tickers)
    errors: list[str] = []

    if not tickers:
        return {"checked_at": datetime.now(timezone.utc).isoformat(), "attempts": 0, "fresh": 0, "stale": 0, "rows": []}

    async with public_api.PublicAPIClient(use_sdk=False) as client:
        for attempt in range(1, attempts + 1):
            if not pending:
                break
            try:
                payload = await client.quotes(sorted(pending))
                by_symbol = {_symbol(row): row for row in _quotes(payload)}
            except Exception as exc:
                errors.append(f"public_quote_refresh:{exc.__class__.__name__}")
                by_symbol = {}
            for ticker in list(pending):
                quote = by_symbol.get(ticker) or {}
                quote_plan, quote_reason = _execution_quote(quote, side="BUY")
                price = _num((quote_plan or {}).get("limit_price"))
                fresh, age = safety.quote_is_fresh({"ts": _execution_quote_timestamp(quote)})
                rows[ticker] = {
                    "ticker": ticker,
                    "fresh": bool(fresh and price > 0 and quote_plan),
                    "price": price or None,
                    "bid": (quote_plan or {}).get("bid"),
                    "ask": (quote_plan or {}).get("ask"),
                    "spread_bps": (quote_plan or {}).get("spread_bps"),
                    "quote_reason": quote_reason,
                    "age_seconds": age,
                    "source": "public",
                    "quote_time": _execution_quote_timestamp(quote),
                    "attempt": attempt,
                }
                if fresh and price > 0 and quote_plan:
                    pending.discard(ticker)
            if pending and attempt < attempts and retry_seconds:
                await asyncio.sleep(retry_seconds)

    # Alpaca fallback prices remain diagnostic-only. A Public order must use a
    # fresh two-sided Public quote, never another provider's reference mark.
    if pending:
        try:
            from . import pricer
            feeds = [os.environ.get("ALPACA_STOCK_FEED", "").strip() or None, "iex", None]
            for ticker in list(pending):
                for feed in dict.fromkeys(feeds):
                    meta = await pricer._alpaca_trade_meta(ticker, feed=feed)
                    if not meta:
                        continue
                    candidate = {
                        "ticker": ticker,
                        "fresh": False,
                        "price": meta.get("price"),
                        "age_seconds": meta.get("age_seconds"),
                        "source": f"{meta.get('source') or 'alpaca'}_diagnostic_only",
                        "quote_reason": "public_execution_requires_two_sided_public_quote",
                        "quote_time": meta.get("provider_ts"),
                        "attempt": attempts,
                    }
                    # Do not replace a newer stale Public quote with an older
                    # Alpaca mark. The dashboard needs the newest observation
                    # even when no provider is fresh enough for execution.
                    prior_age = rows[ticker].get("age_seconds")
                    candidate_age = candidate.get("age_seconds")
                    if prior_age is None or (
                        candidate_age is not None and candidate_age < prior_age
                    ):
                        rows[ticker] = candidate
                    if meta.get("execution_eligible"):
                        pending.discard(ticker)
                        break
        except Exception as exc:
            errors.append(f"alpaca_quote_refresh:{exc.__class__.__name__}")

    ordered = [rows[ticker] for ticker in tickers]
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "attempts": attempts,
        "fresh": sum(1 for row in ordered if row.get("fresh")),
        "stale": sum(1 for row in ordered if not row.get("fresh")),
        "rows": ordered,
        "errors": errors,
    }


async def reconciliation_health(max_age_seconds: int = 900) -> dict[str, Any]:
    state = await get_db().bot_state.find_one({"_id": "public_reconciliation"}, {"_id": 0}) or {}
    checked_at = state.get("last_success_at")
    if not checked_at:
        return {"ok": False, "reason": "public_reconciliation_not_initialized"}
    try:
        parsed = datetime.fromisoformat(str(checked_at).replace("Z", "+00:00"))
        age = max(0, int((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()))
    except (TypeError, ValueError):
        return {"ok": False, "reason": "public_reconciliation_timestamp_invalid"}
    if age > max_age_seconds:
        return {"ok": False, "reason": "public_reconciliation_stale", "age_seconds": age}
    coverage = await protection_coverage()
    if coverage["unprotected_open"]:
        # Imported broker positions predate the terminal's decision ledger. They
        # remain visibly unmanaged, but must not make unrelated new PM entries
        # impossible. Fresh terminal-owned positions still fail closed unless
        # they have broker protection or an explicit monitored-exit routing
        # rejection.
        if coverage["managed_unresolved_unprotected_open"] == 0 and monitored_exit_override_enabled():
            warnings = []
            if coverage["monitored_exit_only_open"]:
                warnings.append("public_monitored_exit_only_override")
            if coverage["legacy_unmanaged_open"]:
                warnings.append("public_legacy_positions_unmanaged")
            return {
                "ok": True,
                "warning": warnings[0] if warnings else "public_protection_override",
                "warnings": warnings,
                "monitored_exit_override": True,
                "legacy_unmanaged_override": bool(coverage["legacy_unmanaged_open"]),
                "age_seconds": age,
                "last_success_at": checked_at,
                **coverage,
            }
        # A stale price on an already-known monitored-exit position must stay
        # visible as a portfolio-protection problem, but it is not evidence
        # that a *new* candidate has a stale price. New entries always take
        # their own fresh Public quote, stop, halt, buying-power and preflight
        # checks below. Do not freeze the entire book because premarket has
        # not printed a current two-sided quote for an older holding.
        entry_ok = coverage["unresolved_unprotected_open"] == 0
        return {
            "ok": False,
            "reason": "public_protection_coverage_incomplete",
            "entry_ok": entry_ok,
            "entry_warning": "public_existing_positions_monitor_stale" if entry_ok else None,
            "age_seconds": age,
            "last_success_at": checked_at,
            **coverage,
        }
    return {"ok": True, "entry_ok": True, "age_seconds": age, "last_success_at": checked_at, **coverage}


async def protection_coverage() -> dict[str, Any]:
    """Report broker and verified terminal-monitor protection separately."""
    rows = await get_db().tf_trades.find(
        {"broker_base": BROKER_BASE, "status": "OPEN", "fill_status": {"$in": ["FILLED", "PARTIALLY_FILLED"]}},
        {
            "_id": 0,
            "ticker": 1,
            "protective_order_id": 1,
            "protective_order_status": 1,
            "protective_order_error": 1,
            "broker_imported": 1,
            "management_state": 1,
            "current_stop": 1,
            "pm_active_stop": 1,
            "pm_ratchet_plan": 1,
            "pm_last_ratchet_check": 1,
            "protection_state": 1,
        },
    ).to_list(500)
    unprotected = []
    broker_protected = 0
    monitored_protected = 0
    for row in rows:
        if row.get("protective_order_id") and str(row.get("protective_order_status") or "").upper() == "SUBMITTED":
            broker_protected += 1
            continue
        monitored, monitor_age = _verified_monitored_exit(row)
        if monitored:
            monitored_protected += 1
            continue
        is_legacy_unmanaged = bool(row.get("broker_imported")) and row.get("management_state") == "REQUIRES_STRATEGY_RECONCILIATION"
        unprotected.append({
            "ticker": _symbol(row),
            "status": "LEGACY_UNMANAGED" if is_legacy_unmanaged else (
                "MONITORED_EXIT_STALE_OR_INCOMPLETE" if str(row.get("protection_state") or "").upper() == "MONITORED_EXIT_ONLY" else str(row.get("protective_order_status") or "MISSING").upper()
            ),
            "reason": str(row.get("protective_order_error") or "broker_protective_order_missing")[:220],
            "legacy_unmanaged": is_legacy_unmanaged,
            "monitor_age_seconds": monitor_age,
        })
    monitored_only = [row for row in unprotected if row["status"] == "MONITORED_EXIT_STALE_OR_INCOMPLETE"]
    legacy_unmanaged = [row for row in unprotected if row["legacy_unmanaged"]]
    # A stale terminal-monitored exit is visible as degraded coverage, but it
    # is not an unknown protection state. Keep it separate from genuinely
    # unresolved rows so reconciliation/reporting does not misclassify it.
    monitored_statuses = {"MONITORED_EXIT_ONLY", "MONITORED_EXIT_STALE_OR_INCOMPLETE"}
    managed_unresolved = [row for row in unprotected if row["status"] not in monitored_statuses and not row["legacy_unmanaged"]]
    return {
        "filled_open": len(rows),
        "protected_open": broker_protected + monitored_protected,
        "broker_protected_open": broker_protected,
        "verified_monitored_open": monitored_protected,
        "unprotected_open": len(unprotected),
        "monitored_exit_only_open": len(monitored_only),
        "legacy_unmanaged_open": len(legacy_unmanaged),
        "managed_unresolved_unprotected_open": len(managed_unresolved),
        # Kept for existing reporting clients; it excludes monitored-only
        # routing rejects but intentionally includes legacy warnings.
        "unresolved_unprotected_open": len(unprotected) - len(monitored_only),
        "unprotected": unprotected[:25],
    }


async def execute_pm_equity(pm_rows: list[dict[str, Any]], *, cycle_id: str | None = None) -> dict[str, Any]:
    """Submit only PM-approved equity rows, with Public preflight first."""
    if not enabled():
        return {"skipped": True, "reason": "public_live_equity_disabled", "executed": [], "rejected": []}
    approved = [row for row in pm_rows if str(row.get("action") or "").upper() in {"ACCUMULATE", "STARTER"} and _allocation(row) > 0]
    executed: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    if not approved:
        return {"skipped": False, "reason": "no_pm_equity_approvals", "executed": [], "rejected": []}
    async with public_api.PublicAPIClient(use_sdk=False) as client:
        health = await reconciliation_health()
        if not health.get("entry_ok", health.get("ok")):
            # Bootstrap the health record once after deployment. This performs
            # reconciliation only; it does not submit a buy order.
            try:
                await reconcile()
            except Exception as exc:
                logger.exception("Public reconciliation bootstrap failed")
                return {"skipped": False, "reason": "public_reconciliation_failed", "executed": [], "rejected": [{"ticker": "", "reason": "public_reconciliation_failed", "detail": str(exc)[:220]}]}
            health = await reconciliation_health()
            if not health.get("entry_ok", health.get("ok")):
                return {"skipped": False, "reason": health.get("reason") or "public_reconciliation_unhealthy", "executed": [], "rejected": [{"ticker": "", "reason": health.get("reason") or "public_reconciliation_unhealthy"}]}
        risk_allowed, safety_status = await execution_safety.add_risk_allowed("public_equity")
        if not risk_allowed:
            return {"skipped": False, "reason": safety_status.get("reason") or "safety_halt", "executed": [], "rejected": []}
        portfolio = await client.portfolio()
        account = await client.accounts()
        account_id = public_api.config().account_id
        allowed, permission_reason = _account_allows_buys(account, account_id)
        if not allowed:
            reason = permission_reason or "public_account_buy_permission_unavailable"
            return {"skipped": False, "reason": reason, "executed": [], "rejected": [{"ticker": "", "reason": reason}]}
        buying_power = _cash_buying_power(portfolio)
        if buying_power is None:
            reason = "public_cash_buying_power_unavailable"
            return {"skipped": False, "reason": reason, "executed": [], "rejected": [{"ticker": "", "reason": reason}], "rejection_reason_counts": {reason: 1}}
        from . import trading_halts
        try:
            halt_payload = await trading_halts.fetch_halts()
        except Exception as exc:
            logger.exception("Public halt feed check failed")
            return {"skipped": False, "reason": "public_halt_feed_unavailable", "executed": [], "rejected": [{"ticker": "", "reason": "public_halt_feed_unavailable", "detail": str(exc)[:220]}], "rejection_reason_counts": {"public_halt_feed_unavailable": 1}}
        if not halt_payload.get("ok"):
            return {"skipped": False, "reason": "public_halt_feed_unavailable", "executed": [], "rejected": [{"ticker": "", "reason": "public_halt_feed_unavailable"}], "rejection_reason_counts": {"public_halt_feed_unavailable": 1}}
        active_halts = {str(item.get("symbol") or "").upper() for item in halt_payload.get("halts") or [] if item.get("active")}
        held = {_symbol(row) for row in _positions(portfolio)}
        quote_rows = _quotes(await client.quotes([_symbol(row) for row in approved]))
        quote_by_symbol = {_symbol(row): row for row in quote_rows}
        for row in approved:
            ticker = _symbol(row)
            amount = _allocation(row)
            day2_allowed, day2_reason = _day2_entry_gate(row)
            if not day2_allowed:
                rejected.append({"ticker": ticker, "reason": day2_reason})
                continue
            intended_route = str(row.get("route") or "").upper()
            preferred_route = str(row.get("preferred_route") or "").upper()
            if intended_route == "OPTION" or preferred_route == "OPTION":
                rejected.append({"ticker": ticker, "reason": "public_equity_route_not_authorized", "intended_route": intended_route or preferred_route})
                continue
            quote_row = quote_by_symbol.get(ticker) or {}
            quote_source = "public"
            entry_quote, entry_quote_reason = _execution_quote(quote_row, side="BUY")
            price = _num((entry_quote or {}).get("limit_price"))
            if not ticker or price <= 0:
                fresh, age = False, None
            else:
                fresh, age = safety.quote_is_fresh({"ts": _execution_quote_timestamp(quote_row)})
            if ticker in active_halts:
                rejected.append({"ticker": ticker, "reason": "public_symbol_actively_halted"})
                continue
            if not fresh or not entry_quote:
                rejected.append({
                    "ticker": ticker,
                    "reason": entry_quote_reason or "public_execution_quote_stale_or_unverifiable",
                    "age_seconds": age,
                    "public_price": _quote_price(quote_row),
                })
                continue
            stop_price = _stop_price(row)
            if not (0 < stop_price < price):
                rejected.append({
                    "ticker": ticker,
                    "reason": "public_invalid_protective_stop",
                    "limit_price": round(price, 4),
                    "stop_price": stop_price,
                })
                continue
            proxy_target = bool(row.get("target_is_proxy")) or str(row.get("target_source") or "") == "thesis_lane_proxy_pending_validation"
            if proxy_target and not _lottery_proxy_target_can_execute(row):
                rejected.append({"ticker": ticker, "reason": "proxy_target_requires_lottery_ratchet_plan"})
                continue
            if ticker in held:
                rejected.append({"ticker": ticker, "reason": "public_position_exists"})
                continue
            if amount > buying_power:
                rejected.append({"ticker": ticker, "reason": "public_buying_power_insufficient", "required_usd": amount, "available_usd": buying_power})
                continue
            # Quote and preflight calls can take long enough for a fast name
            # to move. Re-read the Public quote immediately before claiming
            # the order; do not silently submit against the earlier price.
            try:
                final_rows = _quotes(await client.quotes([ticker]))
                final_row = next((item for item in final_rows if _symbol(item) == ticker), final_rows[0] if final_rows else {})
                final_quote, final_quote_reason = _execution_quote(final_row, side="BUY")
                final_price = _num((final_quote or {}).get("limit_price"))
                final_fresh, final_age = safety.quote_is_fresh({"ts": _execution_quote_timestamp(final_row)})
            except Exception:
                final_price, final_fresh, final_age, final_quote, final_quote_reason = 0.0, False, None, None, "public_final_quote_unavailable"
            if not final_fresh or final_price <= 0 or not final_quote:
                rejected.append({"ticker": ticker, "reason": final_quote_reason or "public_final_quote_stale_or_unverifiable", "age_seconds": final_age})
                continue
            if price > 0 and abs(final_price - price) / price > 0.01:
                rejected.append({"ticker": ticker, "reason": "public_quote_changed_before_submit", "initial_price": price, "final_price": final_price})
                continue
            price = final_price
            if not (0 < stop_price < price):
                rejected.append({"ticker": ticker, "reason": "public_final_stop_not_below_limit", "limit_price": price, "stop_price": stop_price})
                continue
            # Session legality depends on the final executable quote. In
            # extended hours Public accepts whole shares only, so calculate
            # the integer quantity after the revalidation quote, not before.
            order_shape, shape_reason = _entry_order_shape(amount, price)
            if not order_shape:
                rejected.append({"ticker": ticker, "reason": shape_reason, "allocation_usd": amount, "limit_price": price})
                continue
            protection_capability = _broker_protection_capability(order_shape, stop_price)
            client_id = execution_safety.stable_client_order_id("public_pm", cycle_id or "", ticker, amount, prefix="public")
            claim = await execution_safety.claim_execution_intent(scope="public_equity", client_order_id=client_id, symbol=ticker, side="buy", metadata={"amount": amount, "price": price, "cycle_id": cycle_id})
            if not claim.get("ok"):
                rejected.append({"ticker": ticker, "reason": claim.get("reason") or "duplicate_execution_intent"})
                continue
            try:
                result = await client.submit_equity_order(
                    symbol=ticker,
                    side="BUY",
                    amount=order_shape.get("amount"),
                    quantity=order_shape.get("quantity"),
                    limit_price=price,
                    session=str(order_shape["session"]),
                    client_order_id=client_id,
                )
            except Exception as exc:
                await execution_safety.mark_execution_intent(client_id, "broker_rejected", {"error": str(exc)[:220]})
                rejected.append({"ticker": ticker, "reason": "public_preflight_or_submission_failed", "detail": str(exc)[:220]})
                continue
            order = result.get("order") or {}
            order_id = order.get("orderId") or order.get("id")
            if not order_id:
                await execution_safety.mark_execution_intent(client_id, "broker_response_invalid", {"response_keys": sorted(order.keys())})
                rejected.append({"ticker": ticker, "reason": "public_submission_missing_order_id"})
                continue
            attribution = _strategy_attribution(row)
            ratchet_plan = row.get("ratchet_plan") or {"enabled": False}
            structural_target = _num(
                row.get("target")
                or row.get("target_blended")
                or (row.get("targets") or {}).get("target_blended")
                or ratchet_plan.get("initial_target_price")
            )
            phase_plan = _public_phase_plan(
                price,
                structural_target,
                no_capped_tp=bool(ratchet_plan.get("no_capped_tp")),
                proxy_target=proxy_target,
            )
            entry_decision = _entry_decision_packet(
                row,
                attribution=attribution,
                quote=final_quote,
                limit_price=price,
                stop_price=stop_price,
                structural_target=structural_target,
                ratchet_plan=ratchet_plan,
            )
            await get_db().tf_trades.insert_one(stamped({
                "client_order_id": client_id, "public_order_id": order_id, "broker_base": BROKER_BASE,
                "ticker": ticker, "instrument": "EQUITY", "notional": amount, "allocation_usd": amount,
                "limit_price": price, "pm_action": str(row.get("action") or "").upper(), "pm_score": row.get("pm_score"),
                "case_score": row.get("case_score"), "strategy_confidence": row.get("strategy_confidence"),
                "signals": list(row.get("signals") or []), "horizon_days": row.get("horizon_days") or row.get("hold_window_days"),
                "signal_groups": list(row.get("signal_groups") or []),
                "independent_signal_count": row.get("independent_signal_count"),
                "score_breakdown": dict(row.get("score_breakdown") or {}),
                "regime": row.get("regime"), "regime_playbook": row.get("regime_playbook"),
                "lifecycle_plan": dict(row.get("lifecycle_plan") or {}),
                "quote_source": quote_source,
                "execution_quote": final_quote,
                "entry_decision": entry_decision,
                "execution_target_mode": "RATCHET_ONLY_PROXY_TARGET" if proxy_target else "STRUCTURAL_TARGET",
                "cycle_id": cycle_id, "status": "OPEN", "fill_status": "PENDING", "qty_remaining": 0.0,
                "current_stop": stop_price, "pm_active_stop": stop_price,
                "broker_protection_capability": protection_capability,
                "protection_state": protection_capability["mode"],
                "protection_note": (
                    "Broker-linked bracket protection is eligible but disabled pending an account-specific preflight contract check."
                    if protection_capability["eligible"] and not protection_capability["enabled"]
                    else "This position requires the terminal's fresh-quote monitored exit path."
                ),
                "pm_ratchet_plan": ratchet_plan, "submitted_at": datetime.now(timezone.utc).isoformat(),
                "structural_target": structural_target or None,
                "public_phase_plan": phase_plan,
                "phase": 1,
                "phases_hit": {},
                "phase1_target": phase_plan.get("phase1_target"),
                "phase2_target": phase_plan.get("phase2_target"),
                "public_preflight": result.get("preflight"),
                "public_preflight_economics": _preflight_economics(result.get("preflight")),
                **attribution,
                "strategy_attribution": attribution,
            }))
            await execution_safety.mark_execution_intent(client_id, "submitted", {"order_id": order_id, "broker": BROKER_BASE})
            executed.append({"ticker": ticker, "allocation_usd": amount, "limit_price": price, "order_id": order_id, "quote_source": quote_source})
            held.add(ticker)
            buying_power -= amount
    await log_activity("Public equity execution completed", "info", {"executed": len(executed), "rejected": len(rejected)})
    return {"skipped": False, "executed": executed, "rejected": rejected, "rejection_reason_counts": dict(Counter(item.get("reason", "unknown") for item in rejected))}


async def reconcile() -> dict[str, Any]:
    if not enabled():
        return {"skipped": True, "reason": "public_live_equity_disabled"}
    db = get_db()
    async with public_api.PublicAPIClient(use_sdk=False) as client:
        portfolio_snapshot = await client.portfolio()
        broker_orders, broker_order_reconciliation = await _broker_orders_with_search(client, portfolio_snapshot)
        # Keep polling filled entries as well as pending entries.  DAY
        # protective stops can expire outside core hours and must be re-armed
        # from the broker-confirmed fill rather than left unprotected.
        pending = await db.tf_trades.find({"broker_base": BROKER_BASE, "status": "OPEN", "public_order_id": {"$exists": True}}, {"_id": 0}).to_list(500)
        order_updates = 0
        poll_errors = 0
        for trade in pending:
            ticker = _symbol(trade)
            submitted_at = trade.get("submitted_at")
            try:
                submitted_dt = datetime.fromisoformat(str(submitted_at).replace("Z", "+00:00")) if submitted_at else None
            except ValueError:
                submitted_dt = None
            ttl_seconds = max(60, int(_num(os.environ.get("PUBLIC_PENDING_ORDER_TTL_SECONDS"), 900)))
            if str(trade.get("fill_status") or "").upper() == "PENDING" and submitted_dt and (datetime.now(timezone.utc) - submitted_dt.astimezone(timezone.utc)).total_seconds() > ttl_seconds:
                try:
                    cancel_result = await client.cancel_order(str(trade.get("public_order_id")))
                    cancel_status = str((cancel_result or {}).get("status") or (cancel_result or {}).get("orderStatus") or "").upper()
                    if cancel_status not in {"CANCELLED", "CANCELED", "EXPIRED"}:
                        # Some broker cancel endpoints acknowledge with an
                        # empty body. Confirm the terminal state before closing
                        # the local ledger, otherwise a still-working order can
                        # disappear from reconciliation forever.
                        current = await _get_order_with_portfolio_fallback(
                            client,
                            str(trade.get("public_order_id")),
                            broker_orders,
                        )
                        confirmed = str((current or {}).get("status") or (current or {}).get("orderStatus") or "").upper()
                        if confirmed not in {"CANCELLED", "CANCELED", "EXPIRED"}:
                            raise RuntimeError(f"Public cancellation not confirmed: {confirmed or cancel_status or 'UNKNOWN'}")
                    await db.tf_trades.update_one(
                        {"client_order_id": trade.get("client_order_id"), "broker_base": BROKER_BASE},
                        {"$set": {"status": "CLOSED", "fill_status": "EXPIRED_BY_TERMINAL", "qty_remaining": 0.0, "closed_at": datetime.now(timezone.utc).isoformat(), "close_reason": "public_pending_order_ttl"}},
                    )
                    await execution_safety.mark_execution_intent(trade.get("client_order_id"), "expired", {"reason": "public_pending_order_ttl"})
                    order_updates += 1
                except Exception:
                    logger.exception("Public stale-order cancellation failed for %s", ticker)
                    poll_errors += 1
                continue
            try:
                order = await _get_order_with_portfolio_fallback(
                    client,
                    str(trade.get("public_order_id")),
                    broker_orders,
                )
            except Exception:
                poll_errors += 1
                continue
            status = str(order.get("status") or "").upper()
            if status in {"FILLED", "PARTIALLY_FILLED"}:
                filled_qty = _num(order.get("filledQuantity") or order.get("filled_quantity"))
                fill_time = trade.get("filled_at") or order.get("updatedAt") or order.get("updated_at") or datetime.now(timezone.utc).isoformat()
                update = {"fill_status": status, "qty_total": filled_qty, "qty_remaining": filled_qty, "filled_avg_price": _num(order.get("averagePrice") or order.get("average_price")), "filled_at": fill_time, "last_order_status": status}
                stop = _num(trade.get("pm_active_stop") or trade.get("current_stop"))
                protective_id = trade.get("protective_order_id")
                protective_qty = _num(trade.get("protective_order_qty"))
                needs_protective = filled_qty > 0 and stop > 0 and (
                    not protective_id or abs(protective_qty - filled_qty) > 1e-8
                )
                if filled_qty > 0 and stop > 0 and _routing_rejects_stop(trade):
                    update.update({
                        "protection_state": "MONITORED_EXIT_ONLY",
                        "protective_order_status": "ROUTING_UNSUPPORTED",
                        "protection_note": "Public routing rejected stop orders; fresh-quote monitor can only submit an emergency limit exit after breach.",
                    })
                    needs_protective = False
                if needs_protective:
                    if protective_id:
                        try:
                            await client.cancel_order(str(protective_id))
                            update.update({"protective_order_id": None, "protective_order_status": "REPLACEMENT_CANCELLED"})
                        except Exception as exc:
                            update.update({"protective_order_status": "REPLACEMENT_CANCEL_FAILED", "protective_order_error": str(exc)[:220]})
                            await db.tf_trades.update_one({"client_order_id": trade.get("client_order_id"), "broker_base": BROKER_BASE}, {"$set": update})
                            order_updates += 1
                            continue
                    stop_client_id = execution_safety.stable_client_order_id("public_protective", trade.get("client_order_id"), ticker, stop, filled_qty, prefix="public")
                    stop_claim = await execution_safety.claim_execution_intent(
                        scope="public_equity_exit",
                        client_order_id=stop_client_id,
                        symbol=ticker,
                        side="sell",
                        metadata={"stop": stop, "source_order_id": trade.get("public_order_id")},
                    )
                    if stop_claim.get("ok"):
                        try:
                            protective = await client.submit_equity_order(
                                symbol=ticker,
                                side="SELL",
                                quantity=filled_qty,
                                stop_price=stop,
                                limit_price=round(stop * 0.99, 4 if stop < 1 else 2),
                                **_protective_order_terms(),
                                client_order_id=stop_client_id,
                            )
                            protective_order = protective.get("order") or {}
                            protective_id = protective_order.get("orderId") or protective_order.get("id")
                            if not protective_id:
                                raise RuntimeError("Public protective order response missing order id")
                            update.update({"protective_order_id": protective_id, "protective_order_qty": filled_qty, "protective_order_status": "SUBMITTED", "protective_order_preflight": protective.get("preflight")})
                            update["protective_order_type"] = "STOP_LIMIT"
                            update["protective_order_gap_risk"] = "STOP_LIMIT_MAY_NOT_FILL_BELOW_LIMIT"
                            await execution_safety.mark_execution_intent(stop_client_id, "submitted", {"order_id": protective_id, "broker": BROKER_BASE})
                        except Exception as exc:
                            update.update({"protective_order_status": "FAILED", "protective_order_error": str(exc)[:220]})
                            await execution_safety.mark_execution_intent(stop_client_id, "broker_rejected", {"error": str(exc)[:220]})
                await db.tf_trades.update_one({"client_order_id": trade.get("client_order_id"), "broker_base": BROKER_BASE}, {"$set": update})
                # Record the entry exactly once. Reconciliation continues to
                # poll filled orders so expired DAY stops can be re-armed.
                newly_filled = str(trade.get("fill_status") or "").upper() not in {"FILLED", "PARTIALLY_FILLED"}
                if filled_qty > 0 and newly_filled:
                    try:
                        from . import lottery
                        await lottery.record_filled_lottery_entry(
                            broker="public",
                            broker_order_id=str(trade.get("public_order_id")),
                            ticker=ticker,
                            asset_type="EQUITY",
                            fill_price=_num(update.get("filled_avg_price") or trade.get("limit_price")),
                            quantity=filled_qty,
                            filled_at=update.get("filled_at"),
                            metadata={**trade, **(trade.get("strategy_attribution") or {})},
                        )
                    except Exception:
                        logger.exception("Lottery fill ledger failed for Public order %s", trade.get("public_order_id"))
                order_updates += 1
            elif status in {"CANCELLED", "REJECTED", "EXPIRED", "FAILED"}:
                await db.tf_trades.update_one({"client_order_id": trade.get("client_order_id"), "broker_base": BROKER_BASE}, {"$set": {"status": "CLOSED", "fill_status": status, "qty_remaining": 0.0, "closed_at": datetime.now(timezone.utc).isoformat(), "close_reason": f"public_order_{status.lower()}", "last_order_status": status}})
                order_updates += 1

        # An emergency exit is a distinct Public order. Poll and record it
        # before the generic portfolio-absence pass so a broker fill is never
        # downgraded to an unattributed "position absent" closure.
        emergency_trades = await db.tf_trades.find(
            {"broker_base": BROKER_BASE, "status": "OPEN", "emergency_exit_order_id": {"$exists": True, "$ne": None}},
            {"_id": 0},
        ).to_list(500)
        for trade in emergency_trades:
            emergency_id = str(trade.get("emergency_exit_order_id"))
            try:
                emergency = await _get_order_with_portfolio_fallback(client, emergency_id, broker_orders)
            except Exception:
                poll_errors += 1
                continue
            emergency_status = str(emergency.get("status") or emergency.get("orderStatus") or "").upper()
            update = {"emergency_exit_status": emergency_status, "last_order_status": emergency_status}
            if emergency_status in {"FILLED", "PARTIALLY_FILLED"}:
                exit_reason = str(trade.get("emergency_exit_reason") or "public_emergency_limit_exit")
                exit_qty = _num(emergency.get("filledQuantity") or emergency.get("filled_quantity"))
                exit_price = _num(emergency.get("averagePrice") or emergency.get("average_price"))
                remaining = max(0.0, _qty(trade) - exit_qty)
                update.update({
                    "emergency_exit_filled_qty": exit_qty,
                    "emergency_exit_fill_price": exit_price,
                    "last_exit_synced_at": datetime.now(timezone.utc).isoformat(),
                })
                if remaining <= 0:
                    entry_price = _num(trade.get("filled_avg_price") or trade.get("limit_price"))
                    realized_pnl = round((exit_price - entry_price) * exit_qty, 4) if entry_price > 0 else None
                    update.update({
                        "status": "CLOSED",
                        "fill_status": "EXIT_FILLED",
                        "qty_remaining": 0.0,
                        "closed_at": datetime.now(timezone.utc).isoformat(),
                        "close_reason": f"{exit_reason}_filled",
                        "broker_exit_verified": bool(entry_price > 0 and exit_price > 0),
                        "broker_exit_price": exit_price or None,
                        "broker_exit_filled_at": datetime.now(timezone.utc).isoformat(),
                        "broker_exit_source": "public_direct_emergency_order_fill",
                        "realized_pnl": realized_pnl,
                        "realized_pl_pct": round((exit_price - entry_price) / entry_price * 100, 4) if entry_price > 0 else None,
                    })
                else:
                    update["qty_remaining"] = remaining
                try:
                    from . import lottery
                    await lottery.close_filled_lottery_entry(
                        broker="public",
                        entry_order_id=str(trade.get("public_order_id") or ""),
                        exit_price=exit_price,
                        exit_quantity=exit_qty,
                        reason=f"{exit_reason}_filled",
                    )
                except Exception:
                    logger.exception("Lottery exit ledger failed for Public emergency order %s", emergency_id)
            await db.tf_trades.update_one(
                {"client_order_id": trade.get("client_order_id"), "broker_base": BROKER_BASE},
                {"$set": update},
            )
            order_updates += 1
        protective_trades = await db.tf_trades.find(
            {"broker_base": BROKER_BASE, "status": "OPEN", "protective_order_id": {"$exists": True, "$ne": None}},
            {"_id": 0},
        ).to_list(500)
        for trade in protective_trades:
            protective_id = trade.get("protective_order_id")
            try:
                protective = await _get_order_with_portfolio_fallback(
                    client,
                    str(protective_id),
                    broker_orders,
                )
            except Exception:
                poll_errors += 1
                continue
            protective_status = str(protective.get("status") or protective.get("orderStatus") or "").upper()
            if protective_status in {"FILLED", "PARTIALLY_FILLED"}:
                cumulative_exit_qty = _num(protective.get("filledQuantity") or protective.get("filled_quantity"))
                exit_price = _num(protective.get("averagePrice") or protective.get("average_price") or protective.get("limitPrice") or protective.get("limit_price"))
                prior_exit_qty = _num(trade.get("protective_filled_qty"))
                exit_qty = max(0.0, cumulative_exit_qty - prior_exit_qty)
                if exit_qty <= 0 or exit_price <= 0:
                    await db.tf_trades.update_one(
                        {"client_order_id": trade.get("client_order_id"), "broker_base": BROKER_BASE},
                        {"$set": {"protective_order_status": protective_status, "last_order_status": protective_status}},
                    )
                    order_updates += 1
                    continue
                remaining = max(0.0, _qty(trade) - exit_qty)
                update = {
                    "protective_order_status": protective_status,
                    "protective_filled_qty": cumulative_exit_qty,
                    "protective_fill_price": exit_price,
                    "last_order_status": protective_status,
                    "last_exit_synced_at": datetime.now(timezone.utc).isoformat(),
                }
                if remaining <= 0:
                    entry_price = _num(trade.get("filled_avg_price") or trade.get("limit_price"))
                    realized_pnl = round((exit_price - entry_price) * exit_qty, 4) if entry_price > 0 else None
                    update.update({
                        "status": "CLOSED",
                        "fill_status": "EXIT_FILLED",
                        "qty_remaining": 0.0,
                        "closed_at": datetime.now(timezone.utc).isoformat(),
                        "close_reason": "public_protective_stop_filled",
                        "broker_exit_verified": bool(entry_price > 0 and exit_price > 0),
                        "broker_exit_price": exit_price or None,
                        "broker_exit_filled_at": datetime.now(timezone.utc).isoformat(),
                        "broker_exit_source": "public_direct_protective_order_fill",
                        "realized_pnl": realized_pnl,
                        "realized_pl_pct": round((exit_price - entry_price) / entry_price * 100, 4) if entry_price > 0 else None,
                    })
                else:
                    update["qty_remaining"] = remaining
                await db.tf_trades.update_one(
                    {"client_order_id": trade.get("client_order_id"), "broker_base": BROKER_BASE},
                    {"$set": update},
                )
                if exit_qty > 0:
                    try:
                        from . import lottery
                        await lottery.close_filled_lottery_entry(
                            broker="public",
                            entry_order_id=str(trade.get("public_order_id") or ""),
                            exit_price=exit_price,
                            exit_quantity=exit_qty,
                            reason="public_protective_stop_filled",
                        )
                    except Exception:
                        logger.exception("Lottery exit ledger failed for Public order %s", trade.get("public_order_id"))
                order_updates += 1
            elif protective_status in {"CANCELLED", "CANCELED", "REJECTED", "EXPIRED", "FAILED"}:
                await db.tf_trades.update_one(
                    {"client_order_id": trade.get("client_order_id"), "broker_base": BROKER_BASE},
                    {"$set": {
                        "protective_order_id": None,
                        "protective_order_qty": 0.0,
                        "protective_order_status": protective_status,
                        "last_order_status": protective_status,
                        "protective_rearm_required": True,
                    }},
                )
                order_updates += 1
        # ``portfolio_snapshot`` is the broker-truth snapshot for this entire
        # reconciliation pass. Re-fetching it here doubled account traffic and
        # made one monitor pass vulnerable to a second transient outage.
        positions = {_symbol(row): row for row in _positions(portfolio_snapshot)}
        imported = await _import_unmanaged_broker_positions(positions)
        rows = await db.tf_trades.find({"broker_base": BROKER_BASE, "status": "OPEN"}, {"_id": 0}).to_list(500)
        updated = closed = 0
        for trade in rows:
            ticker = _symbol(trade)
            position = positions.get(ticker)
            if position:
                qty = _qty(position)
                unit_cost, total_cost = _public_position_cost_basis(position)
                mark_price, provider_gain_pct, mark_timestamp = _public_position_mark(position)
                unrealized_pct = _public_position_unrealized_pct(position, total_cost)
                await db.tf_trades.update_one(
                    {"client_order_id": trade.get("client_order_id"), "broker_base": BROKER_BASE},
                    {"$set": {
                        "fill_status": "FILLED" if qty > 0 else trade.get("fill_status", "PENDING"),
                        "qty_remaining": qty,
                        "qty_total": max(qty, _num(trade.get("qty_total"))),
                        "filled_avg_price": _num(trade.get("filled_avg_price")) or unit_cost,
                        "broker_cost_basis_unit": unit_cost,
                        "broker_cost_basis_total": total_cost,
                        "broker_mark_price": mark_price,
                        "broker_mark_timestamp": mark_timestamp,
                        "broker_unrealized_pct": unrealized_pct,
                        "broker_provider_gain_pct": provider_gain_pct,
                        "last_synced_at": datetime.now(timezone.utc).isoformat(),
                    }},
                )
                updated += 1
            elif trade.get("fill_status") == "FILLED":
                await db.tf_trades.update_one(
                    {"client_order_id": trade.get("client_order_id"), "broker_base": BROKER_BASE},
                    {"$set": {
                        "status": "CLOSED",
                        "qty_remaining": 0.0,
                        "closed_at": datetime.now(timezone.utc).isoformat(),
                        "close_reason": "public_position_absent",
                        "broker_exit_unconfirmed": True,
                        "broker_exit_unconfirmed_at": datetime.now(timezone.utc).isoformat(),
                    }},
                )
                try:
                    from . import lottery
                    await lottery.mark_broker_exit_unconfirmed(
                        broker="public",
                        entry_order_id=str(trade.get("public_order_id") or ""),
                        reason="public_position_absent_without_matched_sell_fill",
                    )
                except Exception:
                    logger.exception("Lottery exit reconciliation flag failed for Public order %s", trade.get("public_order_id"))
                closed += 1
        phase_updates = await _reconcile_public_phase_exits(client, broker_orders)
        order_updates += phase_updates["updates"]
        poll_errors += phase_updates["poll_errors"]
        history_reconciliation = await _reconcile_closed_broker_history(client)
        history_import = await _import_unattributed_closed_broker_history(client)
        legacy_labeled = 0
        try:
            legacy_labeled = await label_legacy_unattributed_positions()
            learning_sync = await sync_public_learning_ledger()
        except Exception as exc:
            # Analytics must never suppress broker reconciliation or exits.
            logger.exception("Public learning-ledger sync failed")
            learning_sync = {"written": 0, "verified_outcomes": 0, "learning_eligible": 0, "error": exc.__class__.__name__}
    result = {
        "skipped": False,
        "ok": poll_errors == 0,
        "order_updates": order_updates,
        "updated": updated,
        "closed": closed,
        "poll_errors": poll_errors,
        "broker_position_imported": imported,
        "broker_history_reconciliation": history_reconciliation,
        "broker_history_import": history_import,
        "learning_sync": learning_sync,
        "legacy_attribution_labeled": legacy_labeled,
        "phase_exit_reconciliation": phase_updates,
        "broker_order_reconciliation": broker_order_reconciliation,
        "broker": BROKER_BASE,
    }
    state_update = {"last_attempt_at": datetime.now(timezone.utc).isoformat(), "last_result": result}
    if poll_errors == 0:
        state_update["last_success_at"] = datetime.now(timezone.utc).isoformat()
    await db.bot_state.update_one({"_id": "public_reconciliation"}, {"$set": state_update}, upsert=True)
    return result


async def _reconcile_public_phase_exits(
    client: public_api.PublicAPIClient,
    portfolio_orders: dict[str, dict[str, Any]],
) -> dict[str, int]:
    """Resolve Public partial-exit orders against broker truth.

    A phase does not advance when a sell is merely submitted. It advances only
    after Public confirms the fill, keeping the remaining quantity and stop
    state internally consistent with the broker.
    """
    db = get_db()
    rows = await db.tf_trades.find(
        {"broker_base": BROKER_BASE, "status": "OPEN", "public_phase_order_id": {"$exists": True, "$ne": None}},
        {"_id": 0},
    ).to_list(500)
    updates = poll_errors = 0
    for trade in rows:
        raw_phase_order_id = trade.get("public_phase_order_id")
        if not raw_phase_order_id:
            continue
        phase_order_id = str(raw_phase_order_id)
        try:
            order = await _get_order_with_portfolio_fallback(client, phase_order_id, portfolio_orders)
        except Exception:
            poll_errors += 1
            continue
        status = str(order.get("status") or order.get("orderStatus") or "").upper()
        phase = int(_num(trade.get("public_phase_order_phase"), _num(trade.get("phase"), 1)))
        update: dict[str, Any] = {"public_phase_order_status": status, "last_order_status": status}
        if status in {"FILLED", "PARTIALLY_FILLED"}:
            cumulative_qty = _num(order.get("filledQuantity") or order.get("filled_quantity"))
            prior_qty = _num(trade.get("public_phase_filled_qty"))
            delta_qty = max(0.0, cumulative_qty - prior_qty)
            fill_price = _num(order.get("averagePrice") or order.get("average_price") or order.get("limitPrice") or order.get("limit_price"))
            if delta_qty > 0 and fill_price > 0:
                entry = _num(trade.get("filled_avg_price") or trade.get("entry_price_ref"))
                await db.public_phase_exits.insert_one(stamped({
                    "parent_client_order_id": trade.get("client_order_id"),
                    "public_order_id": phase_order_id,
                    "ticker": _symbol(trade),
                    "phase": phase,
                    "qty": delta_qty,
                    "fill_price": fill_price,
                    "reason": f"public_phase{phase}_target_hit",
                    "realized_pct_on_slice": round((fill_price - entry) / entry * 100, 2) if entry > 0 else None,
                    "submitted_at": trade.get("public_phase_submitted_at"),
                    "filled_at": datetime.now(timezone.utc).isoformat(),
                }))
                update.update({
                    "public_phase_filled_qty": cumulative_qty,
                    "public_phase_fill_price": fill_price,
                    "last_exit_synced_at": datetime.now(timezone.utc).isoformat(),
                })
            if status == "FILLED":
                entry = _num(trade.get("filled_avg_price") or trade.get("entry_price_ref"))
                plan = trade.get("public_phase_plan") or {}
                phases_hit = dict(trade.get("phases_hit") or {})
                existing = dict(phases_hit.get(str(phase)) or {})
                total_phase_qty = _num(trade.get("public_phase_filled_qty"))
                if delta_qty > 0:
                    total_phase_qty = cumulative_qty
                if fill_price <= 0:
                    fill_price = _num(trade.get("public_phase_fill_price"))
                if phase == 1:
                    new_stop = max(_num(trade.get("current_stop") or trade.get("pm_active_stop")), entry)
                    next_phase = 2
                else:
                    p1_exit = _num((phases_hit.get("1") or {}).get("exit_price"), entry)
                    new_stop = max(_num(trade.get("current_stop") or trade.get("pm_active_stop")), p1_exit)
                    next_phase = 3
                phases_hit[str(phase)] = {
                    **existing,
                    "hit_at": datetime.now(timezone.utc).isoformat(),
                    "trigger_price": _num(trade.get("public_phase_trigger_price")),
                    "exit_price": fill_price,
                    "qty_sold": total_phase_qty,
                    "stop_moved_to": new_stop,
                }
                update.update({
                    "phase": next_phase,
                    "phases_hit": phases_hit,
                    "current_stop": new_stop,
                    "pm_active_stop": new_stop,
                    "public_phase_order_id": None,
                    "public_phase_order_phase": None,
                    "public_phase_order_status": "FILLED",
                    "public_phase_filled_qty": 0.0,
                })
        elif status in {"CANCELLED", "CANCELED", "REJECTED", "EXPIRED", "FAILED"}:
            update.update({
                "public_phase_order_id": None,
                "public_phase_order_phase": None,
                "public_phase_order_status": status,
                "public_phase_filled_qty": 0.0,
            })
        await db.tf_trades.update_one(
            {"client_order_id": trade.get("client_order_id"), "broker_base": BROKER_BASE},
            {"$set": update},
        )
        updates += 1
    return {"updates": updates, "poll_errors": poll_errors}


async def process_public_phase_exits() -> dict[str, Any]:
    """Submit one Public limit partial-exit per position when its PM target hits."""
    if not enabled():
        return {"skipped": True, "reason": "public_live_equity_disabled"}
    db = get_db()
    rows = await db.tf_trades.find(
        {"broker_base": BROKER_BASE, "status": "OPEN", "fill_status": "FILLED", "qty_remaining": {"$gt": 0}},
        {"_id": 0},
    ).to_list(500)
    submitted: list[dict[str, Any]] = []
    deferred: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    eligible: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for trade in rows:
        if trade.get("public_phase_order_id") or trade.get("emergency_exit_order_id"):
            continue
        plan = _phase_plan_for_trade(trade)
        if not plan.get("enabled"):
            continue
        if plan != (trade.get("public_phase_plan") or {}):
            await db.tf_trades.update_one(
                {"client_order_id": trade.get("client_order_id"), "broker_base": BROKER_BASE},
                {"$set": {"public_phase_plan": plan, "phase1_target": plan.get("phase1_target"), "phase2_target": plan.get("phase2_target")}},
            )
        eligible.append((trade, plan))
    if not eligible:
        return {"skipped": False, "ok": True, "checked": len(rows), "eligible": 0, "submitted": submitted, "deferred": deferred, "errors": errors}
    async with public_api.PublicAPIClient(use_sdk=False) as client:
        # This is a monitor, not yet a requested sale. One batch quote avoids
        # spending the full retry window on every held position each minute.
        refreshed = await _refresh_exit_quotes(client, [_symbol(trade) for trade, _plan in eligible], max_attempts=1)
        for trade, plan in eligible:
            ticker = _symbol(trade)
            refreshed_quote = (refreshed.get("resolved") or {}).get(ticker) or {}
            phase_quote = refreshed_quote.get("execution_quote")
            current = _num((phase_quote or {}).get("mid"))
            if current <= 0 or not phase_quote:
                unresolved = (refreshed.get("unresolved") or {}).get(ticker) or {}
                deferred.append({"ticker": ticker, "reason": unresolved.get("reason") or "public_phase_quote_stale_or_unverifiable"})
                continue
            phase = int(_num(trade.get("phase"), 1))
            if phase == 3:
                entry = _num(trade.get("filled_avg_price") or trade.get("entry_price_ref"))
                peak = max(_num(trade.get("peak_price_since_entry"), entry), current)
                trail_pct = _num(plan.get("phase3_trail_pct"), PUBLIC_PHASE3_TRAIL_PCT)
                trail_stop = entry * (1 + ((peak - entry) / entry) * (1 - trail_pct)) if entry > 0 else 0.0
                prior_stop = _num(trade.get("current_stop") or trade.get("pm_active_stop"))
                if trail_stop > prior_stop:
                    await db.tf_trades.update_one(
                        {"client_order_id": trade.get("client_order_id"), "broker_base": BROKER_BASE},
                        {"$set": {
                            "current_stop": round(trail_stop, 4),
                            "pm_active_stop": round(trail_stop, 4),
                            "phase3_trail_pct_active": trail_pct,
                            "peak_price_since_entry": peak,
                        }},
                    )
                continue
            if phase not in {1, 2}:
                continue
            target = _num(plan.get(f"phase{phase}_target"))
            if target <= 0 or current < target:
                continue
            total_qty = _num(trade.get("qty_total"))
            remaining = _qty(trade)
            close_pct = _num(plan.get(f"phase{phase}_close_pct"))
            requested_qty = min(remaining, total_qty * close_pct)
            shape, shape_reason = _exit_order_shape(requested_qty)
            if not shape:
                deferred.append({"ticker": ticker, "reason": shape_reason or "public_phase_exit_shape_invalid"})
                continue
            client_id = execution_safety.stable_client_order_id(
                "public_phase", trade.get("client_order_id"), phase, target, prefix="public"
            )
            claim = await execution_safety.claim_execution_intent(
                scope="public_equity_phase_exit",
                client_order_id=client_id,
                symbol=ticker,
                side="sell",
                metadata={"phase": phase, "target": target, "parent_client_order_id": trade.get("client_order_id")},
            )
            if not claim.get("ok"):
                deferred.append({"ticker": ticker, "reason": claim.get("reason") or "duplicate_phase_exit"})
                continue
            try:
                limit_price = _num(phase_quote.get("limit_price"))
                result = await client.submit_equity_order(
                    symbol=ticker,
                    side="SELL",
                    quantity=float(shape["quantity"]),
                    limit_price=limit_price,
                    time_in_force="DAY",
                    session=str(shape["session"]),
                    client_order_id=client_id,
                )
                order = result.get("order") or {}
                order_id = order.get("orderId") or order.get("id")
                if not order_id:
                    raise RuntimeError("Public phase exit response missing order id")
                await db.tf_trades.update_one(
                    {"client_order_id": trade.get("client_order_id"), "broker_base": BROKER_BASE},
                    {"$set": {
                        "public_phase_order_id": order_id,
                        "public_phase_order_phase": phase,
                        "public_phase_order_status": "SUBMITTED",
                        "public_phase_order_qty": float(shape["quantity"]),
                        "public_phase_filled_qty": 0.0,
                        "public_phase_trigger_price": current,
                        "public_phase_limit": limit_price,
                        "public_phase_quote": phase_quote,
                        "public_phase_submitted_at": datetime.now(timezone.utc).isoformat(),
                        "public_phase_preflight": result.get("preflight"),
                    }},
                )
                await execution_safety.mark_execution_intent(client_id, "submitted", {"order_id": order_id, "broker": BROKER_BASE})
                submitted.append({"ticker": ticker, "phase": phase, "order_id": order_id, "qty": float(shape["quantity"]), "limit_price": limit_price})
            except Exception as exc:
                await execution_safety.mark_execution_intent(client_id, "broker_rejected", {"error": str(exc)[:220]})
                errors.append({"ticker": ticker, "reason": f"public_phase_exit_submit_failed:{exc.__class__.__name__}"})
    return {
        "skipped": False,
        "ok": not errors,
        "checked": len(rows),
        "eligible": len(eligible),
        "submitted": submitted,
        "deferred": deferred,
        "errors": errors,
        "quote_refresh": {"attempts": refreshed.get("attempts"), "unresolved": refreshed.get("unresolved")},
    }


async def process_protective_exits() -> dict[str, Any]:
    """Emergency fresh-quote limit exit after a monitored stop breach.

    Public has rejected resting stop orders for the configured routing profile.
    This is intentionally not called broker-side stop protection: it is a
    one-minute monitored fallback used only after the PM stop has breached.
    """
    if not enabled():
        return {"skipped": True, "reason": "public_live_equity_disabled"}
    db = get_db()
    rows = await db.tf_trades.find({"broker_base": BROKER_BASE, "status": "OPEN", "fill_status": "FILLED", "qty_remaining": {"$gt": 0}}, {"_id": 0}).to_list(500)
    if not rows:
        return {"skipped": False, "ok": True, "checked": 0, "submitted": [], "errors": []}
    submitted: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    async with public_api.PublicAPIClient(use_sdk=False) as client:
        # A breached stop can only be established from a current quote. Keep
        # routine monitoring to one batch request; an explicit exit request
        # below receives the rapid one-minute retry window.
        refreshed = await _refresh_exit_quotes(client, [_symbol(row) for row in rows], emergency=True, max_attempts=1)
        broker_positions = None
        for trade in rows:
            if trade.get("public_phase_order_id") or trade.get("emergency_exit_order_id"):
                # A phase trim is already working at the broker. Do not race
                # it with a full emergency close using stale local quantity.
                continue
            ticker = _symbol(trade)
            stop = _num(trade.get("pm_active_stop") or trade.get("current_stop"))
            refreshed_quote = (refreshed.get("resolved") or {}).get(ticker) or {}
            exit_quote = refreshed_quote.get("execution_quote")
            current = _num((exit_quote or {}).get("mid"))
            quantity = _qty(trade)
            if stop <= 0 or current <= 0 or not exit_quote or quantity <= 0 or current > stop:
                continue
            if broker_positions is None:
                broker_positions = {
                    _symbol(position): position
                    for position in _positions(await client.portfolio())
                }
            broker_quantity = _qty(broker_positions.get(ticker) or {})
            order_shape, shape_note = _exit_order_shape(broker_quantity)
            if not order_shape:
                deferred.append({"ticker": ticker, "reason": shape_note})
                continue
            quantity = _num(order_shape.get("quantity"))
            client_id = execution_safety.stable_client_order_id("public_stop", trade.get("client_order_id"), ticker, stop, prefix="public")
            claim = await execution_safety.claim_execution_intent(scope="public_equity_exit", client_order_id=client_id, symbol=ticker, side="sell", metadata={"stop": stop})
            if not claim.get("ok"):
                continue
            try:
                # A conditional order is rejected by the current broker
                # routing profile. Submit a plainly labelled, fresh-quote
                # limit close instead; it can still remain unfilled, which is
                # why the position stays reconciled until broker confirmation.
                exit_limit = _num(exit_quote.get("limit_price"))
                result = await client.submit_equity_order(
                    symbol=ticker,
                    side="SELL",
                    quantity=quantity,
                    limit_price=exit_limit,
                    time_in_force="DAY",
                    session=str(order_shape["session"]),
                    client_order_id=client_id,
                )
                order = result.get("order") or {}
                order_id = order.get("orderId") or order.get("id")
                if not order_id:
                    raise RuntimeError("Public protective order response missing order id")
                await db.tf_trades.update_one(
                    {"client_order_id": trade.get("client_order_id"), "broker_base": BROKER_BASE},
                    {"$set": {"emergency_exit_order_id": order_id, "emergency_exit_order_qty": quantity, "emergency_exit_status": "SUBMITTED", "emergency_exit_limit": exit_limit, "emergency_exit_quote": exit_quote, "emergency_exit_preflight": result.get("preflight"), "emergency_exit_session": order_shape["session"], "emergency_exit_shape_note": shape_note}},
                )
                submitted.append({"ticker": ticker, "order_id": order_id, "stop": stop, "limit_price": exit_limit, "order_type": "EMERGENCY_LIMIT_EXIT"})
                await execution_safety.mark_execution_intent(client_id, "submitted", {"order_id": order_id})
            except Exception as exc:
                await execution_safety.mark_execution_intent(client_id, "broker_rejected", {"error": str(exc)[:220]})
                errors.append({"ticker": ticker, "reason": f"emergency_exit_submit_failed:{exc.__class__.__name__}"})
    return {
        "skipped": False,
        "ok": not errors,
        "checked": len(rows),
        "submitted": submitted,
        "errors": errors,
        "deferred": deferred,
        "quote_refresh": {
            "attempts": refreshed.get("attempts"),
            "unresolved": refreshed.get("unresolved"),
        },
    }


async def submit_exit_to_cash(ticker: str, *, reason: str = "pm_or_operator_exit") -> dict[str, Any]:
    """Submit one fully reconciled Public limit exit from broker truth.

    This is deliberately narrow: it is for an explicit PM or operator decision
    to flatten one existing position. It never infers a sell from a stale local
    ledger row, never uses a market order, and records the broker order in the
    established exit fields so ``reconcile`` owns confirmation and closure.
    """
    symbol = str(ticker or "").upper().strip()
    if not symbol:
        return {"submitted": False, "reason": "public_exit_ticker_required"}
    if not enabled():
        return {"submitted": False, "ticker": symbol, "reason": "public_live_equity_disabled"}

    db = get_db()
    trade = await db.tf_trades.find_one(
        {"broker_base": BROKER_BASE, "status": "OPEN", "fill_status": "FILLED", "$or": [{"ticker": symbol}, {"symbol": symbol}]},
        {"_id": 0},
    )
    if not trade:
        return {"submitted": False, "ticker": symbol, "reason": "public_open_trade_ledger_missing"}
    if trade.get("emergency_exit_order_id") or trade.get("public_phase_order_id"):
        return {"submitted": False, "ticker": symbol, "reason": "public_exit_order_already_active"}

    async with public_api.PublicAPIClient(use_sdk=False) as client:
        portfolio = await client.portfolio()
        broker_position = next((row for row in _positions(portfolio) if _symbol(row) == symbol), None)
        broker_quantity = _qty(broker_position or {})
        if broker_quantity <= 0:
            return {"submitted": False, "ticker": symbol, "reason": "public_broker_position_not_found"}

        refreshed = await _refresh_exit_quotes(client, [symbol])
        refreshed_quote = (refreshed.get("resolved") or {}).get(symbol) or {}
        exit_quote = refreshed_quote.get("execution_quote")
        price = _num((exit_quote or {}).get("mid"))
        if price <= 0 or not exit_quote:
            unresolved = (refreshed.get("unresolved") or {}).get(symbol) or {}
            await _record_exit_quote_unavailable(
                trade,
                ticker=symbol,
                reason=unresolved.get("reason") or "public_exit_quote_stale_or_unverifiable",
                quote_refresh=refreshed,
            )
            return {
                "submitted": False,
                "ticker": symbol,
                "reason": unresolved.get("reason") or "public_exit_quote_stale_or_unverifiable",
                "age_seconds": unresolved.get("age_seconds"),
                "quote_refresh_attempts": refreshed.get("attempts"),
            }
        order_shape, shape_note = _exit_order_shape(broker_quantity)
        if not order_shape:
            return {"submitted": False, "ticker": symbol, "reason": shape_note or "public_exit_shape_unavailable"}
        quantity = _num(order_shape.get("quantity"))
        exit_limit = _num(exit_quote.get("limit_price"))
        client_id = execution_safety.stable_client_order_id(
            "public_exit_to_cash", trade.get("client_order_id"), symbol, reason, prefix="public"
        )
        claim = await execution_safety.claim_execution_intent(
            scope="public_equity_exit",
            client_order_id=client_id,
            symbol=symbol,
            side="sell",
            metadata={"reason": reason, "broker_quantity": broker_quantity, "limit_price": exit_limit},
        )
        if not claim.get("ok"):
            return {"submitted": False, "ticker": symbol, "reason": claim.get("reason") or "duplicate_execution_intent"}
        try:
            result = await client.submit_equity_order(
                symbol=symbol,
                side="SELL",
                quantity=quantity,
                limit_price=exit_limit,
                time_in_force="DAY",
                session=str(order_shape["session"]),
                client_order_id=client_id,
            )
            order = result.get("order") or {}
            order_id = order.get("orderId") or order.get("id")
            if not order_id:
                raise RuntimeError("Public exit response missing order id")
            await db.tf_trades.update_one(
                {"client_order_id": trade.get("client_order_id"), "broker_base": BROKER_BASE},
                {"$set": {
                    "emergency_exit_order_id": order_id,
                    "emergency_exit_order_qty": quantity,
                    "emergency_exit_status": "SUBMITTED",
                    "emergency_exit_limit": exit_limit,
                    "emergency_exit_quote": exit_quote,
                    "emergency_exit_preflight": result.get("preflight"),
                    "emergency_exit_reason": reason,
                    "emergency_exit_session": order_shape["session"],
                    "emergency_exit_shape_note": shape_note,
                }},
            )
            await execution_safety.mark_execution_intent(client_id, "submitted", {"order_id": order_id, "broker": BROKER_BASE})
            return {
                "submitted": True,
                "ticker": symbol,
                "order_id": order_id,
                "quantity": quantity,
                "limit_price": exit_limit,
                "session": order_shape["session"],
                "reason": reason,
            }
        except Exception as exc:
            await execution_safety.mark_execution_intent(client_id, "broker_rejected", {"error": str(exc)[:220]})
            return {"submitted": False, "ticker": symbol, "reason": f"public_exit_submit_failed:{exc.__class__.__name__}"}


async def analytics(limit: int = 500) -> dict[str, Any]:
    """Read-only Public execution and protection coverage metrics."""
    db = get_db()
    rows = await db.tf_trades.find({"broker_base": BROKER_BASE}, {"_id": 0}).sort("submitted_at", -1).to_list(max(1, min(limit, 5000)))
    status_counts = Counter(str(row.get("fill_status") or row.get("status") or "UNKNOWN").upper() for row in rows)
    slippage_bps: list[float] = []
    arrival_mid_slippage_bps: list[float] = []
    estimated_fee_usd: list[float] = []
    by_strategy: Counter[str] = Counter()
    filled = 0
    protected = 0
    for row in rows:
        strategy = str(row.get("strategy_id") or row.get("screener_id") or "UNATTRIBUTED")
        by_strategy[strategy] += 1
        fill_price = _num(row.get("filled_avg_price"))
        limit_price = _num(row.get("limit_price"))
        if fill_price > 0 and limit_price > 0:
            slippage_bps.append(round(((fill_price - limit_price) / limit_price) * 10000, 2))
        arrival_mid = _num((row.get("execution_quote") or {}).get("mid"))
        if fill_price > 0 and arrival_mid > 0:
            arrival_mid_slippage_bps.append(round(((fill_price - arrival_mid) / arrival_mid) * 10000, 2))
        preflight_fees = _num((row.get("public_preflight_economics") or {}).get("estimated_total_fees"), -1.0)
        if preflight_fees >= 0:
            estimated_fee_usd.append(preflight_fees)
        if str(row.get("fill_status") or "").upper() in {"FILLED", "PARTIALLY_FILLED"} or _qty(row) > 0:
            filled += 1
            if row.get("protective_order_id") and row.get("protective_order_status") == "SUBMITTED":
                protected += 1
    return {
        "ok": True,
        "read_only": True,
        "broker": BROKER_BASE,
        "records": len(rows),
        "status_counts": dict(status_counts),
        "filled_records": filled,
        "protected_filled_records": protected,
        "protection_coverage_pct": round(protected / filled * 100, 2) if filled else None,
        "slippage_bps": {"n": len(slippage_bps), "avg": round(sum(slippage_bps) / len(slippage_bps), 2) if slippage_bps else None, "worst": max(slippage_bps) if slippage_bps else None},
        "arrival_mid_slippage_bps": {"n": len(arrival_mid_slippage_bps), "avg": round(sum(arrival_mid_slippage_bps) / len(arrival_mid_slippage_bps), 2) if arrival_mid_slippage_bps else None, "worst": max(arrival_mid_slippage_bps) if arrival_mid_slippage_bps else None},
        "preflight_estimated_fees_usd": {"n": len(estimated_fee_usd), "total": round(sum(estimated_fee_usd), 4) if estimated_fee_usd else None, "avg": round(sum(estimated_fee_usd) / len(estimated_fee_usd), 4) if estimated_fee_usd else None},
        "by_strategy": dict(by_strategy),
    }
