"""Free public-source data scrapers: OpenInsider, Finviz, Yahoo Finance."""
from __future__ import annotations
import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
TIMEOUT = httpx.Timeout(30.0, connect=15.0)
TICKER_RE = re.compile(r"^[A-Z]{1,5}(?:[\.\-][A-Z]{1,2})?$")


def _safe_text(el) -> str:
    return el.get_text(strip=True) if el else ""


# ---------- OpenInsider: cluster buys ----------
async def fetch_openinsider_cluster_buys(limit: int = 50) -> list[dict[str, Any]]:
    url = (
        "http://openinsider.com/screener?"
        "s=&o=&pl=&ph=&ll=&lh=&fd=90&fdr=&td=0&tdr=&fdlyl=&fdlyh=&daysago=&"
        "xp=1&xs=1&"
        "vl=&vh=&ocl=&och=&sic1=-1&sicl=100&sich=9999&"
        "grp=0&nfl=&nfh=&nil=&nih=&nol=&noh=&v2l=&v2h=&"
        "oc2l=&oc2h=&sortcol=0&cnt=200&page=1"
    )
    rows: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        table = soup.find("table", class_="tinytable")
        if not table or not table.find("tbody"):
            return []
        for tr in table.find("tbody").find_all("tr"):
            cols = tr.find_all("td")
            if len(cols) < 13:
                continue
            ticker = _safe_text(cols[3]).upper()
            if not TICKER_RE.match(ticker):
                continue
            rows.append({
                "ticker": ticker,
                "company": _safe_text(cols[4]),
                "insider": _safe_text(cols[5]),
                "title": _safe_text(cols[6]),
                "trade_type": _safe_text(cols[7]),
                "price": _safe_text(cols[8]),
                "qty": _safe_text(cols[9]),
                "value": _safe_text(cols[12]),
                "filing_date": _safe_text(cols[1]),
            })
            if len(rows) >= limit * 4:
                break
    except Exception as e:
        logger.warning("OpenInsider scrape failed: %s", e)
        return []

    agg: dict[str, dict[str, Any]] = {}
    for r0 in rows:
        t = r0["ticker"]
        a = agg.setdefault(t, {
            "ticker": t,
            "company": r0["company"],
            "insiders": set(),
            "buy_count": 0,
            "total_value": 0.0,
            "latest_filing": r0["filing_date"],
            "samples": [],
        })
        a["insiders"].add(r0["insider"])
        a["buy_count"] += 1
        try:
            v = float(r0["value"].replace("$", "").replace(",", "").replace("+", ""))
        except ValueError:
            v = 0.0
        a["total_value"] += v
        if len(a["samples"]) < 3:
            a["samples"].append(r0)

    clusters = []
    for t, a in agg.items():
        if len(a["insiders"]) >= 2:
            clusters.append({
                "ticker": t,
                "company": a["company"],
                "insider_count": len(a["insiders"]),
                "buy_count": a["buy_count"],
                "total_value_usd": round(a["total_value"], 2),
                "latest_filing": a["latest_filing"],
                "samples": a["samples"],
            })
    clusters.sort(key=lambda x: (x["insider_count"], x["total_value_usd"]), reverse=True)
    return clusters[:limit]


# ---------- Finviz: high short interest ----------
async def fetch_finviz_high_short_interest(min_pct: float = 10.0, limit: int = 300) -> list[dict[str, Any]]:
    """Finviz screener pre-filtered for short_float>10%. Paginates."""
    base = (
        "https://finviz.com/screener.ashx?"
        "v=152&f=sh_short_o10,sh_avgvol_o100&o=-shortinterestshare&c=0,1,65"
    )
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as client:
            for page_start in range(1, limit + 1, 20):
                url = f"{base}&r={page_start}"
                # 1 retry on transient failure
                resp_text = None
                for attempt in range(2):
                    try:
                        r = await client.get(url)
                        if r.status_code == 200:
                            resp_text = r.text
                            break
                    except Exception:
                        pass
                    await asyncio.sleep(0.4)
                if not resp_text:
                    break
                soup = BeautifulSoup(resp_text, "lxml")
                added = 0
                # Finviz changed link pattern from `quote?t=...` to `stock?t=...` (2026).
                for a in soup.find_all("a", href=re.compile(r"^(?:stock|quote)\?t=")):
                    t = a.get_text(strip=True).upper()
                    if not TICKER_RE.match(t) or t in seen:
                        continue
                    short_pct = None
                    tr = a.find_parent("tr")
                    if tr:
                        for cell in tr.find_all("td"):
                            txt = cell.get_text(strip=True)
                            m = re.match(r"^(\d{1,2}(?:\.\d+)?)%$", txt)
                            if m:
                                v = float(m.group(1))
                                if 5.0 <= v <= 100.0:
                                    short_pct = v
                                    break
                    seen.add(t)
                    out.append({
                        "ticker": t,
                        "short_float_pct": short_pct if short_pct is not None else f">{min_pct:.0f}",
                    })
                    added += 1
                    if len(out) >= limit:
                        return out
                if added == 0 and page_start > 1:
                    # added==0 on a non-first page means we've exhausted results
                    break
                await asyncio.sleep(0.25)
    except Exception as e:
        logger.warning("Finviz scrape failed: %s", e)
        return []
    return out


# ---------- Yahoo Finance: upcoming earnings ----------
async def fetch_finviz_upcoming_earnings(days: str = "nextweek", limit: int = 300) -> list[dict[str, Any]]:
    """Use Finviz earnings filter (earningsdate_nextweek/nextdays5/...).
    Complements Yahoo for breadth."""
    base = (
        "https://finviz.com/screener.ashx?"
        f"v=152&f=earningsdate_{days}&o=earningsdate&c=0,1,68"
    )
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as client:
            for page_start in range(1, limit + 1, 20):
                url = f"{base}&r={page_start}"
                try:
                    r = await client.get(url)
                    if r.status_code != 200:
                        break
                except Exception:
                    break
                soup = BeautifulSoup(r.text, "lxml")
                added = 0
                for a in soup.find_all("a", href=re.compile(r"^(?:stock|quote)\?t=")):
                    t = a.get_text(strip=True).upper()
                    if not TICKER_RE.match(t) or t in seen:
                        continue
                    seen.add(t)
                    out.append({"ticker": t, "earnings_date": days})
                    added += 1
                    if len(out) >= limit:
                        return out
                if added == 0:
                    break
    except Exception as e:
        logger.warning("Finviz earnings scrape failed: %s", e)
        return []
    return out


async def fetch_yahoo_upcoming_earnings(days_ahead: int = 14, limit: int = 500) -> list[dict[str, Any]]:
    """Scrape Yahoo earnings calendar across `days_ahead` days. Paginates each
    day with offset/size params."""
    from datetime import date, timedelta
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    async with httpx.AsyncClient(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as client:
        for offset_day in range(days_ahead):
            d = date.today() + timedelta(days=offset_day)
            ds = d.isoformat()
            for offset in range(0, 300, 100):
                url = f"https://finance.yahoo.com/calendar/earnings?day={ds}&offset={offset}&size=100"
                try:
                    r = await client.get(url)
                    if r.status_code != 200:
                        break
                except Exception:
                    break
                soup = BeautifulSoup(r.text, "lxml")
                table = None
                for t in soup.find_all("table"):
                    headers = [th.get_text(strip=True).lower() for th in t.find_all("th")]
                    if "symbol" in headers or "company" in headers:
                        table = t
                        break
                if not table:
                    break
                added = 0
                for tr in table.find_all("tr"):
                    cells = tr.find_all("td")
                    if not cells:
                        continue
                    a = cells[0].find("a") if cells else None
                    sym = (a.get_text(strip=True) if a else cells[0].get_text(strip=True)).upper()
                    if not TICKER_RE.match(sym) or sym in seen:
                        continue
                    seen.add(sym)
                    out.append({"ticker": sym, "earnings_date": ds})
                    added += 1
                    if len(out) >= limit:
                        return out
                if added == 0:
                    break
    return out


# ---------- Quote helper ----------
async def fetch_quote(ticker: str) -> dict[str, Any] | None:
    """Use yfinance which handles rate-limiting via curl_cffi sessions."""
    ticker = ticker.upper()
    try:
        import yfinance as yf
        loop = asyncio.get_event_loop()
        def _sync():
            t = yf.Ticker(ticker)
            fast = getattr(t, "fast_info", None)
            price = None
            prev_close = None
            currency = None
            name = None
            if fast:
                price = fast.get("last_price") if hasattr(fast, "get") else getattr(fast, "last_price", None)
                prev_close = fast.get("previous_close") if hasattr(fast, "get") else getattr(fast, "previous_close", None)
                currency = fast.get("currency") if hasattr(fast, "get") else getattr(fast, "currency", None)
            if price is None:
                hist = t.history(period="5d")
                if len(hist):
                    price = float(hist["Close"].iloc[-1])
            try:
                info = t.info
                name = info.get("longName") or info.get("shortName")
            except Exception:
                pass
            return {"price": float(price) if price else None,
                    "previous_close": float(prev_close) if prev_close else None,
                    "currency": currency, "name": name}
        data = await loop.run_in_executor(None, _sync)
        if not data or data.get("price") is None:
            return None
        return {"ticker": ticker, **data}
    except Exception as e:
        logger.warning("fetch_quote failed for %s: %s", ticker, e)
        return None


async def collect_all_signals() -> dict[str, Any]:
    # OpenInsider + Yahoo can run concurrently with the Finviz chain.
    # All Finviz calls (short interest + 2 earnings windows) run sequentially
    # to avoid concurrent rate-limiting on the same host.
    async def finviz_chain():
        a = await fetch_finviz_high_short_interest()
        b = await fetch_finviz_upcoming_earnings(days="nextweek")
        c = await fetch_finviz_upcoming_earnings(days="thismonth")
        return a, b, c

    insiders, yh_earn, fv = await asyncio.gather(
        fetch_openinsider_cluster_buys(),
        fetch_yahoo_upcoming_earnings(),
        finviz_chain(),
        return_exceptions=True,
    )
    if isinstance(insiders, Exception):
        insiders = []
    if isinstance(yh_earn, Exception):
        yh_earn = []
    if isinstance(fv, Exception):
        shorts, fv_earn1, fv_earn2 = [], [], []
    else:
        shorts, fv_earn1, fv_earn2 = fv

    earn_map: dict[str, dict[str, Any]] = {}
    for e in yh_earn:
        earn_map[e["ticker"]] = e
    for e in fv_earn1 + fv_earn2:
        earn_map.setdefault(e["ticker"], e)

    return {
        "insider_clusters": insiders,
        "high_short_interest": shorts,
        "upcoming_earnings": list(earn_map.values()),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }
