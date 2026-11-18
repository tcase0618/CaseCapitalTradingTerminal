import asyncio
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from services import finviz_discovery, lottery, portfolio_manager, pricer, public_api, scrapers, strategy_screeners, trade_floor


def grid(ticker='TEST', **values):
    values = {'price': '4.00', 'change': '21.5%', 'volume': '2,500,000', **values}
    return f'<div class="fv-table_row" data-row-key="{ticker}">' + ''.join(
        f'<div data-column-id="{key}">{value}</div>' for key, value in values.items()) + '</div>'


def test_grid_uses_named_columns_and_preserves_measurements():
    rows = finviz_discovery.grid_rows(BeautifulSoup(grid(company='Test Inc', relativeVolume='8.2', sharesFloat='3.5M', shortInterestShare='18.5%'), 'html.parser'), 'test')
    assert len(rows) == 1
    row = rows[0]
    assert row['ticker'] == 'TEST'
    assert row['volume'] == 2500000
    assert row['change_pct'] == 21.5
    assert row['relative_volume'] == 8.2
    assert row['float_shares'] == 3500000
    assert row['short_float_pct'] == 18.5
    assert row['finviz_parse_mode'] == 'column_id_grid'


def test_missing_metrics_remain_missing_and_numeric_zero_survives_merge():
    row = finviz_discovery.grid_rows(BeautifulSoup(grid(relativeVolume='-', change='0%'), 'html.parser'), 'test')[0]
    assert row['relative_volume'] is None
    merged = finviz_discovery.merge_observations({'relative_volume': 8, 'company': 'Test Inc', 'change_pct': 20}, row)
    assert merged['relative_volume'] == 8
    assert merged['company'] == 'Test Inc'
    assert merged['change_pct'] == 0
    assert finviz_discovery.number('nan') is None
    assert finviz_discovery.number('inf') is None


def test_legacy_overview_price_is_not_pe_or_market_cap():
    row = lottery._finviz_row_from_cells(['1','TEST','Test Inc','Technology','Software','USA','25M','22.5','4.00','12%','2.5M'], 'test')
    assert row['price'] == 4
    assert row['volume'] == 2500000
    assert row['relative_volume'] is None


def test_pagination_handles_new_grid_beyond_second_page(monkeypatch):
    calls = []

    async def page(_client, url):
        calls.append(url)
        ticker = ['AAA', 'BBB', 'CCC'][len(calls)-1]
        return type('Response', (), {'status_code': 200, 'text': grid(ticker)})()

    monkeypatch.setattr(lottery, 'fetch_page', page)
    rows = asyncio.run(lottery._fetch_finviz_url('https://finviz.com/screener.ashx?v=111', 'test', limit=60))
    assert len(calls) == 3
    assert [r['ticker'] for r in rows] == ['AAA', 'BBB', 'CCC']
    assert all(r['volume'] == 2500000 for r in rows)


def test_navigation_quote_links_are_not_candidates(monkeypatch):
    async def page(*args):
        return type('Response', (), {'status_code': 200, 'text': '<a href="/quote?t=FAKE">FAKE</a>'})()
    monkeypatch.setattr(lottery, 'fetch_page', page)
    assert asyncio.run(lottery._fetch_finviz_url('https://finviz.com', 'test', limit=20)) == []


def test_repeated_upstream_page_stops_pagination_without_inflating_count(monkeypatch):
    calls=[]
    async def page(*args):
        calls.append(1)
        return type('Response', (), {'status_code': 200, 'text': grid()})()
    monkeypatch.setattr(lottery, 'fetch_page', page)
    rows=asyncio.run(lottery._fetch_finviz_url('https://finviz.com', 'test', limit=160))
    assert len(calls)==2
    assert len(rows)==1


def test_scored_lottery_keeps_raw_evidence_for_final_classification(monkeypatch):
    row = {'ticker': 'TEST', 'price': 4, 'change_pct': 22, 'volume': 2500000,
           'relative_volume': 8, 'float_shares': 3000000, 'day_high': 4.1, 'day_low': 3,
           'signals': ['ATTENTION'], 'prior_runner_events': 2, 'source': 'finviz_lottery_relvol'}
    first = lottery._score_candidate(row, set())
    assert first['day_high'] == 4.1
    assert first['prior_runner_events'] == 2
    assert first['components']['attention'] == 8
    assert first['components']['structure'] > 0

    async def dilution(_ticker):
        return {'active': False}
    monkeypatch.setattr(lottery, '_dilution_flag', dilution)
    final = asyncio.run(lottery._enrich_candidate(first, set()))
    assert final['score'] == first['score']
    assert final['float_proxy'] == 3000000
    assert 'SERIAL_RUNNER' in final['strategy_fits']


def test_shared_high_short_parser_reads_grid_not_empty_anchor_rows(monkeypatch):
    async def page(*args):
        return type('Response', (), {'status_code': 200, 'text': grid(shortInterestShare='23.4%', sharesFloat='4M')})()
    monkeypatch.setattr(scrapers, 'fetch_page', page)
    rows = asyncio.run(scrapers.fetch_finviz_high_short_interest(limit=1))
    assert rows[0]['short_float_pct'] == 23.4
    assert rows[0]['float_shares'] == 4000000
    assert rows[0]['volume'] == 2500000


def test_quote_link_handles_current_path_and_logo_without_accepting_wrong_symbol():
    soup = BeautifulSoup('<a href="/quote?t=TEST"><span>T</span><span>TEST</span></a><a href="/quote?t=ABC">XYZ</a>', 'html.parser')
    assert scrapers._finviz_anchor_ticker(soup.find_all('a')[0]) == 'TEST'
    assert scrapers._finviz_anchor_ticker(soup.find_all('a')[1]) is None


def test_public_display_quote_cannot_invent_timestamp(monkeypatch):
    quote = {'lastPrice': 4}
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def quotes(self, symbols): return {'quotes': {'TEST': quote}}
    monkeypatch.setattr(public_api, 'configured', lambda: True)
    monkeypatch.setattr(public_api, 'PublicAPIClient', lambda **kwargs: Client())
    result = asyncio.run(pricer.live_price_meta('TEST'))
    assert result['price'] == 4
    assert result['provider_ts'] is None
    assert result['age_seconds'] is None
    assert result['fresh'] is False
    assert result['execution_eligible'] is False
    quote.update(lastTimestamp=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat(), quoteTime=datetime.now(timezone.utc).isoformat())
    result = asyncio.run(pricer.live_price_meta('TEST'))
    assert result['age_seconds'] > 7000
    assert result['fresh'] is False


def test_readonly_pm_uses_cycle_payload_without_rewriting_history(monkeypatch):
    writes = []
    class Collection:
        async def update_one(self, *args, **kwargs): writes.append(args)
    class DB:
        portfolio_manager_history = Collection()
    async def book(): return {'broker': 'public', 'equity': 100, 'cash_buying_power': 50, 'positions': []}
    async def regime(): return {'status': 'green'}
    async def forbidden(**kwargs): raise AssertionError('Do not rebuild specialist scans for an attached cycle')
    monkeypatch.setattr(portfolio_manager, 'get_db', lambda: DB())
    monkeypatch.setattr(portfolio_manager, '_equity_book_context', book)
    monkeypatch.setattr(trade_floor, 'regime_status', regime)
    monkeypatch.setattr(strategy_screeners, 'pm_rows', forbidden)
    scan = {'finished_at': '2026-11-18T13:00:00Z', 'results': [], 'strategy_payload': {'candidates': []}}
    asyncio.run(portfolio_manager.latest_portfolio_plan(scan=scan))
    assert writes == []
    asyncio.run(portfolio_manager.latest_portfolio_plan(scan=scan, persist_history=True))
    assert len(writes) == 1


def test_options_cap_ranks_evidence_instead_of_first_source(monkeypatch):
    async def fetch(url, source, limit):
        if 'momentum' in source:
            return [{'ticker':'LOW','price':4,'volume':500000}]
        if 'breakout' in source:
            return [{'ticker':'HIGH','price':5,'volume':2500000,'change_pct':40,'relative_volume':10}]
        return []
    async def empty(*args,**kwargs):return []
    async def prices(*args,**kwargs):return {}
    from services import x_factor,pharma
    monkeypatch.setattr(lottery,'_fetch_finviz_url',fetch)
    monkeypatch.setattr(scrapers,'fetch_finviz_high_short_interest',empty)
    monkeypatch.setattr(x_factor,'yahoo_trending_set',empty)
    monkeypatch.setattr(pharma,'get_pdufa_within_days',empty)
    monkeypatch.setattr(pricer,'batch_live_price_meta',prices)
    rows=asyncio.run(strategy_screeners._independent_options_rows(limit=1))
    assert [r['ticker'] for r in rows]==['HIGH']


def test_finviz_rate_limit_retry_is_bounded(monkeypatch):
    calls=[]
    class Client:
        async def get(self,url):
            calls.append(url)
            return type('Response',(),{'status_code':429,'headers':{'Retry-After':'500'},'url':url})()
    sleeps=[]
    async def sleep(seconds):sleeps.append(seconds)
    monkeypatch.setattr(finviz_discovery.asyncio,'sleep',sleep)
    result=asyncio.run(finviz_discovery.fetch_page(Client(),'https://finviz.com'))
    assert result.status_code==429
    assert len(calls)==2
    assert max(sleeps)<=10
