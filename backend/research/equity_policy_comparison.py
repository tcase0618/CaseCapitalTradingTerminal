"""Offline first-open, fully matched policy counterfactuals; no I/O or production imports.

Runner::

    result = compare_policies(episodes, public_daily_ohlc, sessions=known_sessions)
    rows, exclusions = result['rows'], result['excluded']

episodes are ALREADY deduplicated dictionaries with ticker, strategy_id and
aware ISO observed_at (e.g. study_close_marks(... )['episodes']). No dedup,
optimizer, summaries, or holdout decisions are made here. Signal entry_price
is not used: alternative entry is the first 09:30 New York open STRICTLY after
the signal. At exactly 09:30, RTH, and after-hours, this is the next session.
Horizon 1 closes on the alternative entry day; 3/5/10/20 count from that day.
SPY enters at that SAME session open and exits at the SAME horizon close.

Actual supplied OHLC only. Each horizon requires coherent complete equity AND
SPY OHLC across its entire window, even if a modeled stop exits early. Never
forward-fill or synthesize OHLC. Supply a complete session calendar to avoid
compressed horizons from missing SPY dates; inferred calendar completeness is
unverified. No caller calendar can be independently verified without a source.

Daily adaptation of services/pm_ratchet.py:public_stop_policy, read 2026-10-08:
round gain_pct to 10 decimals; strict >5 => floor5, >10 => floor10, >20 =>
milestone=max(20,(ceil(gain_pct/10)-1)*10), floor20 at milestone20, otherwise
milestone-5. Round active stops to 6 decimals, never lower a previous stop.
No additional peak-minus-10% trail. Source version: uncapped_floors_v2.

Only prior COMPLETED entry-and-later session highs arm next-day floors.
Each day tests open/low against the existing stop BEFORE using its high.
Gap below stop exits at open; otherwise low touching stop exits at modeled stop.
Full-position exit; proceeds held flat (zero interest, no reinvestment) through
common horizon. This cannot reproduce intraday monitoring, liquidity or fills,
and is not guaranteed to be pessimistic relative to actual execution.
All costs are modeled fixed round-trip bps subtractions from simple returns;
the same cost assumption applies to SPY, so paired net alpha equals gross alpha.
Public API origin, adjustment consistency, and completeness are caller concerns.
"""

from bisect import bisect_left
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta, timezone
from math import ceil, isfinite

HORIZONS = (1, 3, 5, 10, 20)
COST_BPS = (0, 25, 100, 250)
# Exact prespecified hypotheses, not a search space.
HYPOTHESES = (
    ('no_stop', None, None),
    ('fixed_stop10', 10, None),
    ('fixed_stop8', 8, None),
    ('current_tier_floors10', 10, 5),
    ('current_tier_floors8', 8, 5),
    ('delayed_first_arm7_floor5_initial10', 10, 7),
)
POLICY_SOURCE_SHA256 = '4d84cab2848fe9d7d23417fb9a23e19fe27d475d3053217a8b60bac527ddbd5b'


def _price(value):
    if isinstance(value, bool):
        raise ValueError('boolean price')
    value = float(value)
    if not isfinite(value) or value <= 0:
        raise ValueError('nonpositive or nonfinite price')
    return value


def _signal(value):
    if isinstance(value, str) and value.endswith('-00:00'):
        raise ValueError('unknown local offset')
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('aware ISO timestamp required')
    stamp = stamp.astimezone(timezone.utc)
    if stamp.year < 2007:
        raise ValueError('modern US DST rules only')
    march, november = date(stamp.year, 3, 8), date(stamp.year, 11, 1)
    start = march + timedelta(days=(6 - march.weekday()) % 7)
    end = november + timedelta(days=(6 - november.weekday()) % 7)
    dst = (datetime.combine(start, time(7), timezone.utc) <= stamp
           < datetime.combine(end, time(6), timezone.utc))
    return stamp, stamp.astimezone(timezone(timedelta(hours=-4 if dst else -5)))


def _normalize(raw):
    if not isinstance(raw, Mapping):
        raise ValueError('daily_bars must be a mapping of ticker to list of mappings')
    for ticker, values in raw.items():
        if not isinstance(values, list) or any(not isinstance(value, Mapping) for value in values):
            raise ValueError(f'daily_bars[{ticker!r}] must be a list of mappings')
    books, spy_dates = {}, set()
    for ticker, values in raw.items():
        book, seen = {}, set()
        for value in values:
            try:
                day = date.fromisoformat(value['date'])
            except (KeyError, TypeError, ValueError):
                continue
            if ticker == 'SPY':
                spy_dates.add(day)
            if day in seen:
                book.pop(day, None)
                continue
            seen.add(day)
            try:
                bar = {field: _price(value[field]) for field in ('open', 'high', 'low', 'close')}
                if not (bar['low'] <= min(bar['open'], bar['close'])
                        <= max(bar['open'], bar['close']) <= bar['high']):
                    raise ValueError('incoherent OHLC')
                book[day] = bar
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
        books[ticker] = book
    return books, sorted(spy_dates)


def reconstructed_stop(entry, prior_peak, previous_stop=0.0, *, initial_loss_pct=10, first_arm_pct=5):
    """Pure reconstruction of current floors; 8% initial / 7% arm are hypotheses.

    first_arm_pct=7 changes ONLY the lowest tier's trigger, not its 5% floor or
    the >10%, >20%, and higher rules. Not an independent single-floor ratchet.
    """
    entry, prior_peak = _price(entry), _price(prior_peak)
    if not isfinite(previous_stop) or previous_stop < 0:
        raise ValueError('invalid previous stop')
    if initial_loss_pct not in (8, 10) or first_arm_pct not in (5, 7):
        raise ValueError('unsupported prespecified hypothesis')
    gain = round((prior_peak / entry - 1) * 100, 10)
    if not isfinite(gain):
        raise ValueError('nonfinite gain')
    floor = None
    if gain > 20:
        milestone = max(20, (ceil(gain / 10) - 1) * 10)
        floor = 20.0 if milestone == 20 else float(milestone - 5)
    elif gain > 10:
        floor = 10.0
    elif gain > first_arm_pct:
        floor = 5.0
    stop = max(previous_stop, entry * (1 - initial_loss_pct / 100),
               entry * (1 + floor / 100) if floor is not None else 0)
    stop = round(stop, 6)
    if not isfinite(stop) or stop <= 0:
        raise ValueError('invalid modeled stop')
    return {'active_stop': stop, 'profit_floor_pct': floor}


def _exit(entry, path, initial_loss, arm):
    stop = round(entry * (1 - initial_loss / 100), 6) if initial_loss is not None else None
    peak = entry
    if stop is not None and (not isfinite(stop) or stop <= 0):
        raise ValueError('invalid modeled initial stop')
    for index, (day, bar) in enumerate(path):
        if stop is not None:
            if bar['open'] < stop:
                return bar['open'], day, 'stop_gap', stop
            if bar['low'] <= stop:
                return stop, day, 'stop_touch', stop
        peak = max(peak, bar['high'])
        if arm is not None and index + 1 < len(path):
            stop = reconstructed_stop(entry, peak, stop, initial_loss_pct=initial_loss,
                                      first_arm_pct=arm)['active_stop']
    return path[-1][1]['close'], path[-1][0], 'horizon', stop


def compare_policies(episodes, daily_bars, *, sessions=None, cost_bps=COST_BPS,
                     horizons=HORIZONS):
    """Return metadata, flat per-episode/horizon/policy rows, and window exclusions.

    sessions: optional ordered unique ISO dates, authoritative calendar.
    horizons: nonempty iterable of unique positive integers; supplied order is retained.
    daily_bars: mapping of ticker to a list of bar mappings; wrong shapes raise
    ValueError, whereas missing/invalid bar fields remain window exclusions.
    All six policy rows for a window share EXACT entry, horizon, and SPY data.
    Invalid observations/window data are excluded rather than partly compared.
    """
    try:
        horizons = tuple(horizons)
    except TypeError as exc:
        raise ValueError('horizons must be a nonempty iterable of unique positive integers') from exc
    if not horizons or any(isinstance(horizon, bool) or not isinstance(horizon, int)
                           or horizon <= 0 for horizon in horizons):
        raise ValueError('horizons must be a nonempty iterable of unique positive integers')
    if len(set(horizons)) != len(horizons):
        raise ValueError('horizons must be unique')
    costs = tuple(cost_bps)
    if any(isinstance(bps, bool) or not isinstance(bps, (int, float))
           or not isfinite(bps) or bps < 0 for bps in costs):
        raise ValueError('costs must be finite nonnegative round-trip bps')
    books, calendar = _normalize(daily_bars)
    if sessions is not None:
        supplied = [date.fromisoformat(day) for day in sessions]
        supplied_set = set(supplied)
        if supplied != sorted(supplied_set):
            raise ValueError('sessions must be unique and ordered')
        if supplied and any(supplied[0] <= day <= supplied[-1] and day not in supplied_set
                            for day in calendar):
            raise ValueError('supplied calendar omits exported SPY sessions')
        calendar = supplied
    calendar_set = set(calendar)
    spy = books.get('SPY', {})
    rows, excluded = [], []
    for position, episode in enumerate(episodes):
        context = {'episode_index': position, 'input_index': episode.get('input_index', position),
                   'ticker': episode.get('ticker'), 'strategy_id': episode.get('strategy_id')}
        try:
            if any(not isinstance(context[key], str) or not context[key].strip()
                   for key in ('ticker', 'strategy_id')):
                raise ValueError('invalid ticker/lane')
            stamp, local = _signal(episode['observed_at'])
            first = bisect_left(calendar, local.date())
            if local.date() in calendar_set and local.time() >= time(9, 30):
                first += 1
            if first >= len(calendar):
                raise ValueError('no later RTH open')
        except (KeyError, TypeError, ValueError, OverflowError):
            excluded.append(dict(context, reason='invalid_signal_or_no_entry_session'))
            continue
        context.update(observed_at=stamp.isoformat(), entry_date=calendar[first].isoformat())
        equity = books.get(context['ticker'], {})
        for horizon in horizons:
            end = first + horizon - 1
            window = dict(context, horizon=horizon,
                          terminal_date=calendar[end].isoformat() if end < len(calendar) else None)
            if end >= len(calendar):
                excluded.append(dict(window, reason='outside_calendar'))
                continue
            days = calendar[first:end + 1]
            missing_equity = [day.isoformat() for day in days if day not in equity]
            missing_spy = [day.isoformat() for day in days if day not in spy]
            if missing_equity or missing_spy:
                excluded.append(dict(window, reason='missing_or_invalid_window_ohlc',
                                     missing_equity_dates=missing_equity, missing_spy_dates=missing_spy))
                continue
            entry, spy_entry = equity[days[0]]['open'], spy[days[0]]['open']
            path = [(day, equity[day]) for day in days]
            spy_return = spy[days[-1]]['close'] / spy_entry - 1
            unstopped = equity[days[-1]]['close'] / entry - 1
            matched = []
            try:
                for policy, initial_loss, arm in HYPOTHESES:
                    price, exit_day, reason, active_stop = _exit(entry, path, initial_loss, arm)
                    gross = price / entry - 1
                    alpha = gross - spy_return
                    if not all(isfinite(value) for value in (gross, alpha, spy_return, unstopped)):
                        raise ValueError('nonfinite computed return')
                    matched.append(dict(window, policy=policy, entry_price_proxy=entry,
                                        spy_entry_price_proxy=spy_entry, spy_entry_basis='same_session_open',
                                        exit_date=exit_day.isoformat(), exit_price_proxy=price,
                                        exit_reason=reason, active_stop=active_stop,
                                        common_horizon_value_proxy=price, gross_return=gross,
                                        unstopped_matched_gross_return=unstopped,
                                        delta_vs_unstopped=gross - unstopped,
                                        spy_gross_return=spy_return, paired_spy_alpha=alpha,
                                        modeled_net_returns={bps: gross - bps / 10000 for bps in costs},
                                        spy_modeled_net_returns={bps: spy_return - bps / 10000 for bps in costs},
                                        modeled_paired_alpha={bps: alpha for bps in costs},
                                        modeled_not_actual_fill=True, full_position_exit=True,
                                        proceeds_flat_no_reinvestment=True,
                                        prior_completed_session_highs_only=True))
            except (ValueError, OverflowError):
                excluded.append(dict(window, reason='invalid_modeled_arithmetic'))
                continue
            rows.extend(matched)
    return {'rows': rows, 'excluded': excluded,
            'metadata': {'research_only': True, 'optimizer': False, 'deduplication': 'caller_supplied_episodes',
                         'horizons': horizons, 'cost_bps': costs, 'cost_basis': 'fixed_round_trip_bps',
                         'entry_rule': 'first_0930_New_York_open_strictly_after_signal',
                         'horizon_rule': '1_is_entry_session_close',
                         'benchmark': 'SPY_same_entry_open_common_horizon_close',
                         'calendar_source': 'supplied_sessions' if sessions is not None else 'inferred_spy_dates',
                         'calendar_completeness_verified': False,
                         'policy_source': 'backend/services/pm_ratchet.py:public_stop_policy',
                         'policy_source_version': 'uncapped_floors_v2',
                         'policy_source_read_date': '2026-10-08',
                         'policy_source_sha256': POLICY_SOURCE_SHA256,
                         'hypotheses': [{'id': name, 'initial_loss_pct': loss, 'first_arm_strict_gt_pct': arm,
                                         'floor_rule': 'current_uncapped_tiers' if arm is not None else None}
                                        for name, loss, arm in HYPOTHESES],
                         'limitations': ['Not actual fills or an intraday monitor simulation',
                                         'Only prior completed daily highs activate floors',
                                         'No guarantee of profit floors under gaps or execution costs',
                                         'No liquidity, split adjustment, or data-origin validation',
                                         'Inferred calendars may omit sessions and compress horizons']}}
