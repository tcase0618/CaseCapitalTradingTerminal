from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import statistics
from types import SimpleNamespace

import pandas as pd
import pytest

from services import iv_history, options_engine

NOW = datetime(2026, 10, 7, 16, tzinfo=timezone.utc)
PROVIDER, FEED = "ALPACA_OPTIONS", "indicative"


@pytest.fixture(autouse=True)
def offline_price_history(monkeypatch):
    from services import pricer

    async def missing(*args, **kwargs):
        return {}

    monkeypatch.setattr(pricer, "get_history", missing)


def observation(at, value, ticker="AAA", provider=PROVIDER, feed=FEED):
    return {
        "ticker": ticker, "provider": provider, "feed": feed, "method": iv_history.METHOD,
        "observed_at": at.isoformat(), "observation_date": at.date().isoformat(), "atm_iv": value,
    }


def history(value=None):
    rows = []
    for age in range(364, 0, -1):
        at = NOW - timedelta(days=age)
        if at.weekday() < 5:
            rows.append(observation(at, value if value is not None else .2 + .4 * len(rows) / 259))
    return rows


def rank(rows, current=.4, **kwargs):
    return iv_history.rank_from_observations(
        current, rows, ticker=kwargs.get("ticker", "AAA"), provider=kwargs.get("provider", PROVIDER),
        feed=kwargs.get("feed", FEED), as_of=kwargs.get("as_of", NOW),
    )


class Collection:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.query = None

    async def insert_one(self, row):
        if not any(existing.get("_id") == row["_id"] for existing in self.rows):
            self.rows.append(dict(row))

    def find(self, query):
        self.query = query
        return self

    def sort(self, *args):
        return self

    async def to_list(self, limit):
        # Return all rows deliberately: production's pure calculator must
        # defend point-in-time and series boundaries even without DB filtering.
        return list(self.rows)


def setup_store(monkeypatch, rows=()):
    store = Collection(rows)
    monkeypatch.setattr(iv_history, "_now", lambda: NOW)
    monkeypatch.setattr(iv_history, "get_db", lambda: SimpleNamespace(iv_history=store))
    return store


async def observe(**kwargs):
    return await iv_history.observe_atm_iv(
        "aaa", kwargs.pop("atm_iv", .4), provider=PROVIDER, feed=FEED,
        expiration=kwargs.pop("expiration", "2026-11-06"), strike=kwargs.pop("strike", 100),
        spot=100, contract_symbol="AAA261106C00100000", **kwargs,
    )


def test_rank_is_historical_range_not_absolute_bucket_or_percentile():
    rows = history()
    result = rank(rows)
    low, high = min(r["atm_iv"] for r in rows), max(r["atm_iv"] for r in rows)
    assert result["iv_rank"] == round(100 * (.4 - low) / (high - low), 2)
    assert result["iv_rank_history"]["status"] == "AVAILABLE"
    assert rank(history(.4))["iv_rank"] is None


@pytest.mark.parametrize("current,expected", [(.01, 0), (2, 100)])
def test_new_extremes_are_clamped(current, expected):
    assert rank(history(), current)["iv_rank"] == expected


@pytest.mark.parametrize("current", [None, 0, -1, float("nan"), float("inf"), "bad"])
def test_invalid_current_iv_is_unknown(current):
    assert rank(history(), current)["iv_rank_history"]["status"] == "INVALID_CURRENT_IV"


def test_requires_count_and_year_coverage():
    assert rank(history()[-199:])["iv_rank_history"]["status"] == "INSUFFICIENT_HISTORY"
    compressed = history()[-230:]
    assert len(compressed) >= 200
    assert rank(compressed)["iv_rank_history"]["status"] == "INSUFFICIENT_HISTORY"


def test_duplicates_do_not_manufacture_history():
    result = rank(history()[-10:] * 30)
    assert result["iv_rank"] is None
    assert result["iv_rank_history"]["observation_count"] == 10


@pytest.mark.parametrize("changes", [{"ticker": "BBB"}, {"provider": "PUBLIC_OPTIONS"}, {"feed": "opra"}, {"method": "hv_proxy"}])
def test_series_are_isolated(changes):
    assert rank([{**r, **changes} for r in history()])["iv_rank"] is None


def test_point_in_time_and_window_filtering():
    rows = history()
    extra = [
        observation(NOW + timedelta(days=1), 100),
        observation(NOW - timedelta(hours=1), 100),
        observation(NOW - timedelta(days=370), .001),
        observation(NOW - timedelta(days=3), 100),  # Sunday
        {**observation(NOW - timedelta(days=20), 100), "observed_at": "invalid"},
        {**observation(NOW - timedelta(days=20), 100), "observation_date": "2020-01-01"},
    ]
    assert rank(rows + extra) == rank(rows)
    earlier = NOW - timedelta(days=120)
    assert rank(rows, as_of=earlier)["iv_rank"] is None


def test_stale_and_zero_range_history_are_unknown():
    stale = [r for r in history() if r["observed_at"] < (NOW - timedelta(days=10)).isoformat()]
    assert rank(stale)["iv_rank_history"]["status"] == "STALE_HISTORY"
    assert rank(history(.4))["iv_rank_history"]["status"] == "ZERO_HISTORY_RANGE"


@pytest.mark.parametrize("iv,label", [(None, "UNKNOWN"), (.29, "LOW"), (.3, "MODERATE"), (.6, "HIGH"), (.9, "VERY_HIGH")])
def test_absolute_iv_labels_are_explicit(iv, label):
    assert iv_history.absolute_iv_label(iv) == label


@pytest.mark.asyncio
async def test_daily_persistence_is_compact_immutable_and_provenanced(monkeypatch):
    store = setup_store(monkeypatch)
    first = await observe(quote_time=NOW.isoformat())
    await observe(atm_iv=.9)
    assert len(store.rows) == 1
    row = store.rows[0]
    assert row["atm_iv"] == .4
    assert row["observed_at"] == NOW.isoformat()
    assert row["quote_time"] == NOW.isoformat()
    assert row["provider"] == PROVIDER and row["feed"] == FEED
    assert row["rv_20"] is None and row["iv_rv"] is None
    assert "calls" not in row and "puts" not in row
    assert first["iv_rank"] is None and first["iv_label"] == "UNKNOWN"
    assert first["iv_rank_history"]["observation_count"] == 0
    assert store.query["observed_at"]["$lte"] == NOW.isoformat()


@pytest.mark.asyncio
async def test_persisted_history_survives_independent_calls(monkeypatch):
    setup_store(monkeypatch, history())
    assert (await observe())["iv_rank"] == rank(history())["iv_rank"]


@pytest.mark.asyncio
async def test_db_failure_does_not_fabricate_rank(monkeypatch):
    setup_store(monkeypatch)

    def unavailable():
        raise RuntimeError("offline")

    monkeypatch.setattr(iv_history, "get_db", unavailable)
    result = await observe()
    assert result["iv_rank"] is None
    assert result["iv_rank_history"]["status"] == "HISTORY_UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs,status", [
    ({"expiration": "2027-11-06"}, "NO_COMPARABLE_ATM_CONTRACT"),
    ({"strike": 120}, "NO_COMPARABLE_ATM_CONTRACT"),
    ({"quote_time": (NOW - timedelta(days=3)).isoformat()}, "STALE_OR_INVALID_QUOTE"),
    ({"quote_time": (NOW + timedelta(seconds=1)).isoformat()}, "STALE_OR_INVALID_QUOTE"),
    ({"quote_time": "bad"}, "STALE_OR_INVALID_QUOTE"),
])
async def test_incomparable_or_stale_observations_are_not_written(monkeypatch, kwargs, status):
    store = setup_store(monkeypatch)
    assert (await observe(**kwargs))["iv_rank_history"]["status"] == status
    assert not store.rows


@pytest.mark.asyncio
async def test_weekend_does_not_add_a_daily_mark(monkeypatch):
    store = setup_store(monkeypatch)
    monkeypatch.setattr(iv_history, "_now", lambda: NOW + timedelta(days=3))
    await observe()
    assert not store.rows


@pytest.mark.asyncio
async def test_engine_chooses_comparable_atm_expiry_without_mutating_chain(monkeypatch):
    captured = {}

    async def capture(ticker, iv, **kwargs):
        captured.update(ticker=ticker, iv=iv, **kwargs)
        return iv_history._result("INSUFFICIENT_HISTORY")

    monkeypatch.setattr(iv_history, "observe_atm_iv", capture)
    today = datetime.now(timezone.utc).date()
    calls = pd.DataFrame([
        {"contractSymbol": "far", "expiration": (today + timedelta(days=400)).isoformat(), "strike": 100, "impliedVolatility": .9},
        {"contractSymbol": "near_otm", "expiration": (today + timedelta(days=30)).isoformat(), "strike": 102, "impliedVolatility": .5},
        {"contractSymbol": "near_atm", "expiration": (today + timedelta(days=30)).isoformat(), "strike": 100, "impliedVolatility": .4},
    ])
    original = calls.copy(deep=True)
    result = await options_engine._historical_iv_metrics("AAA", calls, 100, PROVIDER, FEED)
    assert captured["contract_symbol"] == "near_atm"
    assert captured["iv"] == .4 and result["iv_rank"] is None
    pd.testing.assert_frame_equal(calls, original)


@pytest.mark.parametrize("stock", [
    {"signals": ["upcoming_earnings"], "score": 60, "risk_reward": 1.5},
    {"signals": ["bearish"], "score": 50},
    {"squeeze": {"score": 90}, "time_target": {"days_remaining": 10}},
    {"signals": ["insider_cluster_buy"], "squeeze": {"score": 60}, "time_target": {"days_remaining": 40}},
    {"score": 80, "time_target": {"days_remaining": 150}},
    {"signals": ["contract_surge"], "score": 60, "risk_reward": 1.4},
    {"signals": ["contract_surge"], "score": 50, "risk_reward": 1.3},
    {"signals": ["contract_surge"], "score": 55},
    {},
])
@pytest.mark.parametrize("chain", [None, {}, {"iv_rank": None}, {"iv_rank": float("nan")}])
def test_unknown_rank_preserves_neutral_strategy_selection(stock, chain):
    unknown = options_engine.select_strategy(stock, chain)
    neutral = options_engine.select_strategy(stock, {"iv_rank": 50})
    assert (unknown["strategy"], unknown["direction"]) == (neutral["strategy"], neutral["direction"])
    assert "acceptable IV" not in unknown["reason"]


def test_known_rank_rules_still_apply_and_unknown_crush_is_explicit():
    stock = {"signals": ["upcoming_earnings"], "time_target": {"days_remaining": 5}}
    assert options_engine.select_strategy(stock, {"iv_rank": 85})["strategy"] == "AVOID_OPTIONS"
    assert options_engine.assess_iv_crush_risk(stock, {"iv_rank": 85})["crush_risk"] == "SEVERE"
    assert options_engine.assess_iv_crush_risk(stock, {"iv_rank": None})["crush_risk"] == "UNKNOWN"


@pytest.mark.asyncio
async def test_standalone_output_preserves_history_and_absolute_label(monkeypatch):
    async def chain(*args, **kwargs):
        return {**iv_history._result("INSUFFICIENT_HISTORY"), "atm_iv": .9, "absolute_iv_label": "VERY_HIGH"}

    monkeypatch.setattr(options_engine, "get_options_data", chain)
    from services import pricer

    async def no_prices(*args, **kwargs):
        return {}

    monkeypatch.setattr(pricer, "get_history", no_prices)
    result = await options_engine.calculate_iv_rank("AAA")
    assert result["iv_rank"] is None and result["absolute_iv_label"] == "VERY_HIGH"
    assert result["iv_rank_history"]["status"] == "INSUFFICIENT_HISTORY"


@pytest.mark.asyncio
async def test_both_feed_adapters_keep_contracts_and_report_unknown_not_50(monkeypatch):
    from services import public_api

    now = datetime.now(timezone.utc)
    expiration = (now.date() + timedelta(days=30)).isoformat()
    symbol = f"AAA{(now.date() + timedelta(days=30)).strftime('%y%m%d')}C00100000"
    store = setup_store(monkeypatch)
    monkeypatch.setattr(iv_history, "_now", lambda: now)
    monkeypatch.setattr(options_engine, "_alpaca_options_configured", lambda: True)

    class HTTPClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    async def page(client, ticker, params):
        return {"snapshots": {symbol: {
            "impliedVolatility": .9, "latestQuote": {"bp": 1, "ap": 1.1, "t": now.isoformat()},
            "openInterest": 500, "greeks": {"delta": .5},
        }}}

    monkeypatch.setattr(options_engine.httpx, "AsyncClient", HTTPClient)
    monkeypatch.setattr(options_engine, "_alpaca_chain_page", page)
    alpaca = await options_engine._fetch_alpaca_options_data("AAA", spot_hint=100)

    class PublicClient:
        async def option_expirations(self, ticker):
            return {"expirations": [expiration]}

        async def option_chain(self, ticker, expiration):
            return {"options": [{
                "symbol": symbol, "type": "CALL", "strike": 100,
                "bid": 1, "ask": 1.1, "iv": .9, "openInterest": 500,
                "delta": .5, "expiration": expiration, "updatedAt": now.isoformat(),
            }]}

    monkeypatch.setattr(public_api, "configured", lambda: True)
    public = await options_engine._fetch_public_options_data("AAA", spot_hint=100, public_client=PublicClient())
    for chain, provider, feed in [(alpaca, "ALPACA_OPTIONS", options_engine.ALPACA_OPTIONS_FEED), (public, "PUBLIC_OPTIONS", "public")]:
        assert chain is not None
        assert chain["iv_rank"] is None and chain["iv_label"] == "UNKNOWN"
        assert chain["absolute_iv_label"] == "VERY_HIGH"
        assert chain["atm_iv"] == .9
        assert chain["calls"].iloc[0]["contractSymbol"] == symbol
        assert chain["calls"].iloc[0]["bid"] == 1
        assert chain["data_provider"] == provider and chain["data_feed"] == feed
        assert chain["iv_rank_history"]["status"] == "INSUFFICIENT_HISTORY"
    assert public["execution_eligible"] is False
    if now.weekday() < 5:
        assert len(store.rows) == 2


def price_history(count=41):
    sessions = []
    for age in range(85, 0, -1):
        day = (NOW - timedelta(days=age)).date()
        if day.weekday() < 5:
            sessions.append(day)
    price = 100.0
    prices = {}
    for i, session in enumerate(sessions[-count:]):
        price *= math.exp(.01 if i % 2 else -.006)
        prices[session.isoformat()] = price
    return prices


def mock_prices(monkeypatch, prices):
    from services import pricer

    async def fetch(*args, **kwargs):
        return prices

    monkeypatch.setattr(pricer, "get_history", fetch)
    monkeypatch.setattr(iv_history, "_now", lambda: NOW)


@pytest.mark.asyncio
async def test_rv_uses_exactly_twenty_completed_log_return_sessions(monkeypatch):
    prices = price_history()
    window = list(prices.values())[-21:]
    returns = [math.log(window[i] / window[i - 1]) for i in range(1, 21)]
    expected = statistics.stdev(returns) * math.sqrt(252)
    # Current/future sessions cannot leak into the point-in-time RV estimate.
    prices[NOW.date().isoformat()] = 100000
    prices[(NOW.date() + timedelta(days=1)).isoformat()] = 1
    mock_prices(monkeypatch, prices)
    result = await iv_history.realized_iv_metrics("AAA", .4)
    assert result["rv_20"] == round(expected, 6)
    assert result["iv_rv"] == round(.4 - expected, 6)
    assert result["rv_history"]["return_sessions"] == 20
    assert result["rv_history"]["annualization_sessions"] == 252
    assert result["rv_history"]["standard_deviation_ddof"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [0, 1, 20])
async def test_missing_price_sessions_never_fabricate_rv(monkeypatch, count):
    prices = price_history(count) if count else {}
    mock_prices(monkeypatch, prices)
    result = await iv_history.realized_iv_metrics("AAA", .4)
    assert result["rv_20"] is None and result["iv_rv"] is None


@pytest.mark.asyncio
async def test_invalid_price_is_not_dropped_to_bridge_missing_returns(monkeypatch):
    prices = price_history()
    prices[sorted(prices)[-10]] = None
    mock_prices(monkeypatch, prices)
    result = await iv_history.realized_iv_metrics("AAA", .4)
    assert result["rv_20"] is None and result["iv_rv"] is None
    assert result["rv_history"]["status"] == "INVALID_PRICE_WINDOW"


@pytest.mark.asyncio
async def test_zero_realized_volatility_is_valid_not_missing(monkeypatch):
    mock_prices(monkeypatch, {day: 100 for day in price_history(21)})
    result = await iv_history.realized_iv_metrics("AAA", .4)
    assert result["rv_20"] == 0 and result["iv_rv"] == .4
    result = await iv_history.realized_iv_metrics("AAA", None)
    assert result["rv_20"] == 0 and result["iv_rv"] is None


@pytest.mark.asyncio
async def test_observations_store_real_iv_rv_and_explicit_method(monkeypatch):
    store = setup_store(monkeypatch)
    mock_prices(monkeypatch, price_history())
    result = await observe()
    assert store.rows[0]["iv_rv"] == result["iv_rv"]
    assert store.rows[0]["rv_20"] == result["rv_20"]
    assert store.rows[0]["rv_history"]["return_sessions"] == 20


def test_postgres_daily_keys_remain_provider_feed_specific():
    from services.postgres_store import doc_key

    first = {"_id": f"{iv_history.METHOD}:AAA:ALPACA_OPTIONS:indicative:2026-10-07", "ticker": "AAA"}
    second = {"_id": f"{iv_history.METHOD}:AAA:PUBLIC_OPTIONS:public:2026-10-07", "ticker": "AAA"}
    assert doc_key("iv_history", first) == first["_id"]
    assert doc_key("iv_history", first) != doc_key("iv_history", second)


@pytest.mark.parametrize("value", [None, "bad", float("nan"), float("inf")])
@pytest.mark.parametrize("score,rr,action", [(80, 1.8, "STARTER"), (72, 1.5, "STARTER"), (65, 1.4, "WATCH")])
def test_options_desk_unknown_iv_is_visible_without_implicit_routing_block(value, score, rr, action):
    from services import options_desk

    pm = {"action": action, "pm_score": score, "risk_reward": rr}
    scan = {"options": {"strategy": "LONG_CALL", "contract": {"symbol": "AAA"}, "iv_rank": value}}
    neutral = {"options": {**scan["options"], "iv_rank": 50}}
    route, reasons = options_desk._route(pm, scan)
    assert route == options_desk._route(pm, neutral)[0]
    assert options_desk._pm_can_consider_options(pm, scan) == options_desk._pm_can_consider_options(pm, neutral)
    if route in {"OPTION", "BOTH"}:
        assert any("no favorable IV evidence" in reason for reason in reasons)
    lane = options_desk._strategy_lane(pm, {}, scan["options"], {"dte": 30}, "OPTION")
    assert lane["iv_rank"] is None and lane["iv_evidence"] == "UNKNOWN"
    cached = options_desk._legacy_strategy_lane({"route": "OPTION", "iv_rank": value}, {"dte": 30})
    assert cached["iv_rank"] is None and cached["iv_evidence"] == "UNKNOWN"


def test_options_desk_zero_rank_is_real_and_high_rank_rules_remain():
    from services import options_desk

    assert options_desk._historical_iv_rank(0) == 0
    lane = options_desk._strategy_lane({}, {}, {"iv_rank": 80}, {"dte": 30}, "OPTION")
    assert lane["lane"] == "HIGH_IV_SPREAD_OR_PASS"
    assert lane["iv_rank"] == 80


@pytest.mark.parametrize("chain", [None, {}, {"iv_rank": None}, {"iv_rank": float("nan")}])
def test_tail_hunter_unknown_is_not_a_favorable_exhibit_or_new_block(chain):
    from services import tail_hunter

    row = {"squeeze": {"score": 90}}
    instrument = {"delta": .18}
    passed, reasons = tail_hunter.gate_check(row, {}, instrument, {}, chain)
    assert passed and len(reasons) == 2
    assert not any("IV rank" in reason for reason in reasons)
    assert tail_hunter._iv_rank(chain) is None
    # A single non-IV exhibit still fails the existing two-exhibit rule.
    passed, reasons = tail_hunter.gate_check({}, {}, instrument, {}, chain)
    assert not passed and len(reasons) == 1
    passed, reasons = tail_hunter.gate_check({}, {}, instrument, {}, {"iv_rank": 0})
    assert passed and "IV rank under 50" in reasons


def test_missing_current_tail_iv_does_not_fall_back_to_cached_favorable_iv():
    from services import tail_hunter

    assert tail_hunter._iv_rank({}, {"iv_rank": 10}) is None


@pytest.mark.parametrize("chain", [None, {}, {"iv_rank": None}, {"iv_rank": float("nan")}, {"iv_rank": "bad"}])
@pytest.mark.parametrize("days", [2, 5, 10, 30])
def test_unknown_crush_rank_never_reaches_numeric_comparisons(chain, days):
    stock = {"signals": ["upcoming_earnings"], "time_target": {"days_remaining": days}}
    result = options_engine.assess_iv_crush_risk(stock, chain)
    assert result["crush_risk"] == "UNKNOWN"
    assert "unassessed" in result["recommendation"]


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), "bad"])
def test_telegram_unknown_rank_is_not_low_iv_or_displayed_as_none(value):
    from services import telegram_service

    assert not telegram_service._low_iv_entry({"iv_rank": value, "strategy": "LONG_CALL"})
    assert telegram_service._format_iv_rank(value) == "UNKNOWN"
    opts = {"iv_rank": value, "iv_label": "CHEAP", "strategy": "LONG_CALL", "contract": {
        "strike": 100, "expiration": "2026-11-06", "premium": 1, "max_loss": 100,
    }}
    text = telegram_service._format_options_block(opts)
    assert "IV rank: UNKNOWN" in text
    assert "None%" not in text and "CHEAP" not in text


def test_telegram_known_low_iv_including_zero_remains_eligible():
    from services import telegram_service

    assert telegram_service._low_iv_entry({"iv_rank": 0, "strategy": "LONG_CALL"})
    assert telegram_service._low_iv_entry({"iv_rank": 34, "strategy": "LONG_CALL"})
    assert not telegram_service._low_iv_entry({"iv_rank": 35, "strategy": "LONG_CALL"})
    assert telegram_service._format_iv_rank(0) == "0%"


@pytest.mark.asyncio
async def test_telegram_iv_command_shows_unknown_without_suppressing_available_atm_iv(monkeypatch):
    from services import telegram_service

    messages = []

    async def calculate(*args, **kwargs):
        return {"iv_rank": None, "iv_label": "UNKNOWN", "atm_iv": .4, "hv_30": None}

    async def send(text, **kwargs):
        messages.append(text)
        return True

    async def log(*args, **kwargs):
        pass

    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(telegram_service, "send_message", send)
    monkeypatch.setattr(telegram_service, "log_activity", log)
    monkeypatch.setattr(telegram_service, "get_db", lambda: object())
    monkeypatch.setattr(options_engine, "calculate_iv_rank", calculate)
    await telegram_service.handle_update({"message": {"text": "/iv AAA", "chat": {"id": 1}}})
    assert len(messages) == 1
    assert "IV rank: <b>UNKNOWN</b>" in messages[0]
    assert "ATM IV: 0.4" in messages[0]
    assert "Crush risk: <b>UNKNOWN</b>" in messages[0]
    assert "None/100" not in messages[0]


@pytest.mark.asyncio
async def test_telegram_low_iv_command_excludes_unknown_and_keeps_real_zero(monkeypatch):
    from services import telegram_service

    messages = []

    async def latest():
        return {"started_at": datetime.now(timezone.utc).isoformat(), "results": [
            {"ticker": "UNKNOWN", "options": {"iv_rank": None, "strategy": "LONG_CALL"}},
            {"ticker": "MISSING", "options": None},
            {"ticker": "ZERO", "options": {"iv_rank": 0, "strategy": "LONG_CALL"}},
        ]}

    async def send(text, **kwargs):
        messages.append(text)
        return True

    async def log(*args, **kwargs):
        pass

    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr(telegram_service, "send_message", send)
    monkeypatch.setattr(telegram_service, "log_activity", log)
    monkeypatch.setattr(telegram_service, "get_db", lambda: object())
    monkeypatch.setattr(telegram_service.scanner, "latest_scan", latest)
    await telegram_service.handle_update({"message": {"text": "/noiv", "chat": {"id": 1}}})
    assert len(messages) == 1
    assert "$ZERO" in messages[0]
    assert "$UNKNOWN" not in messages[0] and "$MISSING" not in messages[0]
