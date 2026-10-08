"""Descriptive follow-up outputs only; no strategy selection or live mutation."""
import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
from statistics import mean

from run_equity_study import summarize, csv_write


def regime(entry_date, spy):
    dates=sorted(spy); index=bisect_left(dates,entry_date)
    if index<21: return 'unknown'
    move=spy[dates[index-1]]/spy[dates[index-21]]-1
    return 'up' if move>.02 else 'down' if move<-.02 else 'flat'


def main():
    p=argparse.ArgumentParser(); p.add_argument('--results',required=True); p.add_argument('--bars',required=True); p.add_argument('--report',required=True); args=p.parse_args()
    root=Path(args.results); result=json.loads((root/'results.json').read_text()); spy={}; provider=Counter()
    with open(args.bars,encoding='utf-8') as source:
        for line in source:
            item=json.loads(line); bars=item.get('payload',{}).get('bars') or []
            provider['responses']+=1; provider['errors']+=not item.get('ok'); provider['empty']+=not bars
            if item['symbol']=='SPY': spy={b['timestamp'][:10]:float(b['close']) for b in bars if float(b.get('close') or 0)>0}
    groups=defaultdict(list); weekly=defaultdict(list); signal_groups=defaultdict(list)
    with (root/'policy-outcomes.csv').open(newline='',encoding='utf-8') as source:
        for row in csv.DictReader(source):
            for key in ['gross_return','spy_gross_return','paired_spy_alpha','delta_vs_unstopped']: row[key]=float(row[key])
            key=(row['strategy_id'],row['horizon'],row['policy'],row['partition'],regime(row['entry_date'],spy)); groups[key].append(row)
            if row['horizon']=='5' and row['policy']=='current_tier_floors10': weekly[(row['entry_date'][:7],row['strategy_id'])].append(row)
            if row['horizon']=='5' and row['policy']=='current_tier_floors10':
                count=row.get('signal_count')
                try:
                    numeric=float(count)
                    band=str(min(5,max(0,int(numeric))))+('+' if numeric>=5 else '')
                except (ValueError,TypeError,OverflowError):
                    band='unknown'
                signal_groups[(row['strategy_id'],row['partition'],band)].append(row)
    regimes=[]
    for key,values in sorted(groups.items()):
        regimes.append(dict(strategy_id=key[0],horizon=key[1],policy=key[2],partition=key[3],regime=key[4],n=len(values),mean_pct=mean(r['gross_return'] for r in values)*100,win_pct=mean(r['gross_return']>0 for r in values)*100,spy_mean_pct=mean(r['spy_gross_return'] for r in values)*100,excess_spy_mean_pct=mean(r['paired_spy_alpha'] for r in values)*100))
    csv_write(root/'regime-summary.csv',regimes,['strategy_id','horizon','policy','partition','regime','n','mean_pct','win_pct','spy_mean_pct','excess_spy_mean_pct'])
    csv_write(root/'monthly-cohorts.csv',[dict(month=k[0],strategy_id=k[1],**summarize(v)) for k,v in sorted(weekly.items())],['month','strategy_id','n','mean_pct','median_pct','win_pct'])
    csv_write(root/'signal-count-cohorts.csv',[dict(strategy_id=k[0],partition=k[1],signals=k[2],**summarize(v)) for k,v in sorted(signal_groups.items())],['strategy_id','partition','signals','n','mean_pct','median_pct','win_pct'])
    policies=result['policy_summary']; signals=result['summary']
    fmt=lambda value:'--' if value is None else f'{value:+.2f}%'
    lines=['# Case Capital Equity Signal Study','',
        '## Verdict','',
        'Research-only retrospective study, not a trading-policy release. No optimizer was used. All tested variants and missing observations are retained. A highest historical mean is not a validated future edge.',
        '', '## Scope and Evidence','',
        f"- Signal period: {result['first_signal']} through {result['last_signal']}; completed historical closes end {result['last_close']}.",
        f"- {result['raw_valid_source_sightings']:,} valid source sightings; {result['canonical_sightings']:,} canonical sightings; {result['unique_tickers']:,} unique tickers; {result['calendar_sessions']} SPY-derived sessions.",
        f"- Public history responses: {provider['responses']}; request errors: {provider['errors']}; empty bar payloads: {provider['empty']}. Empty/missing data are not zero returns.",
        f"- Retrospective holdout starts {result['split_session']}; common 20-session purge applies to all horizons. This is deliberately conservative, not horizon-specific purging.",
        '- Four independent agents reviewed rules, data, methodology, and policy reconstruction. Their reports are in this directory. Later agent capacity was exhausted; the main reviewer completed integration and result checks.',
        '- Options-named rows below measure their EQUITY UNDERLYINGS, never option contract performance. Earnings and SEC remain research-only. No broker orders, strategy settings or execution gates were changed for the study.',
        '- LOTTERY_BOARD_* identifies broad persisted discovery-board fits, not the separately scored lottery_* specialist proposals. UNCLASSIFIED rows are prescreen discoveries, not approved signals. FIRST_SEEN_UNATTRIBUTED is ticker-level historical discovery without a recoverable lane.',
        '', '## Five-Session Matched Counterfactuals','',
        'All columns use the same next-eligible-RTH-open entry and common fifth-session endpoint. Stops can exit earlier; proceeds then stay flat. These are modeled gross returns, not actual fills. The no-stop column is a comparator, not a faithful reconstruction of every historical live policy.',
        '', '| Strategy | N | No Stop | Current Floors / -10% | Current Floors / -8% | Delay First Arm to 7% | Matched SPY | Current Win Rate |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    table={}
    for row in policies:
        if row['horizon']==5 and row['cohort']=='all': table.setdefault(row['strategy_id'],{})[row['policy']]=row
    for lane,by_policy in sorted(table.items()):
        current=by_policy['current_tier_floors10']
        lines.append(f"| {lane} | {current['n']} | {fmt(by_policy['no_stop']['mean_pct'])} | {fmt(current['mean_pct'])} | {fmt(by_policy['current_tier_floors8']['mean_pct'])} | {fmt(by_policy['delayed_first_arm7_floor5_initial10']['mean_pct'])} | {fmt(current['spy_mean_pct'])} | {current['win_pct']:.1f}% |")
    lines+=['','## Retrospective Holdout: Five Sessions','','| Strategy | N | No Stop | Current Floors / -10% | Delta vs No Stop | Excess vs SPY |','|---|---:|---:|---:|---:|---:|']
    for lane in sorted(table):
        values={r['policy']:r for r in policies if r['strategy_id']==lane and r['horizon']==5 and r['cohort']=='holdout'}
        cur=values.get('current_tier_floors10'); old=values.get('no_stop')
        if cur: lines.append(f"| {lane} | {cur['n']} | {fmt(old['mean_pct'])} | {fmt(cur['mean_pct'])} | {fmt(cur['delta_vs_unstopped_pct'])} | {fmt(cur['excess_spy_mean_pct'])} |")
    lines+=['','## Recorded Entry-Price Diagnostic','','This is the requested time/recorded-price view. It is NOT an executable backtest: entry marks may be stale or on a different split/symbol basis. Extreme apparent Lottery profits must not be called alpha. Flags are sensitivity labels, not proof of an error or permission to repair prices.','','| Strategy | Five-Session N | Mean | Median | Win Rate | Scale-Flagged Episodes | Unflagged Sensitivity Mean |','|---|---:|---:|---:|---:|---:|---:|']
    for row in signals:
        if row['horizon']!=5 or row['cohort']!='all': continue
        sensitivity=next(r for r in signals if r['strategy_id']==row['strategy_id'] and r['horizon']==5 and r['cohort']=='unflagged_sensitivity')
        lines.append(f"| {row['strategy_id']} | {row['n']} | {fmt(row['mean_pct'])} | {fmt(row['median_pct'])} | {fmt(row['win_pct'])} | {row['scale_flagged']} | {fmt(sensitivity['mean_pct'])} |")
    present={r['strategy_id'] for r in result['summary'] if r['n']>0}
    configured=['lottery_supernova','lottery_red_green','lottery_serial_runner','lottery_signal_confluence','pharma_core_overlap','options_squeeze_call','sec_filings','X_FACTOR']
    lines+=['','## Zero-Usable-Outcome Lanes','',', '.join('`'+lane+'`' for lane in configured if lane not in present)+'. These have no resolved comparable observations under their specific identities in this export; this is not a 0% win-rate claim. Some have excluded price/timestamp records; others have no persisted matching lane. See entry-exclusions.csv and strategy reconstruction.', '', '## What Should Change Next','',
        '1. Repair and persist entry-price provenance, security identity and corporate-action basis before using historical mark returns to tune the PM. Several-fold discrepancies are documented in data-schema-audit.md and the flagged-record CSV.',
        '2. Keep strategy-specific holding horizons. Review all 1/3/5/10/20-session results rather than adopting a single winning horizon in hindsight. Sparse lanes and zero-data strategies have no estimated edge.',
        '3. Treat any stop/floor advantage as a hypothesis for a prospective shadow trial, not an automatic live promotion. Daily bars cannot establish intraminute ratchet performance or limit-order fills.',
        '4. Use paired market-relative results and entry-known market regimes. regime-summary.csv uses only the previous 20 completed SPY sessions, with fixed +/-2% regime boundaries. It does not filter on future market returns.',
        '5. Capture actual paid fees and broker execution history. At $2-$6 sizing, fixed transaction costs can dominate apparently small gross edges. The 0/25/100/250 bps sensitivity is a scenario, not a fee estimate.',
        '6. Separate signal quality from PM selection and execution. Nearest-ticker PM matches are descriptive, not lane-specific approvals; approved and rejected cohorts are not randomized.',
        '', '## Actual Fills Are a Separate Evidence Tier','',
        'The fill review found 82 Trade Floor records and 9 mirrored Lottery tickets. Only 24 Trade Floor closes supported strict fill-price returns; one mirrored ticket supplied an additional distinct return. Of the 18 supported Public closes, gross dollar P&L summed to -$3.678685. This is an incomplete subset, not account net P&L. Five documented Alpaca paper closes summed to +$26.018672 gross. Missing fees, open positions, cash-only exits and legacy identities prevent a complete portfolio return. See fill-diagnostics.md.',
        '', '## Reproducibility and Limits','',
        f"Normalized input SHA-256: `{result['input_sha256']}`.",f"Fresh Public history SHA-256: `{result['public_bars_sha256']}`.",
        f"Counterfactual exclusions: `{json.dumps(result['policy_exclusion_counts'],sort_keys=True)}`.",
        '- No survivorship correction, tick-level simulation, verified adjustment basis, or contemporaneous spread/liquidity history is available. Delisted/unavailable windows are reported, not forward-filled.',
        '- Means are equally weighted observations, not portfolio returns. Cross-strategy duplicate tickers remain separate strategy tests; do not sum their profits or claim independent samples.',
        '- Confidence intervals use ticker clusters only, not complete two-way ticker/date dependence. Small samples are inconclusive. Multiple hypotheses and hindsight mean no confirmatory p-value or expected future alpha is claimed.',
        '- The common 20-session purge can leave sparse or empty training sets. The historical holdout has been discussed before; it is not prospectively untouched.',
        '- The partial current session is excluded consistently. Daily-model stops activate from prior completed highs; gaps fill at modeled open, not at a guaranteed floor. Liquidity may prevent actual fills.',
        '', '## Detailed Outputs','',
        'Private source exports and per-sighting outcomes stay outside Git, under `C:/Users/tcase/.codex/tmp/equity-study-2026-10-08/final/`.',
        '- all-raw-source-outcomes.csv: every valid source sighting across five horizons, including duplicate/superseded source records.',
        '- all-sighting-outcomes.csv: every canonical sighting with recorded time/entry and exact terminal close where available.',
        '- entry-exclusions.csv, selection-dispositions.csv, helper-exclusions.json: invalid, overlap-suppressed and unresolved evidence.',
        '- signal-outcomes.csv and strategy-summary.csv: nonoverlapping recorded-mark diagnostics.',
        '- policy-outcomes.csv and policy-summary.csv: all six matched policies and all horizons, including chronological partitions.',
        '- policy-exclusions.json, regime-summary.csv, monthly-cohorts.csv: missing windows and market-conditioned descriptions.',
        '', '## Offline Reproduction', '',
        'Use the recorded immutable input files; do not recollect and call the new snapshot identical.',
        '```powershell',
        'python backend/research/run_equity_study.py --input <normalized.json> --public-bars <public-daily-bars-complete.jsonl> --output <results-directory>',
        'python backend/research/analyze_equity_study.py --results <results-directory> --bars <public-daily-bars-complete.jsonl> --report <RESULTS.md>',
        'python backend/research/validate_equity_outputs.py --results <results-directory>',
        '```', '',
        'The planned 1/5/10-session inactivity sensitivity was not executed; the actual rule is horizon-specific nonoverlapping windows. Spread, dilution and intraday timing filters were not backtested because point-in-time inputs are unavailable.',
        '', 'Primary references: [Public historical bars](https://public.com/api/docs/resources/market-data/get-bars-v2-with-aggregation), [backtest overfitting](https://escholarship.org/uc/item/4w1110bb).']
    Path(args.report).write_text('\n'.join(lines)+'\n',encoding='utf-8')
    # Summaries are safe to version; raw account and signal export files are not.
    for name in ['strategy-summary.csv','policy-summary.csv','regime-summary.csv','monthly-cohorts.csv','signal-count-cohorts.csv']:
        (Path(args.report).parent/name).write_bytes((root/name).read_bytes())
    print(json.dumps({'provider':dict(provider),'policy_cohort_groups':len(policies),'regime_groups':len(regimes),'report':args.report}))

if __name__=='__main__': main()
