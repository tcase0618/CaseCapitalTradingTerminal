"""Offline source provenance and no-lookahead regime regression tests."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'research'))
from prepare_equity_study import prepare, positive
from analyze_equity_study import regime


class EvidenceTests(unittest.TestCase):
    def test_date_only_first_seen_is_excluded_not_timestamped_at_creation(self):
        row={'collection':'signal_first_seen','doc_key':'x','payload':{'ticker':'AAA','first_seen_date':'2026-10-01','created_at':'2026-10-02T12:00:00Z','first_seen_price':10}}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'x.jsonl'; path.write_text(json.dumps(row)+'\n')
            result=prepare([path])
        self.assertEqual(result['observations'],[])
        self.assertIn('invalid_or_naive_timestamp',result['exclusions'][0]['reasons'])

    def test_lottery_board_fits_remain_separate_from_specialist_proposals(self):
        row={'collection':'ll_scans','doc_key':'x','payload':{'scanned_at':'2026-10-01T12:00:00Z','candidates':[{'ticker':'AAA','price':10,'strategy_fits':['DAY2_CONTINUATION','CATALYST_RUNNER']}]}}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'x.jsonl'; path.write_text(json.dumps(row)+'\n'); result=prepare([path])
        self.assertEqual({r['strategy_id'] for r in result['observations']},{'LOTTERY_BOARD_DAY2_CONTINUATION','LOTTERY_BOARD_CATALYST_RUNNER'})

    def test_regime_never_uses_entry_day_or_future_close(self):
        spy={f'2026-09-{day:02}':100 for day in range(1,23)}
        spy['2026-09-23']=10000
        self.assertEqual(regime('2026-09-23',spy),'flat')
        spy['2026-09-22']=110
        self.assertEqual(regime('2026-09-23',spy),'up')

    def test_boolean_price_is_invalid(self):
        self.assertIsNone(positive(True))

if __name__=='__main__': unittest.main()
