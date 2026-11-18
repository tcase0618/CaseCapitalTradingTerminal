import asyncio
from types import SimpleNamespace

import pytest

from services import scrapers


def grid(short_pct=None, ticker="TEST"):
    fields = {"price": "4.00", "change": "12%", "volume": "2500000"}
    if short_pct is not None:
        fields["shortInterestShare"] = short_pct
    return f'<div class="fv-table_row" data-row-key="{ticker}">' + "".join(
        f'<div data-column-id="{key}">{value}</div>' for key, value in fields.items()
    ) + "</div>"


def legacy(extra_percent="22.5"):
    return (
        '<table><tr><td>1</td><td><a href="stock?t=LOTTO">L LOTTO</a></td>'
        '<td>Lottery Corp</td><td>Technology</td><td>Software</td><td>USA</td>'
        f'<td>25M</td><td>{extra_percent}</td><td>4.00</td>'
        '<td>12.0%</td><td>2.5M</td></tr></table>'
    )


def fetch(monkeypatch, html, min_pct=10):
    async def page(_client, url):
        assert "sh_short_o10" in url
        return SimpleNamespace(status_code=200, text=html)

    async def sleep(_delay):
        pass

    monkeypatch.setattr(scrapers, "fetch_page", page)
    monkeypatch.setattr(scrapers.asyncio, "sleep", sleep)
    return asyncio.run(scrapers.fetch_finviz_high_short_interest(min_pct=min_pct, limit=1))


@pytest.mark.parametrize("path", ["/stock", "stock", "/quote", "quote.ashx"])
@pytest.mark.parametrize("wrapper", ["{link}", "<table><tr><td>{link}</td></tr></table>"])
def test_navigation_is_not_high_short_membership(monkeypatch, path, wrapper):
    link = f'<a href="{path}?t=FAKE">FAKE</a>'
    assert fetch(monkeypatch, wrapper.format(link=link)) == []


def test_navigation_does_not_displace_real_grid_row(monkeypatch):
    html = '<a href="/stock?t=FAKE">FAKE</a>' + grid("28%")
    assert [row["ticker"] for row in fetch(monkeypatch, html)] == ["TEST"]


@pytest.mark.parametrize("value", [None, "-", "nan", "inf", ">10", "<25"])
def test_missing_or_unqualified_measurement_keeps_only_explicit_bound(monkeypatch, value):
    row = fetch(monkeypatch, grid(value))[0]
    assert row["short_float_pct"] is None
    assert row["short_float_membership_bound"] == {
        "operator": ">", "value": 10.0, "source_filter": "sh_short_o10"
    }


@pytest.mark.parametrize("value", [None, "-", ">10", "<25", "24.9%", "0%", "-2%"])
def test_higher_minimum_requires_qualifying_measurement(monkeypatch, value):
    assert fetch(monkeypatch, grid(value), min_pct=25) == []


@pytest.mark.parametrize("value", ["0%", "5%", "9.9%", "-2%"])
def test_below_minimum_measurement_cannot_fall_back_to_membership(monkeypatch, value):
    assert fetch(monkeypatch, grid(value)) == []


@pytest.mark.parametrize("value", ["25%", "30.5%", "105.69%"])
def test_qualifying_measurement_remains_exact(monkeypatch, value):
    row = fetch(monkeypatch, grid(value), min_pct=25)[0]
    assert row["short_float_pct"] == float(value.rstrip("%"))


@pytest.mark.parametrize("extra_percent", ["22.5", "5%", "75%", "-12%"])
def test_legacy_price_volume_preserved_without_guessing_short_float(monkeypatch, extra_percent):
    row = fetch(monkeypatch, legacy(extra_percent))[0]
    assert row["ticker"] == "LOTTO"
    assert row["price"] == 4.0
    assert row["volume"] == "2.5M"
    assert row["change_pct"] == 12.0
    assert row["short_float_pct"] is None
    assert row["short_float_membership_bound"]["value"] == 10.0


def test_legacy_arbitrary_percent_cannot_prove_higher_minimum(monkeypatch):
    assert fetch(monkeypatch, legacy("75%"), min_pct=25) == []


def test_legacy_mismatched_anchor_is_not_a_candidate(monkeypatch):
    html = legacy().replace("L LOTTO</a>", "L OTHER</a>")
    assert fetch(monkeypatch, html) == []


def test_rejected_grid_row_cannot_reenter_through_legacy_fallback(monkeypatch):
    html = legacy().replace("L LOTTO</a>", "L LOTTO</a>" + grid("5%", ticker="LOTTO"))
    assert fetch(monkeypatch, html) == []
