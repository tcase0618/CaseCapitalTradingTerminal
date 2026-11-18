import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services import options_desk, options_research, tail_hunter


def ticket():
    now = datetime.now(timezone.utc)
    return {
        'candidate_id': 'opt-TEST-1', 'cycle_id': 'cycle-1', 'ticker': 'TEST',
        'route': 'OPTION', 'pm_action': 'STARTER', 'generated_at': now.isoformat(),
        'research_options': {'data_provider': 'PUBLIC_OPTIONS', 'contract': {
            'contractSymbol': 'TEST261218C00010000', 'expiration': '2026-12-18',
            'bid': 1, 'ask': 1.1, 'quote_time': now.isoformat()}},
        'instrument': {'symbol': 'WRONG_REBUILT_SYMBOL'},
    }


def test_original_research_contract_and_ask_are_frozen():
    record = options_research.approval_record(ticket())
    assert record['symbol'] == 'TEST261218C00010000'
    assert record['entry_ask'] == 1.1
    assert record['entry_status'] == 'QUOTED_NOT_FILLED'


@pytest.mark.parametrize('field,value', [('route', 'EQUITY'), ('pm_action', 'WATCH'), ('cycle_id', None)])
def test_only_attributed_pm_options_approvals_recorded(field, value):
    row = ticket()
    row[field] = value
    assert options_research.approval_record(row) is None


@pytest.mark.parametrize('quote_time', [None, 'invalid', '2020-01-01T00:00:00Z'])
def test_missing_or_stale_entry_is_unpriced_not_deleted(quote_time):
    row = ticket()
    row['research_options']['contract']['quote_time'] = quote_time
    record = options_research.approval_record(row)
    assert record['entry_ask'] is None
    assert record['entry_status'] == 'UNPRICED_OR_UNSUPPORTED'


def test_review_is_ask_to_bid_not_midpoint_or_realized_pnl():
    row = options_research.approval_record(ticket())
    now = datetime.now(timezone.utc) + timedelta(seconds=5)
    quote = {'bid': 1.21, 'ask': 1.8, 'bidTimestamp': now.isoformat()}
    result = options_research.review_mark(row, quote, now)
    assert result['return_pct'] == 10
    assert result['status'] == 'QUOTED_NOT_REALIZED'


@pytest.mark.parametrize('quote', [{}, {'bid': 9}, {'bid': 9, 'bidTimestamp': '2020-01-01T00:00:00Z'}])
def test_review_missing_timestamp_or_old_mark_is_not_a_return(quote):
    result = options_research.review_mark(options_research.approval_record(ticket()), quote, datetime.now(timezone.utc))
    assert result['return_pct'] is None


def test_all_entry_surfaces_stop_before_db_or_broker_when_research_only(monkeypatch):
    monkeypatch.setenv('OPTIONS_RESEARCH_ONLY', 'true')
    monkeypatch.setattr(options_desk, 'OPTIONS_EXECUTION_ENABLED', True)

    def forbidden(*args, **kwargs):
        pytest.fail('research-only entry reached database or broker')

    monkeypatch.setattr(options_desk, 'get_db', forbidden)
    monkeypatch.setattr(tail_hunter, 'get_db', forbidden)
    assert not options_desk.options_execution_enabled()
    assert asyncio.run(options_desk.execute('candidate'))['reason'] == 'options_research_only'
    assert asyncio.run(options_desk.auto_execute_latest())['submitted'] == []
    assert not asyncio.run(tail_hunter.execute_tail('candidate'))['ok']


def test_recording_preserves_first_observation_and_exports_markdown(monkeypatch, tmp_path):
    records = {}

    class Cursor:
        def sort(self, *args):
            return self

        async def to_list(self, limit):
            return list(records.values())[:limit]

    class Collection:
        async def update_one(self, query, update, **kwargs):
            records.setdefault(query['_id'], deepcopy(update['$setOnInsert']))

        def find(self, *args):
            return Cursor()

    monkeypatch.setattr(options_research, 'get_db', lambda: SimpleNamespace(options_research_approvals=Collection()))
    monkeypatch.setenv('OPTIONS_RESEARCH_DIR', str(tmp_path))
    original = ticket()
    changed = deepcopy(original)
    changed['research_options']['contract']['ask'] = 1.2

    async def run():
        await options_research.record_candidates([original])
        await options_research.record_candidates([changed])

    asyncio.run(run())
    assert len(records) == 1
    assert next(iter(records.values()))['entry_ask'] == 1.1
    files = list(tmp_path.glob('*.md'))
    assert len(files) == 1
    assert 'TEST261218C00010000' in files[0].read_text(encoding='utf-8')
    assert 'not a fill' in files[0].read_text(encoding='utf-8')


@pytest.mark.asyncio
async def test_real_candidate_builder_archives_all_before_display_limit(monkeypatch):
    from services import options_contract_learning, options_engine

    monkeypatch.setenv('OPTIONS_RESEARCH_ONLY', 'true')
    collection = SimpleNamespace(find_one=AsyncMock(return_value={}), delete_many=AsyncMock(), insert_many=AsyncMock())
    db = SimpleNamespace(options_lane_throttle=collection, options_desk_candidates=collection,
                         options_desk_candidate_history=collection)
    monkeypatch.setattr(options_desk, 'get_db', lambda: db)
    monkeypatch.setattr(options_contract_learning, 'record_selection', AsyncMock(return_value=None))
    refresh = AsyncMock(side_effect=AssertionError('Alpaca execution research rebuild must not run'))
    monkeypatch.setattr(options_engine, 'analyze_ticker', refresh)
    archive = AsyncMock(return_value={'approved': 2})
    monkeypatch.setattr(options_research, 'record_candidates', archive)
    monkeypatch.setattr(options_desk, '_route', lambda *args: ('OPTION', []))
    original = ticket()
    scan = {'cycle_id': 'frozen-cycle', 'finished_at': original['generated_at'], 'results': []}
    recommendations = [{'ticker': symbol, 'action': 'STARTER', 'pm_score': 80,
                        'risk_reward': 3, 'options': original['research_options']}
                       for symbol in ['TEST', 'NEXT']]
    result = await options_desk.build_candidates(limit=1, persist=True, scan=scan,
                                                  pm_recommendations=recommendations)
    refresh.assert_not_awaited()
    assert len(archive.call_args.args[0]) == 2
    assert all(row['cycle_id'] == 'frozen-cycle' for row in archive.call_args.args[0])
    assert len(result['candidates']) == 1
    assert not result['candidates'][0]['manual_fire_ready']
    assert result['options_research_archive']['approved'] == 2


@pytest.mark.asyncio
async def test_weekly_review_reads_public_and_exports_without_mutations(monkeypatch, tmp_path):
    from services import public_api

    record = options_research.approval_record(ticket())
    updates = []

    class Collection:
        def find(self, *args):
            return SimpleNamespace(to_list=AsyncMock(return_value=[record]))

        async def update_one(self, query, update, **kwargs):
            updates.append(update['$set'])

    db = SimpleNamespace(options_research_approvals=Collection(), bot_state=Collection())
    monkeypatch.setattr(options_research, 'get_db', lambda: db)
    monkeypatch.setenv('OPTIONS_RESEARCH_DIR', str(tmp_path))
    export = AsyncMock()
    monkeypatch.setattr(options_research, 'export_day', export)

    class ReadOnlyClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def option_chain(self, ticker, expiration):
            return {'options': [{'symbol': record['symbol'], 'bid': 1.21,
                                 'bidTimestamp': datetime.now(timezone.utc).isoformat()}]}

    monkeypatch.setattr(public_api, 'PublicAPIClient', ReadOnlyClient)
    result = await options_research.weekly_review()
    assert result['marked'] == 1
    assert updates[0]['review']['return_pct'] == 10
    export.assert_awaited_once_with(record['day_et'])
    assert (tmp_path / 'latest-sunday-review.md').exists()


@pytest.mark.asyncio
async def test_research_chain_never_falls_back_to_alpaca(monkeypatch):
    from services import options_engine

    monkeypatch.setenv('OPTIONS_RESEARCH_ONLY', 'true')
    public = AsyncMock(return_value=None)
    alpaca = AsyncMock(side_effect=AssertionError('no Alpaca fallback during research pause'))
    monkeypatch.setattr(options_engine, '_fetch_public_options_data', public)
    monkeypatch.setattr(options_engine, '_fetch_alpaca_options_data', alpaca)
    assert await options_engine.get_options_data('TEST', execution_preferred=True) is None
    public.assert_awaited_once()
    alpaca.assert_not_awaited()
