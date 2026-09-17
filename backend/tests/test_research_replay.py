import pytest

from services import pm_backtest, pm_learning, pnl_tracker, research_replay


def test_episodeize_removes_repeated_same_episode_but_allows_reset():
    rows = [
        {"ticker": "ABC", "source_scan": "lottery_day2", "observed_at": "2026-09-01T14:00:00Z"},
        {"ticker": "ABC", "source_scan": "lottery_day2", "observed_at": "2026-09-03T14:00:00Z"},
        {"ticker": "ABC", "source_scan": "lottery_day2", "observed_at": "2026-09-10T14:00:00Z"},
        {"ticker": "ABC", "source_scan": "core", "observed_at": "2026-09-03T14:00:00Z"},
    ]

    episodes = research_replay.episodeize(rows, cooldown_days=5)

    assert [(r["ticker"], r["episode_id"]) for r in episodes] == [
        ("ABC", "ABC:CORE:1"),
        ("ABC", "ABC:LOTTERY_DAY2:1"),
        ("ABC", "ABC:LOTTERY_DAY2:2"),
    ]


def test_observation_quality_rejects_proxy_or_missing_exit_facts():
    valid, reasons = research_replay.observation_quality({
        "price": 10,
        "targets": {"target_blended": 12},
        "stop_loss": 9,
    })
    assert valid is True
    assert reasons == []

    valid, reasons = research_replay.observation_quality({
        "price": 10,
        "targets": {"target_blended": 14},
        "stop_loss": 9,
        "target_is_proxy": True,
    })
    assert valid is False
    assert reasons == ["proxy_target"]


def test_return_summary_reports_dispersion_and_missing_benchmark_coverage():
    summary = research_replay.summarize_returns([
        {"return_pct": 10, "benchmark_return_pct": 2},
        {"return_pct": -5, "benchmark_return_pct": -1},
        {"return_pct": None, "benchmark_return_pct": 0},
    ])

    assert summary["observations"] == 3
    assert summary["matured"] == 2
    assert summary["mean_return_pct"] == 2.5
    assert summary["median_return_pct"] == 2.5
    assert summary["mean_alpha_pct"] == 2.0
    assert summary["excluded"]["missing_return"] == 1


@pytest.mark.asyncio
async def test_scan_pick_ledger_keeps_an_immutable_cycle_observation(monkeypatch):
    class Collection:
        def __init__(self):
            self.calls = []

        async def update_one(self, query, update, upsert=False):
            self.calls.append((query, update, upsert))

    class DB:
        strategy_observations = Collection()
        signal_performance = Collection()

    async def no_log(*_args, **_kwargs):
        return None

    db = DB()
    monkeypatch.setattr(pnl_tracker, "get_db", lambda: db)
    monkeypatch.setattr(pnl_tracker, "log_activity", no_log)

    await pnl_tracker.record_scan_picks({
        "cycle_id": "cycle-1",
        "scan_finished_at": "2026-09-17T14:15:00Z",
        "results": [{
            "ticker": "ABC",
            "price": 10.5,
            "signals": ["RVOL", "GAP"],
            "targets": {"target_blended": 12.0},
            "stop_loss": 9.5,
            "strategy_scanner": {"screener_id": "lottery_day2", "family": "LOTTERY", "lane": "DAY2_CONTINUATION"},
        }],
    }, include_first_seen=False)

    observation = db.strategy_observations.calls[0][1]["$setOnInsert"]
    performance = db.signal_performance.calls[0][1]["$setOnInsert"]
    assert observation["cycle_id"] == "cycle-1"
    assert observation["observed_at"] == "2026-09-17T14:15:00Z"
    assert observation["target"] == 12.0
    assert observation["stop"] == 9.5
    assert performance["observed_at"] == "2026-09-17T14:15:00Z"
    assert performance["cycle_id"] == "cycle-1"


def test_pm_learning_uses_eastern_trading_date_for_overnight_scan():
    assert pm_learning._date_key({"finished_at": "2026-09-18T00:30:00Z"}) == "2026-09-17"


def test_research_report_keeps_raw_and_episode_metrics_separate():
    report = pm_backtest._research_report([
        {
            "ticker": "ABC", "strategy_id": "lottery_day2", "observed_at": "2026-09-01T14:00:00Z",
            "return_pct": 10, "benchmark_return_pct": 2, "replayable": True, "quality_reasons": [],
        },
        {
            "ticker": "ABC", "strategy_id": "lottery_day2", "observed_at": "2026-09-03T14:00:00Z",
            "return_pct": -10, "benchmark_return_pct": 0, "replayable": False, "quality_reasons": ["proxy_target"],
        },
        {
            "ticker": "ABC", "strategy_id": "lottery_day2", "observed_at": "2026-09-10T14:00:00Z",
            "return_pct": 5, "benchmark_return_pct": 1, "replayable": True, "quality_reasons": [],
        },
    ])

    lane = report["by_strategy"][0]
    assert lane["raw_sightings"]["matured"] == 3
    assert lane["episode_deduped"]["matured"] == 2
    assert lane["episode_quality_qualified"]["matured"] == 2
    assert report["quality_exclusion_counts"]["proxy_target"] == 1


def test_research_return_never_substitutes_a_longer_horizon_for_7d():
    assert pm_backtest._research_return({"return_30d": 22, "return_7d": None}) == (None, None)
    assert pm_backtest._research_return({"return_7d": 3.5, "return_30d": 22}) == (3.5, "7d")
