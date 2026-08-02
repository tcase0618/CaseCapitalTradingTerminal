"""Earnings Engine — full current-week schedule + Beat Probability model.

Pulls every stock reporting Mon-Fri of the current calendar week via yfinance
calendar API. For each ticker, computes a 5-95% Beat Probability blending:

  • EPS surprise streak (last 8 quarters)        — strongest predictor
  • Analyst revision direction (last 30 days)    — quiet ups before earnings
  • 20-day price momentum into print
  • Revenue acceleration across last 3 quarters
  • Finviz short interest                         — low short = institutions not betting on miss

Final blend = 60% model + 40% options-flow direction (48h pre-earnings).

Strategy recommendation:
  • Beat >70 + cheap IV   → LONG CALL
  • Beat >70 + rich IV    → CALL SPREAD (sell premium)
  • Beat 45-70            → AVOID
  • Beat <45              → BEAR PUT SPREAD
"""
from __future__ import annotations
import asyncio
import csv
import io
import logging
import math
import os
from datetime import datetime, timedelta, timezone, date
from typing import Any

import httpx

from .db import get_db, log_activity, stamped
from . import options_engine, scrapers

logger = logging.getLogger(__name__)
EARNINGS_FEATURE_VERSION = "3.2"
_BACKGROUND_REFRESH_KEYS: set[str] = set()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _target_week_window(today: date | None = None) -> tuple[date, date]:
    """Current trading week, rolling weekends forward to the next Monday."""
    today = today or _now().date()
    monday = _monday(today)
    if today.weekday() >= 5:
        monday = monday + timedelta(days=7)
    return monday, monday + timedelta(days=4)


# ─────────────────────────── yfinance helpers ───────────────────────────
async def _yf_earnings_history(ticker: str) -> list[dict[str, Any]]:
    """Returns last N quarterly EPS prints with actual vs estimate."""
    def _sync():
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            h = t.get_earnings_history()
            if h is None or len(h) == 0:
                return []
            rows = []
            for _, r in h.iterrows():
                rows.append({
                    "quarter": str(r.get("quarter") or ""),
                    "eps_actual": float(r.get("epsActual") or 0),
                    "eps_estimate": float(r.get("epsEstimate") or 0),
                    "surprise_pct": float(r.get("surprisePercent") or 0),
                })
            return rows[-8:]
        except Exception as e:
            logger.debug("yf earnings hist %s: %s", ticker, e)
            return []
    return await asyncio.get_event_loop().run_in_executor(None, _sync)


async def _yf_fundamentals(ticker: str) -> dict[str, Any]:
    """Returns {industry, sector, market_cap, current_price, momentum_20d,
    revenue_accel, iv_rank, implied_move}."""
    def _sync():
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            info = t.info or {}
            hist = t.history(period="30d")
            momentum_20d = None
            if len(hist) >= 20:
                cur = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-20])
                momentum_20d = round((cur - prev) / prev * 100, 2)
            rev_accel = None
            try:
                fin = t.quarterly_financials
                if fin is not None and "Total Revenue" in fin.index:
                    rev = fin.loc["Total Revenue"].dropna()[:3]
                    if len(rev) == 3:
                        # Most recent is column 0
                        latest, mid, old = float(rev.iloc[0]), float(rev.iloc[1]), float(rev.iloc[2])
                        g1 = (latest - mid) / mid if mid else 0
                        g2 = (mid - old) / old if old else 0
                        rev_accel = round((g1 - g2) * 100, 1)
            except Exception:
                pass
            return {
                "industry": info.get("industry"),
                "sector": info.get("sector"),
                "market_cap": info.get("marketCap"),
                "current_price": info.get("regularMarketPrice"),
                "average_volume": info.get("averageVolume") or info.get("averageDailyVolume10Day"),
                "trailing_pe": info.get("trailingPE"),
                "forward_pe": info.get("forwardPE"),
                "profit_margin": info.get("profitMargins"),
                "revenue_growth": info.get("revenueGrowth"),
                "earnings_growth": info.get("earningsGrowth"),
                "target_mean_price": info.get("targetMeanPrice"),
                "momentum_20d_pct": momentum_20d,
                "revenue_accel": rev_accel,
                "short_pct": info.get("shortPercentOfFloat"),
                "earnings_time": info.get("earningsTimestamp"),
            }
        except Exception as e:
            logger.debug("yf fund %s: %s", ticker, e)
            return {}
    return await asyncio.get_event_loop().run_in_executor(None, _sync)


async def _yf_earnings_moves(ticker: str, hist_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Approximate next-session earnings reactions from yfinance price history."""
    quarters = [h.get("quarter") for h in hist_rows if h.get("quarter")]
    if not quarters:
        return {"moves": [], "avg_abs_move_pct": None, "avg_signed_move_pct": None, "gap_fade_rate": None}

    def _sync():
        try:
            import yfinance as yf
            t = yf.Ticker(ticker)
            px = t.history(period="3y")
            if px is None or px.empty:
                return {"moves": [], "avg_abs_move_pct": None, "avg_signed_move_pct": None, "gap_fade_rate": None}
            px = px.reset_index()
            px["date_only"] = px["Date"].dt.date
            moves = []
            for q in quarters[-8:]:
                try:
                    qd = datetime.fromisoformat(str(q)).date()
                except Exception:
                    continue
                idxs = px.index[px["date_only"] >= qd].tolist()
                if len(idxs) < 2:
                    continue
                i = idxs[0]
                if i == 0:
                    continue
                prev_close = float(px.loc[i - 1, "Close"])
                close = float(px.loc[i, "Close"])
                open_px = float(px.loc[i, "Open"])
                if prev_close <= 0:
                    continue
                signed = (close - prev_close) / prev_close * 100
                gap = (open_px - prev_close) / prev_close * 100
                faded = (gap > 0 and close < open_px) or (gap < 0 and close > open_px)
                moves.append({
                    "date": qd.isoformat(),
                    "move_pct": round(signed, 2),
                    "gap_pct": round(gap, 2),
                    "faded": bool(faded),
                })
            if not moves:
                return {"moves": [], "avg_abs_move_pct": None, "avg_signed_move_pct": None, "gap_fade_rate": None}
            return {
                "moves": moves,
                "avg_abs_move_pct": round(sum(abs(m["move_pct"]) for m in moves) / len(moves), 2),
                "avg_signed_move_pct": round(sum(m["move_pct"] for m in moves) / len(moves), 2),
                "gap_fade_rate": round(sum(1 for m in moves if m["faded"]) / len(moves) * 100, 1),
            }
        except Exception as e:
            logger.debug("yf earnings moves %s: %s", ticker, e)
            return {"moves": [], "avg_abs_move_pct": None, "avg_signed_move_pct": None, "gap_fade_rate": None}

    return await asyncio.get_event_loop().run_in_executor(None, _sync)


async def _yf_news_synopsis(ticker: str) -> dict[str, Any]:
    """Short earnings-call proxy from recent finance headlines.

    This intentionally does not pretend to be a full transcript parser. It
    surfaces earnings/call/guidance headlines when available and labels the
    synopsis as news-derived.
    """
    def _sync():
        try:
            import yfinance as yf
            news = yf.Ticker(ticker).news or []
            hits = []
            for item in news[:20]:
                content = item.get("content") if isinstance(item, dict) else {}
                title = (content or {}).get("title") or item.get("title") or ""
                summary = (content or {}).get("summary") or item.get("summary") or ""
                publisher = (content or {}).get("provider", {}).get("displayName") if content else item.get("publisher")
                hay = f"{title} {summary}".lower()
                if any(word in hay for word in ["earnings", "guidance", "quarter", "results", "call", "eps", "revenue"]):
                    hits.append({"title": title, "summary": summary, "publisher": publisher})
                if len(hits) >= 3:
                    break
            if not hits:
                return {
                    "available": False,
                    "source": "yfinance news",
                    "text": "No recent earnings-call headline context found. Attach a transcript source later for a true management-call recap.",
                    "headlines": [],
                }
            points = [h["title"] for h in hits if h.get("title")]
            return {
                "available": True,
                "source": "yfinance news headlines",
                "text": "Recent earnings context centers on " + "; ".join(points[:2]) + ".",
                "headlines": hits,
            }
        except Exception as e:
            logger.debug("yf news synopsis %s: %s", ticker, e)
            return {
                "available": False,
                "source": "yfinance news",
                "text": "Earnings-call synopsis unavailable from current free sources.",
                "headlines": [],
            }

    return await asyncio.get_event_loop().run_in_executor(None, _sync)


async def _options_snapshot(ticker: str, earnings_date: str | None) -> dict[str, Any]:
    chain = await options_engine.get_options_data(ticker, earnings_date)
    if not chain:
        return {
            "iv_rank": None,
            "iv_label": "UNKNOWN",
            "atm_iv": None,
            "implied_move_pct": None,
            "expiration": None,
            "source": "options unavailable",
        }
    implied = None
    try:
        atm_iv = chain.get("atm_iv")
        exp = chain.get("expiration")
        if atm_iv and exp:
            exp_date = datetime.fromisoformat(exp).date()
            dte = max(1, (exp_date - _now().date()).days)
            implied = round(float(atm_iv) * math.sqrt(dte / 365.0) * 100, 2)
    except Exception:
        implied = None
    return {
        "iv_rank": chain.get("iv_rank"),
        "iv_label": chain.get("iv_label"),
        "atm_iv": chain.get("atm_iv"),
        "implied_move_pct": implied,
        "expiration": chain.get("expiration"),
        "source": "yfinance options",
    }


async def _yf_post_earnings_reaction(ticker: str, earnings_date: str | None,
                                     am_pm: str | None = None) -> dict[str, Any]:
    """Regular-session reaction after an earnings print.

    AM reports compare report-day close to prior close. PM reports compare the
    next trading-day close to report-day close.
    """
    if not earnings_date:
        return {"status": "unknown", "reaction_pct": None, "reaction_label": "UNKNOWN"}
    try:
        report_date = datetime.fromisoformat(earnings_date).date()
    except Exception:
        return {"status": "unknown", "reaction_pct": None, "reaction_label": "UNKNOWN"}
    if report_date >= _now().date():
        return {"status": "pending", "reaction_pct": None, "reaction_label": "PENDING"}

    def _sync():
        try:
            import yfinance as yf
            start = report_date - timedelta(days=7)
            end = report_date + timedelta(days=10)
            px = yf.Ticker(ticker).history(start=start.isoformat(), end=end.isoformat())
            if px is None or px.empty or len(px) < 2:
                return {"status": "unavailable", "reaction_pct": None, "reaction_label": "NO PRICE DATA"}
            px = px.reset_index()
            px["date_only"] = px["Date"].dt.date
            trade_dates = list(px["date_only"])
            report_idxs = [i for i, d in enumerate(trade_dates) if d >= report_date]
            if not report_idxs:
                return {"status": "unavailable", "reaction_pct": None, "reaction_label": "NO PRICE DATA"}
            report_idx = report_idxs[0]
            if (am_pm or "").upper() == "PM":
                base_idx = report_idx
                react_idx = report_idx + 1
            else:
                base_idx = report_idx - 1
                react_idx = report_idx
            if base_idx < 0 or react_idx >= len(px):
                return {"status": "pending", "reaction_pct": None, "reaction_label": "PENDING"}
            base_close = float(px.loc[base_idx, "Close"])
            react_close = float(px.loc[react_idx, "Close"])
            if base_close <= 0:
                return {"status": "unavailable", "reaction_pct": None, "reaction_label": "NO PRICE DATA"}
            reaction = round((react_close - base_close) / base_close * 100, 2)
            if reaction >= 3:
                label = "BULLISH REACTION"
            elif reaction <= -3:
                label = "BEARISH REACTION"
            elif reaction > 0:
                label = "POSITIVE DRIFT"
            elif reaction < 0:
                label = "NEGATIVE DRIFT"
            else:
                label = "FLAT"
            return {
                "status": "complete",
                "reaction_pct": reaction,
                "reaction_label": label,
                "base_close": round(base_close, 2),
                "reaction_close": round(react_close, 2),
                "reaction_date": px.loc[react_idx, "date_only"].isoformat(),
                "source": "yfinance regular-session closes",
            }
        except Exception as e:
            logger.debug("yf post earnings reaction %s: %s", ticker, e)
            return {"status": "unavailable", "reaction_pct": None, "reaction_label": "NO PRICE DATA"}

    return await asyncio.get_event_loop().run_in_executor(None, _sync)


# ─────────────────────────── Beat Probability ───────────────────────────
def _compute_beat_probability(ticker_data: dict[str, Any]) -> dict[str, Any]:
    """Returns {prob_pct: float 5-95, components: {...}, strategy: str}."""
    score = 50.0
    comp = {}

    # 1) EPS surprise streak
    hist = ticker_data.get("eps_history") or []
    if hist:
        beats = sum(1 for h in hist if h["surprise_pct"] > 0)
        streak_pct = beats / len(hist)
        delta = (streak_pct - 0.5) * 30  # ±15
        score += delta
        comp["eps_streak"] = {"value": f"{beats}/{len(hist)}", "delta": round(delta, 1)}

    # 2) Momentum 20D
    mom = ticker_data.get("momentum_20d_pct")
    if mom is not None:
        if mom > 5:
            delta = 8
        elif mom > 0:
            delta = 3
        elif mom > -5:
            delta = -3
        else:
            delta = -8
        score += delta
        comp["momentum_20d"] = {"value": f"{mom:+.1f}%", "delta": delta}

    # 3) Revenue acceleration
    rev = ticker_data.get("revenue_accel")
    if rev is not None:
        if rev > 5:
            delta = 7
        elif rev > 0:
            delta = 3
        elif rev > -5:
            delta = -3
        else:
            delta = -7
        score += delta
        comp["revenue_accel"] = {"value": f"{rev:+.1f}%", "delta": delta}

    # 4) Short interest (low = institutions not betting on miss)
    short_pct = ticker_data.get("short_pct")
    if short_pct is not None:
        sp = float(short_pct) * 100  # yf returns 0.0-1.0
        if sp > 20:
            delta = -6
        elif sp > 10:
            delta = -2
        elif sp > 3:
            delta = 2
        else:
            delta = 4
        score += delta
        comp["short_pct"] = {"value": f"{sp:.1f}%", "delta": delta}

    # 5) Options flow direction (caller passes flow_score in [-1, 1])
    flow_score = ticker_data.get("flow_score")
    if flow_score is not None:
        flow_pct = 50 + flow_score * 50  # → 0-100 scale
        score = score * 0.6 + flow_pct * 0.4
        comp["flow_blend"] = {"value": f"{flow_score:+.2f}", "delta": "60/40 blend"}

    # Clamp 5-95 (per spec — never 0 or 100)
    score = max(5.0, min(95.0, score))

    # Strategy
    iv_rank = ticker_data.get("iv_rank")
    iv_cheap = iv_rank is None or iv_rank < 35
    if score >= 70 and iv_cheap:
        strat = "LONG CALL"
    elif score >= 70:
        strat = "CALL SPREAD (sell rich IV)"
    elif score >= 45:
        strat = "AVOID"
    else:
        strat = "BEAR PUT SPREAD"
    return {
        "prob_pct": round(score, 1),
        "components": comp,
        "strategy": strat,
        "iv_cheap": iv_cheap,
    }


def _num(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    return value


def _score_component(value: float | None, good: float, bad: float, *, reverse: bool = False) -> int:
    if value is None:
        return 50
    if reverse:
        if value <= good:
            return 85
        if value >= bad:
            return 20
    else:
        if value >= good:
            return 85
        if value <= bad:
            return 20
    return 55


def _earnings_setup(row: dict[str, Any], moves: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    beat = _num(row.get("beat_probability_pct"))
    mom = _num(row.get("momentum_20d_pct"))
    rev = _num(row.get("revenue_accel"))
    iv_rank = _num(options.get("iv_rank"))
    implied = _num(options.get("implied_move_pct"))
    hist_move = _num(moves.get("avg_abs_move_pct"))
    short_pct = _num(row.get("short_pct"))
    short_display = short_pct * 100 if short_pct is not None and short_pct <= 1 else short_pct

    components = {
        "expectations_risk": _score_component(beat, 65, 42),
        "options_pricing": _score_component(iv_rank, 35, 75, reverse=True),
        "momentum_into_print": _score_component(mom, 4, -5),
        "revenue_acceleration": _score_component(rev, 5, -5),
        "historical_move_gap": 50,
        "short_interest": _score_component(short_display, 5, 20, reverse=True),
        "liquidity": _score_component(_num(row.get("average_volume")), 1_000_000, 150_000),
    }
    if implied is not None and hist_move is not None:
        spread = implied - hist_move
        if spread <= -1.0:
            components["historical_move_gap"] = 85
        elif spread >= 2.0:
            components["historical_move_gap"] = 25
        else:
            components["historical_move_gap"] = 55
    score = round(sum(components.values()) / len(components), 1)
    if score >= 70:
        rating = "TRADEABLE"
    elif score >= 55:
        rating = "WATCH"
    else:
        rating = "AVOID"

    pricing_signal = "UNKNOWN"
    if implied is not None and hist_move is not None:
        if implied >= hist_move + 2.0:
            pricing_signal = "OPTIONS OVERPRICED"
        elif implied <= hist_move - 1.0:
            pricing_signal = "OPTIONS UNDERPRICED"
        else:
            pricing_signal = "FAIRLY PRICED"

    avoid_flags = []
    if iv_rank is not None and iv_rank >= 80:
        avoid_flags.append("IV extremely expensive")
    if implied is not None and hist_move is not None and implied > hist_move + 3:
        avoid_flags.append("Expected move rich versus history")
    if row.get("average_volume") and row["average_volume"] < 150_000:
        avoid_flags.append("Thin common-stock liquidity")
    if short_display is not None and short_display > 20:
        avoid_flags.append("High short interest into binary event")
    if beat is not None and beat < 45:
        avoid_flags.append("Beat probability below trade threshold")

    return {
        "score": score,
        "rating": rating,
        "components": components,
        "pricing_signal": pricing_signal,
        "avoid_flags": avoid_flags,
    }


def _beat_miss_stats(hist: list[dict[str, Any]], moves: dict[str, Any]) -> dict[str, Any]:
    if not hist:
        return {
            "eps_beats": 0,
            "eps_misses": 0,
            "beat_streak": 0,
            "miss_streak": 0,
            "avg_move_after_beat_pct": None,
            "avg_move_after_miss_pct": None,
        }
    beats = [h for h in hist if _num(h.get("surprise_pct")) is not None and h["surprise_pct"] > 0]
    misses = [h for h in hist if _num(h.get("surprise_pct")) is not None and h["surprise_pct"] <= 0]
    streak_type = None
    streak = 0
    for h in reversed(hist):
        typ = "beat" if _num(h.get("surprise_pct")) and h["surprise_pct"] > 0 else "miss"
        if streak_type is None:
            streak_type = typ
        if typ != streak_type:
            break
        streak += 1
    move_by_date = {m.get("date"): m for m in (moves.get("moves") or [])}
    beat_moves, miss_moves = [], []
    for h in hist:
        m = move_by_date.get(str(h.get("quarter")))
        if not m:
            continue
        if _num(h.get("surprise_pct")) and h["surprise_pct"] > 0:
            beat_moves.append(m["move_pct"])
        else:
            miss_moves.append(m["move_pct"])
    return {
        "eps_beats": len(beats),
        "eps_misses": len(misses),
        "beat_streak": streak if streak_type == "beat" else 0,
        "miss_streak": streak if streak_type == "miss" else 0,
        "avg_move_after_beat_pct": round(sum(beat_moves) / len(beat_moves), 2) if beat_moves else None,
        "avg_move_after_miss_pct": round(sum(miss_moves) / len(miss_moves), 2) if miss_moves else None,
    }


def _option_strategy(setup: dict[str, Any], beat: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    iv_rank = _num(options.get("iv_rank"))
    rating = setup.get("rating")
    prob = _num(beat.get("prob_pct")) or 50
    if rating == "AVOID":
        return {"name": "NO TRADE", "reason": "Setup score or avoid flags fail the earnings gate."}
    if iv_rank is not None and iv_rank >= 70:
        if prob >= 62:
            return {"name": "CALL DEBIT SPREAD", "reason": "Bullish setup, but IV is rich; cap premium risk."}
        if prob <= 42:
            return {"name": "PUT DEBIT SPREAD", "reason": "Bearish setup, but IV is rich; cap premium risk."}
        return {"name": "WAIT UNTIL AFTER PRINT", "reason": "IV is high and direction is not decisive."}
    if prob >= 65:
        return {"name": "LONG CALL / CALL SPREAD", "reason": "Beat model is favorable and IV is not prohibitive."}
    if prob <= 40:
        return {"name": "PUT DEBIT SPREAD", "reason": "Beat model is weak; define risk into the event."}
    return {"name": "WATCHLIST ONLY", "reason": "Mixed setup; wait for report or price dislocation."}


def _earnings_call_tone(row: dict[str, Any], synopsis: dict[str, Any]) -> dict[str, Any]:
    text = " ".join([
        str((synopsis or {}).get("text") or ""),
        " ".join(str(h.get("title") or "") for h in ((synopsis or {}).get("headlines") or []) if isinstance(h, dict)),
    ]).lower()
    bullish_words = [
        "beat", "beats", "raise", "raises", "raised", "strong", "growth", "record",
        "upbeat", "profit", "tops", "surge", "accelerat", "better-than-expected",
    ]
    bearish_words = [
        "miss", "misses", "lower", "lowers", "cut", "cuts", "weak", "decline",
        "warn", "pressure", "slump", "loss", "disappoint", "below", "downgrade",
    ]
    bull = sum(1 for word in bullish_words if word in text)
    bear = sum(1 for word in bearish_words if word in text)
    if _num(row.get("revenue_growth")) is not None:
        bull += 1 if row["revenue_growth"] > 0.08 else 0
        bear += 1 if row["revenue_growth"] < 0 else 0
    if _num(row.get("earnings_growth")) is not None:
        bull += 1 if row["earnings_growth"] > 0.08 else 0
        bear += 1 if row["earnings_growth"] < 0 else 0
    if _num(row.get("beat_probability_pct")) is not None:
        bull += 1 if row["beat_probability_pct"] >= 65 else 0
        bear += 1 if row["beat_probability_pct"] < 45 else 0

    if bull >= bear + 2:
        tone = "BULLISH"
    elif bear >= bull + 2:
        tone = "BEARISH"
    else:
        tone = "MIXED"
    return {
        "tone": tone,
        "bullish_score": bull,
        "bearish_score": bear,
        "source": (synopsis or {}).get("source") or "free-data inference",
        "note": "Tone inferred from free news/headline context and available growth fields; transcript provider not yet connected.",
    }


def _earnings_divergence(row: dict[str, Any]) -> dict[str, Any]:
    tone = ((row.get("earnings_call_tone") or {}).get("tone") or "UNKNOWN").upper()
    reaction = _num((row.get("post_earnings_reaction") or {}).get("reaction_pct"))
    reaction_status = (row.get("post_earnings_reaction") or {}).get("status")
    beat = _num(row.get("beat_probability_pct"))
    if reaction_status != "complete" or reaction is None:
        return {"active": False, "type": "PENDING", "label": "Pending post-print reaction", "severity": "LOW"}

    signals: list[dict[str, Any]] = []
    if tone == "BULLISH" and reaction <= -2:
        signals.append({
            "type": "BULLISH_CALL_SELLOFF",
            "label": "Bullish call, bearish stock reaction",
            "read": "Possible overreaction dip, guide skepticism, or institutional selling despite positive tone.",
            "action": "Watch for reclaim of post-print high before chasing.",
            "severity": "HIGH" if reaction <= -5 else "MEDIUM",
        })
    if tone == "BEARISH" and reaction >= 2:
        signals.append({
            "type": "BEARISH_CALL_RIP",
            "label": "Bearish call, bullish stock reaction",
            "read": "Possible short squeeze or bad news already priced in.",
            "action": "Do not fade until price loses the reaction-day low.",
            "severity": "HIGH" if reaction >= 5 else "MEDIUM",
        })
    if beat is not None and beat >= 65 and reaction <= -2:
        signals.append({
            "type": "STRONG_SETUP_WEAK_REACTION",
            "label": "Strong setup, weak reaction",
            "read": "Distribution warning: the bar may have been too high.",
            "action": "Require confirmation before PM approves a long.",
            "severity": "HIGH" if reaction <= -4 else "MEDIUM",
        })
    if beat is not None and beat < 45 and reaction >= 2:
        signals.append({
            "type": "WEAK_SETUP_GREEN_REACTION",
            "label": "Weak setup, green reaction",
            "read": "Market may have already priced in the miss risk.",
            "action": "Squeeze watch; avoid shorting strength without breakdown.",
            "severity": "MEDIUM",
        })
    if tone == "MIXED" and abs(reaction) >= 5:
        signals.append({
            "type": "PRICE_ACTION_LEADS",
            "label": "Mixed call, violent price reaction",
            "read": "Price action is carrying the information edge more than the call-tone model.",
            "action": "Let the reaction high/low define the next setup.",
            "severity": "MEDIUM",
        })

    if not signals:
        label = "Aligned bullish reaction" if reaction > 0 else "Aligned bearish reaction" if reaction < 0 else "Flat reaction"
        return {
            "active": False,
            "type": "ALIGNED",
            "label": label,
            "severity": "LOW",
            "read": "Call tone and price action do not show a material divergence.",
            "action": "Monitor normally.",
        }

    primary = sorted(signals, key=lambda s: 0 if s["severity"] == "HIGH" else 1)[0]
    return {
        "active": True,
        "type": primary["type"],
        "label": primary["label"],
        "severity": primary["severity"],
        "read": primary["read"],
        "action": primary["action"],
        "signals": signals,
    }


def _battle_card(row: dict[str, Any], hist: list[dict[str, Any]], moves: dict[str, Any],
                 options: dict[str, Any], setup: dict[str, Any], synopsis: dict[str, Any]) -> dict[str, Any]:
    latest = hist[-1] if hist else {}
    latest_surprise = _num(latest.get("surprise_pct"))
    bull = []
    bear = []
    if row.get("momentum_20d_pct") is not None and row["momentum_20d_pct"] > 0:
        bull.append(f"Positive 20D drift of {row['momentum_20d_pct']}% into print.")
    if row.get("revenue_accel") is not None and row["revenue_accel"] > 0:
        bull.append(f"Revenue acceleration improved by {row['revenue_accel']} pts.")
    if latest_surprise is not None and latest_surprise > 0:
        bull.append(f"Last EPS print beat by {latest_surprise:.1f}%.")
    if row.get("momentum_20d_pct") is not None and row["momentum_20d_pct"] < 0:
        bear.append(f"Negative 20D drift of {row['momentum_20d_pct']}% into print.")
    if row.get("revenue_accel") is not None and row["revenue_accel"] < 0:
        bear.append(f"Revenue acceleration weakened by {abs(row['revenue_accel'])} pts.")
    if setup.get("avoid_flags"):
        bear.extend(setup["avoid_flags"][:2])
    return {
        "bull_case": bull[:3] or ["No clear bullish edge from current free-data inputs."],
        "bear_case": bear[:3] or ["No major bearish red flag detected in the current setup model."],
        "key_number_to_watch": "Guidance and revenue growth versus expectations.",
        "guidance_risk": "HIGH" if setup.get("score", 0) < 55 else "MEDIUM" if setup.get("score", 0) < 70 else "LOW",
        "expected_move": options.get("implied_move_pct"),
        "historical_move": moves.get("avg_abs_move_pct"),
        "best_structure": _option_strategy(setup, {"prob_pct": row.get("beat_probability_pct")}, options),
        "rating": setup.get("rating"),
        "call_tone": row.get("earnings_call_tone"),
        "post_earnings_reaction": row.get("post_earnings_reaction"),
        "divergence": row.get("earnings_divergence"),
        "earnings_call_synopsis": synopsis,
    }


# ─────────────────────────── Week schedule ───────────────────────────
async def _nasdaq_earnings_for_week(start: date, end: date) -> list[dict[str, Any]]:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.nasdaq.com",
        "Referer": "https://www.nasdaq.com/",
    }
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    try:
        async with httpx.AsyncClient(headers=headers, timeout=15.0, follow_redirects=True) as client:
            d = start
            while d <= end:
                ds = d.isoformat()
                try:
                    r = await client.get("https://api.nasdaq.com/api/calendar/earnings", params={"date": ds})
                    if r.status_code != 200:
                        d += timedelta(days=1)
                        continue
                    data = r.json() or {}
                except Exception:
                    d += timedelta(days=1)
                    continue
                rows = ((data.get("data") or {}).get("rows")) or []
                for row in rows:
                    ticker = str(row.get("symbol") or "").upper().strip()
                    if not ticker or not ticker.replace(".", "").replace("-", "").isalnum():
                        continue
                    key = (ticker, ds)
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append({
                        "ticker": ticker,
                        "earnings_date": ds,
                        "source": "Nasdaq earnings calendar",
                        "company_name": row.get("name"),
                        "report_time": row.get("time"),
                        "eps_forecast": row.get("epsForecast"),
                        "estimates_count": row.get("noOfEsts"),
                        "last_year_eps": row.get("lastYearEPS"),
                        "market_cap_calendar": row.get("marketCap"),
                    })
                d += timedelta(days=1)
    except Exception as e:
        logger.warning("Nasdaq earnings calendar failed: %s", e)
    return out


async def _alpha_vantage_earnings_for_week(start: date, end: date) -> list[dict[str, Any]]:
    """Backup earnings calendar via Alpha Vantage's free CSV endpoint."""
    key = os.environ.get("ALPHA_VANTAGE_API_KEY", "").strip()
    if not key:
        return []

    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    try:
        async with httpx.AsyncClient(timeout=18.0, follow_redirects=True) as client:
            r = await client.get(
                "https://www.alphavantage.co/query",
                params={
                    "function": "EARNINGS_CALENDAR",
                    "horizon": "3month",
                    "apikey": key,
                },
            )
        if r.status_code != 200 or not r.text.strip():
            return []
        if "Thank you for using Alpha Vantage" in r.text or "Our standard API rate limit" in r.text:
            logger.warning("Alpha Vantage earnings calendar rate-limited")
            return []
        reader = csv.DictReader(io.StringIO(r.text))
        for row in reader:
            ticker = str(row.get("symbol") or "").upper().strip()
            ds = str(row.get("reportDate") or "").strip()
            if not ticker or not ds:
                continue
            if not ticker.replace(".", "").replace("-", "").isalnum():
                continue
            try:
                d = datetime.fromisoformat(ds).date()
            except Exception:
                continue
            if d < start or d > end:
                continue
            key_tuple = (ticker, ds)
            if key_tuple in seen:
                continue
            seen.add(key_tuple)
            out.append({
                "ticker": ticker,
                "earnings_date": ds,
                "source": "Alpha Vantage earnings calendar",
                "company_name": row.get("name"),
                "eps_forecast": row.get("estimate"),
                "currency": row.get("currency"),
                "fiscal_date_ending": row.get("fiscalDateEnding"),
            })
    except Exception as e:
        logger.warning("Alpha Vantage earnings calendar failed: %s", e)
    return out


async def _earnings_calendar_for_week(start: date, end: date) -> list[dict[str, Any]]:
    """Returns reporting tickers for a Monday-Friday window."""
    source_counts = {"Yahoo earnings calendar": 0, "Nasdaq earnings calendar": 0, "Alpha Vantage earnings calendar": 0}
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    try:
        yahoo_rows, nasdaq_rows, alpha_rows = await asyncio.gather(
            asyncio.wait_for(scrapers.fetch_yahoo_upcoming_earnings(days_ahead=14), timeout=float(os.getenv("EARNINGS_YAHOO_TIMEOUT_SEC", "4"))),
            asyncio.wait_for(_nasdaq_earnings_for_week(start, end), timeout=float(os.getenv("EARNINGS_NASDAQ_TIMEOUT_SEC", "18"))),
            asyncio.wait_for(_alpha_vantage_earnings_for_week(start, end), timeout=float(os.getenv("EARNINGS_ALPHA_TIMEOUT_SEC", "6"))),
            return_exceptions=True,
        )
    except Exception as e:
        logger.warning("earnings calendar source fanout failed: %s", e)
        yahoo_rows, nasdaq_rows, alpha_rows = [], [], []

    if isinstance(yahoo_rows, Exception):
        logger.warning("Yahoo earnings calendar failed: %s", yahoo_rows)
        yahoo_rows = []
    if isinstance(nasdaq_rows, Exception):
        logger.warning("Nasdaq earnings calendar timed out/failed: %s", nasdaq_rows)
        nasdaq_rows = []
    if isinstance(alpha_rows, Exception):
        logger.warning("Alpha Vantage earnings calendar timed out/failed: %s", alpha_rows)
        alpha_rows = []

    raw = yahoo_rows or []
    for r in raw or []:
        try:
            d = datetime.fromisoformat(r["earnings_date"]).date()
        except Exception:
            continue
        if d < start or d > end:
            continue
        key = (r["ticker"], r["earnings_date"])
        seen.add(key)
        source_counts["Yahoo earnings calendar"] += 1
        out.append({
            "ticker": r["ticker"],
            "earnings_date": r["earnings_date"],
            "source": "Yahoo earnings calendar",
            "sources": ["Yahoo earnings calendar"],
        })
    for source_rows in (nasdaq_rows or [], alpha_rows or []):
        for r in source_rows:
            source = r.get("source") or "Unknown earnings calendar"
            key = (r["ticker"], r["earnings_date"])
            source_counts[source] = source_counts.get(source, 0) + 1
            if key not in seen:
                r["sources"] = [source]
                out.append(r)
                seen.add(key)
                continue
            for existing in out:
                if (existing.get("ticker"), existing.get("earnings_date")) == key:
                    existing_sources = existing.setdefault("sources", [existing.get("source")])
                    if source not in existing_sources:
                        existing_sources.append(source)
                    for field in ("company_name", "eps_forecast", "currency", "fiscal_date_ending",
                                  "report_time", "estimates_count", "last_year_eps", "market_cap_calendar"):
                        if existing.get(field) in (None, "") and r.get(field) not in (None, ""):
                            existing[field] = r[field]
                    existing["source"] = " + ".join(existing_sources)
                    break
    setattr(_earnings_calendar_for_week, "last_source_counts", source_counts)
    return out


def _calendar_only_row(item: dict[str, Any], scan_tickers: set[str] | None = None) -> dict[str, Any]:
    ticker = str(item.get("ticker") or "").upper()
    return {
        "ticker": ticker,
        "earnings_date": item.get("earnings_date"),
        "am_pm": None,
        "calendar_source": item.get("source"),
        "calendar_sources": item.get("sources") or [item.get("source")],
        "company_name": item.get("company_name"),
        "report_time": item.get("report_time"),
        "eps_forecast": item.get("eps_forecast"),
        "estimates_count": item.get("estimates_count"),
        "last_year_eps": item.get("last_year_eps"),
        "market_cap_calendar": item.get("market_cap_calendar"),
        "beat_probability_pct": None,
        "beat_components": {},
        "strategy": "CALENDAR ONLY",
        "iv_cheap": False,
        "axiom_match": ticker in (scan_tickers or set()),
        "options": {},
        "historical_moves": {},
        "beat_miss_history": {},
        "eps_history": [],
        "post_earnings_reaction": {"status": "pending_enrichment"},
        "earnings_call_tone": {"tone": "PENDING", "bull": 0, "bear": 0, "source": "calendar_only"},
        "earnings_divergence": {"active": False, "label": "Pending enrichment", "severity": "LOW"},
        "setup": {"score": 0, "rating": "PENDING", "pricing_signal": "PENDING", "avoid_flags": []},
        "earnings_setup_score": 0,
        "earnings_setup_rating": "PENDING",
        "options_pricing_signal": "PENDING",
        "avoid_flags": [],
        "option_strategy": {"name": "PENDING ENRICHMENT", "reason": "Calendar row loaded; fundamentals/options enrichment is still refreshing."},
        "battle_card": {
            "rating": "PENDING",
            "bull_case": [],
            "bear_case": [],
            "earnings_call_synopsis": {
                "source": "calendar_only",
                "text": "Earnings date is confirmed from the calendar feed. Full battle card enrichment is still refreshing.",
            },
        },
        "data_quality": "calendar_only",
    }


async def calendar_only_week(scan_tickers: set[str] | None = None,
                             week_offset: int = 0,
                             status: str = "CALENDAR_ONLY") -> dict[str, Any]:
    base_monday, _ = _target_week_window()
    monday = base_monday + timedelta(days=7 * int(week_offset or 0))
    friday = monday + timedelta(days=4)
    calendar = await _earnings_calendar_for_week(monday, friday)
    if not calendar and int(week_offset or 0) == 0:
        monday = monday + timedelta(days=7)
        friday = monday + timedelta(days=4)
        calendar = await _earnings_calendar_for_week(monday, friday)

    raw_calendar_total = len(calendar)
    source_counts = getattr(_earnings_calendar_for_week, "last_source_counts", {})
    balanced_calendar: list[dict[str, Any]] = []
    per_date_items: dict[str, list[dict[str, Any]]] = {}
    for item in calendar:
        per_date_items.setdefault(item.get("earnings_date") or "", []).append(item)
    for ds in sorted(per_date_items):
        balanced_calendar.extend(per_date_items[ds][:7])
    rows = [_json_safe(_calendar_only_row(item, scan_tickers)) for item in balanced_calendar[:35]]
    by_day: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        try:
            d = datetime.fromisoformat(str(r["earnings_date"])).date()
        except Exception:
            continue
        by_day.setdefault(d.strftime("%A").upper(), []).append(r)

    return _json_safe({
        "week_of": monday.isoformat(),
        "week_end": friday.isoformat(),
        "week_offset": int(week_offset or 0),
        "by_day": by_day,
        "total": len(rows),
        "raw_calendar_total": raw_calendar_total,
        "enriched_total": 0,
        "calendar_limited": raw_calendar_total > len(rows),
        "calendar_source_counts": source_counts,
        "calendar_sources": [k for k, v in source_counts.items() if v],
        "earnings_divergences": [],
        "earnings_divergence_count": 0,
        "feature_version": EARNINGS_FEATURE_VERSION,
        "cache_status": status,
        "cache_age_minutes": None,
    })


async def current_week_with_probability(scan_tickers: set[str] | None = None,
                                          flow_scores: dict[str, float] | None = None,
                                          week_offset: int = 0,
                                          ) -> dict[str, Any]:
    """Builds the full current week (Mon→Fri) calendar grouped by day, with
    Beat Probability + strategy + AXIOM_MATCH flag for each ticker.
    `scan_tickers` is the set of tickers that appeared in this week's scans
    (used to flag AXIOM_MATCH). `flow_scores[ticker]` (optional) is a -1..+1
    options-flow direction score."""
    base_monday, _ = _target_week_window()
    monday = base_monday + timedelta(days=7 * int(week_offset or 0))
    friday = monday + timedelta(days=4)

    calendar = await _earnings_calendar_for_week(monday, friday)
    if not calendar:
        monday = monday + timedelta(days=7)
        friday = monday + timedelta(days=4)
        calendar = await _earnings_calendar_for_week(monday, friday)
    if not calendar:
        source_counts = getattr(_earnings_calendar_for_week, "last_source_counts", {})
        return {
            "week_of": monday.isoformat(),
            "week_end": friday.isoformat(),
            "week_offset": int(week_offset or 0),
            "by_day": {},
            "total": 0,
            "raw_calendar_total": 0,
            "enriched_total": 0,
            "calendar_limited": False,
            "calendar_source_counts": source_counts,
            "calendar_sources": [k for k, v in source_counts.items() if v],
            "feature_version": EARNINGS_FEATURE_VERSION,
        }
    raw_calendar_total = len(calendar)
    source_counts = getattr(_earnings_calendar_for_week, "last_source_counts", {})
    balanced: list[dict[str, Any]] = []
    per_date: dict[str, list[dict[str, Any]]] = {}
    for item in calendar:
        per_date.setdefault(item.get("earnings_date") or "", []).append(item)
    for ds in sorted(per_date):
        balanced.extend(per_date[ds][:5])
    calendar = balanced

    # Concurrent fundamentals + history
    sem = asyncio.Semaphore(3)

    async def _one(item):
        async with sem:
            t = item["ticker"]
            fund, hist = await asyncio.gather(
                _yf_fundamentals(t), _yf_earnings_history(t),
            )
            data = {**fund, "eps_history": hist,
                     "flow_score": (flow_scores or {}).get(t)}
            beat = _compute_beat_probability(data)
            moves, option_snap, synopsis = await asyncio.gather(
                _yf_earnings_moves(t, hist),
                _options_snapshot(t, item.get("earnings_date")),
                _yf_news_synopsis(t),
            )
            # AM/PM flag
            ts = fund.get("earnings_time")
            am_pm = None
            if ts:
                try:
                    h = datetime.fromtimestamp(ts, tz=timezone.utc).hour
                    am_pm = "AM" if h < 14 else "PM"
                except Exception:
                    pass
            if not am_pm and item.get("report_time"):
                report_time = str(item.get("report_time") or "").lower()
                if "before" in report_time or "pre" in report_time or "morning" in report_time:
                    am_pm = "AM"
                elif "after" in report_time or "post" in report_time or "time not supplied" not in report_time:
                    am_pm = "PM" if "after" in report_time or "post" in report_time else None
            row = {
                "ticker": t,
                "earnings_date": item["earnings_date"],
                "am_pm": am_pm,
                "calendar_source": item.get("source"),
                "calendar_sources": item.get("sources") or [item.get("source")],
                "company_name": item.get("company_name"),
                "report_time": item.get("report_time"),
                "eps_forecast": item.get("eps_forecast"),
                "estimates_count": item.get("estimates_count"),
                "last_year_eps": item.get("last_year_eps"),
                "market_cap_calendar": item.get("market_cap_calendar"),
                "industry": fund.get("industry"),
                "sector": fund.get("sector"),
                "market_cap": fund.get("market_cap"),
                "current_price": fund.get("current_price"),
                "average_volume": fund.get("average_volume"),
                "trailing_pe": fund.get("trailing_pe"),
                "forward_pe": fund.get("forward_pe"),
                "profit_margin": fund.get("profit_margin"),
                "revenue_growth": fund.get("revenue_growth"),
                "earnings_growth": fund.get("earnings_growth"),
                "target_mean_price": fund.get("target_mean_price"),
                "momentum_20d_pct": fund.get("momentum_20d_pct"),
                "short_pct": fund.get("short_pct"),
                "beat_probability_pct": beat["prob_pct"],
                "beat_components": beat["components"],
                "strategy": beat["strategy"],
                "iv_cheap": beat["iv_cheap"],
                "axiom_match": (t in (scan_tickers or set())),
                "options": option_snap,
                "historical_moves": moves,
                "beat_miss_history": _beat_miss_stats(hist, moves),
                "eps_history": hist,
            }
            reaction = await _yf_post_earnings_reaction(t, item.get("earnings_date"), am_pm)
            row["post_earnings_reaction"] = reaction
            row["earnings_call_tone"] = _earnings_call_tone(row, synopsis)
            row["earnings_divergence"] = _earnings_divergence(row)
            setup = _earnings_setup(row, moves, option_snap)
            row["setup"] = setup
            row["earnings_setup_score"] = setup["score"]
            row["earnings_setup_rating"] = setup["rating"]
            row["options_pricing_signal"] = setup["pricing_signal"]
            row["avoid_flags"] = setup["avoid_flags"]
            row["option_strategy"] = _option_strategy(setup, beat, option_snap)
            row["battle_card"] = _battle_card(row, hist, moves, option_snap, setup, synopsis)
            return _json_safe(row)

    rows = await asyncio.gather(*[_one(i) for i in calendar])

    by_day: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        try:
            d = datetime.fromisoformat(r["earnings_date"]).date()
        except Exception:
            continue
        day_name = d.strftime("%A").upper()
        by_day.setdefault(day_name, []).append(r)
    for day in by_day.values():
        day.sort(key=lambda x: (x.get("am_pm") or "ZZ", -x["beat_probability_pct"]))
    divergences = sorted(
        [r for r in rows if (r.get("earnings_divergence") or {}).get("active")],
        key=lambda x: (
            0 if (x.get("earnings_divergence") or {}).get("severity") == "HIGH" else 1,
            -abs(_num((x.get("post_earnings_reaction") or {}).get("reaction_pct")) or 0),
        ),
    )

    return _json_safe({
        "week_of": monday.isoformat(),
        "week_end": friday.isoformat(),
        "week_offset": int(week_offset or 0),
        "by_day": by_day,
        "total": len(rows),
        "raw_calendar_total": raw_calendar_total,
        "enriched_total": len(rows),
        "calendar_limited": raw_calendar_total > len(rows),
        "calendar_source_counts": source_counts,
        "calendar_sources": [k for k, v in source_counts.items() if v],
        "earnings_divergences": divergences[:12],
        "earnings_divergence_count": len(divergences),
        "feature_version": EARNINGS_FEATURE_VERSION,
    })


# ─────────────────────────── Persistence ───────────────────────────
async def store_week_snapshot(snapshot: dict[str, Any]) -> None:
    db = get_db()
    doc = stamped(snapshot)
    doc["feature_version"] = EARNINGS_FEATURE_VERSION
    await db.earnings_snapshots.update_one(
        {"week_of": snapshot["week_of"]},
        {"$set": doc},
        upsert=True,
    )


async def _refresh_week_snapshot_background(scan_tickers: set[str] | None,
                                            week_offset: int,
                                            refresh_key: str) -> None:
    try:
        snapshot = await current_week_with_probability(scan_tickers=scan_tickers, week_offset=week_offset)
        await store_week_snapshot(snapshot)
    except Exception as e:
        logger.warning("earnings background refresh failed: %s", e)
    finally:
        _BACKGROUND_REFRESH_KEYS.discard(refresh_key)


async def current_week_cached(scan_tickers: set[str] | None = None,
                              max_age_minutes: int = 30,
                              week_offset: int = 0) -> dict[str, Any]:
    """Fast path for the UI: use a fresh stored War Room snapshot when present."""
    db = get_db()
    target_monday, _target_friday = _target_week_window()
    target_week = (target_monday + timedelta(days=7 * int(week_offset or 0))).isoformat()
    latest = None
    try:
        latest = await db.earnings_snapshots.find_one(
            {"week_of": target_week},
            {"_id": 0},
            sort=[("created_at", -1)],
        )
        if latest and latest.get("created_at") and latest.get("week_of") == target_week:
            created = datetime.fromisoformat(str(latest["created_at"]).replace("Z", "+00:00"))
            age = (_now() - created).total_seconds() / 60.0
            if age <= max_age_minutes:
                latest["cache_status"] = "HIT"
                latest["cache_age_minutes"] = round(age, 1)
                return _json_safe(latest)
            latest["cache_status"] = "STALE_REFRESHING"
            latest["cache_age_minutes"] = round(age, 1)
            refresh_key = f"{target_week}:{int(week_offset or 0)}"
            if refresh_key not in _BACKGROUND_REFRESH_KEYS:
                _BACKGROUND_REFRESH_KEYS.add(refresh_key)
                asyncio.create_task(_refresh_week_snapshot_background(scan_tickers, week_offset, refresh_key))
            return _json_safe(latest)
    except Exception:
        pass

    try:
        snapshot = await asyncio.wait_for(
            current_week_with_probability(scan_tickers=scan_tickers, week_offset=week_offset),
            timeout=float(os.getenv("EARNINGS_UI_REFRESH_TIMEOUT_SEC", "8")),
        )
    except Exception as e:
        if latest:
            try:
                created = datetime.fromisoformat(str(latest.get("created_at", "")).replace("Z", "+00:00"))
                latest["cache_age_minutes"] = round((_now() - created).total_seconds() / 60.0, 1)
            except Exception:
                latest["cache_age_minutes"] = None
            latest["cache_status"] = "STALE"
            latest["refresh_error"] = f"Live earnings refresh timed out or failed: {type(e).__name__}"
            return _json_safe(latest)

        refresh_key = f"{target_week}:{int(week_offset or 0)}"
        if refresh_key not in _BACKGROUND_REFRESH_KEYS:
            _BACKGROUND_REFRESH_KEYS.add(refresh_key)
            asyncio.create_task(_refresh_week_snapshot_background(scan_tickers, week_offset, refresh_key))
        try:
            fallback = await asyncio.wait_for(
                calendar_only_week(
                    scan_tickers=scan_tickers,
                    week_offset=week_offset,
                    status="CALENDAR_ONLY_REFRESHING",
                ),
                timeout=float(os.getenv("EARNINGS_CALENDAR_ONLY_TIMEOUT_SEC", "24")),
            )
            fallback["refresh_error"] = f"Full earnings enrichment timed out or failed: {type(e).__name__}"
            try:
                await store_week_snapshot(fallback)
            except Exception as store_exc:
                logger.warning("earnings calendar-only snapshot store failed: %s", store_exc)
            return _json_safe(fallback)
        except Exception as fallback_exc:
            logger.warning("earnings calendar-only fallback failed: %s", fallback_exc)

        monday = target_monday + timedelta(days=7 * int(week_offset or 0))
        empty = {
            "week_of": monday.isoformat(),
            "week_end": (monday + timedelta(days=4)).isoformat(),
            "week_offset": int(week_offset or 0),
            "by_day": {},
            "total": 0,
            "raw_calendar_total": 0,
            "enriched_total": 0,
            "calendar_limited": False,
            "calendar_source_counts": {},
            "calendar_sources": [],
            "earnings_divergences": [],
            "earnings_divergence_count": 0,
            "feature_version": EARNINGS_FEATURE_VERSION,
            "cache_status": "EMPTY_TIMEOUT",
            "cache_age_minutes": None,
            "refresh_error": f"Live earnings refresh timed out or failed: {type(e).__name__}",
        }
        return _json_safe(empty)

    try:
        await store_week_snapshot(snapshot)
    except Exception as e:
        logger.warning("earnings snapshot store failed: %s", e)
    snapshot["cache_status"] = "MISS"
    snapshot["cache_age_minutes"] = 0
    return _json_safe(snapshot)
