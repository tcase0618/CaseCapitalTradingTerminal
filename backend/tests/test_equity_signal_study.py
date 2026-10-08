"""Standalone stdlib suite: python -B backend/tests/test_equity_signal_study.py."""

import importlib.util
from collections import UserDict
from datetime import date, timedelta
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    'equity_signal_study', Path(__file__).resolve().parents[1] / 'research' / 'equity_signal_study.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
study = MODULE.study
study_close_marks = MODULE.study_close_marks


def fixtures(count=80):
    days = []
    day = date(2026, 1, 5)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    bars = {ticker: [{'date': day, 'open': 100, 'high': 102, 'low': 98, 'close': 101}
                     for day in days] for ticker in ('SPY', 'AAA')}
    return days, bars


def observation(day='2026-01-05', clock='10:00:00-05:00', **changes):
    return dict({'ticker': 'AAA', 'strategy_id': 'lane',
                 'observed_at': day + 'T' + clock, 'entry_price': 100}, **changes)


class EquityStudyTests(unittest.TestCase):
    def test_price_input_requires_ticker_mapping(self):
        for function in (study, study_close_marks):
            for prices in (None, [], 'SPY', 100):
                with self.subTest(interface=function.__name__, prices=prices):
                    with self.assertRaisesRegex(ValueError, '^prices must be a ticker mapping'):
                        function([], prices)

    def test_price_values_require_lists_not_close_maps(self):
        days, bars = fixtures()
        invalid_values = ({days[0]: 101}, {}, tuple(bars['AAA']), iter(bars['AAA']), None, 'bad', 100)
        for function in (study, study_close_marks):
            for ticker in ('SPY', 'AAA'):
                for values in invalid_values:
                    prices = dict(bars, **{ticker: values})
                    with self.subTest(interface=function.__name__, ticker=ticker, values=values):
                        with self.assertRaisesRegex(
                                ValueError, rf"^prices\['{ticker}'\] must be a list of row mappings$"):
                            function([observation()], prices, sessions=days)

    def test_price_rows_require_mappings_and_report_index(self):
        days, bars = fixtures()
        for function in (study, study_close_marks):
            for value in (None, 'bad', 100, [], True):
                prices = dict(bars, AAA=[bars['AAA'][0], value])
                with self.subTest(interface=function.__name__, row=value):
                    with self.assertRaisesRegex(
                            ValueError, r"^prices\['AAA'\]\[1\] must be a row mapping$"):
                        function([observation()], prices, sessions=days)

    def test_price_mapping_implementations_and_empty_lists_are_supported(self):
        days, bars = fixtures()
        prices = UserDict({ticker: [UserDict(row) for row in rows]
                           for ticker, rows in bars.items()})
        for function in (study, study_close_marks):
            with self.subTest(interface=function.__name__):
                result = function([observation()], prices, sessions=days)
                self.assertEqual(result['rows'][0]['status'], 'marked')
                self.assertEqual(function([], {'SPY': [], 'AAA': []})['rows'], [])

    def test_well_shaped_bad_price_rows_remain_data_quality_exclusions(self):
        days, bars = fixtures()
        for function in (study, study_close_marks):
            for value in (None, 0, -1, True, float('nan'), float('inf'), 'bad'):
                prices = {ticker: [dict(row) for row in rows] for ticker, rows in bars.items()}
                prices['AAA'][1]['close'] = value
                with self.subTest(interface=function.__name__, close=value):
                    result = function([observation()], prices, sessions=days)
                    self.assertEqual(result['rows'][0]['status'], 'missing_equity_terminal')
                    self.assertNotIn('gross_return', result['rows'][0])
            for row in ({}, {'date': 'bad', 'close': 101}, {'date': days[1]}):
                prices = dict(bars, AAA=[row])
                with self.subTest(interface=function.__name__, row=row):
                    result = function([observation()], prices, sessions=days)
                    self.assertEqual(result['rows'][0]['status'], 'missing_equity_terminal')

    def test_horizons_and_timezone(self):
        days, bars = fixtures()
        for clock, offset in [('08:00:00-05:00', 0), ('10:00:00-05:00', 1),
                              ('16:00:00-05:00', 1), ('23:00:00-05:00', 1),
                              ('20:00:00+00:00', 1)]:
            with self.subTest(clock=clock):
                result = study([observation(clock=clock)], bars)
                for row in result['rows']:
                    self.assertEqual(row['terminal_date'], days[offset + row['horizon'] - 1])
        result = study([observation('2026-01-10')], bars)
        self.assertEqual(result['rows'][0]['terminal_date'], '2026-01-12')

    def test_duplicates_first_chronological_and_inactivity(self):
        days, bars = fixtures()
        observations = [observation(days[0], '15:00:00-05:00', entry_price=105),
                        observation(days[0], '08:00:00-05:00'),
                        observation(days[4]), observation(days[8]), observation(days[13])]
        result = study(observations, bars)
        self.assertEqual([e['input_index'] for e in result['episodes']], [1, 4])
        self.assertEqual(result['strategies']['lane']['deduplicated'], 3)
        self.assertEqual(len(study(observations, bars, inactivity_sessions=1)['episodes']), 4)
        self.assertEqual(len(study(observations, bars, inactivity_sessions=10)['episodes']), 1)
        observations.append(observation(days[0], strategy_id='other'))
        self.assertEqual(study(observations, bars)['strategies']['other']['episodes'], 1)

    def test_missing_exact_terminal_and_intermediate(self):
        days, bars = fixtures()
        bars['AAA'].pop(1)
        result = study([observation()], bars)
        self.assertEqual(result['rows'][0]['status'], 'missing_equity_terminal')
        self.assertEqual(result['rows'][1]['status'], 'marked')
        self.assertEqual(result['rows'][1]['replay_status'], 'missing_intermediate_bar')
        self.assertEqual(result['strategies']['lane']['coverage'][1]['marked'], 0)
        bars['SPY'][3]['close'] = float('nan')
        result = study([observation()], bars)
        self.assertNotIn('paired_spy_alpha', result['rows'][1])
        self.assertEqual(result['rows'][1]['terminal_date'], days[3])

    def test_bad_prices_unknown_timezone_and_duplicate_bars(self):
        days, bars = fixtures()
        bad = [observation(entry_price=p) for p in (0, -1, float('nan'), float('inf'), True)]
        bad.extend([observation(clock='10:00:00'), observation(clock='10:00:00-XX:00'),
                    observation(clock='10:00:00-00:00')])
        result = study(bad, bars)
        self.assertEqual(result['strategies']['lane']['invalid'], len(bad))
        self.assertEqual(result['episodes'], [])
        for field, value in [('open', 0), ('high', float('nan')), ('low', 103)]:
            with self.subTest(field=field):
                _, fresh = fixtures()
                fresh['AAA'][1][field] = value
                self.assertEqual(study([observation()], fresh)['rows'][0]['status'],
                                 'missing_equity_terminal')
        bars['AAA'].extend([dict(bars['AAA'][1]), dict(bars['AAA'][1])])
        self.assertEqual(study([observation()], bars)['rows'][0]['status'], 'missing_equity_terminal')

    def test_prior_high_only_and_gap_no_guaranteed_floor(self):
        days, bars = fixtures()
        bars['AAA'][1].update(open=100, high=120, low=95, close=110)
        bars['AAA'][2].update(open=80, high=90, low=75, close=85)
        result = study([observation()], bars)
        one, three = result['rows'][:2]
        self.assertEqual(one['stops']['ratchet_floor5']['reason'], 'horizon')
        self.assertAlmostEqual(one['stops']['ratchet_floor5']['gross_return'], .10)
        for label in three['stops']:
            replay = three['stops'][label]
            self.assertEqual(replay['reason'], 'stop_gap')
            self.assertEqual(replay['exit_date'], days[2])
            self.assertAlmostEqual(replay['gross_return'], -.20)
            self.assertAlmostEqual(replay['unstopped_matched_gross_return'], .01)

    def test_intrabar_low_precedes_new_high_and_stop_touch(self):
        _, bars = fixtures()
        bars['AAA'][1].update(open=100, high=130, low=85, close=120)
        row = study([observation()], bars)['rows'][0]
        for replay in row['stops'].values():
            self.assertEqual(replay['exit_price_proxy'], 90)
            self.assertEqual(replay['reason'], 'stop_touch')

    def test_benchmark_basis_costs_and_no_input_mutation(self):
        _, bars = fixtures()
        bars['SPY'][0].update(open=100, high=110, low=99, close=110)
        pre = study([observation(clock='08:00:00-05:00')], bars)['rows'][0]
        regular = study([observation()], bars)['rows'][0]
        self.assertEqual(pre['spy_entry_basis'], 'open')
        self.assertAlmostEqual(pre['spy_gross_return'], .10)
        self.assertEqual(regular['spy_entry_basis'], 'close')
        self.assertAlmostEqual(regular['spy_gross_return'], 101 / 110 - 1)
        for bps in (0, 25, 100, 250):
            self.assertAlmostEqual(pre['modeled_net_returns'][bps], .01 - bps / 10000)
            self.assertAlmostEqual(pre['modeled_paired_alpha'][bps], -.09)
        self.assertEqual(bars['SPY'][0]['open'], 100)

    def test_holdout_purges_max_horizon_overlap(self):
        days, bars = fixtures(100)
        observations = [observation(days[i], '08:00:00-05:00') for i in (50, 51, 69, 70)]
        result = study(observations, bars, inactivity_sessions=1)
        self.assertEqual([e['partition'] for e in result['episodes']],
                         ['train', 'purged', 'purged', 'holdout'])
        self.assertEqual(result['metadata']['holdout_session'], days[70])
        for row in result['rows']:
            if row['partition'] == 'train':
                self.assertLess(row['terminal_date'], days[70])
        after_close = study([observation(days[69], '17:00:00-05:00')], bars)
        self.assertEqual(after_close['episodes'][0]['partition'], 'purged')
        summary = result['strategies']['lane']['summaries']['train'][20]
        self.assertEqual(summary['paired'], 1)
        self.assertAlmostEqual(summary['mean_paired_spy_alpha'], 101 / 100 - 1 - (101 / 100 - 1))

    def test_gapped_calendar_and_incomplete_horizon(self):
        days, bars = fixtures(5)
        for book in bars.values():
            book.pop(1)
        result = study([observation(clock='08:00:00-05:00')], bars)
        self.assertEqual(result['rows'][1]['terminal_date'], days[3])
        self.assertEqual(result['rows'][2]['status'], 'outside_calendar')
        self.assertEqual(result['strategies']['lane']['coverage'][5]['marked'], 0)
        self.assertIsNone(result['strategies']['lane']['summaries']['purged'][5]['mean_gross_return'])

    def test_half_day_and_dst_equivalent_instants(self):
        _, bars = fixtures()
        result = study([observation(clock='13:00:00-05:00')], bars,
                       session_closes={'2026-01-05': '2026-01-05T13:00:00-05:00'})
        self.assertEqual(result['episodes'][0]['timing'], 'after_close')
        for stamp in ('2026-07-06T12:00:00+00:00', '2026-07-06T08:00:00-04:00'):
            summer = {ticker: [{'date': '2026-07-06', 'open': 100, 'high': 102,
                                'low': 98, 'close': 101}] for ticker in ('SPY', 'AAA')}
            self.assertEqual(study([observation(observed_at=stamp)], summer)['episodes'][0]['timing'],
                             'premarket')

    def test_empty_and_invalid_configuration(self):
        self.assertEqual(study([], {})['rows'], [])
        _, bars = fixtures()
        with self.assertRaises(ValueError):
            study([], bars, inactivity_sessions=2)
        with self.assertRaises(ValueError):
            study([], bars, cost_bps=[float('nan')])
        with self.assertRaises(ValueError):
            study([], bars, session_closes={'2026-01-05': '2026-01-05T13:00:00'})

    def test_entry_day_25_percent_peak_cannot_infer_floor_or_stop(self):
        _, bars = fixtures()
        bars['AAA'][0].update(open=100, high=125, low=85, close=124)
        result = study([observation(clock='15:59:00-05:00')], bars)
        row = result['rows'][0]
        self.assertTrue(row['entry_day_post_signal_path_excluded'])
        self.assertTrue(row['spy_entry_uses_future_close'])
        self.assertEqual(row['ratchet_mode_definition'], 'independent_single_floor_not_tiered')
        for replay in row['stops'].values():
            self.assertEqual(replay['reason'], 'horizon')
            self.assertEqual(replay['exit_price_proxy'], 101)
        self.assertIn('RTH replay omits entry-day post-signal stop path', result['metadata']['limitations'])

    def test_close_only_baseline_has_no_ohlc_or_replay(self):
        days, bars = fixtures()
        closes = {ticker: [{'date': bar['date'], 'close': bar['close']}
                           for bar in book] for ticker, book in bars.items()}
        closes['SPY'][1]['close'] = 110
        observations = [observation(days[1], '08:00:00-05:00'),
                        observation(days[1]), observation(days[7])]
        baseline = study_close_marks(observations, closes)
        ohlc = study(observations, bars)
        self.assertEqual([e['input_index'] for e in baseline['episodes']],
                         [e['input_index'] for e in ohlc['episodes']])
        self.assertEqual([r['terminal_date'] for r in baseline['rows']],
                         [r['terminal_date'] for r in ohlc['rows']])
        self.assertEqual(baseline['strategies']['lane']['deduplicated'], 1)
        row = baseline['rows'][0]
        self.assertEqual(row['spy_entry_basis'], 'close')
        self.assertEqual(row['spy_entry_date'], days[0])
        self.assertAlmostEqual(row['spy_gross_return'], 110 / 101 - 1)
        self.assertAlmostEqual(row['modeled_net_returns'][250], .01 - .025)
        for row in baseline['rows']:
            self.assertNotIn('stops', row)
            self.assertNotIn('replay_status', row)
        for coverage in baseline['strategies']['lane']['coverage'].values():
            self.assertNotIn('replay_matched', coverage)
        for partition in baseline['strategies']['lane']['summaries'].values():
            for summary in partition.values():
                self.assertNotIn('stops', summary)
                self.assertNotIn('replay_matched', summary)
        self.assertEqual(set(closes['AAA'][0]), {'date', 'close'})
        self.assertEqual(baseline['metadata']['interface'], 'close_only_baseline')

    def test_close_only_rth_future_close_and_missing_previous_benchmark(self):
        days, bars = fixtures()
        closes = {ticker: [{'date': bar['date'], 'close': bar['close']}
                           for bar in book] for ticker, book in bars.items()}
        closes['SPY'][0]['close'] = 110
        rth = study_close_marks([observation()], closes)['rows'][0]
        self.assertEqual(rth['spy_entry_date'], days[0])
        self.assertTrue(rth['spy_entry_uses_future_close'])
        self.assertAlmostEqual(rth['spy_gross_return'], 101 / 110 - 1)
        pre = study_close_marks([observation(clock='08:00:00-05:00')], closes)['rows'][0]
        self.assertEqual(pre['status'], 'marked')
        self.assertIsNone(pre['spy_entry_date'])
        self.assertNotIn('paired_spy_alpha', pre)

    def test_supplied_calendar_missing_session_invalidates_windows(self):
        days, bars = fixtures()
        closes = {ticker: [{'date': bar['date'], 'close': bar['close']}
                           for bar in book] for ticker, book in bars.items()}
        bars['SPY'].pop(2)
        closes['SPY'].pop(2)
        for function, prices in ((study, bars), (study_close_marks, closes)):
            with self.subTest(interface=function.__name__):
                result = function([observation()], prices, sessions=days)
                self.assertEqual(result['rows'][0]['terminal_date'], days[1])
                self.assertEqual(result['rows'][0]['status'], 'marked')
                row = result['rows'][1]
                self.assertEqual(row['terminal_date'], days[3])
                self.assertEqual(row['status'], 'invalid_calendar_window_missing_spy')
                self.assertNotIn('gross_return', row)
                self.assertNotIn('paired_spy_alpha', row)
                self.assertEqual(result['strategies']['lane']['coverage'][3]['marked'], 0)
                inferred = function([observation()], prices)
                self.assertEqual(inferred['rows'][1]['terminal_date'], days[4])
                self.assertFalse(inferred['metadata']['calendar_completeness_verified'])
                with self.assertRaises(ValueError):
                    function([], prices, sessions=[days[1], days[0]])
                with self.assertRaises(ValueError):
                    function([], prices, sessions=[days[0], days[0]])

    def test_close_only_invalid_closes_and_duplicate_dates(self):
        _, bars = fixtures()
        closes = {ticker: [{'date': bar['date'], 'close': bar['close']}
                           for bar in book] for ticker, book in bars.items()}
        for invalid in (0, float('nan')):
            closes['AAA'][1]['close'] = invalid
            self.assertEqual(study_close_marks([observation()], closes)['rows'][0]['status'],
                             'missing_equity_terminal')
        closes['AAA'][1]['close'] = 101
        closes['AAA'].append(dict(closes['AAA'][1]))
        self.assertEqual(study_close_marks([observation()], closes)['rows'][0]['status'],
                         'missing_equity_terminal')
        # Missing OHLC remains unavailable to study(), never synthesized from close.
        self.assertEqual(study([observation()], closes)['rows'][0]['status'], 'missing_equity_terminal')

    def test_supplied_calendar_cannot_omit_known_spy_session(self):
        days, bars = fixtures()
        incomplete = days[:2] + days[3:]
        for function in (study, study_close_marks):
            with self.subTest(interface=function.__name__):
                with self.assertRaisesRegex(ValueError, 'omits exported SPY sessions'):
                    function([observation()], bars, sessions=incomplete)


if __name__ == '__main__':
    unittest.main()
