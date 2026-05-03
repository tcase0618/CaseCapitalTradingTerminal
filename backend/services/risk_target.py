"""Pure-Python risk score + 3-method price target. ZERO Claude tokens."""
from __future__ import annotations
import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


# Sector → average Price/Sales multiple (rough US 2024 medians)
SECTOR_PS: dict[str, float] = {
    "technology": 6.0,
    "communication services": 3.5,
    "healthcare": 4.5,
    "consumer cyclical": 1.5,
    "consumer defensive": 1.2,
    "financial services": 2.5,
    "industrials": 2.0,
    "energy": 1.5,
    "basic materials": 1.8,
    "real estate": 6.0,
    "utilities": 2.5,
    "industrial conglomerates": 2.5,
    "aerospace & defense": 2.5,
    "default": 2.5,
}

# Historical signal-bundle uplift map (heuristic)
SIGNAL_UPLIFT: dict[frozenset, float] = {
    frozenset({"CONTRACT_SURGE", "insider_cluster_buy"}): 0.34,
    frozenset({"high_short_interest", "upcoming_earnings"}): 0.28,
    frozenset({"CONCENTRATION_WIN"}): 0.41,
    frozenset({"NEW_WINNER", "CONTRACT_SURGE"}): 0.30,
    frozenset({"MOMENTUM_STACK"}): 0.22,
    frozenset({"BUDGET_SURGE"}): 0.18,
    frozenset({"insider_cluster_buy", "high_short_interest"}): 0.25,
}


def _yf_info(ticker: str) -> dict[str, Any]:
    try:
        import yfinance as yf
        # Retry on empty info (yfinance is flaky under concurrent load)
        info: dict = {}
        for attempt in range(2):
            t = yf.Ticker(ticker)
            try:
                info = t.info or {}
            except Exception:
                info = {}
            if info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose"):
                break
        return {
            "price": info.get("currentPrice") or info.get("regularMarketPrice")
                      or info.get("previousClose"),
            "market_cap": info.get("marketCap"),
            "sector": (info.get("sector") or "").lower(),
            "industry": info.get("industry"),
            "beta": info.get("beta"),
            "trailing_eps": info.get("trailingEps"),
            "trailing_revenue": info.get("totalRevenue"),
            "ps_ratio": info.get("priceToSalesTrailing12Months"),
            "analyst_target_mean": info.get("targetMeanPrice"),
            "analyst_target_low": info.get("targetLowPrice"),
            "analyst_target_high": info.get("targetHighPrice"),
            "avg_volume": info.get("averageDailyVolume10Day") or info.get("averageVolume"),
            "float_shares": info.get("floatShares") or info.get("sharesOutstanding"),
            "short_ratio": info.get("shortRatio"),
            "name": info.get("longName") or info.get("shortName"),
        }
    except Exception as e:
        logger.warning("yf.info failed for %s: %s", ticker, e)
        return {}


async def fetch_fundamentals(ticker: str) -> dict[str, Any]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _yf_info, ticker)


# ---------- RISK ----------
def compute_risk(fund: dict[str, Any], signals: list[str], gov: dict[str, Any] | None,
                  short_pct: float | None, insider_buys: int) -> dict[str, Any]:
    factors: list[str] = []
    score = 0

    mc = fund.get("market_cap") or 0
    if mc and mc < 500_000_000:
        score += 2
        factors.append(f"Market cap ${mc/1e6:.0f}M < $500M")

    if short_pct and short_pct > 20:
        score += 2
        factors.append(f"Short interest {short_pct}% > 20%")
    elif "high_short_interest" in signals and not short_pct:
        score += 2
        factors.append("Short interest > 10%")

    teps = fund.get("trailing_eps")
    if teps is not None and teps < 0:
        score += 1
        factors.append("Unprofitable (negative TTM EPS)")

    rev = fund.get("trailing_revenue") or 0
    big = (gov or {}).get("biggest_award") or {}
    big_amt = big.get("amount", 0) if big else 0
    if rev and big_amt and big_amt > 0.30 * rev:
        score += 2
        factors.append(f"Single contract ${big_amt/1e6:.0f}M >30% of TTM revenue")

    beta = fund.get("beta")
    if beta and beta > 1.5:
        score += 1
        factors.append(f"Beta {beta:.2f} > 1.5")

    # Active short seller report — no free reliable feed; default 0.

    if insider_buys and insider_buys > 0:
        score -= 1
        factors.append(f"Insider open-market buying ({insider_buys}) [-]")

    agencies = (gov or {}).get("agencies_30d") or []
    if len(agencies) >= 3:
        score -= 1
        factors.append(f"{len(agencies)}+ agencies winning [-]")

    # Clamp score to non-negative for display sanity (negative just means
    # offsetting positive factors dominate; semantically still LOW risk).
    score = max(0, score)

    if score <= 1:
        level, emoji = "LOW", "🟢"
    elif score <= 3:
        level, emoji = "MEDIUM", "🟡"
    elif score <= 5:
        level, emoji = "HIGH", "🔴"
    else:
        level, emoji = "EXTREME", "☠️"

    return {"score": score, "level": level, "emoji": emoji, "factors": factors}


# ---------- TARGETS ----------
def _signal_uplift(signals: list[str]) -> tuple[float, str]:
    s = set(signals)
    best_uplift = 0.0
    best_match = ""
    for combo, uplift in SIGNAL_UPLIFT.items():
        if combo.issubset(s):
            if uplift > best_uplift:
                best_uplift = uplift
                best_match = " + ".join(sorted(combo))
    if best_uplift == 0.0:
        # default fallback by count of signals
        n = len(s)
        best_uplift = 0.10 + 0.05 * max(0, n - 1)
        best_match = f"{n} signals"
    return best_uplift, best_match


def compute_targets(fund: dict[str, Any], signals: list[str],
                     gov: dict[str, Any] | None) -> dict[str, Any]:
    price = fund.get("price")
    methods: dict[str, dict[str, Any] | None] = {
        "contract_revenue_multiple": None,
        "analyst_consensus": None,
        "signal_adjusted": None,
    }

    # Method 1: Contract Revenue Multiple
    big = (gov or {}).get("biggest_award") or {}
    contract_amount = big.get("amount", 0) if big else 0
    rev = fund.get("trailing_revenue") or 0
    if contract_amount and rev:
        # Annualize: assume multi-year IDIQ; conservatively divide by 5,
        # then cap at 25% of TTM revenue (single contracts rarely exceed that).
        annualized = min(contract_amount / 5.0, rev * 0.25)
        new_rev = rev + annualized
        sector = (fund.get("sector") or "default").lower()
        ps = SECTOR_PS.get(sector, SECTOR_PS["default"])
        mc_now = fund.get("market_cap") or 0
        implied_mc = new_rev * ps
        if mc_now and price:
            implied_price = price * implied_mc / mc_now
            methods["contract_revenue_multiple"] = {
                "value": round(implied_price, 2),
                "ps_used": ps,
                "annualized_contract": annualized,
                "sector": sector,
            }

    # Method 2: Analyst consensus
    atm = fund.get("analyst_target_mean")
    if atm and price:
        methods["analyst_consensus"] = {
            "value": round(float(atm), 2),
            "low": fund.get("analyst_target_low"),
            "high": fund.get("analyst_target_high"),
        }

    # Method 3: Signal-adjusted upside
    uplift, label = _signal_uplift(signals)
    if price:
        methods["signal_adjusted"] = {
            "value": round(price * (1 + uplift), 2),
            "uplift_pct": round(uplift * 100, 1),
            "match": label,
        }

    # Blend (with divergence guard: if contract_revenue method is more than
    # 2× the analyst consensus, drop it from the blend — it's likely amplified
    # by a giant lifetime IDIQ contract and shouldn't dominate).
    method_values: dict[str, float] = {}
    for k, m in methods.items():
        if m and m.get("value"):
            method_values[k] = float(m["value"])
    ac = method_values.get("analyst_consensus")
    cr = method_values.get("contract_revenue_multiple")
    if ac and cr and (cr > 2 * ac or cr < 0.5 * ac):
        method_values.pop("contract_revenue_multiple", None)
        if methods["contract_revenue_multiple"]:
            methods["contract_revenue_multiple"]["excluded_from_blend"] = True
    values = list(method_values.values())
    blended = round(sum(values) / len(values), 2) if values else None

    # Bear / bull from analyst range; fallback to blended +/- 10%
    if methods["analyst_consensus"] and methods["analyst_consensus"].get("low") and methods["analyst_consensus"].get("high"):
        target_low = float(methods["analyst_consensus"]["low"])
        target_high = float(methods["analyst_consensus"]["high"])
    elif blended:
        target_low = round(blended * 0.85, 2)
        target_high = round(blended * 1.20, 2)
    else:
        target_low = target_high = None

    def pct(v):
        if not v or not price:
            return None
        return round((v - price) / price * 100, 1)

    return {
        "current_price": price,
        "methods": methods,
        "target_low": target_low,
        "target_high": target_high,
        "target_blended": blended,
        "upside_low": pct(target_low),
        "upside_high": pct(target_high),
        "upside_blended": pct(blended),
    }


def compute_stop_loss(fund: dict[str, Any], risk: dict[str, Any]) -> float | None:
    price = fund.get("price")
    if not price:
        return None
    # tighter stop for higher risk
    pct = {"LOW": 0.10, "MEDIUM": 0.12, "HIGH": 0.15, "EXTREME": 0.20}.get(risk["level"], 0.12)
    return round(price * (1 - pct), 2)
