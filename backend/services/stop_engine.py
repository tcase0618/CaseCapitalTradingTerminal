"""Analytical Stop Engine — replaces ATR with a learnable, multi-factor
stop calculator. Alpaca is the sole data source for price + volatility.

Inputs to compute_stop():
  • entry_price (limit price actually used on the order)
  • signal_combo (list[str]) — drives signal-type adjustment
  • score (AXIOM trade_score or learning_score)
  • hold_window_days (midpoint of recommended hold)
  • sector (string from scan row; "unknown" if missing)
  • instrument ("fractional" | "options")
  • ticker (used to fetch Alpaca historical volatility)

Default formula (used until Trade Floor Learning Engine has data):
  base = 10% below entry
  adjusted by hold window, sector, score tier, instrument, Alpaca 30d
  realized volatility. Hard clamped 5%-25%.

After the engine has ≥10 closed trades the recalibrator overwrites the
coefficients stored in `tf_stop_engine` based on (calculated_stop_pct vs
lowest_price_reached vs realized_pct) per signal-type/sector/score-tier.
"""
from __future__ import annotations
import logging
import os
import statistics
from datetime import datetime, timezone
from typing import Any

import httpx

from .db import get_db

logger = logging.getLogger(__name__)

ALPACA_KEY = os.environ.get("APCA_API_KEY_ID", "").strip()
ALPACA_SECRET = os.environ.get("APCA_API_SECRET_KEY", "").strip()
ALPACA_DATA_BASE = "https://data.alpaca.markets/v2"
HEADERS = {"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}

# Base stop = 10% below entry. Everything else is a delta applied to this.
DEFAULT_COEFFICIENTS = {
    "base_pct": 0.10,                # 10% below entry — interim default
    "hold_window_delta": {           # delta vs 14d midpoint
        "7": -0.03, "14": 0.0, "21": 0.01, "30": 0.03, "45": 0.05, "60": 0.07,
    },
    "sector_delta": {
        "healthcare": 0.03, "biotech": 0.04, "biotechnology": 0.04,
        "technology": 0.015, "energy": 0.02, "consumer cyclical": 0.005,
        "consumer defensive": -0.01, "communication services": 0.005,
        "industrials": 0.0, "financial services": 0.0, "real estate": -0.005,
        "utilities": -0.015, "basic materials": 0.01, "unknown": 0.0,
        # AXIOM tag aliases
        "defense": -0.01, "aerospace & defense": -0.01,
    },
    "score_tier_delta": {
        "20-24": 0.01,   # lower conviction → wider stop
        "25-29": 0.005,
        "30-49": 0.0,
        "50+": -0.01,    # high conviction → tighter stop
    },
    "instrument_delta": {
        "fractional": 0.0,
        "options": 0.05,   # options decay → need more room
    },
    "signal_combo_delta": {           # special combos
        "CONGRESSIONAL_BUY+government_contract_award": -0.01,  # tighter, high conviction gov
        "insider_cluster_buy+CONGRESSIONAL_BUY": -0.005,
        "high_short_interest+UNUSUAL_FLOW": 0.01,              # squeezes → wider
    },
    "vol_brackets": [                 # 30d realized vol bracket → delta
        {"max": 0.02, "delta": -0.01},   # <2% daily stdev
        {"max": 0.04, "delta": 0.0},     # 2-4%
        {"max": 0.06, "delta": 0.03},    # 4-6%
        {"max": 0.10, "delta": 0.05},    # 6-10%
        {"max": 999,  "delta": 0.07},    # >10%
    ],
    "min_pct": 0.05,
    "max_pct": 0.25,
}


async def _ensure_coefficients() -> dict[str, Any]:
    """Seed the coefficients on first call. Idempotent."""
    db = get_db()
    doc = await db.tf_stop_engine.find_one({"_id": "current"})
    if doc:
        return doc.get("coefficients") or DEFAULT_COEFFICIENTS
    await db.tf_stop_engine.insert_one({
        "_id": "current",
        "coefficients": DEFAULT_COEFFICIENTS,
        "initialized_at": datetime.now(timezone.utc).isoformat(),
        "note": "Interim defaults; recalibrated weekly by Trade Floor Learning Engine.",
    })
    return DEFAULT_COEFFICIENTS


async def alpaca_realized_volatility(ticker: str, days: int = 30) -> float | None:
    """30-day realized volatility = stdev of daily simple returns. Sole
    data source is Alpaca historical bars. Returns None on failure (so
    the stop engine simply skips the vol delta and uses the rest)."""
    if not (ALPACA_KEY and ALPACA_SECRET):
        return None
    try:
        async with httpx.AsyncClient(timeout=8.0, headers=HEADERS) as c:
            r = await c.get(
                f"{ALPACA_DATA_BASE}/stocks/{ticker.upper()}/bars",
                params={"timeframe": "1Day", "limit": days + 1, "feed": "iex",
                          "adjustment": "raw"},
            )
            if r.status_code != 200:
                return None
            bars = r.json().get("bars") or []
            closes = [float(b.get("c") or 0) for b in bars if b.get("c")]
            if len(closes) < 5:
                return None
            rets = [(closes[i] - closes[i-1]) / closes[i-1]
                       for i in range(1, len(closes)) if closes[i-1]]
            return round(statistics.pstdev(rets), 6) if rets else None
    except Exception as e:
        logger.debug("alpaca vol %s: %s", ticker, e)
        return None


def _score_tier(score: float) -> str:
    if score >= 50:
        return "50+"
    if score >= 30:
        return "30-49"
    if score >= 25:
        return "25-29"
    return "20-24"


def _bucket_hold_window(days: int | None) -> str:
    if not days or days <= 0:
        return "14"
    if days <= 8:
        return "7"
    if days <= 17:
        return "14"
    if days <= 25:
        return "21"
    if days <= 37:
        return "30"
    if days <= 52:
        return "45"
    return "60"


async def compute_stop(
    *,
    ticker: str,
    entry_price: float,
    signal_combo: list[str],
    score: float,
    hold_window_days: int | None,
    sector: str | None,
    instrument: str = "fractional",
) -> dict[str, Any]:
    """Return {'stop_price', 'stop_pct', 'breakdown'} for the given trade.

    Breakdown is a dict explaining every factor that contributed — useful
    for the Trade Floor Engine's recalibrator (the recalibrator reads
    realized outcomes per breakdown.signal_tier/sector/etc and adjusts
    the coefficient table)."""
    coef = await _ensure_coefficients()
    breakdown: dict[str, Any] = {"base_pct": coef["base_pct"]}
    stop_pct = float(coef["base_pct"])

    # Hold window
    hbucket = _bucket_hold_window(hold_window_days)
    h_delta = float(coef["hold_window_delta"].get(hbucket, 0.0))
    stop_pct += h_delta
    breakdown["hold_bucket"] = hbucket
    breakdown["hold_delta"] = h_delta

    # Sector
    sec_key = (sector or "unknown").strip().lower()
    s_delta = float(coef["sector_delta"].get(sec_key, 0.0))
    stop_pct += s_delta
    breakdown["sector"] = sec_key
    breakdown["sector_delta"] = s_delta

    # Score tier
    tier = _score_tier(float(score or 0))
    sc_delta = float(coef["score_tier_delta"].get(tier, 0.0))
    stop_pct += sc_delta
    breakdown["score_tier"] = tier
    breakdown["score_delta"] = sc_delta

    # Instrument
    inst_delta = float(coef["instrument_delta"].get(instrument, 0.0))
    stop_pct += inst_delta
    breakdown["instrument"] = instrument
    breakdown["instrument_delta"] = inst_delta

    # Signal combo specials
    combo_key = "+".join(sorted(signal_combo or []))
    combo_delta = float(coef["signal_combo_delta"].get(combo_key, 0.0))
    stop_pct += combo_delta
    breakdown["signal_combo"] = combo_key
    breakdown["combo_delta"] = combo_delta

    # Alpaca 30d realized vol
    vol = await alpaca_realized_volatility(ticker, days=30)
    vol_delta = 0.0
    if vol is not None:
        for b in coef["vol_brackets"]:
            if vol <= b["max"]:
                vol_delta = float(b["delta"])
                break
        stop_pct += vol_delta
    breakdown["realized_vol_30d"] = vol
    breakdown["vol_delta"] = vol_delta

    # Clamp
    stop_pct = max(float(coef["min_pct"]), min(float(coef["max_pct"]), stop_pct))
    breakdown["final_stop_pct"] = round(stop_pct, 4)

    stop_price = round(entry_price * (1 - stop_pct), 4)
    return {
        "stop_price": stop_price,
        "stop_pct": round(stop_pct, 4),
        "breakdown": breakdown,
    }
