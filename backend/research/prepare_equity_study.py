"""Normalize exported snapshots offline without reevaluating historical PM rules."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from math import isfinite
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')

def stamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError,TypeError): return None

def positive(value):
    if isinstance(value,bool): return None
    try:
        value = float(value)
        return value if isfinite(value) and value > 0 else None
    except (ValueError,TypeError): return None

def lane(row, default='CORE'):
    scanner = row.get('strategy_scanner') or {}
    return str(row.get('strategy_id') or row.get('screener_id') or row.get('source_scan') or scanner.get('screener_id') or scanner.get('id') or default)

def prepare(paths):
    populations = defaultdict(list); history = {}; trades = []; provenance = Counter(); counts = Counter()
    quality = Counter(); decisions = []; exclusions = []
    for path in paths:
        with open(path,encoding='utf-8') as source:
            for line in source:
                record = json.loads(line); name = record['collection']; row = record['payload']; counts[name] += 1
                if name == 'price_history_cache':
                    history[row.get('ticker')] = {'closes':row.get('closes') or {},'source':row.get('source'),'fetched_at':row.get('fetched_at')}
                if name == 'tf_trades': trades.append(row)
                if name == 'portfolio_manager_history':
                    for rec in row.get('recommendations') or []:
                        decisions.append(dict(rec, decision_at=row.get('generated_at'), parent_key=record['doc_key']))
                if name == 'pm_decision_ledger': decisions.append(row)
                if name == 'strategy_observations':
                    candidates = [row]; at = row.get('observed_at'); default = row.get('scanner_family','UNKNOWN')
                elif name == 'strategy_screeners_history':
                    candidates = row.get('candidates') or []; at = row.get('scan_finished_at') or row.get('generated_at'); default = 'UNKNOWN'
                elif name == 'scan_results':
                    candidates = row.get('results') or []; at = row.get('finished_at') or row.get('created_at'); default = 'CORE'
                elif name == 'll_scans':
                    candidates=[]
                    for candidate in row.get('candidates') or []:
                        for fit in candidate.get('strategy_fits') or ['UNCLASSIFIED']:
                            candidates.append(dict(candidate,strategy_id='LOTTERY_BOARD_'+str(fit)))
                    at=row.get('scanned_at') or row.get('created_at'); default='LOTTERY_BOARD'
                elif name == 'signal_performance':
                    candidates=[row]; at=row.get('observed_at'); default='UNATTRIBUTED_PERFORMANCE'
                elif name == 'signal_first_seen':
                    candidates=[dict(row,entry_price=row.get('first_seen_price'))]
                    at=row.get('first_seen_ts'); default='FIRST_SEEN_UNATTRIBUTED'
                elif name in {'dark_horse_alerts','x_factor_alerts','narrative_lock_alerts','max_conviction_picks'}:
                    candidates=[dict(row,entry_price=row.get('price') or row.get('close'))]
                    at=row.get('fired_at') or row.get('logged_at') or row.get('created_at'); default=name.removesuffix('_alerts').removesuffix('_picks').upper()
                else: continue
                for candidate in candidates:
                    when = stamp(candidate.get('observed_at') or at)
                    price = positive(candidate.get('entry_price') or candidate.get('price'))
                    ticker = str(candidate.get('ticker') or '').upper()
                    if not ticker or when is None or price is None:
                        quality['invalid_'+name] += 1
                        exclusions.append({'ticker':ticker,'strategy_id':lane(candidate,default),'source_collection':name,
                                           'source_doc_key':record['doc_key'],'raw_observed_at':candidate.get('observed_at') or at,
                                           'reasons':[reason for reason,invalid in [('missing_ticker',not ticker),('invalid_or_naive_timestamp',when is None),('missing_or_invalid_entry_price',price is None)] if invalid]})
                        continue
                    quote = candidate.get('quote_meta') or candidate.get('price_meta') or {}
                    obs = {'ticker':ticker,'strategy_id':lane(candidate,default),
                           'observed_at':when.isoformat(),'entry_price':price,
                           'source_collection':name,'source_doc_key':record['doc_key'],
                           'scanner_family':candidate.get('scanner_family') or default,
                           'read_only':bool(candidate.get('read_only')),
                           'pm_routable':candidate.get('pm_routable'),
                           'signals':candidate.get('signals') or [],
                           'case_score':candidate.get('case_score') or candidate.get('signal_score'),
                           'signal_count':candidate.get('independent_signal_count'),
                           'entry_price_source':candidate.get('entry_price_source') or quote.get('source'),
                           'entry_quote_timestamp':candidate.get('entry_quote_timestamp') or quote.get('ts'),
                           'entry_quote_age_seconds':candidate.get('entry_quote_age_seconds') or quote.get('age_s'),
                           'strategy_version':candidate.get('strategy_version'),
                           'cycle_id':candidate.get('cycle_id') or row.get('cycle_id')}
                    obs['observation_id']=hashlib.sha256(json.dumps([name,record['doc_key'],ticker,obs['strategy_id'],obs['observed_at'],price],separators=(',',':')).encode()).hexdigest()
                    populations[name].append(obs)
    # Primary append-only observations replace duplicate strategy snapshot coverage
    # on each ticker/lane/date. Earlier snapshots remain where no observations exist.
    primary_keys = {(r['ticker'],r['strategy_id'],stamp(r['observed_at']).astimezone(NY).date()) for r in populations['strategy_observations']}
    raw_observations=[r for values in populations.values() for r in values]
    fallback_sources={'strategy_screeners_history','signal_performance','signal_first_seen'}
    observations = [r for name,values in populations.items() if name not in fallback_sources for r in values]
    for r in populations['strategy_screeners_history']:
        key = (r['ticker'],r['strategy_id'],stamp(r['observed_at']).astimezone(NY).date())
        if key in primary_keys: quality['snapshot_superseded_by_observation'] += 1
        else: observations.append(r)
    covered={(r['ticker'],r['strategy_id'],stamp(r['observed_at']).astimezone(NY).date()) for r in observations}
    for name in ['signal_performance','signal_first_seen']:
        for r in populations[name]:
            key=(r['ticker'],r['strategy_id'],stamp(r['observed_at']).astimezone(NY).date())
            if key in covered: quality[name+'_superseded_by_scan']+=1
            else: observations.append(r)
    observations.sort(key=lambda r:(r['observed_at'],r['ticker'],r['strategy_id'],r['source_doc_key']))
    unique=[]; seen=set()
    for r in observations:
        key=(r['ticker'],r['strategy_id'],r['observed_at'],r['entry_price'])
        if key in seen: quality['exact_duplicate'] += 1; continue
        seen.add(key); unique.append(r); provenance[r['source_collection']] += 1
    return {'observations':unique,'raw_observations':raw_observations,'history':history,'trades':trades,'decisions':decisions,'exclusions':exclusions,
            'audit':{'export_counts':dict(counts),'raw_population_counts':{k:len(v) for k,v in populations.items()},
                     'canonical_sources':dict(provenance),'quality':dict(quality),
                     'scope':'all signaled equity underlyings, including research-only and options-source underlyings; no option contract returns',
                     'snapshot_supersession':'ticker/lane/ET date with append-only observation takes precedence; snapshot repeats retained on uncovered dates',
                     'historical_fill_claim':False}}

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('inputs',nargs='+'); parser.add_argument('--output',required=True)
    args=parser.parse_args(); result=prepare(args.inputs)
    result['audit']['input_sha256']={p:hashlib.file_digest(open(p,'rb'),'sha256').hexdigest() for p in args.inputs}
    target=Path(args.output); target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(result,allow_nan=False),encoding='utf-8')
    print(json.dumps({'observations':len(result['observations']),'unique_tickers':len({r['ticker'] for r in result['observations']}),'histories':len(result['history']),'audit':result['audit']}))
if __name__=='__main__': main()
