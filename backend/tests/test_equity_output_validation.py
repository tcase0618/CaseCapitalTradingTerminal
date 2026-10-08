"""Exercise the independent saved-output validator without live data."""
import csv
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'research'))
from validate_equity_outputs import validate


def save_csv(path, rows):
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def fixture(root):
    mark = dict(observation_id='one', entry_price=100, terminal_close=101, gross_return_pct=1)
    for name in ('all-sighting-outcomes.csv', 'all-raw-source-outcomes.csv'):
        save_csv(root / name, [dict(mark, horizon=h) for h in (1, 3, 5, 10, 20)])
    policies = ['no_stop', 'fixed_stop10', 'fixed_stop8', 'current_tier_floors10',
                'current_tier_floors8', 'delayed_first_arm7_floor5_initial10']
    rows = [dict(observation_id='one', ticker='AAA', strategy_id='CORE', horizon=1,
                 policy=p, partition='holdout', entry_date='2026-10-05',
                 terminal_date='2026-10-05', gross_return=.01, spy_gross_return=.005,
                 paired_spy_alpha=.005, delta_vs_unstopped=0) for p in policies]
    save_csv(root / 'policy-outcomes.csv', rows)
    summaries = [dict(strategy_id='CORE', horizon=1, policy=p, cohort='all', n=1,
                      mean_pct=1, spy_mean_pct=.5, excess_spy_mean_pct=.5,
                      delta_vs_unstopped_pct=0, win_pct=100) for p in policies]
    (root / 'results.json').write_text(json.dumps(dict(canonical_sightings=1,
        raw_valid_source_sightings=1, last_close='2026-10-07', policy_summary=summaries)))
    return rows


def test_saved_output_validation(tmp_path):
    fixture(tmp_path)
    result = validate(tmp_path)
    assert result['ok'] and result['matched_policy_windows'] == 1


def test_missing_policy_is_not_silently_compared(tmp_path):
    rows = fixture(tmp_path)
    save_csv(tmp_path / 'policy-outcomes.csv', rows[:-1])
    with pytest.raises(AssertionError, match='policy cohorts differ'):
        validate(tmp_path)


def test_bad_paired_alpha_is_rejected(tmp_path):
    rows = fixture(tmp_path)
    rows[0]['paired_spy_alpha'] = .5
    save_csv(tmp_path / 'policy-outcomes.csv', rows)
    with pytest.raises(AssertionError):
        validate(tmp_path)
