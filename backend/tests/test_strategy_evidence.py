from datetime import datetime, timezone
from types import SimpleNamespace
from copy import deepcopy
import pytest

from services import strategy_evidence as evidence, research_trials


class Collection:
    def __init__(self, rows=()):
        self.rows = deepcopy(list(rows))

    def find(self, query, projection=None):
        from services.postgres_store import _matches
        rows = [deepcopy(row) for row in self.rows if _matches(row, query)]
        class Cursor:
            def sort(self, *args):
                return self

            async def to_list(self, length):
                return rows if length is None else rows[:length]
        return Cursor()

    async def find_one(self, query, projection=None):
        return next(iter(await self.find(query).to_list(1)), None)

    async def update_one(self, query, update, upsert=False):
        from services.postgres_store import _matches
        row = next((r for r in self.rows if _matches(r, query)), None)
        if row is None and upsert:
            row = {**query, **deepcopy(update.get('$setOnInsert', {}))}
            self.rows.append(row)
        if row is not None:
            row.update(deepcopy(update.get('$set', {})))


@pytest.mark.asyncio
async def test_refresh_persists_once_and_provider_outage_preserves_outcomes(monkeypatch):
    from services import pricer
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    observed = (now - timedelta(days=8)).replace(hour=14, minute=0, second=0, microsecond=0)
    db = SimpleNamespace(pm_company_observations=Collection([mark(at=observed.isoformat())]),
                         strategy_evidence_outcomes=Collection(), bot_state=Collection())
    monkeypatch.setattr(evidence, 'get_db', lambda: db)
    async def history(*args):
        return {(now - timedelta(days=d)).date().isoformat(): 110 for d in range(35)}
    monkeypatch.setattr(pricer, 'get_history_range', history)
    first = await evidence.refresh(limit=100, lookback_days=90, max_symbols=10)
    saved = deepcopy(db.strategy_evidence_outcomes.rows)
    assert saved
    await evidence.refresh(limit=100, lookback_days=90, max_symbols=10)
    assert db.strategy_evidence_outcomes.rows == saved
    async def unavailable(*args):
        raise TimeoutError('provider offline')
    monkeypatch.setattr(pricer, 'get_history_range', unavailable)
    third = await evidence.refresh(limit=100, lookback_days=90, max_symbols=10)
    assert db.strategy_evidence_outcomes.rows == saved
    assert third['scorecards'] == first['scorecards']
    assert third['provider_errors']
    stored_report = await evidence.latest()
    assert stored_report['asset_basis'] == 'UNDERLYING_EQUITY'
    assert evidence.ASSET_LIMITATION in stored_report['limitations']


@pytest.mark.asyncio
async def test_registered_experiment_is_immutable(monkeypatch):
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    db = SimpleNamespace(research_experiments=Collection())
    monkeypatch.setattr(research_trials, 'get_db', lambda: db)
    payload = {'experiment_id': 'immutable', 'strategy_id': 'DAY2', 'hypothesis': 'test',
               'train_end': now.isoformat(), 'holdout_start': (now + timedelta(days=1)).isoformat(),
               'holdout_end': (now + timedelta(days=30)).isoformat(), 'metric': 'gross_return',
               'config': {'horizon_sessions': 5}}
    first = await research_trials.register(payload)
    assert await research_trials.register(payload) == first
    with pytest.raises(ValueError, match='immutable'):
        await research_trials.register({**payload, 'config': {'horizon_sessions': 20}})
    assert len(db.research_experiments.rows) == 1


def mark(ticker="AAA", at="2026-10-07T14:00:00Z", score=70, price=100, **extra):
    return {"observation_id": f"{ticker}:{at}", "ticker": ticker, "observed_at": at,
            "pm_score": score, "price": price, "source_scans": ["DAY2"], "action": "WATCH", **extra}


def test_session_targets_include_holidays_early_close_and_utc_boundary():
    assert evidence.target_close("2026-11-25T22:00:00Z", 1).isoformat() == "2026-11-27T18:00:00+00:00"
    assert evidence.target_close("2026-10-07T14:00:00Z", 1).isoformat() == "2026-10-07T20:00:00+00:00"
    assert evidence.target_close("2026-10-08T00:30:00Z", 1).isoformat() == "2026-10-08T20:00:00+00:00"


def test_same_ticker_repeated_sightings_not_independent():
    rows, excluded = evidence.episodes([mark(), mark(at="2026-10-07T15:00:00Z"), mark(at="2026-10-08T14:00:00Z")])
    assert len(rows) == 4
    assert excluded["overlapping_sighting"] == 5
    assert len({r["episode_id"] for r in rows}) == 4


def test_families_are_not_substituted_for_strategy_ids():
    rows, _ = evidence.episodes([mark(source_scans=[], strategy_views=[{"screener_id": "lottery_day2", "lane": "DAY2"}])])
    assert {r["strategy_id"] for r in rows} == {"LOTTERY_DAY2"}


def test_production_merge_pm_observation_to_episode_contract():
    from services import portfolio_manager, pm_brain, strategy_screeners
    row = strategy_screeners._base_row(
        row={'ticker': 'TEST', 'price': 10, 'target_blended': 14, 'stop_loss': 9,
             'signals': ['GAP/SURGE', 'RVOL']},
        screener_id='lottery_day2_continuation', family='LOTTERY', lane='DAY2_CONTINUATION', score=80)
    merged = portfolio_manager._merge_strategy_rows([], [row])
    recommendation = portfolio_manager.evaluate_rows(merged)[0]
    observation = pm_brain.build_observation(recommendation, cycle_id='fixture', observed_at='2026-10-07T14:00:00Z')
    rows, _ = evidence.episodes([observation])
    assert rows
    assert {r['strategy_id'] for r in rows} == {'LOTTERY_DAY2_CONTINUATION'}
    assert {r['scoring_version'] for r in rows} == {'pm_points_v1'}
    assert all(r['entry_mark'] == 10 and r['pm_score'] > 0 for r in rows)
    assert all(r['price_evidence']['status'] == 'UNVERIFIED_MARK' for r in rows)


@pytest.mark.parametrize("price", [None, 0, -1, float("nan"), float("inf")])
def test_invalid_entry_prices_excluded(price):
    rows, excluded = evidence.episodes([mark(price=price)])
    assert not rows and excluded["invalid_observation"] == 1


def test_exact_completed_close_only_no_nearest_day_or_future():
    episode = evidence.episodes([mark()])[0][0]
    before = datetime(2026, 10, 7, 19, 59, tzinfo=timezone.utc)
    assert evidence.resolve(episode, {"2026-10-07": 110}, {}, before)["status"] == "PENDING"
    after = datetime(2026, 10, 7, 21, tzinfo=timezone.utc)
    assert evidence.resolve(episode, {"2026-10-06": 110}, {}, after)["status"] == "MISSING_EXACT_TARGET_CLOSE"
    outcome = evidence.resolve(episode, {"2026-10-07": 110}, {"2026-10-06": 100, "2026-10-07": 102}, after)
    assert outcome["gross_return_pct"] == 10
    assert outcome["excess_return_pct"] == 8
    assert outcome["net_return_pct"] is None
    assert "prior close" in outcome["benchmark_mismatch"]


def test_rank_ties_and_constant_score_not_fake_ic():
    assert evidence.ranks([1, 1, 3]) == [1.5, 1.5, 3]
    assert evidence.spearman([1, 1, 1], [1, 2, 3]) is None
    assert evidence.spearman([1, 2, 3], [3, 2, 1]) == pytest.approx(-1)


def test_no_sample_not_zero_and_no_promotion():
    card = evidence.scorecard([])
    assert card["win_rate"] is None
    assert card["mean_daily_rank_ic"] is None
    assert card["mean_net_return_pct"] is None
    assert card["live_promotion_allowed"] is False


def test_cross_section_ic_not_pooling_repeated_dates():
    rows = [{"status": "RESOLVED", "pm_score": i, "gross_return_pct": i, "excess_return_pct": i,
             "cohort_date": "2026-10-07", "action": "WATCH"} for i in range(5)]
    card = evidence.scorecard(rows)
    assert card["mean_daily_rank_ic"] == pytest.approx(1)
    assert card["ic_days"] == 1
    assert card["ic_t_stat"] is None


def test_preregistration_no_past_holdout_or_nonfinite_config():
    now = datetime(2026, 10, 7, tzinfo=timezone.utc)
    payload = {"experiment_id": "A", "strategy_id": "DAY2", "hypothesis": "higher score predicts higher returns",
               "train_end": "2026-10-06T20:00:00Z", "holdout_start": "2026-10-08T13:30:00Z",
               "holdout_end": "2026-11-08T20:00:00Z", "metric": "net_expectancy", "config": {"floor": 5}}
    assert research_trials.validate_registration(payload, now)["live_promotion_allowed"] is False
    with pytest.raises(ValueError):
        research_trials.validate_registration({**payload, "holdout_start": "2026-10-06T22:00:00Z"}, now)
    with pytest.raises(ValueError):
        research_trials.validate_registration({**payload, "config": {"floor": float("nan")}}, now)
