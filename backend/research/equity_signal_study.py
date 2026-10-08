"""Offline, descriptive equity signal study; no fills, orders, I/O, or optimizer.

Runner example::

    result = study(observations, daily_bars)
    coverage = result['strategies']['lane-a']['coverage']
    baseline = study_close_marks(observations, cached_closes, sessions=known_sessions)

cached_closes is {ticker: [{'date': 'YYYY-MM-DD', 'close': positive_price}]}.
Both interfaces require a ticker mapping with lists of row mappings. Invalid
container shapes raise ValueError; invalid dates/prices remain data exclusions.
Neither interface imports production code or fabricates missing OHLC.

Returns inspectable episode/horizon rows, coverage, and matched stop comparisons.
Horizon 1: premarket/non-session observations use the next session's close;
regular-session and after-close observations use the following session's close.
Intraday/after-close SPY entry proxy is that day's close (not executable at the
signal timestamp); premarket/non-session proxy is the first session's open.
Equity entry_price is always a supplied signal proxy, never a real fill.
RTH replay omits the entry-day post-signal path entirely: it cannot identify
entry-day stops or infer a floor from an entry-day intraday peak. Consequently
it is NOT a conservative full-trade simulation. RTH SPY same-day close is future
information relative to the signal, so alpha is a descriptive approximation.

Close-only baseline uses signal-session SPY close for RTH/after-close signals,
and the previous session close for premarket/non-session signals. This daily-close
approximation is not a contemporaneous entry or execution-matched alpha. If the
previous close is unavailable, equity marks survive but benchmark pairing does not.

Ratchet floor N starts at -10%. After a COMPLETED replay session reaches +N%,
the next session stop is max(entry*(1+N%), prior_peak*0.90). Daily low tests
the previously active stop before today's high can raise it. Gaps below a stop
exit at open, so even an activated +5% floor does NOT guarantee a +5% return.
Modes 5/10/20 are independent SINGLE-floor experiments, not a tiered schedule
and not an implementation of the production stop policy.
Costs are fixed round-trip bps deducted from gross simple returns, not estimates
of actual spreads, slippage, commissions, market impact, or execution feasibility.

Default closes are 16:00 New York; supply session_closes for early closes.
The stdlib-only modern US DST conversion supports dates from 2007 onward.
Prices must be positive, finite, coherent OHLC. Duplicate bar dates invalidate
that ticker/date. SPY's supplied dates define the calendar even if OHLC is bad.
Missing exact terminal bars are never forward-filled. Replay also requires every
intermediate bar. Episodic inactivity uses elapsed SPY-session index distance;
every valid sighting resets the clock, including suppressed sightings.
Supply sessions as an authoritative, complete ordered ISO-date calendar. Missing
SPY data anywhere in a supplied-calendar window invalidates that entire window.
A supplied calendar omitting an exported SPY date within its bounds is rejected.
Without sessions, undetectable omitted SPY dates can compress all session counts;
calendar completeness is unverified. Holidays cannot be inferred from weekdays.
"""

from bisect import bisect_left
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite

HORIZONS = (1, 3, 5, 10, 20)
COST_BPS = (0, 25, 100, 250)


def _number(value):
    if isinstance(value, bool):
        raise ValueError('boolean price')
    value = float(value)
    if not isfinite(value) or value <= 0:
        raise ValueError('price must be finite and positive')
    return value


def _timestamp(value):
    if isinstance(value, str) and value.endswith('-00:00'):
        raise ValueError('unknown local UTC offset')
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('aware ISO timestamp required')
    return stamp.astimezone(timezone.utc)


def _ny(stamp):
    year = stamp.year
    if year < 2007:
        raise ValueError('unsupported historical DST rules')
    march = date(year, 3, 8)
    november = date(year, 11, 1)
    start = march + timedelta(days=(6 - march.weekday()) % 7)
    end = november + timedelta(days=(6 - november.weekday()) % 7)
    start = datetime.combine(start, time(7), timezone.utc)
    end = datetime.combine(end, time(6), timezone.utc)
    offset = -4 if start <= stamp < end else -5
    return stamp.astimezone(timezone(timedelta(hours=offset)))


def _bars(raw, close_only=False):
    if not isinstance(raw, Mapping):
        raise ValueError('prices must be a ticker mapping with lists of row mappings')
    normalized = {}
    dates = set()
    for ticker, values in raw.items():
        if not isinstance(values, list):
            raise ValueError(f'prices[{ticker!r}] must be a list of row mappings')
        book, seen = {}, set()
        for index, value in enumerate(values):
            if not isinstance(value, Mapping):
                raise ValueError(f'prices[{ticker!r}][{index}] must be a row mapping')
            try:
                day = date.fromisoformat(value['date'])
            except (KeyError, TypeError, ValueError):
                continue
            if ticker == 'SPY':
                dates.add(day)
            if day in seen:
                book.pop(day, None)
                continue
            seen.add(day)
            try:
                fields = ('close',) if close_only else ('open', 'high', 'low', 'close')
                bar = {key: _number(value[key]) for key in fields}
                if not close_only and not (bar['low'] <= min(bar['open'], bar['close'])
                        <= max(bar['open'], bar['close']) <= bar['high']):
                    raise ValueError('incoherent OHLC')
                book[day] = bar
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
        normalized[ticker] = book
    return normalized, sorted(dates)


def _replay(entry, path, floor):
    stop, peak = entry * 0.90, entry
    for day, bar in path:
        if bar['open'] <= stop:
            return {'exit_price_proxy': bar['open'], 'exit_date': day.isoformat(), 'reason': 'stop_gap'}
        if bar['low'] <= stop:
            return {'exit_price_proxy': stop, 'exit_date': day.isoformat(), 'reason': 'stop_touch'}
        peak = max(peak, bar['high'])
        if floor is not None and peak >= entry * (1 + floor / 100):
            stop = max(stop, entry * (1 + floor / 100), peak * 0.90)
    return {'exit_price_proxy': path[-1][1]['close'], 'exit_date': path[-1][0].isoformat(), 'reason': 'horizon'}


def _costs(gross, costs):
    return {bps: gross - bps / 10000 for bps in costs}


def study(observations, daily_bars, *, inactivity_sessions=5,
          cost_bps=COST_BPS, session_closes=None, sessions=None):
    """Return a plain dict; call separately with inactivity_sessions=1/10.

    Holdout begins at floor(70% * supplied SPY sessions). Training observations
    are retained only when their maximum 20-session outcome ends before that
    boundary; the intervening observations are purged. No tuning is performed.
    Invalid session-close configuration raises ValueError; bad data is excluded.
    """
    return _study(observations, daily_bars, inactivity_sessions=inactivity_sessions,
                  cost_bps=cost_bps, session_closes=session_closes, sessions=sessions,
                  close_only=False)


def study_close_marks(observations, daily_closes, *, inactivity_sessions=5,
                      cost_bps=COST_BPS, session_closes=None, sessions=None):
    """Close-only baseline, same horizon/dedup/holdout rules as study.

    Input: {ticker: [{'date': ISO_date, 'close': price}]}, including SPY.
    Output: metadata, strategies (coverage/summaries), episodes, rows, excluded.
    No OHLC construction, replay, stop results, or intraday peak inference.
    See module docstring for benchmark and supplied-calendar limitations.
    """
    return _study(observations, daily_closes, inactivity_sessions=inactivity_sessions,
                  cost_bps=cost_bps, session_closes=session_closes, sessions=sessions,
                  close_only=True)


def _study(observations, raw_prices, *, inactivity_sessions, cost_bps,
           session_closes, sessions, close_only):
    if isinstance(inactivity_sessions, bool) or inactivity_sessions not in (1, 5, 10):
        raise ValueError('inactivity_sessions must be 1, 5, or 10')
    costs = tuple(cost_bps)
    if any(isinstance(bps, bool) or not isinstance(bps, (int, float))
           or not isfinite(bps) or bps < 0 for bps in costs):
        raise ValueError('cost bps must be finite and nonnegative')
    books, calendar = _bars(raw_prices, close_only)
    if sessions is not None:
        supplied = [date.fromisoformat(day) for day in sessions]
        if supplied != sorted(set(supplied)):
            raise ValueError('sessions must be unique and strictly chronological')
        if supplied and any(supplied[0] <= day <= supplied[-1] and day not in set(supplied)
                            for day in calendar):
            raise ValueError('supplied calendar omits exported SPY sessions')
        calendar = supplied
    indices = {day: i for i, day in enumerate(calendar)}
    closes = {}
    for key, value in (session_closes or {}).items():
        day, stamp = date.fromisoformat(key), _timestamp(value)
        local = _ny(stamp)
        if day not in indices or local.date() != day or local.time() <= time(9, 30):
            raise ValueError('invalid session close')
        closes[day] = local.time()
    split = int(len(calendar) * 0.70)
    strategies, valid, excluded = {}, [], []
    for position, obs in enumerate(observations):
        lane = obs.get('strategy_id')
        if not isinstance(lane, str) or not lane.strip():
            excluded.append({'input_index': position, 'reason': 'invalid_strategy'})
            continue
        stats = strategies.setdefault(lane, {'observations': 0, 'invalid': 0,
                                            'deduplicated': 0, 'episodes': 0,
                                            'coverage': {h: {'eligible': 0, 'marked': 0, 'paired': 0,
                                                             **({} if close_only else {'replay_matched': 0})}
                                                         for h in HORIZONS}})
        stats['observations'] += 1
        try:
            ticker = obs['ticker']
            if not isinstance(ticker, str) or not ticker.strip():
                raise ValueError('invalid ticker')
            stamp, entry = _timestamp(obs['observed_at']), _number(obs['entry_price'])
            local = _ny(stamp)
            day = local.date()
            idx = bisect_left(calendar, day)
            if day in indices:
                premarket = local.time() < time(9, 30)
                first = idx if premarket else idx + 1
                basis = 'open' if premarket else 'close'
                benchmark_idx = idx
                timing = 'premarket' if premarket else (
                    'after_close' if local.time() >= closes.get(day, time(16)) else 'regular_session')
            else:
                first, benchmark_idx, basis, timing = idx, idx, 'open', 'non_session'
            if close_only:
                basis = 'close'
                benchmark_idx = idx - 1 if timing in ('premarket', 'non_session') else idx
            if first >= len(calendar):
                raise ValueError('no future session')
            valid.append((stamp, position, ticker, lane, entry, first, benchmark_idx, basis, timing, idx))
        except (KeyError, TypeError, ValueError, OverflowError):
            stats['invalid'] += 1
            excluded.append({'input_index': position, 'reason': 'invalid_observation_or_calendar'})
    rows, episodes, last = [], [], {}
    for stamp, position, ticker, lane, entry, first, bench_idx, basis, timing, sighting_idx in sorted(valid):
        stats = strategies[lane]
        key = (ticker, lane)
        previous = last.get(key)
        last[key] = sighting_idx
        if previous is not None and sighting_idx - previous < inactivity_sessions:
            stats['deduplicated'] += 1
            continue
        stats['episodes'] += 1
        partition = 'holdout' if sighting_idx >= split else ('train' if first + 19 < split else 'purged')
        episode = {'input_index': position, 'ticker': ticker, 'strategy_id': lane,
                   'observed_at': stamp.isoformat(), 'entry_price_proxy': entry,
                   'timing': timing, 'partition': partition,
                   'first_mark_session': calendar[first].isoformat(),
                   'spy_entry_basis': basis,
                   'spy_entry_date': calendar[bench_idx].isoformat() if bench_idx >= 0 else None,
                   'spy_benchmark_approximation': True,
                   'spy_entry_uses_future_close': timing == 'regular_session',
                   **({} if close_only else {
                       'entry_day_post_signal_path_excluded': timing == 'regular_session',
                       'ratchet_mode_definition': 'independent_single_floor_not_tiered'})}
        episodes.append(episode)
        equity = books.get(ticker, {})
        spy = books.get('SPY', {})
        for horizon in HORIZONS:
            coverage = stats['coverage'][horizon]
            coverage['eligible'] += 1
            terminal_idx = first + horizon - 1
            row = dict(episode, horizon=horizon, terminal_date=None, status='outside_calendar')
            rows.append(row)
            if terminal_idx >= len(calendar):
                continue
            terminal = calendar[terminal_idx]
            row['terminal_date'] = terminal.isoformat()
            window_start = max(0, min(sighting_idx, bench_idx))
            if sessions is not None and any(day not in spy
                                           for day in calendar[window_start:terminal_idx + 1]):
                row['status'] = 'invalid_calendar_window_missing_spy'
                continue
            if terminal not in equity:
                row['status'] = 'missing_equity_terminal'
                continue
            coverage['marked'] += 1
            gross = equity[terminal]['close'] / entry - 1
            row.update(status='marked', gross_return=gross, modeled_net_returns=_costs(gross, costs))
            if bench_idx >= 0 and calendar[bench_idx] in spy and terminal in spy:
                benchmark = spy[terminal]['close'] / spy[calendar[bench_idx]][basis] - 1
                row.update(spy_gross_return=benchmark, paired_spy_alpha=gross - benchmark,
                           spy_modeled_net_returns=_costs(benchmark, costs),
                           modeled_paired_alpha={bps: gross - benchmark for bps in costs})
                coverage['paired'] += 1
            else:
                row['benchmark_status'] = 'missing_spy_entry_or_terminal'
            if close_only:
                continue
            days = calendar[first:terminal_idx + 1]
            if any(day not in equity for day in days):
                row['replay_status'] = 'missing_intermediate_bar'
                continue
            coverage['replay_matched'] += 1
            row['replay_status'] = 'matched'
            row['stops'] = {}
            path = [(day, equity[day]) for day in days]
            for label, floor in (('fixed_minus10', None), ('ratchet_floor5', 5),
                                 ('ratchet_floor10', 10), ('ratchet_floor20', 20)):
                replay = _replay(entry, path, floor)
                stopped = replay['exit_price_proxy'] / entry - 1
                replay.update(gross_return=stopped, unstopped_matched_gross_return=gross,
                              delta_vs_unstopped=stopped - gross,
                              modeled_net_returns=_costs(stopped, costs))
                row['stops'][label] = replay
    # Summaries never mix training, purged, and holdout outcomes or unpaired alpha.
    for lane, stats in strategies.items():
        stats['summaries'] = {}
        for partition in ('train', 'purged', 'holdout'):
            stats['summaries'][partition] = {}
            for horizon in HORIZONS:
                selected = [row for row in rows if row['strategy_id'] == lane
                            and row['partition'] == partition and row['horizon'] == horizon]
                marked = [row for row in selected if 'gross_return' in row]
                paired = [row for row in selected if 'paired_spy_alpha' in row]
                matched = [row for row in selected if row.get('replay_status') == 'matched']
                summary = {'eligible': len(selected), 'marked': len(marked), 'paired': len(paired),
                           **({} if close_only else {'replay_matched': len(matched)}),
                           'mean_gross_return': (sum(row['gross_return'] for row in marked) / len(marked)
                                                 if marked else None),
                           'mean_paired_spy_alpha': (sum(row['paired_spy_alpha'] for row in paired)
                                                     / len(paired) if paired else None)}
                if not close_only:
                    summary['stops'] = {}
                for label in (() if close_only else
                              ('fixed_minus10', 'ratchet_floor5', 'ratchet_floor10', 'ratchet_floor20')):
                    summary['stops'][label] = {
                        'matched_count': len(matched),
                        'mean_delta_vs_unstopped': (
                            sum(row['stops'][label]['delta_vs_unstopped'] for row in matched) / len(matched)
                            if matched else None)}
                stats['summaries'][partition][horizon] = summary
    return {'metadata': {'research_only': True, 'entry_prices_are_proxies': True,
                         'interface': 'close_only_baseline' if close_only else 'daily_ohlc_study',
                         'spy_benchmark': ('daily_close_approximation_not_execution_matched' if close_only
                                           else 'daily_open_close_entry_proxy_not_execution_matched'),
                         'calendar_source': 'supplied_sessions' if sessions is not None else 'inferred_spy_dates',
                         'calendar_completeness_verified': False,
                         'limitations': [
                             'SPY close for RTH entry is future information at signal time',
                             'Calendar completeness cannot be established from cached prices alone',
                             *(['Close-only data cannot support stops or intraday peaks'] if close_only else [
                                 'RTH replay omits entry-day post-signal stop path',
                                 'Replay is not a conservative full-trade simulation',
                                 'Ratchet modes are single floors, not the production tiered schedule'])],
                         'cost_basis': 'modeled_fixed_round_trip_bps', 'cost_bps': costs,
                         'inactivity_sessions': inactivity_sessions, 'horizons': HORIZONS,
                         'default_close': '16:00 America/New_York; override half-days',
                         'holdout_session': calendar[split].isoformat() if calendar else None,
                         'purge_sessions': 20, 'optimizer': False},
            'strategies': strategies, 'episodes': episodes, 'rows': rows, 'excluded': excluded}
