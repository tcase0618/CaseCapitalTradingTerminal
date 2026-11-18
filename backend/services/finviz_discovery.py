"""Shared Finviz response parsing and bounded request pacing."""
from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
_request_lock = asyncio.Lock()
_last_request = 0.0


def number(value):
    text = str('' if value is None else value).replace(',', '').replace('%', '').strip()
    multiplier = 1
    if text[-1:].upper() in {'K', 'M', 'B'}:
        multiplier = {'K': 1e3, 'M': 1e6, 'B': 1e9}[text[-1].upper()]
        text = text[:-1]
    try:
        result = float(text) * multiplier
        return result if math.isfinite(result) else None
    except ValueError:
        return None


def grid_rows(soup, source):
    rows = []
    for row in soup.select('.fv-table_row[data-row-key]'):
        ticker = str(row.get('data-row-key') or '').strip().upper()
        if not re.fullmatch(r'[A-Z]{1,5}(?:[.\-][A-Z]{1,2})?', ticker):
            continue
        fields = {cell.get('data-column-id'): cell.get_text(' ', strip=True)
                  for cell in row.select('[data-column-id]')}
        rows.append({
            'ticker': ticker, 'company': fields.get('company') or ticker,
            'sector': fields.get('sector') or '-', 'price': number(fields.get('price')),
            'change_pct': number(fields.get('change')), 'volume': number(fields.get('volume')),
            'relative_volume': number(fields.get('relativeVolume') or fields.get('relVolume')),
            'float_shares': number(fields.get('sharesFloat')),
            'shares_outstanding': number(fields.get('sharesOutstanding2')),
            'short_float_pct': number(fields.get('shortInterestShare')),
            'short_ratio': number(fields.get('shortInterestRatio')),
            'average_volume': number(fields.get('averageVolume')),
            'source': source, 'signals': ['FINVIZ_UNIVERSE'],
            'finviz_parse_mode': 'column_id_grid',
            'discovery_fetched_at': datetime.now(timezone.utc).isoformat(),
        })
    return rows


def merge_observations(existing, incoming):
    """Do not replace measured fields with missing values from another view."""
    return {**existing, **{k: v for k, v in incoming.items() if v is not None and v != '' and v != '-'
                          and not (k == 'company' and v == incoming.get('ticker') and existing.get('company'))}}


async def fetch_page(client, url):
    global _last_request
    for attempt in range(2):
        async with _request_lock:
            await asyncio.sleep(max(0.0, 0.75 - (time.monotonic() - _last_request)))
            response = await client.get(url)
            _last_request = time.monotonic()
            if response.status_code == 429 and attempt == 0:
                delay = number(response.headers.get('Retry-After'))
                await asyncio.sleep(min(10.0, max(2.0, delay or 3.0)))
                continue
        if response.status_code != 200:
            logger.warning('Finviz discovery response status=%s url=%s', response.status_code, response.url)
        return response
    return response
