"""Independent arithmetic checks over saved outputs; never connects to a broker."""
import argparse
from collections import Counter, defaultdict
import csv
import json
from math import isclose
from pathlib import Path
from statistics import mean


def validate(root):
    root = Path(root)
    manifest = json.loads((root / 'results.json').read_text())
    counts = {}
    for name, expected in [('all-sighting-outcomes.csv', manifest['canonical_sightings'] * 5),
                           ('all-raw-source-outcomes.csv', manifest['raw_valid_source_sightings'] * 5)]:
        with (root / name).open(newline='', encoding='utf-8') as stream:
            rows = csv.DictReader(stream)
            count = 0
            for row in rows:
                count += 1
                if row.get('gross_return_pct'):
                    calculated = (float(row['terminal_close']) / float(row['entry_price']) - 1) * 100
                    assert isclose(calculated, float(row['gross_return_pct']), abs_tol=1e-9)
                assert row.get('observation_id'), 'missing stable observation identity'
            assert count == expected, (name, count, expected)
            counts[name] = count

    groups = defaultdict(list)
    paired = defaultdict(dict)
    with (root / 'policy-outcomes.csv').open(newline='', encoding='utf-8') as stream:
        for row in csv.DictReader(stream):
            for key in ('gross_return', 'spy_gross_return', 'paired_spy_alpha', 'delta_vs_unstopped'):
                row[key] = float(row[key])
            assert isclose(row['gross_return'] - row['spy_gross_return'], row['paired_spy_alpha'], abs_tol=1e-12)
            assert row['terminal_date'] <= manifest['last_close']
            identity = (row['observation_id'], row['horizon'])
            assert row['policy'] not in paired[identity], 'duplicate policy-window row'
            paired[identity][row['policy']] = row
            key = (row['strategy_id'], int(row['horizon']), row['policy'])
            groups[key].append(row)
    for policies in paired.values():
        assert len(policies) == 6, 'policy cohorts differ'
        unstopped = policies['no_stop']
        for row in policies.values():
            assert row['entry_date'] == unstopped['entry_date']
            assert row['terminal_date'] == unstopped['terminal_date']
            assert row['spy_gross_return'] == unstopped['spy_gross_return']
            assert isclose(row['gross_return'] - unstopped['gross_return'], row['delta_vs_unstopped'], abs_tol=1e-12)

    checked = 0
    for summary in manifest['policy_summary']:
        cohort = groups[(summary['strategy_id'], summary['horizon'], summary['policy'])]
        if summary['cohort'] != 'all':
            cohort = [r for r in cohort if r['partition'] == summary['cohort']]
        assert summary['n'] == len(cohort)
        if cohort:
            for source, target in [('gross_return', 'mean_pct'), ('spy_gross_return', 'spy_mean_pct'),
                                   ('paired_spy_alpha', 'excess_spy_mean_pct'), ('delta_vs_unstopped', 'delta_vs_unstopped_pct')]:
                assert isclose(mean(r[source] for r in cohort) * 100, summary[target], abs_tol=1e-9)
            assert isclose(mean(r['gross_return'] > 0 for r in cohort) * 100, summary['win_pct'], abs_tol=1e-9)
        checked += 1
    result = {'ok': True, 'exhaustive_rows': counts, 'matched_policy_windows': len(paired),
              'verified_policy_summaries': checked, 'policy_rows': sum(len(v) for v in groups.values()),
              'checks': ['entry/close arithmetic', 'stable observation IDs', 'six identical policy cohorts',
                         'same-window SPY', 'paired policy differences', 'all summary means and win rates'],
              'not_proven': ['historical price-basis correctness', 'intraday fills', 'future alpha', 'broker safety']}
    (root / 'validation.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--results', required=True)
    print(json.dumps(validate(parser.parse_args().results)))
