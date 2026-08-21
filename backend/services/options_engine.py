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
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

ALPACA_DATA_BASE = "https://data.alpaca.markets"
ALPACA_OPTIONS_FEED = os.environ.get("OPTIONS_APCA_DATA_FEED", "indicative").strip() or "indicative"
OCC_SYMBOL_RE = re.compile(r"^([A-Z]{1,6})(\d{6})([CP])(\d{8})$")


def _alpaca_key() -> str:
    return os.environ.get("OPTIONS_APCA_API_KEY_ID", "").strip()


def _alpaca_secret() -> str:
    return os.environ.get("OPTIONS_APCA_API_SECRET_KEY", "").strip()


def _alpaca_headers() -> dict[str, str]:
    return {"APCA-API-KEY-ID": _alpaca_key(), "APCA-API-SECRET-KEY": _alpaca_secret()}


def _alpaca_options_configured() -> bool:
    return bool(_alpaca_key() and _alpaca_secret())


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


def _days_to_expiration(expiration: Any) -> int:
    try:
        return max(0, (datetime.fromisoformat(str(expiration)).date() - date.today()).days)
    except Exception:
        return 30


# All yfinance access is sync → wrap each call in run_in_executor.
async def _to_thread(fn, *a, **kw):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: fn(*a, **kw))


def _option_expiration_target(catalyst_date: str | None = None) -> tuple[date, date]:
    target_min, target_max = 14, 70
    if catalyst_date:
        try:
            base = datetime.fromisoformat(catalyst_date).date()
            target_min, target_max = 5, 45
        except Exception:
            base = date.today()
    else:
        base = date.today()
    return base + timedelta(days=target_min), base + timedelta(days=target_max)


def _parse_occ_symbol(symbol: str) -> dict[str, Any] | None:
    m = OCC_SYMBOL_RE.match(str(symbol or "").upper())
    if not m:
        return None
    root, yymmdd, cp, strike_raw = m.groups()
    try:
        exp = datetime.strptime(yymmdd, "%y%m%d").date()
        strike = int(strike_raw) / 1000.0
    except Exception:
        return None
    return {"root": root, "expiration": exp.isoformat(), "type": cp, "strike": strike}


async def _alpaca_stock_price(ticker: str) -> float | None:
    if not _alpaca_options_configured():
        return None
    try:
        async with httpx.AsyncClient(timeout=12.0, headers=_alpaca_headers()) as client:
            r = await client.get(f"{ALPACA_DATA_BASE}/v2/stocks/{ticker}/trades/latest", params={"feed": "iex"})
        if r.status_code == 200:
            trade = (r.json() or {}).get("trade") or {}
            price = _safe_float(trade.get("p"))
            if price > 0:
                return price
    except Exception:
        pass
    return None


async def _alpaca_chain_page(client: httpx.AsyncClient, ticker: str, params: dict[str, Any]) -> dict[str, Any]:
    r = await client.get(f"{ALPACA_DATA_BASE}/v1beta1/options/snapshots/{ticker}", params=params)
    if r.status_code != 200:
        return {}
    return r.json() or {}


async def _fetch_alpaca_options_data(ticker: str, catalyst_date: str | None = None) -> dict[str, Any] | None:
    """Fetch a broad Alpaca indicative chain and normalize it to yfinance-like frames."""
    if not _alpaca_options_configured():
        return None
    try:
        import pandas as pd

        ticker = ticker.upper()
        spot = await _alpaca_stock_price(ticker)
        if not spot or spot <= 0:
            return None
        target_lo, target_hi = _option_expiration_target(catalyst_date)
        strike_lo = max(0.5, spot * 0.55)
        strike_hi = spot * 1.75
        params = {
            "feed": ALPACA_OPTIONS_FEED,
            "limit": 1000,
            "root_symbol": ticker,
            "expiration_date_gte": target_lo.isoformat(),
            "expiration_date_lte": target_hi.isoformat(),
            "strike_price_gte": round(strike_lo, 2),
            "strike_price_lte": round(strike_hi, 2),
        }
        snapshots: dict[str, Any] = {}
        async with httpx.AsyncClient(timeout=25.0, headers=_alpaca_headers()) as client:
            for _ in range(8):
                page = await _alpaca_chain_page(client, ticker, params)
                snapshots.update(page.get("snapshots") or {})
                token = page.get("next_page_token")
                if not token:
                    break
                params["page_token"] = token
        if not snapshots:
            return None

        rows = []
        for symbol, snap in snapshots.items():
            parsed = _parse_occ_symbol(symbol)
            if not parsed:
                continue
            quote = snap.get("latestQuote") or {}
            trade = snap.get("latestTrade") or {}
            greeks = snap.get("greeks") or {}
            daily = snap.get("dailyBar") or {}
            bid = _safe_float(quote.get("bp"))
            ask = _safe_float(quote.get("ap"))
            last = _safe_float(trade.get("p")) or ((bid + ask) / 2 if bid > 0 and ask > 0 else 0.0)
            volume = _safe_int(daily.get("v") or trade.get("s") or 0)
            rows.append({
                "contractSymbol": symbol,
                "strike": parsed["strike"],
                "lastPrice": last,
                "bid": bid,
                "ask": ask,
                "impliedVolatility": _safe_float(snap.get("impliedVolatility")),
                "openInterest": _safe_int(snap.get("openInterest"), -1),
                "volume": volume,
                "delta": _safe_float(greeks.get("delta")),
                "gamma": _safe_float(greeks.get("gamma")),
                "theta": _safe_float(greeks.get("theta")),
                "vega": _safe_float(greeks.get("vega")),
                "expiration": parsed["expiration"],
                "type": parsed["type"],
                "quoteTime": quote.get("t"),
                "dataProvider": "ALPACA_OPTIONS",
                "dataFeed": ALPACA_OPTIONS_FEED,
                "openInterestSource": "alpaca_snapshot" if snap.get("openInterest") is not None else "unavailable",
            })
        if not rows:
            return None

        df = pd.DataFrame(rows)
        expirations = sorted(df["expiration"].dropna().unique().tolist())
        if not expirations:
            return None
        ideal = target_lo + ((target_hi - target_lo) / 2)
        best = min(expirations, key=lambda exp: abs((datetime.fromisoformat(exp).date() - ideal).days))
        calls = df[df["type"] == "C"].copy()
        puts = df[df["type"] == "P"].copy()
        if not len(calls) and not len(puts):
            return None

        atm_iv = None
        valid_iv = calls[calls["impliedVolatility"] > 0] if len(calls) else df[df["impliedVolatility"] > 0]
        if len(valid_iv):
            idx = (valid_iv["strike"] - spot).abs().idxmin()
            atm_iv = _safe_float(valid_iv.loc[idx, "impliedVolatility"])
        iv_rank = 50
        iv_label = "FAIR"
        if atm_iv and atm_iv > 0:
            # Free Alpaca indicative feed gives current IV, not a full IV history.
            if atm_iv < 0.3:
                iv_rank, iv_label = 25, "CHEAP"
            elif atm_iv < 0.6:
                iv_rank, iv_label = 50, "FAIR"
            elif atm_iv < 0.9:
                iv_rank, iv_label = 70, "ELEVATED"
            else:
                iv_rank, iv_label = 85, "EXPENSIVE"
        return {
            "ticker": ticker,
            "calls": calls,
            "puts": puts,
            "price": spot,
            "expirations": expirations,
            "expiration": best,
            "expiration_window": {"gte": target_lo.isoformat(), "lte": target_hi.isoformat()},
            "strike_window": {"gte": round(strike_lo, 2), "lte": round(strike_hi, 2)},
            "snapshot_count": len(rows),
            "atm_iv": atm_iv,
            "iv_rank": iv_rank,
            "iv_label": iv_label,
            "data_provider": "ALPACA_OPTIONS",
            "data_feed": ALPACA_OPTIONS_FEED,
            "data_quality": "INDICATIVE" if ALPACA_OPTIONS_FEED == "indicative" else "EXECUTION_GRADE",
        }
    except Exception as e:
        logger.warning("Alpaca options data failed for %s: %s", ticker, e)
        return None


# ---------------- chain fetcher (Part 2) ----------------
async def get_options_data(ticker: str, catalyst_date: str | None = None) -> dict[str, Any] | None:
    """Returns chain dataframes + ATM IV + iv_rank, or None if fetch fails."""
    ticker = ticker.upper()
    alpaca_chain = await _fetch_alpaca_options_data(ticker, catalyst_date)
    if alpaca_chain:
        return alpaca_chain
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
                "data_provider": "YFINANCE",
                "data_feed": "fallback",
                "data_quality": "FALLBACK_RESEARCH",
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


def _liquidity_flag(oi: int, spread: float, premium: float = 0.0, volume: int = 0) -> str:
    spread_pct = spread / premium if premium > 0 else 1.0
    bad_oi = oi < 500
    bad_spread = spread > 0.75 or spread_pct > 0.08
    thin = oi < 500 and volume < 200
    if thin:
        return "POOR"
    if bad_oi and bad_spread:
        return "POOR"
    if bad_oi or bad_spread:
        return "WARN"
    return "GOOD"


def _premium_from_quote(row: dict | Any) -> float:
    bid = _safe_float(row.get("bid"))
    ask = _safe_float(row.get("ask"))
    last = _safe_float(row.get("lastPrice"))
    if bid > 0 and ask > 0:
        return (bid + ask) / 2
    if ask > 0:
        return ask
    return last


def find_best_contract(chain_data: dict, direction: str, budget: float = 300.0) -> dict | None:
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
        df["bid_safe"] = df["bid"].apply(_safe_float) if "bid" in df.columns else 0.0
        df["ask_safe"] = df["ask"].apply(_safe_float) if "ask" in df.columns else 0.0
        df["last_safe"] = df["lastPrice"].apply(_safe_float) if "lastPrice" in df.columns else 0.0
        df["premium_safe"] = df.apply(_premium_from_quote, axis=1)
        df = df[df["premium_safe"] > 0].copy()
        if len(df) == 0:
            return None
        df["max_loss_safe"] = df["premium_safe"] * 100
        df["spread_safe"] = (df["ask_safe"] - df["bid_safe"]).clip(lower=0.0)
        df["oi_safe"] = df["openInterest"].apply(_safe_int) if "openInterest" in df.columns else 0
        df["volume_safe"] = df["volume"].apply(_safe_int) if "volume" in df.columns else 0
        df["dist"] = (df["strike"] - target_strike).abs()
        if "expiration" in df.columns:
            df["days_to_exp"] = df["expiration"].apply(_days_to_expiration)
        else:
            df["days_to_exp"] = 30
        if "delta" not in df.columns:
            return None
        df["provider_delta_present"] = df["delta"].apply(lambda v: _safe_float(v) != 0)
        df = df[df["provider_delta_present"]].copy()
        if len(df) == 0:
            return None
        df["delta_safe"] = df["delta"].apply(_safe_float).abs()

        affordable = df[df["max_loss_safe"] <= float(budget)]
        pool = affordable if len(affordable) else df
        pool = pool.copy()
        pool["spread_pct"] = pool["spread_safe"] / pool["premium_safe"].replace(0, 0.01)
        target_delta = 0.55
        pool["delta_penalty"] = (pool["delta_safe"] - target_delta).abs()
        pool["expiry_penalty"] = (pool["days_to_exp"] - 35).abs() / 35
        pool["liquidity_penalty"] = (
            (pool["oi_safe"] < 500).astype(int) * 4
            + (pool["volume_safe"] < 200).astype(int) * 2
            + ((pool["spread_safe"] > 0.75) | (pool["spread_pct"] > 0.08)).astype(int) * 4
            + (pool["ask_safe"] <= 0).astype(int) * 10
            + (pool["bid_safe"] <= 0).astype(int) * 2
        )
        pool["budget_penalty"] = (pool["max_loss_safe"] > float(budget)).astype(int) * 8
        pool["score"] = (
            pool["budget_penalty"]
            + pool["liquidity_penalty"]
            + pool["expiry_penalty"]
            + pool["delta_penalty"] * 2
            + (pool["dist"] / max(spot, 1.0))
        )
        df = pool.sort_values(["score", "dist"]).head(1)
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
        premium = ((bid + ask) / 2 if bid > 0 and ask > 0 else ask or last)
        if premium <= 0:
            return None
        provider_delta = _safe_float(row.get("delta"))
        if provider_delta == 0:
            return None
        delta = provider_delta
        affordable = max(0, int(budget // (premium * 100))) if premium > 0 else 0
        return {
            "symbol": str(row.get("contractSymbol") or ""),
            "contractSymbol": str(row.get("contractSymbol") or ""),
            "data_provider": chain_data.get("data_provider") or row.get("dataProvider") or "YFINANCE",
            "data_feed": chain_data.get("data_feed") or row.get("dataFeed"),
            "data_quality": chain_data.get("data_quality") or "FALLBACK_RESEARCH",
            "open_interest_source": row.get("openInterestSource") or ("reported" if oi >= 0 else "unavailable"),
            "strike": round(strike, 2),
            "expiration": str(row.get("expiration") or chain_data.get("expiration") or ""),
            "days_to_expiration": _safe_int(row.get("days_to_exp")),
            "premium": round(premium, 2),
            "bid": round(bid, 2),
            "ask": round(ask, 2),
            "iv": round(iv, 4),
            "delta": round(delta, 3),
            "provider_delta_present": True,
            "open_interest": max(0, oi),
            "volume": volume,
            "spread": round(spread, 2),
            "contracts_at_budget": affordable,
            "max_loss": round(premium * 100, 2),
            "liquidity": _liquidity_flag(oi, spread, premium, volume),
            "type": "C" if is_call else "P",
        }
    except Exception as e:
        logger.warning("find_best_contract failed: %s", e)
        return None


# ---------------- strategy selector (Part 4) ----------------
def select_strategy(stock: dict, chain: dict | None) -> dict:
    """Pure-Python decision tree. Returns {strategy, reason, direction}."""
    signals = stock.get("signals") or []
    risk_level = str((stock.get("risk") or {}).get("level", "MEDIUM")).upper()
    sq_score = (stock.get("squeeze") or {}).get("score") or 0
    days = (stock.get("time_target") or {}).get("days_remaining") or 30
    iv_rank = (chain or {}).get("iv_rank", 50)
    score = _safe_float(stock.get("score") or stock.get("case_score") or stock.get("pm_score"))
    rr = _safe_float(stock.get("risk_reward") or stock.get("rr") or stock.get("riskReward"))
    signal_set = {str(s) for s in signals}
    lower_signals = {str(s).lower() for s in signals}

    is_insider = "insider_cluster_buy" in lower_signals or "insider" in " ".join(lower_signals)
    is_earnings = "upcoming_earnings" in lower_signals or "earnings" in " ".join(lower_signals)
    is_contract = "contract_surge" in lower_signals or "contract" in " ".join(lower_signals)
    is_congress = "congressional_buy" in lower_signals or "congress" in " ".join(lower_signals)
    is_flow = bool({"call_sweep", "unusual_flow", "cheap_iv", "options_flow_bullish"} & lower_signals)
    is_squeeze = "high_short_interest" in lower_signals or sq_score >= 55
    is_bearish = bool({"bearish", "risk_off", "negative_catalyst", "dark_pool_bearish"} & lower_signals)
    has_bullish_anchor = any([is_insider, is_contract, is_congress, is_flow, is_squeeze, is_earnings])

    # Rule order matters.
    if is_earnings and iv_rank > 80:
        if has_bullish_anchor and score >= 55 and rr >= 1.5:
            return {"strategy": "LONG_CALL_EVENT_SCOUT", "direction": "BULL",
                    "reason": "High-IV near-event setup is allowed only as a capped paper scout after Alpaca liquidity clears"}
        if days >= 2:
            return {"strategy": "AVOID_OPTIONS", "direction": "NONE",
                    "reason": "Elevated earnings IV without enough PM evidence for a capped event scout"}
        return {"strategy": "AVOID_OPTIONS", "direction": "NONE",
                "reason": "Binary event is inside 48h with extreme IV; single-leg premium is not a clean paper scout"}
    if risk_level == "EXTREME" and score < 70:
        return {"strategy": "AVOID_OPTIONS", "direction": "NONE",
                "reason": "Extreme setup needs stronger PM score before the Options Desk can scout it"}
    if is_bearish:
        if iv_rank > 75 and score < 70:
            return {"strategy": "AVOID_OPTIONS", "direction": "NONE",
                    "reason": "Bearish setup has expensive IV without enough score for a paper scout"}
        return {"strategy": "LONG_PUT", "direction": "BEAR",
                "reason": "Bearish evidence detected - PM should express with puts only if liquidity clears"}
    if sq_score > 75 and days < 14 and iv_rank < 75:
        return {"strategy": "LONG_CALL", "direction": "BULL",
                "reason": "High squeeze probability with a near catalyst - directional call candidate"}
    if is_insider and sq_score > 50 and days > 30 and iv_rank < 65:
        return {"strategy": "LONG_CALL", "direction": "BULL",
                "reason": "Insider accumulation with acceptable IV - directional call candidate"}
    if is_congress and risk_level == "LOW":
        return {"strategy": "LONG_CALL", "direction": "BULL",
                "reason": "High-conviction low-risk congressional setup - single-leg call only if liquidity clears"}
    if score >= 78 and days >= 120 and iv_rank < 70:
        return {"strategy": "LEAPS_CALL_CANDIDATE", "direction": "BULL",
                "reason": "High-score long-horizon setup - route to LEAPS sleeve for long-dated exposure"}
    if has_bullish_anchor and score >= 58 and rr >= 1.3 and iv_rank < 80:
        return {"strategy": "LONG_CALL_SCOUT", "direction": "BULL",
                "reason": "PM-grade bullish anchor with acceptable IV; small paper option scout if Alpaca liquidity clears"}
    if has_bullish_anchor and score >= 48 and rr >= 1.25 and iv_rank < 70:
        return {"strategy": "LONG_CALL_SCOUT", "direction": "BULL",
                "reason": "Watchlist paper scout candidate; requires live contract and risk preflight"}
    if is_contract and score >= 55 and days >= 14 and iv_rank < 75:
        return {"strategy": "LONG_CALL_SCOUT", "direction": "BULL",
                "reason": "Contract catalyst is option-eligible for a small scout after liquidity validation"}
    return {"strategy": "AVOID_OPTIONS", "direction": "NONE",
            "reason": "No affirmative setup - no default options trade"}

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
        sell_target = primary["strike"] + width
    else:
        sell_target = primary["strike"] - width

    df = df.copy()
    if direction == "BULL":
        df = df[df["strike"].apply(_safe_float) > float(primary["strike"])]
    else:
        df = df[df["strike"].apply(_safe_float) < float(primary["strike"])]
    if df is None or len(df) == 0:
        return None
    df["dist"] = (df["strike"] - sell_target).abs()
    sell_row = df.sort_values("dist").iloc[0]
    sell_strike = float(sell_row["strike"])
    sell_premium = _premium_from_quote(sell_row)
    if sell_premium <= 0:
        sell_premium = 0.05

    buy_premium = primary["premium"]
    net_debit = round(buy_premium - sell_premium, 2)
    if net_debit <= 0:
        net_debit = 0.05
    spread_width = abs(sell_strike - primary["strike"])
    max_profit = round(spread_width * 100 - net_debit * 100, 2)
    max_loss = round(net_debit * 100, 2)
    rr = round(max_profit / max_loss, 1) if max_loss > 0 else 0.0
    breakeven = round(primary["strike"] + net_debit, 2) if direction == "BULL" else round(primary["strike"] - net_debit, 2)

    return {
        "direction": direction,
        "type": "CALL_SPREAD" if direction == "BULL" else "PUT_SPREAD",
        "data_provider": chain_data.get("data_provider"),
        "data_feed": chain_data.get("data_feed"),
        "data_quality": chain_data.get("data_quality"),
        "buy_symbol": primary.get("symbol") or primary.get("contractSymbol"),
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
def _detect_unusual_flow_from_chain(ticker: str, chain: dict | None) -> dict[str, Any]:
    """Detect unusual options activity vs open interest baseline from an existing chain."""
    if not chain:
        return {"unusual_calls": False, "unusual_puts": False, "call_sweep": False,
                "total_call_volume": 0, "total_put_volume": 0,
                "call_put_ratio": 0.0, "flow_bias": "NEUTRAL"}
    try:
        calls = chain["calls"]; puts = chain["puts"]
        if calls is None or puts is None:
            return {"unusual_calls": False, "unusual_puts": False, "call_sweep": False,
                    "total_call_volume": 0, "total_put_volume": 0,
                    "call_put_ratio": 0.0, "flow_bias": "NEUTRAL"}
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


async def detect_unusual_flow(ticker: str) -> dict[str, Any]:
    """Detect unusual options activity vs open interest baseline."""
    return _detect_unusual_flow_from_chain(ticker, await get_options_data(ticker))


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
        flow = _detect_unusual_flow_from_chain(ticker, chain)

        # Don't carry dataframes downstream — they're huge and unnecessary
        return {
            "strategy": selected["strategy"],
            "direction": selected["direction"],
            "strategy_reason": selected["reason"],
            "data_provider": chain.get("data_provider"),
            "data_feed": chain.get("data_feed"),
            "data_quality": chain.get("data_quality"),
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
