"""Offline research integrity tests, no application or DB initialization."""
import importlib.util
from pathlib import Path
import sys
import unittest

BASE=Path(__file__).resolve().parents[1]/'research'
sys.path.insert(0,str(BASE))
spec=importlib.util.spec_from_file_location('runner',BASE/'run_equity_study.py')
runner=importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)

class RunnerTests(unittest.TestCase):
    def test_every_sighting_is_retained_and_exact_missing_is_not_zero(self):
        observations=[dict(ticker='AAA',strategy_id='CORE',observed_at='2026-10-05T12:00:00+00:00',entry_price=10),dict(ticker='AAA',strategy_id='CORE',observed_at='2026-10-05T13:00:00+00:00',entry_price=20)]
        rows=list(runner.every_sighting(observations,{'AAA':{'2026-10-05':12},'SPY':{'2026-10-05':100}},['2026-10-05','2026-10-06']))
        self.assertEqual(len(rows),10)
        self.assertAlmostEqual(rows[0]['gross_return_pct'],20)
        self.assertAlmostEqual(rows[5]['gross_return_pct'],-40)
        self.assertNotIn('gross_return_pct',rows[1])

    def test_nonoverlap_keeps_first_without_using_outcomes(self):
        rows=[dict(ticker='AAA',strategy_id='CORE',observed_at='2026-10-05T12:00:00+00:00',entry_price=10),dict(ticker='AAA',strategy_id='CORE',observed_at='2026-10-06T12:00:00+00:00',entry_price=1)]
        self.assertEqual(runner.nonoverlap(rows,['2026-10-05','2026-10-06','2026-10-07'],3),[rows[0]])

    def test_paired_statistics_use_same_denominator(self):
        rows=[dict(ticker='A',observed_at='2026-10-05',gross_return=.1),dict(ticker='B',observed_at='2026-10-05',gross_return=-.2)]
        result=runner.summarize(rows)
        self.assertEqual(result['n'],2); self.assertEqual(result['win_pct'],50)
        self.assertAlmostEqual(result['mean_pct'],-5)
        self.assertIsNone(result['ticker_cluster_ci95_pct'])

    def test_rth_mark_windows_overlap_but_next_open_windows_do_not(self):
        rows=[dict(ticker='AAA',strategy_id='CORE',observed_at='2026-10-05T18:00:00+00:00',entry_price=10),dict(ticker='AAA',strategy_id='CORE',observed_at='2026-10-06T18:00:00+00:00',entry_price=10)]
        calendar=['2026-10-05','2026-10-06','2026-10-07']
        self.assertEqual(len(runner.nonoverlap(rows,calendar,1)),1)
        self.assertEqual(len(runner.nonoverlap(rows,calendar,1,basis='next_open')),2)

    def test_same_provider_policy_integration_shape(self):
        from equity_policy_comparison import compare_policies
        row=dict(ticker='AAA',strategy_id='CORE',observed_at='2026-10-05T12:00:00+00:00')
        bars={ticker:[dict(date='2026-10-05',open=100,high=102,low=99,close=101)] for ticker in ['AAA','SPY']}
        comparison=compare_policies([row],bars,sessions=['2026-10-05'],horizons=(1,))
        self.assertEqual(len(comparison['rows']),6)
        self.assertAlmostEqual(comparison['rows'][0]['gross_return'],.01)
        self.assertEqual(comparison['excluded'],[])

if __name__=='__main__': unittest.main()
