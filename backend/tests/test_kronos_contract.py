from datetime import timedelta
from types import SimpleNamespace

import pytest

from services import kronos, kronos_contract as contract, postgres_store

NOW = contract.parse_time("2026-10-07T16:02:00Z")


def bars(day="2026-10-07", count=31):
    start = contract.parse_time(f"{day}T13:30:00Z")
    return [{"timestamp": (start + timedelta(minutes=i * 5)).isoformat(), "open": 100 + i,
             "high": 102 + i, "low": 99 + i, "close": 101 + i, "volume": 1000} for i in range(count)]


class Collection:
    def __init__(self):
        self.rows = []

    def find(self, query=None, projection=None):
        rows = [r for r in self.rows if postgres_store._matches(r, query or {})]
        class Cursor:
            def sort(self, *args):
                return self
            async def to_list(self, length):
                return rows[:length] if length else rows
        return Cursor()

    async def find_one(self, query=None, projection=None, **kwargs):
        return next((r for r in self.rows if postgres_store._matches(r, query or {})), None)

    async def update_one(self, query, update, upsert=False):
        row = await self.find_one(query)
        if row is None and upsert:
            row = {**query, **update.get("$setOnInsert", {})}
            self.rows.append(row)
        if row is not None:
            row.update(update.get("$set", {}))
        return SimpleNamespace(modified_count=1)

    async def insert_one(self, row):
        self.rows.append(row)


@pytest.fixture
def db(monkeypatch):
    database = SimpleNamespace(**{name: Collection() for name in ["kronos_candle_predictions", "kronos_candle_outcomes", "kronos_accuracy_snapshots", "kronos_forecast_runs", "kronos_forecast_snapshots", "kronos_pm_disagreements"]})
    monkeypatch.setattr(kronos, "get_db", lambda: database)
    monkeypatch.setattr(kronos, "_now", lambda: NOW)
    return database


def test_old_and_incomplete_bars_cannot_pass_freshness():
    _, old = contract.input_contract(bars("2003-09-10"), "5m", NOW)
    assert old["reason"] == "stale_input_bars"
    complete, good = contract.input_contract(bars(), "5m", NOW)
    assert good["ok"] is True
    assert complete[-1]["timestamp"] == "2026-10-07T15:55:00+00:00"
    assert good["target_at"] == "2026-10-07T16:10:00+00:00"


def test_daily_bar_is_not_completed_at_midnight():
    complete, _ = contract.input_contract(bars() + [{"timestamp": "2026-10-07", "close": 100}], "1d", NOW)
    assert complete == []
    assert contract.bar_close("2026-10-06", "1d", NOW).hour == 20


def test_holiday_and_early_close_are_exchange_aware():
    holiday = contract.parse_time("2026-12-25T17:00:00Z")
    assert contract.bar_close("2026-12-25", "1d", holiday) is None
    early = contract.parse_time("2026-11-27T20:00:00Z")
    assert contract.bar_close("2026-11-27", "1d", early).hour == 18


def test_snapshot_date_and_percent_units_are_explicit():
    assert kronos._snapshot_key("2026-10-08T00:30:00Z") == "kronos-latest-2026-10-07"
    rows = kronos._instrument_rows({"equity_live": [{"ticker": "LDOS", "unrealized_pct": 1.5}, {"ticker": "BA", "unrealized_pct": 0}]})
    assert [r["unrealized_pct"] for r in rows] == [1.5, 0]
    assert all(r["broker"] == "public" for r in rows)


def test_missing_or_invalid_ohlcv_is_not_synthesized():
    assert kronos._norm_candle({"timestamp": "2026-10-07", "close": 100}) is None
    assert kronos._norm_candle({"open": 100, "high": 90, "low": 80, "close": 95}) is None


@pytest.mark.asyncio
async def test_prediction_requests_latest_window_and_is_immutable(monkeypatch, db):
    from services import london_strategic_edge as lse
    requests = []
    async def candles(*args, **kwargs):
        requests.append(kwargs)
        return {"ok": True, "rows": list(reversed(bars()))}
    monkeypatch.setattr(lse, "candles", candles)
    a = await kronos.candle_forecast("SPY", persist=True)
    b = await kronos.candle_forecast("SPY", persist=True)
    assert a["ok"] and a["features"]["last_close"] == 130
    assert all(r["order"] == "desc" for r in requests)
    assert a["prediction_id"] == b["prediction_id"]
    assert len(db.kronos_candle_predictions.rows) == 1
    assert a["model_version"] == contract.MODEL_VERSION
    assert a["calibrated"] is False


@pytest.mark.asyncio
async def test_stale_provider_cannot_create_a_prediction(monkeypatch, db):
    from services import london_strategic_edge as lse
    async def candles(*args, **kwargs):
        return {"ok": True, "rows": bars("2003-09-10")}
    monkeypatch.setattr(lse, "candles", candles)
    result = await kronos.candle_forecast("SPY", persist=True)
    assert result["ok"] is False
    assert db.kronos_candle_predictions.rows == []


@pytest.mark.asyncio
async def test_accuracy_waits_for_exact_closed_target(monkeypatch, db):
    from services import london_strategic_edge as lse
    async def candles(*args, **kwargs):
        return {"ok": True, "rows": list(reversed(bars(count=35)))}
    monkeypatch.setattr(lse, "candles", candles)
    prediction = await kronos.candle_forecast("SPY", persist=True)
    pending = await kronos.candle_accuracy(persist=True)
    assert pending["scored_predictions"] == 0
    monkeypatch.setattr(kronos, "_now", lambda: contract.parse_time("2026-10-07T16:10:00Z"))
    mature = await kronos.candle_accuracy(persist=True)
    assert mature["scored_predictions"] == 1
    assert mature["recent"][0]["actual_at"] == "2026-10-07T16:05:00+00:00"
    assert mature["recent"][0]["prediction_id"] == prediction["prediction_id"]
    assert mature["overall"]["no_change_mae_pct"] is not None


@pytest.mark.asyncio
async def test_calendar_counts_distinct_predictions_not_snapshot_documents(monkeypatch, db):
    async def years(_):
        return [2026]
    monkeypatch.setattr(kronos, "_calendar_years", years)
    db.kronos_candle_predictions.rows = [{"prediction_id": "a", "model_version": contract.MODEL_VERSION, "generated_at": "2026-10-08T00:30:00+00:00", "symbol": "SPY", "timeframe": "5m", "forecast_pct": 1}]
    result = await kronos.calendar_month(2026, 10)
    day = next(d for d in result["days"] if d["date"] == "2026-10-07")
    assert day["predictions"] == 1 and day["resolved_predictions"] == 0
    assert result["summary"]["scored_days"] == 0


def test_postgres_snapshot_identity_does_not_move_with_refresh_timestamp():
    for generated in ["2026-10-07T14:00:00Z", "2026-10-07T14:05:00Z"]:
        assert postgres_store.doc_key("kronos_forecast_snapshots", {"snapshot_key": "day", "generated_at": generated}) == "day"


def test_scheduler_entry_point_still_exists():
    assert callable(kronos.dispatch_morning_forecast)


@pytest.mark.asyncio
async def test_portfolio_forecast_uses_ticker_model_not_pm_canned_returns(monkeypatch, db):
    async def context():
        return {"scan": {"results": []}, "pm": {"recommendations": [{"ticker": "LDOS", "action": "ACCUMULATE", "pm_score": 80}]},
                "equity_live": [{"ticker": "LDOS", "quantity": 1, "market_value": 100, "unrealized_pct": -1.5}],
                "public_portfolio_health": {"ok": True}}
    async def market(**kwargs):
        return {"ok": True, "input_asof": NOW.isoformat(), "direction": "FLAT"}
    async def prediction(*args, **kwargs):
        return {"ok": True, "direction": "DOWN", "forecast_pct": -0.7, "cone_low_pct": -1.2, "cone_high_pct": 0.2, "score": -20,
                "confidence": 45, "features": {"last_close": 100}, "input_asof": NOW.isoformat(), "target_at": "2026-10-08T20:00:00Z", "prediction_id": "LDOS-day"}
    monkeypatch.setattr(kronos, "_latest_context", context)
    monkeypatch.setattr(kronos, "market_forecast", market)
    monkeypatch.setattr(kronos, "candle_forecast", prediction)
    result = await kronos.forecast(persist=True)
    row = result["forecasts"][0]
    assert row["ticker"] == "LDOS" and row["forecast_pct"] == -0.7
    assert row["pm_action"] == "ACCUMULATE" and row["forecast_bias"] == "BEARISH"
    assert row["unrealized_pct"] == -1.5
    assert result["portfolio_day_cone"]["base_usd"] == -0.7
    assert db.kronos_pm_disagreements.rows[0]["prediction_id"] == "LDOS-day"


@pytest.mark.asyncio
async def test_pm_context_reads_real_saved_plan_never_replays_core(monkeypatch):
    from services import portfolio_manager, scanner
    async def saved():
        return {"recommendations": [{"ticker": "ABEO", "action": "WATCH"}], "source": "persisted"}
    async def scan():
        return {"results": [{"ticker": "LDOS"}]}
    def replay(*args, **kwargs):
        raise AssertionError("Kronos must not recreate PM decisions")
    monkeypatch.setattr(portfolio_manager, "latest_persisted_portfolio_plan", saved)
    monkeypatch.setattr(portfolio_manager, "evaluate_rows", replay)
    monkeypatch.setattr(scanner, "latest_scan", scan)
    context = await kronos._scan_pm_context()
    assert context["pm"]["recommendations"][0]["ticker"] == "ABEO"


@pytest.mark.asyncio
async def test_resolved_accuracy_survives_provider_outage(monkeypatch, db):
    from services import london_strategic_edge as lse
    async def unavailable(*args, **kwargs):
        raise AssertionError("A resolved prediction must not need the provider again")
    monkeypatch.setattr(lse, "candles", unavailable)
    db.kronos_candle_predictions.rows = [{"prediction_id": "a", "model_version": contract.MODEL_VERSION, "ok": True, "symbol": "SPY", "timeframe": "5m"}]
    db.kronos_candle_outcomes.rows = [{"prediction_id": "a", "symbol": "SPY", "timeframe": "5m", "regime": "CHOP", "actual_pct": 1, "forecast_pct": 0.5}]
    result = await kronos.candle_accuracy()
    assert result["scored_predictions"] == 1
    assert result["pending"] == 0
