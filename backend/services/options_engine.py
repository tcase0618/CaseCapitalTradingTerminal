"""Options Intelligence — pure-Python pipeline using yfinance.

All computation happens here BEFORE Claude is called. Claude only adds two
small fields per stock (strategy_name + one_liner) — see claude_service.

Public API:
- get_options_data(ticker, catalyst_date)        — fetch chain + IV
- find_best_contract(chain, direction, budget)   — pick a strike
- select_strategy(stock, chain)                  — decision tree → label
- calculate_iv_rank(ticker)                      — HV-based proxy
- build_spread(chain, direction, width)          — bull-call / bear-put math
- detect_unusual_flow(ticker)                    — call/put volume vs OI
- assess_iv_crush_risk(stock, chain)             — pre-catalyst safety
- analyze_ticker(stock)                          — runs the full pipeline

Hard rule: NO function in this module raises. Every function returns a clean
dict or None. The scanner catches None and continues — one bad ticker never
kills the run.
"""
from __future__ import annotations
import asyncio
import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _is_nan(v) -> bool:
    """True if value is NaN/inf/None/non-numeric."""
    if v is None:
        return True
    try:
        f = float(v)
        return math.isnan(f) or math.isinf(f)
    except (TypeError, ValueError):
        return True


def _safe_int(v, default: int = 0) -> int:
    if _is_nan(v):
        return default
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _safe_float(v, default: float = 0.0) -> float:
    if _is_nan(v):
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# All yfinance access is sync → wrap each call in run_in_executor.
async def _to_thread(fn, *a, **kw):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: fn(*a, **kw))


# ---------------- chain fetcher (Part 2) ----------------
async def get_options_data(ticker: str, catalyst_date: str | None = None) -> dict[str, Any] | None:
    """Returns chain dataframes + ATM IV + iv_rank, or None if fetch fails."""
    ticker = ticker.upper()
    try:
        import yfinance as yf

        def _sync():
            t = yf.Ticker(ticker)
            expirations = list(t.options or [])
            if not expirations:
                return None

            # Pick expiry: 5–14d after catalyst, else 21d from today
            target_min, target_max = 5, 14
            if catalyst_date:
                try:
                    base = datetime.fromisoformat(catalyst_date).date()
                except Exception:
                    base = date.today()
            else:
                base = date.today()
                target_min = 18
                target_max = 24
            ideal_lo = base + timedelta(days=target_min)
            ideal_hi = base + timedelta(days=target_max)

            best = None
            best_score = float("inf")
            for e in expirations:
                try:
                    ed = datetime.fromisoformat(e).date()
                except Exception:
                    continue
                if ideal_lo <= ed <= ideal_hi:
                    score = 0  # in window
                else:
                    score = min(abs((ed - ideal_lo).days), abs((ed - ideal_hi).days))
                if score < best_score:
                    best_score = score
                    best = e
            if best is None:
                best = expirations[0]

            chain = t.option_chain(best)
            calls = chain.calls
            puts = chain.puts

            # current price
            price = None
            try:
                fast = getattr(t, "fast_info", None)
                if fast:
                    price = fast.get("last_price") if hasattr(fast, "get") else getattr(fast, "last_price", None)
            except Exception:
                pass
            if price is None:
                try:
                    info = t.info
                    price = info.get("currentPrice") or info.get("regularMarketPrice")
                except Exception:
                    pass
            if price is None:
                try:
                    h = t.history(period="5d")
                    if len(h):
                        price = float(h["Close"].iloc[-1])
                except Exception:
                    pass
            if price is None:
                return None
            price = float(price)

            # ATM IV — pick call closest to spot
            atm_iv = None
            try:
                if "impliedVolatility" in calls.columns and len(calls):
                    # Filter out NaN/inf rows first
                    valid = calls[calls["impliedVolatility"].notna() &
                                  (calls["impliedVolatility"] != math.inf) &
                                  (calls["impliedVolatility"] != -math.inf)]
                    if len(valid):
                        idx = (valid["strike"] - price).abs().idxmin()
                        iv = valid.loc[idx, "impliedVolatility"]
                        if not _is_nan(iv):
                            atm_iv = _safe_float(iv)
            except Exception:
                pass

            # historical vol → IV rank proxy (Part 5)
            iv_rank = None
            iv_label = "FAIR"
            try:
                hist = t.history(period="1y")
                if len(hist) >= 30 and atm_iv is not None and atm_iv > 0:
                    rets = hist["Close"].pct_change().dropna()
                    hv = _safe_float(rets.std() * (252 ** 0.5))
                    if hv > 0:
                        rank = (atm_iv - hv) / hv * 100.0
                        if not _is_nan(rank):
                            iv_rank = max(0, min(100, _safe_int(rank, 50)))
            except Exception:
                pass
            if iv_rank is None:
                iv_rank = 50  # neutral fallback when no IV data available
            if iv_rank < 30:
                iv_label = "CHEAP"
            elif iv_rank < 60:
                iv_label = "FAIR"
            elif iv_rank < 80:
                iv_label = "ELEVATED"
            else:
                iv_label = "EXPENSIVE"

            return {
                "ticker": ticker,
                "calls": calls,
                "puts": puts,
                "price": price,
                "expirations": expirations,
                "expiration": best,
                "atm_iv": atm_iv,
                "iv_rank": iv_rank,
                "iv_label": iv_label,
            }

        return await _to_thread(_sync)
    except Exception as e:
        logger.warning("get_options_data failed for %s: %s", ticker, e)
        return None


# ---------------- contract finder (Part 3) ----------------
def _approx_delta(strike: float, spot: float, is_call: bool) -> float:
    """Crude moneyness-based delta proxy when chain doesn't provide greeks."""
    moneyness = (spot - strike) / spot if is_call else (strike - spot) / spot
    # ATM ≈ 0.5; +5% ITM ≈ 0.7; -5% OTM ≈ 0.3
    delta = 0.5 + moneyness * 4.0
    delta = max(0.02, min(0.98, delta))
    return float(delta) if is_call else float(-delta)


def _liquidity_flag(oi: int, spread: float) -> str:
    bad_oi = oi < 100
    bad_spread = spread > 0.50
    if bad_oi and bad_spread:
        return "POOR"
    if bad_oi or bad_spread:
        return "WARN"
    return "GOOD"


def find_best_contract(chain_data: dict, direction: str, budget: float = 100.0) -> dict | None:
    """Returns the recommended single-leg contract dict for BULL or BEAR.
    Never raises — returns None on any data issue."""
    try:
        if not chain_data:
            return None
        spot = _safe_float(chain_data.get("price"))
        if spot <= 0:
            return None
        if direction == "BULL":
            df = chain_data.get("calls")
            target_strike = spot * 1.03
            is_call = True
        else:
            df = chain_data.get("puts")
            target_strike = spot * 0.97
            is_call = False
        if df is None or len(df) == 0 or "strike" not in df.columns:
            return None

        df = df.copy()
        df["dist"] = (df["strike"] - target_strike).abs()
        df = df.sort_values("dist").head(1)
        if not len(df):
            return None
        row = df.iloc[0]

        strike = _safe_float(row.get("strike"))
        if strike <= 0:
            return None
        last = _safe_float(row.get("lastPrice"))
        bid = _safe_float(row.get("bid"))
        ask = _safe_float(row.get("ask"))
        iv = _safe_float(row.get("impliedVolatility")) or _safe_float(chain_data.get("atm_iv"))
        oi = _safe_int(row.get("openInterest"))
        volume = _safe_int(row.get("volume"))
        spread = max(0.0, ask - bid)
        # Mid-price fallback if lastPrice missing
        premium = last or ((bid + ask) / 2 if bid > 0 and ask > 0 else 0.0)
        if premium <= 0:
            premium = max(0.05, abs(spot - strike) * 0.05)
        delta = _approx_delta(strike, spot, is_call)
        affordable = max(0, int(budget // (premium * 100))) if premium > 0 else 0
        return {
            "strike": round(strike, 2),
            "expiration": chain_data.get("expiration"),
            "premium": round(premium, 2),
            "bid": round(bid, 2),
            "ask": round(ask, 2),
            "iv": round(iv, 4),
            "delta": round(delta, 3),
            "open_interest": oi,
            "volume": volume,
            "spread": round(spread, 2),
            "contracts_at_budget": affordable,
            "max_loss": round(premium * 100, 2),
            "liquidity": _liquidity_flag(oi, spread),
            "type": "C" if is_call else "P",
        }
    except Exception as e:
        logger.warning("find_best_contract failed: %s", e)
        return None


# ---------------- strategy selector (Part 4) ----------------
def select_strategy(stock: dict, chain: dict | None) -> dict:
    """Pure-Python decision tree. Returns {strategy, reason, direction}."""
    signals = stock.get("signals") or []
    risk_level = (stock.get("risk") or {}).get("level", "MEDIUM")
    sq_score = (stock.get("squeeze") or {}).get("score") or 0
    days = (stock.get("time_target") or {}).get("days_remaining") or 30
    iv_rank = (chain or {}).get("iv_rank", 50)

    is_insider = "insider_cluster_buy" in signals
    is_earnings = "upcoming_earnings" in signals
    is_contract = "CONTRACT_SURGE" in signals
    is_congress = "CONGRESSIONAL_BUY" in signals

    # Rule order matters
    if is_earnings and days < 7:
        return {"strategy": "AVOID_OPTIONS", "direction": "NONE",
                "reason": "IV crush imminent — buy stock directly or wait until after print"}
    if risk_level == "EXTREME" or iv_rank > 80:
        return {"strategy": "LOTTERY_CALL", "direction": "BULL",
                "reason": "High risk or expensive IV — small far-OTM lottery only"}
    if sq_score > 75 and days < 14 and iv_rank < 60:
        return {"strategy": "LONG_CALL", "direction": "BULL",
                "reason": "High squeeze prob with near catalyst — directional bet, no spread"}
    if (risk_level == "LOW" and is_contract and 21 <= days <= 60 and iv_rank > 50):
        return {"strategy": "BULL_CALL_SPREAD", "direction": "BULL",
                "reason": "IV elevated on gov contract — spread caps cost vs crush risk"}
    if is_insider and sq_score > 50 and days > 30 and iv_rank < 40:
        return {"strategy": "LONG_CALL", "direction": "BULL",
                "reason": "Cheap IV entry on insider accumulation — buy calls while vol is low"}
    if is_congress and risk_level == "LOW":
        return {"strategy": "BULL_CALL_SPREAD", "direction": "BULL",
                "reason": "High-conviction low-risk congressional setup — spread max R/R"}
    return {"strategy": "LONG_CALL", "direction": "BULL",
            "reason": "Default directional play — pre-catalyst long call"}


# ---------------- IV rank (Part 5) — also exposed standalone ----------------
async def calculate_iv_rank(ticker: str) -> dict[str, Any]:
    chain = await get_options_data(ticker)
    if not chain:
        return {"iv_rank": None, "iv_label": "UNKNOWN", "atm_iv": None, "hv_30": None}
    try:
        import yfinance as yf

        def _hv():
            t = yf.Ticker(ticker)
            h = t.history(period="1y")
            if len(h) < 30:
                return None
            rets = h["Close"].pct_change().dropna()
            return float(rets.std() * (252 ** 0.5))
        hv = await _to_thread(_hv)
    except Exception:
        hv = None
    return {
        "iv_rank": chain.get("iv_rank"),
        "iv_label": chain.get("iv_label"),
        "atm_iv": chain.get("atm_iv"),
        "hv_30": round(hv, 4) if hv else None,
    }


# ---------------- spread builder (Part 6) ----------------
def build_spread(chain_data: dict, direction: str, width: float = 5.0) -> dict | None:
    if not chain_data:
        return None
    primary = find_best_contract(chain_data, direction)
    if not primary:
        return None
    df = chain_data["calls"] if direction == "BULL" else chain_data["puts"]
    if df is None or len(df) == 0:
        return None

    if direction == "BULL":
        sell_target = primary["strike"] + width  # higher strike call
    else:
        sell_target = primary["strike"] - width  # lower strike put

    df = df.copy()
    df["dist"] = (df["strike"] - sell_target).abs()
    sell_row = df.sort_values("dist").iloc[0]
    sell_strike = float(sell_row["strike"])
    sell_premium = float(sell_row.get("lastPrice") or 0)
    if sell_premium <= 0:
        bid = float(sell_row.get("bid") or 0)
        ask = float(sell_row.get("ask") or 0)
        sell_premium = (bid + ask) / 2 if bid and ask else 0.05

    buy_premium = primary["premium"]
    net_debit = round(buy_premium - sell_premium, 2)
    if net_debit <= 0:
        net_debit = 0.05
    spread_width = abs(sell_strike - primary["strike"])
    max_profit = round(spread_width * 100 - net_debit * 100, 2)
    max_loss = round(net_debit * 100, 2)
    rr = round(max_profit / max_loss, 1) if max_loss > 0 else 0.0
    if direction == "BULL":
        breakeven = round(primary["strike"] + net_debit, 2)
    else:
        breakeven = round(primary["strike"] - net_debit, 2)

    return {
        "direction": direction,
        "buy_strike": primary["strike"],
        "sell_strike": sell_strike,
        "buy_premium": round(buy_premium, 2),
        "sell_premium": round(sell_premium, 2),
        "net_debit": net_debit,
        "max_profit": max_profit,
        "max_loss": max_loss,
        "risk_reward": rr,
        "break_even": breakeven,
        "expiration": primary["expiration"],
        "width": spread_width,
    }


# ---------------- unusual flow (Part 7) ----------------
async def detect_unusual_flow(ticker: str) -> dict[str, Any]:
    """Detect unusual options activity vs open interest baseline."""
    chain = await get_options_data(ticker)
    if not chain:
        return {"unusual_calls": False, "unusual_puts": False, "call_sweep": False,
                "total_call_volume": 0, "total_put_volume": 0,
                "call_put_ratio": 0.0, "flow_bias": "NEUTRAL"}
    try:
        calls = chain["calls"]; puts = chain["puts"]
        cv = _safe_int(calls["volume"].fillna(0).sum()) if "volume" in calls.columns else 0
        pv = _safe_int(puts["volume"].fillna(0).sum()) if "volume" in puts.columns else 0
        coi = _safe_int(calls["openInterest"].fillna(0).sum()) if "openInterest" in calls.columns else 0
        poi = _safe_int(puts["openInterest"].fillna(0).sum()) if "openInterest" in puts.columns else 0
        call_ratio = (cv / coi) if coi > 0 else 0.0
        put_ratio = (pv / poi) if poi > 0 else 0.0
        cp_ratio = (cv / pv) if pv > 0 else (cv if cv else 0.0)
        unusual_c = call_ratio > 0.15
        unusual_p = put_ratio > 0.15
        call_sweep = (cv + pv) > 10000 and cp_ratio > 2.0
        if cv >= pv * 1.5 and cv > 0:
            bias = "BULLISH"
        elif pv >= cv * 1.5 and pv > 0:
            bias = "BEARISH"
        else:
            bias = "NEUTRAL"
        return {
            "unusual_calls": bool(unusual_c),
            "unusual_puts": bool(unusual_p),
            "call_sweep": bool(call_sweep),
            "total_call_volume": cv,
            "total_put_volume": pv,
            "call_oi": coi,
            "put_oi": poi,
            "call_volume_ratio": round(call_ratio, 3),
            "put_volume_ratio": round(put_ratio, 3),
            "call_put_ratio": round(cp_ratio, 2),
            "flow_bias": bias,
        }
    except Exception as e:
        logger.warning("detect_unusual_flow failed for %s: %s", ticker, e)
        return {"unusual_calls": False, "unusual_puts": False, "call_sweep": False,
                "total_call_volume": 0, "total_put_volume": 0,
                "call_put_ratio": 0.0, "flow_bias": "NEUTRAL"}


# ---------------- crush risk (Part 8) ----------------
def assess_iv_crush_risk(stock: dict, chain: dict | None) -> dict:
    iv_rank = (chain or {}).get("iv_rank", 50)
    days = (stock.get("time_target") or {}).get("days_remaining") or 30
    is_earnings = "upcoming_earnings" in (stock.get("signals") or [])

    if is_earnings and days < 7 and iv_rank > 65:
        return {"crush_risk": "SEVERE",
                "recommendation": "Do not buy options. Buy stock directly or wait until after earnings."}
    if is_earnings and 7 <= days <= 14 and iv_rank > 50:
        return {"crush_risk": "HIGH",
                "recommendation": "Use spread to cap IV crush exposure. Sell before earnings."}
    if days < 3 and iv_rank > 70:
        return {"crush_risk": "HIGH",
                "recommendation": "IV peaked. Sell existing positions before catalyst fires."}
    if iv_rank < 30:
        return {"crush_risk": "LOW",
                "recommendation": "Good entry. IV is cheap relative to history."}
    return {"crush_risk": "MODERATE",
            "recommendation": "Monitor IV as catalyst approaches. Plan to exit 1–2 days before event."}


# ---------------- top-level pipeline ----------------
async def analyze_ticker(stock: dict) -> dict | None:
    """Full pipeline for one ticker. Returns the options-intelligence block
    that gets attached to the stock dict and surfaced everywhere.
    NEVER raises — returns None on any failure."""
    try:
        ticker = stock.get("ticker")
        if not ticker:
            return None
        catalyst = stock.get("time_target", {}).get("target_date") or stock.get("catalyst_date")
        chain = await get_options_data(ticker, catalyst)
        if not chain:
            return None

        selected = select_strategy(stock, chain)
        contract = find_best_contract(chain, selected["direction"]) if selected["direction"] != "NONE" else None
        spread = None
        if selected["strategy"] == "BULL_CALL_SPREAD":
            spread = build_spread(chain, "BULL")
        elif selected["strategy"] == "BEAR_PUT_SPREAD":
            spread = build_spread(chain, "BEAR")

        crush = assess_iv_crush_risk(stock, chain)
        flow = await detect_unusual_flow(ticker)

        # Don't carry dataframes downstream — they're huge and unnecessary
        return {
            "strategy": selected["strategy"],
            "direction": selected["direction"],
            "strategy_reason": selected["reason"],
            "iv_rank": chain.get("iv_rank"),
            "iv_label": chain.get("iv_label"),
            "atm_iv": chain.get("atm_iv"),
            "expiration": chain.get("expiration"),
            "spot": chain.get("price"),
            "contract": contract,
            "spread": spread,
            "crush_risk": crush.get("crush_risk"),
            "crush_recommendation": crush.get("recommendation"),
            "flow": flow,
        }
    except Exception as e:
        logger.warning("analyze_ticker failed for %s: %s", stock.get("ticker"), e)
        return None
