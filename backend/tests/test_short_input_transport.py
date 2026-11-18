import asyncio

from bs4 import BeautifulSoup

from services import finviz_discovery, scanner, squeeze


def test_measured_short_ratio_survives_source_and_core_handoff(monkeypatch):
    html = '<div class="fv-table_row" data-row-key="TEST"><div data-column-id="shortInterestShare">25%</div><div data-column-id="shortInterestRatio">4.5</div><div data-column-id="sharesFloat">4M</div><div data-column-id="averageVolume">100K</div></div>'
    row = finviz_discovery.grid_rows(BeautifulSoup(html, 'html.parser'), 'FINVIZ_HIGH_SHORT')[0]
    core = scanner._aggregate_market_signals({'high_short_interest': [row]})['TEST']
    inputs = scanner._squeeze_inputs({}, core['short_summary'])
    assert inputs['short_ratio'] == 4.5
    assert inputs['float_shares'] == 4000000
    assert inputs['avg_volume'] == 100000
    async def no_history(*args): return None
    monkeypatch.setattr(squeeze, '_short_30d_change', no_history)
    result = asyncio.run(squeeze.compute_squeeze('TEST', scanner._short_pct(core), inputs))
    assert result['components']['days_to_cover'] == 4.5
    assert result['components']['report_date_status'] == 'UNVERIFIED'


def test_source_average_volume_maps_for_matched_float_fallback():
    result = scanner._squeeze_inputs({}, {'float_shares':4000000, 'average_volume':100000})
    assert result['avg_volume'] == 100000
    assert result['float_shares'] == 4000000
    assert 'short_ratio' not in result
    assert 'float_shares' not in scanner._squeeze_inputs({}, {'shares_outstanding':9000000})


def test_observed_zero_dtc_is_not_replaced_by_derived_value(monkeypatch):
    async def no_history(*args): return None
    monkeypatch.setattr(squeeze, '_short_30d_change', no_history)
    result = asyncio.run(squeeze.compute_squeeze('TEST',25,{'short_ratio':0,'float_shares':4000000,'avg_volume':100000}))
    assert result['components']['days_to_cover'] == 0
