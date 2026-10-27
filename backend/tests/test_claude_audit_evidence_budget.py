"""Offline provider-budget fairness and honest unresolved evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services import postgres_store, pricer, strategy_evidence as evidence
from test_strategy_evidence import Collection


def test_attempt_identity_keeps_same_ticker_episodes_separate():
    first = {'version': evidence.VERSION, 'episode_id': 'first', 'ticker': 'S000', 'last_attempted_at': 'old'}
    second = {**first, 'episode_id': 'second'}
    key = postgres_store.doc_key('strategy_evidence_attempts', first)
    assert key != postgres_store.doc_key('strategy_evidence_attempts', second)
    assert key == postgres_store.doc_key('strategy_evidence_attempts', {**first, 'last_attempted_at': 'new'})
    assert key != postgres_store.doc_key('strategy_evidence_attempts', {**first, 'version': 'other'})


@pytest.fixture
def budget_case(monkeypatch):
    now = datetime.now(timezone.utc)
    rows = [dict(episode_id=f'episode-{n}', ticker=f'S{n:03}', version=evidence.VERSION,
                 target_at=(now - timedelta(days=2)).isoformat(),
                 target_date=(now - timedelta(days=2)).date().isoformat(),
                 observed_at=(now - timedelta(days=8)).isoformat(), entry_mark=100, pm_score=50,
                 strategy_id='OFFLINE', horizon_sessions=1, scoring_version='fixture', mode='research',
                 cohort_date=(now - timedelta(days=8)).date().isoformat()) for n in range(300)]
    db = SimpleNamespace(pm_company_observations=Collection(), strategy_evidence_outcomes=Collection(),
                         strategy_evidence_attempts=Collection(), bot_state=Collection())
    state = SimpleNamespace(rows=rows, db=db, calls=[], unavailable=set(), benchmark_missing=False)
    monkeypatch.setattr(evidence, 'get_db', lambda: db)
    monkeypatch.setattr(evidence, 'episodes', lambda observations: (deepcopy(state.rows), {}))

    async def history(symbol, *args):
        state.calls.append(symbol)
        if symbol in state.unavailable:
            raise TimeoutError('offline provider fixture')
        if symbol == 'SPY' and state.benchmark_missing:
            return {}
        return {(now - timedelta(days=n)).date().isoformat(): 100 if symbol == 'SPY' else 110 for n in range(35)}

    monkeypatch.setattr(pricer, 'get_history_range', history)
    return state


@pytest.mark.asyncio
async def test_never_attempted_due_symbols_are_ordered_by_oldest_target(budget_case):
    state = budget_case
    state.rows = state.rows[:2]
    state.rows[1]['target_at'] = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    await evidence.refresh(max_symbols=1)
    assert [s for s in state.calls if s != 'SPY'] == ['S001']


@pytest.mark.asyncio
async def test_missing_early_symbols_cannot_starve_300_symbol_budget(budget_case):
    state = budget_case
    state.unavailable = {row['ticker'] for row in state.rows}
    for _ in range(3):
        await evidence.refresh(max_symbols=120)
    assert len(set(state.calls) - {'SPY'}) == 300
    assert len(state.db.strategy_evidence_attempts.rows) == 300
    assert not state.db.strategy_evidence_outcomes.rows
    assert all(row['status'] == 'MISSING_EXACT_TARGET_CLOSE' for row in state.db.strategy_evidence_attempts.rows)
    assert all(row['gross_return_pct'] is None for row in state.db.strategy_evidence_attempts.rows)


@pytest.mark.asyncio
async def test_300_available_symbols_resolve_within_three_budgeted_runs(budget_case):
    for _ in range(3):
        await evidence.refresh(max_symbols=120)
    assert len(budget_case.db.strategy_evidence_outcomes.rows) == 300


@pytest.mark.asyncio
@pytest.mark.parametrize('missing', ['symbol', 'benchmark'])
async def test_unresolved_attempt_is_persisted_and_retried_after_recovery(budget_case, missing):
    state = budget_case
    state.rows = state.rows[:1]
    state.unavailable = {'S000'} if missing == 'symbol' else set()
    state.benchmark_missing = missing == 'benchmark'
    await evidence.refresh(max_symbols=1)
    assert not state.db.strategy_evidence_outcomes.rows
    attempt = state.db.strategy_evidence_attempts.rows[0]
    assert attempt['status'] == ('MISSING_EXACT_TARGET_CLOSE' if missing == 'symbol' else 'RESOLVED_GROSS_ONLY')
    assert attempt['last_attempted_at']
    state.unavailable.clear()
    state.benchmark_missing = False
    await evidence.refresh(max_symbols=1)
    assert state.db.strategy_evidence_outcomes.rows[0]['status'] == 'RESOLVED'
    assert state.db.strategy_evidence_attempts.rows[0]['status'] == 'RESOLVED'


@pytest.mark.asyncio
async def test_budget_deferrals_are_counted_without_fabricated_attempts(budget_case):
    budget_case.rows = budget_case.rows[:2]
    report = await evidence.refresh(max_symbols=1)
    assert report['provider_deferred_symbols'] == 1
    assert report['provider_deferred_episodes'] == 1
    assert len(budget_case.db.strategy_evidence_attempts.rows) == 1


@pytest.mark.asyncio
async def test_future_episode_is_not_attempted_or_counted_as_budget_deferred(budget_case):
    state = budget_case
    state.rows = state.rows[:1]
    state.rows[0]['target_at'] = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    report = await evidence.refresh(max_symbols=1)
    assert 'S000' not in state.calls
    assert not state.db.strategy_evidence_attempts.rows
    assert report['provider_deferred_episodes'] == 0
    assert report['provider_deferred_symbols'] == 0


@pytest.mark.asyncio
async def test_multiple_due_episodes_share_one_symbol_budget_slot(budget_case):
    state = budget_case
    state.rows = state.rows[:2]
    state.rows[1]['ticker'] = 'S000'
    await evidence.refresh(max_symbols=1)
    assert state.calls.count('S000') == 1
    assert len(state.db.strategy_evidence_attempts.rows) == 2
    assert len(state.db.strategy_evidence_outcomes.rows) == 2
