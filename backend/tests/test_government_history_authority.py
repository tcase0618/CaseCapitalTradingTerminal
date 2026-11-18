import asyncio
from datetime import date, timedelta

import pytest

from services import usaspending


@pytest.mark.parametrize('payload', [
    None, {}, {'results': None}, {'results': [{}] * 100},
    {'results': [{}], 'page_metadata': {'hasNext': True}},
    {'results': [{}] * 100, 'page_metadata': 'invalid'},
])
def test_unknown_or_truncated_history_is_not_an_empty_valid_history(monkeypatch, payload):
    async def post(*args):
        return payload

    monkeypatch.setattr(usaspending, '_post', post)
    with pytest.raises(RuntimeError):
        asyncio.run(usaspending.fetch_recipient_history('Test Company'))


def test_explicit_complete_page_is_accepted(monkeypatch):
    rows = [{'Award ID': str(i)} for i in range(100)]

    async def post(*args):
        return {'results': rows, 'page_metadata': {'hasNext': False}}

    monkeypatch.setattr(usaspending, '_post', post)
    assert asyncio.run(usaspending.fetch_recipient_history('Test Company')) == rows


@pytest.mark.parametrize('history, expected_new, expected_status', [
    (None, False, 'UNAVAILABLE_OR_INCOMPLETE'),
    ([], False, 'EMPTY_HISTORY_UNVERIFIED'),
    ([{'Awarding Agency': 'TEST', 'Period of Performance Start Date': 'invalid'}],
     False, 'HISTORY_FIELDS_UNVERIFIED'),
    ([{'Awarding Agency': 'TEST', 'Period of Performance Start Date':
       (date.today() - timedelta(days=60)).isoformat()}], False, 'AVAILABLE'),
    ([{'Awarding Agency': 'TEST', 'Period of Performance Start Date':
       date.today().isoformat()}], True, 'AVAILABLE'),
])
def test_new_winner_requires_usable_history(monkeypatch, history, expected_new, expected_status):
    award = {
        'Award ID': 'A', 'Recipient Name': 'LEIDOS', 'Awarding Agency': 'TEST',
        'Award Amount': 20000000,
        'Period of Performance Start Date': date.today().isoformat(),
    }

    async def awards(*args, **kwargs):
        return [award]

    async def recipient(*args, **kwargs):
        if history is None:
            raise RuntimeError('unavailable')
        return history

    async def obligations(*args, **kwargs):
        return {}

    monkeypatch.setattr(usaspending, 'fetch_awards', awards)
    monkeypatch.setattr(usaspending, 'fetch_recipient_history', recipient)
    monkeypatch.setattr(usaspending, 'fetch_agency_obligations_by_month', obligations)
    result = asyncio.run(usaspending.detect_gov_signals())
    row = result['by_ticker']['LDOS']
    assert ('NEW_WINNER' in row['signals']) is expected_new
    assert row['gov_summary']['new_winner_history_status'] == expected_status
