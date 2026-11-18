"""Independent offline discovery contracts; failures document unfixed defects."""
import asyncio

import pytest
from bs4 import BeautifulSoup

from services import finviz_discovery, lottery, portfolio_manager, strategy_screeners


def candidate(**overrides):
    return {
        "ticker": "TEST", "price": 4.0, "change_pct": 22.0,
        "volume": 2500000, "relative_volume": 8.0, "float_shares": 3000000,
        "signals": ["FINVIZ_UNIVERSE"], "source": "finviz_lottery_relvol",
        "discovery_fetched_at": "2026-11-18T14:30:00+00:00",
        "price_timestamp": "2026-11-18T14:30:01+00:00",
        "price_source": "offline_fixture", "quote_age_seconds": 2,
        **overrides,
    }


def qualified_rows(**overrides):
    scored = lottery.annotate_lottery_candidate(lottery._score_candidate(candidate(**overrides), set()))
    return strategy_screeners._lottery_family_rows([scored])


@pytest.mark.parametrize("value", [0, 0.0, "0", "0%"])
def test_numeric_zero_is_a_measurement(value):
    assert finviz_discovery.number(value) == 0


@pytest.mark.parametrize("value", [None, "-", "N/A", "nan", "inf"])
def test_unavailable_measurements_are_not_numbers(value):
    assert finviz_discovery.number(value) is None


def test_named_grid_missing_values_and_fetch_time_are_not_quote_time():
    html = ('<div class="fv-table_row" data-row-key="TEST">'
            '<div data-column-id="price">4</div>'
            '<div data-column-id="change">0%</div></div>')
    row = finviz_discovery.grid_rows(BeautifulSoup(html, "html.parser"), "fixture")[0]
    assert row["change_pct"] == 0
    assert row["volume"] is None
    assert row["relative_volume"] is None
    assert row["discovery_fetched_at"]
    assert row.get("price_timestamp") is None


def test_lottery_does_not_replace_missing_change_with_flat_change():
    result = lottery._score_candidate(candidate(change_pct=None), set())
    assert result["components"]["gap_surge"] == 0
    assert result["change_pct"] is None


def test_explicit_zero_change_beats_stale_alias():
    result = lottery._score_candidate(candidate(change_pct=0, day_change_pct=25), set())
    assert result["change_pct"] == 0
    assert result["components"]["gap_surge"] == 0


def test_source_only_finviz_badges_do_not_create_independent_groups():
    row = candidate(price=None, change_pct=None, volume=None, relative_volume=None,
                    float_shares=None, signals=["FINVIZ_UNIVERSE"],
                    sources=list(lottery.FINVIZ_BROAD_UNIVERSE_SCREENS))
    scored = lottery.annotate_lottery_candidate(lottery._score_candidate(row, set()))
    assert scored["components"]["universe"] > 0
    assert scored["independent_signal_count"] == 0
    assert strategy_screeners._lottery_family_rows([scored]) == []


@pytest.mark.parametrize("attention_signals", [["ATTENTION", "YAHOO_TRENDING"], ["X_FACTOR"], ["DARK_HORSE"]])
def test_attention_observation_is_not_two_independent_evidence_families(attention_signals):
    row = candidate(price=None, change_pct=None, volume=None, relative_volume=None,
                    float_shares=None, source="attention_lottery_screen",
                    signals=attention_signals, sources=["YAHOO_TRENDING"])
    scored = lottery.annotate_lottery_candidate(lottery._score_candidate(row, set()))
    assert scored["signal_groups"] == ["ATTENTION"]
    assert "CATALYST_RUNNER" not in scored["strategy_fits"]


def test_repeated_source_labels_do_not_raise_lottery_independent_count():
    one = lottery.annotate_lottery_candidate(lottery._score_candidate(candidate(), set()))
    many = lottery.annotate_lottery_candidate(lottery._score_candidate(
        candidate(sources=list(lottery.FINVIZ_BROAD_UNIVERSE_SCREENS)), set()))
    assert one["signal_groups"] == many["signal_groups"]
    assert one["independent_signal_count"] == many["independent_signal_count"]


def test_strategy_counts_are_lane_rows_but_pm_deduplicates_ticker():
    rows = strategy_screeners._dedupe(qualified_rows() * 2)
    assert len(rows) >= 2
    assert len({row["ticker"] for row in rows}) == 1
    summary = strategy_screeners._summary(rows)
    assert summary["total"] == len(rows)
    merged = portfolio_manager._merge_strategy_rows([], rows)
    assert len(merged) == 1
    assert len(merged[0]["strategy_views"]) == len(rows)
    assert len(set(merged[0]["signals"])) == rows[0]["independent_signal_count"]


def test_standalone_lottery_provenance_survives_strategy_and_pm():
    rows = qualified_rows()
    assert rows
    row = rows[0]
    assert row["price_timestamp"] == candidate()["price_timestamp"]
    assert row["discovery_fetched_at"] == candidate()["discovery_fetched_at"]
    assert row["raw_source"]["relative_volume"] == 8
    pm = portfolio_manager.evaluate_rows(portfolio_manager._merge_strategy_rows([], rows))[0]
    assert pm["price_evidence"]["provider_timestamp"] == candidate()["price_timestamp"]
    assert pm["independent_signal_count"] == row["independent_signal_count"]
    assert pm["provenance"]["sources"] == ["finviz_lottery_relvol"]


def test_pm_core_price_fallback_keeps_matching_quote_metadata():
    specialist = qualified_rows()[0]
    core = {"ticker": "TEST", "price": None, "signals": []}
    merged = portfolio_manager._merge_strategy_rows([core], [specialist])[0]
    assert merged["price"] == specialist["price"]
    assert merged.get("price_timestamp") == specialist["price_timestamp"]
    assert merged.get("price_source") == specialist["price_source"]


def test_pm_core_overlay_preserves_lottery_discovery_contract():
    specialist = qualified_rows()[0]
    core = {"ticker": "TEST", "price": 5, "signals": ["insider_cluster_buy"]}
    merged = portfolio_manager._merge_strategy_rows([core], [specialist])[0]
    pm = portfolio_manager.evaluate_rows([merged])[0]
    assert pm["price"] == 5
    assert pm["independent_signal_count"] == specialist["independent_signal_count"]
    assert pm["signal_groups"] == specialist["signal_groups"]
    assert pm["provenance"]["sources"] == specialist["provenance"]["sources"]


def test_pm_source_labels_are_not_scored_as_independent_evidence():
    row = {"ticker": "TEST", "signals": ["FINVIZ_UNIVERSE"], "evidence_signals": []}
    _, breakdown = portfolio_manager._pm_score(row, 4, 6, 3)
    assert breakdown["signal_component"] == 0


def test_pm_core_evidence_signals_do_not_hide_specialist_groups():
    specialist = qualified_rows()[0]
    core = {"ticker": "TEST", "price": 5, "signals": ["insider_cluster_buy"],
            "evidence_signals": ["insider_cluster_buy"]}
    merged = portfolio_manager._merge_strategy_rows([core], [specialist])[0]
    assert set(specialist["evidence_signals"]) <= set(portfolio_manager._signals(merged))


def test_quote_enrichment_does_not_invent_provider_timestamp(monkeypatch):
    from services import pricer

    async def quotes(*args, **kwargs):
        return {"TEST": {"price": 5, "source": "fixture", "provider_ts": None,
                         "age_seconds": None}}

    monkeypatch.setattr(pricer, "batch_live_price_meta", quotes)
    row = asyncio.run(lottery._attach_live_price_meta([candidate()]))[0]
    assert row["price"] == 5
    assert row["price_timestamp"] is None
    assert row["quote_age_seconds"] is None
    assert row["discovery_fetched_at"] == candidate()["discovery_fetched_at"]


def test_cycle_lottery_result_is_consumed_without_reading_mutable_current(monkeypatch):
    from services import stop_engine

    class NoDatabase:
        def __getattr__(self, name):
            raise AssertionError("Unexpected database access: " + name)

    async def no_rows(*args, **kwargs):
        return []

    async def no_learning():
        return None

    async def stop(**kwargs):
        return {"stop_price": 3, "stop_pct": 25, "breakdown": {}}

    monkeypatch.setattr(strategy_screeners, "get_db", lambda: NoDatabase())
    monkeypatch.setattr(strategy_screeners, "_lottery_learned_config", no_learning)
    for name in ("_pharma_rows", "_earnings_rows", "_sec_rows", "_independent_options_rows"):
        monkeypatch.setattr(strategy_screeners, name, no_rows)
    monkeypatch.setattr(lottery, "latest_dedicated_lottery", no_rows)
    monkeypatch.setattr(stop_engine, "compute_stop", stop)
    scored = lottery.annotate_lottery_candidate(lottery._score_candidate(candidate(target_blended=6), set()))
    payload = asyncio.run(strategy_screeners.run_all(
        scan={"finished_at": "fixture-cycle", "results": []}, persist=False,
        lottery_result={"candidates": [scored]}))
    assert payload["candidates"]
    assert all(row["raw_source"] == scored for row in payload["candidates"])
    assert all(row["pm_routable"] for row in payload["candidates"])
