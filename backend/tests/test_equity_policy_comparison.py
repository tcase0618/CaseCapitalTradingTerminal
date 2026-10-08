"""Standalone offline suite: python -B backend/tests/test_equity_policy_comparison.py."""

import importlib.util
from collections import UserDict
from datetime import date, timedelta
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    'equity_policy_comparison', Path(__file__).resolve().parents[1] / 'research' / 'equity_policy_comparison.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
compare = MODULE.compare_policies
stop = MODULE.reconstructed_stop


def fixture(count=30):
    days, day = [], date(2026, 1, 5)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    bars = {ticker: [{'date': day, 'open': 100, 'high': 102, 'low': 98, 'close': 101}
                     for day in days] for ticker in ('AAA', 'SPY')}
    return days, bars


def signal(clock='08:00:00-05:00', day='2026-01-05'):
    return {'ticker': 'AAA', 'strategy_id': 'lane', 'observed_at': day + 'T' + clock,
            'entry_price': 999, 'input_index': 42}


def policies(result, horizon):
    return {row['policy']: row for row in result['rows'] if row['horizon'] == horizon}


class PolicyComparisonTests(unittest.TestCase):
    def test_strict_thresholds_and_uncapped_higher_tiers(self):
        expected = [(0, None), (5, None), (5.01, 5), (10, 5), (10.01, 10),
                    (20, 10), (20.01, 20), (30, 20), (30.01, 25), (40, 25),
                    (40.01, 35), (50.01, 45), (100.01, 95), (1000.01, 995)]
        for gain, floor in expected:
            with self.subTest(gain=gain):
                policy = stop(100, 100 + gain)
                self.assertEqual(policy['profit_floor_pct'], floor)
                self.assertEqual(policy['active_stop'], 90 if floor is None else 100 + floor)

    def test_stop_never_loosens_and_rounding_matches_source(self):
        self.assertEqual(stop(100, 140.01, 139)['active_stop'], 139)
        self.assertEqual(stop(100, 101, 105)['active_stop'], 105)
        self.assertEqual(stop(10, 10.5)['active_stop'], 9)
        self.assertEqual(stop(10, 10.5001)['active_stop'], 10.5)
        self.assertEqual(stop(100, 100, initial_loss_pct=8)['active_stop'], 92)

    def test_delayed_first_arm_changes_only_lowest_trigger(self):
        for gain, floor in ((5.01, None), (7, None), (7.01, 5), (10, 5),
                            (10.01, 10), (20.01, 20), (40.01, 35)):
            self.assertEqual(stop(100, 100 + gain, first_arm_pct=7)['profit_floor_pct'], floor)

    def test_first_open_strictly_later_and_same_spy_entry(self):
        days, bars = fixture()
        for clock, first in [('08:00:00-05:00', 0), ('09:29:59-05:00', 0),
                             ('09:30:00-05:00', 1), ('15:59:00-05:00', 1),
                             ('18:00:00-05:00', 1), ('14:30:00+00:00', 1)]:
            with self.subTest(clock=clock):
                result = compare([signal(clock)], bars, sessions=days)
                self.assertEqual(len(result['rows']), 30)
                for row in result['rows']:
                    self.assertEqual(row['entry_date'], days[first])
                    self.assertEqual(row['terminal_date'], days[first + row['horizon'] - 1])
                    self.assertEqual(row['entry_price_proxy'], 100)
                    self.assertEqual(row['spy_entry_price_proxy'], 100)
                    self.assertEqual(row['input_index'], 42)
        weekend = compare([signal(day='2026-01-10')], bars)
        self.assertEqual(weekend['rows'][0]['entry_date'], '2026-01-12')

    def test_gap_exit_cash_flat_to_common_end(self):
        days, bars = fixture()
        bars['AAA'][0].update(open=100, high=106, low=98, close=106)
        bars['AAA'][1].update(open=80, high=90, low=75, close=85)
        bars['AAA'][2].update(open=200, high=210, low=190, close=200)
        bars['SPY'][2].update(open=110, high=120, low=109, close=120)
        rows = policies(compare([signal()], bars), 3)
        self.assertEqual(rows['no_stop']['gross_return'], 1)
        for name, row in rows.items():
            if name == 'no_stop':
                continue
            self.assertEqual(row['exit_reason'], 'stop_gap')
            self.assertEqual(row['exit_date'], days[1])
            self.assertEqual(row['terminal_date'], days[2])
            self.assertEqual(row['common_horizon_value_proxy'], 80)
            self.assertAlmostEqual(row['gross_return'], -.20)
            self.assertAlmostEqual(row['spy_gross_return'], .20)
            self.assertAlmostEqual(row['paired_spy_alpha'], -.40)
            self.assertAlmostEqual(row['modeled_net_returns'][250], -.225)
            self.assertAlmostEqual(row['modeled_paired_alpha'][250], -.40)

    def test_same_day_high_never_arms_stop_before_low(self):
        _, bars = fixture()
        bars['AAA'][0].update(open=100, high=125, low=85, close=124)
        rows = policies(compare([signal()], bars), 1)
        for name, row in rows.items():
            if name != 'no_stop':
                self.assertEqual(row['exit_reason'], 'stop_touch')
                self.assertEqual(row['exit_price_proxy'], 92 if name.endswith('8') else 90)

    def test_floor_activates_only_next_bar_and_stays_tight(self):
        days, bars = fixture()
        bars['AAA'][0].update(open=100, high=111, low=98, close=111)
        bars['AAA'][1].update(open=112, high=112, low=111, close=111)
        bars['AAA'][2].update(open=111, high=112, low=109, close=111)
        result = compare([signal()], bars)
        self.assertEqual(policies(result, 1)['current_tier_floors10']['exit_reason'], 'horizon')
        row = policies(result, 3)['current_tier_floors10']
        self.assertEqual(row['exit_reason'], 'stop_touch')
        self.assertEqual(row['exit_date'], days[2])
        self.assertEqual(row['exit_price_proxy'], 110)

    def test_delayed_arm_daily_counterpart_and_terminal_stop_state(self):
        _, bars = fixture()
        bars['AAA'][0].update(open=100, high=106, low=98, close=106)
        bars['AAA'][1].update(open=104, high=104, low=100, close=101)
        result = compare([signal()], bars)
        one = policies(result, 1)['current_tier_floors10']
        self.assertEqual(one['active_stop'], 90)  # Entry high only affects a NEXT bar.
        three = policies(result, 3)
        self.assertEqual(three['current_tier_floors10']['exit_price_proxy'], 104)
        self.assertEqual(three['current_tier_floors10']['exit_reason'], 'stop_gap')
        self.assertEqual(three['delayed_first_arm7_floor5_initial10']['exit_reason'], 'horizon')
        self.assertEqual(three['delayed_first_arm7_floor5_initial10']['exit_price_proxy'], 101)

    def test_floor_gap_does_not_guarantee_five_and_current8_vs_current10(self):
        _, bars = fixture()
        bars['AAA'][0].update(open=100, high=106, low=91, close=106)
        bars['AAA'][1].update(open=103, high=104, low=102, close=103)
        rows = policies(compare([signal()], bars), 3)
        self.assertEqual(rows['current_tier_floors8']['exit_price_proxy'], 92)
        self.assertEqual(rows['current_tier_floors10']['exit_price_proxy'], 103)
        self.assertAlmostEqual(rows['current_tier_floors10']['gross_return'], .03)

    def test_missing_intermediate_excludes_all_policies_even_after_stop(self):
        days, bars = fixture()
        bars['AAA'][0].update(open=100, high=102, low=80, close=101)
        bars['AAA'].pop(1)
        result = compare([signal()], bars, sessions=days)
        self.assertEqual(len(policies(result, 1)), 6)
        self.assertEqual(policies(result, 3), {})
        exclusion = next(row for row in result['excluded'] if row.get('horizon') == 3)
        self.assertEqual(exclusion['missing_equity_dates'], [days[1]])
        _, bars = fixture()
        bars['SPY'].pop(1)
        self.assertEqual(policies(compare([signal()], bars, sessions=days), 3), {})

    def test_invalid_prices_duplicates_close_only_and_timezone(self):
        days, bars = fixture()
        for invalid in (0, float('nan'), float('inf')):
            bars['AAA'][0]['open'] = invalid
            self.assertEqual(compare([signal()], bars)['rows'], [])
        _, bars = fixture()
        bars['AAA'].append(dict(bars['AAA'][0]))
        self.assertEqual(compare([signal()], bars)['rows'], [])
        _, bars = fixture()
        close_only = {ticker: [{'date': b['date'], 'close': b['close']} for b in book]
                      for ticker, book in bars.items()}
        self.assertEqual(compare([signal()], close_only)['rows'], [])
        for clock in ('08:00:00', '08:00:00-00:00'):
            self.assertEqual(compare([signal(clock)], bars)['rows'], [])
        with self.assertRaises(ValueError):
            compare([], bars, sessions=days[:1] + days[2:])

    def test_dst_empty_inputs_and_hypotheses(self):
        bars = {ticker: [{'date': '2026-07-06', 'open': 100, 'high': 102,
                         'low': 98, 'close': 101}] for ticker in ('AAA', 'SPY')}
        result = compare([signal('13:29:00+00:00', '2026-07-06')], bars)
        self.assertEqual(len(result['rows']), 6)
        self.assertEqual(compare([], {})['rows'], [])
        self.assertEqual(len(result['metadata']['hypotheses']), 6)
        self.assertFalse(result['metadata']['optimizer'])
        with self.assertRaises(ValueError):
            compare([], {}, cost_bps=[float('nan')])

    def test_supplied_horizons_drive_rows_metadata_and_exclusions(self):
        days, bars = fixture()
        default = compare([signal()], bars)
        self.assertEqual(default['metadata']['horizons'], MODULE.HORIZONS)
        custom = compare([signal()], bars, horizons=[2, 1, 31])
        self.assertEqual(custom['metadata']['horizons'], (2, 1, 31))
        self.assertEqual([row['horizon'] for row in custom['rows']], [2] * 6 + [1] * 6)
        self.assertEqual(custom['rows'][0]['terminal_date'], days[1])
        self.assertEqual(custom['excluded'][0]['horizon'], 31)
        self.assertEqual(custom['excluded'][0]['reason'], 'outside_calendar')
        generated = compare([signal()], bars, horizons=(h for h in (1, 3)))
        self.assertEqual(generated['rows'], [row for row in default['rows'] if row['horizon'] in (1, 3)])
        bars['AAA'].pop(1)
        missing = compare([signal()], bars, horizons=[2])
        self.assertEqual(missing['rows'], [])
        self.assertEqual([row['horizon'] for row in missing['excluded']], [2])

    def test_invalid_horizons_rejected_before_processing(self):
        for horizons in (None, 1, [], [0], [-1], [True], [False], [1.0], ['1'],
                         [float('nan')], [float('inf')], [1, 1], [[1]], '13'):
            with self.subTest(horizons=horizons):
                with self.assertRaises(ValueError):
                    compare([], {}, horizons=horizons)

    def test_wrong_daily_bar_shapes_fail_loudly_even_without_episodes(self):
        for raw in (None, [], 'SPY', {'SPY': {}}, {'SPY': {'2026-01-05': 100}},
                    {'SPY': None}, {'SPY': ()}, {'SPY': [None]}, {'SPY': ['2026-01-05']},
                    {'SPY': [100]}, {'SPY': [[]]},
                    {'SPY': [], 'AAA': [{'date': '2026-01-05'}, 'bad']}):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(ValueError, 'daily_bars'):
                    compare([], raw)
        self.assertEqual(compare([], {'SPY': []})['rows'], [])

    def test_mapping_bar_types_supported_and_bad_fields_still_excluded(self):
        _, bars = fixture()
        wrapped = UserDict({ticker: [UserDict(bar) for bar in book] for ticker, book in bars.items()})
        self.assertEqual(compare([signal()], wrapped, horizons=[1])['rows'],
                         compare([signal()], bars, horizons=[1])['rows'])
        wrapped['AAA'][0].pop('open')
        result = compare([signal()], wrapped, horizons=[1])
        self.assertEqual(result['rows'], [])
        self.assertEqual(result['excluded'][0]['reason'], 'missing_or_invalid_window_ohlc')


if __name__ == '__main__':
    unittest.main()
