"""Persist a reproducible offline signal study; never loads broker execution code."""
import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
import csv
from datetime import datetime, time, timezone
import hashlib
import json
from itertools import chain
from math import isfinite
from pathlib import Path
import random
from statistics import mean, median
from zoneinfo import ZoneInfo

from equity_signal_study import study_close_marks

NY=ZoneInfo('America/New_York')
CUTOFF='2026-10-07'
HORIZONS=(1,3,5,10,20)

def number(value):
    try:
        n=float(value); return n if isfinite(n) and not isinstance(value,bool) else None
    except (ValueError,TypeError): return None

def summarize(rows, field='gross_return', bootstrap=500):
    selected=[r for r in rows if number(r.get(field)) is not None]
    values=[float(r[field]) for r in selected]
    if not values: return {'n':0,'mean_pct':None,'median_pct':None,'win_pct':None,'ticker_cluster_ci95_pct':None}
    groups=defaultdict(list)
    for r in selected: groups[r['ticker']].append(float(r[field]))
    sums=[(sum(v),len(v)) for v in groups.values()]
    dates={r.get('observed_at','')[:10] for r in selected}
    ci=None
    if len(groups)>=20 and len(dates)>=10:
        rng=random.Random(20261008); samples=[]
        for _ in range(bootstrap):
            draw=rng.choices(sums,k=len(sums)); samples.append(sum(x[0] for x in draw)/sum(x[1] for x in draw)*100)
        samples.sort(); ci=[samples[int(.025*bootstrap)],samples[int(.975*bootstrap)]]
    gains=sum(v for v in values if v>0); losses=-sum(v for v in values if v<0)
    return {'n':len(values),'unique_tickers':len(groups),'entry_dates':len(dates),
            'mean_pct':mean(values)*100,'median_pct':median(values)*100,
            'win_pct':sum(v>0 for v in values)/len(values)*100,
            'best_pct':max(values)*100,'worst_pct':min(values)*100,
            'profit_factor':gains/losses if losses else None,
            'ticker_cluster_ci95_pct':ci,
            'ci_limit':'one-way ticker clustering only; shared market-date dependence remains'}

def nonoverlap(observations,calendar,horizon, *, basis='recorded_mark', dispositions=None):
    last={}; result=[]
    for row in sorted(observations,key=lambda r:(r['observed_at'],r['ticker'],r['strategy_id'])):
        local=datetime.fromisoformat(row['observed_at']).astimezone(NY)
        idx=bisect_left(calendar,local.date().isoformat())
        first=idx if local.time().hour<9 or (local.time().hour==9 and local.time().minute<30) else idx+int(idx<len(calendar) and calendar[idx]==local.date().isoformat())
        key=(row['ticker'],row['strategy_id'])
        previous=last.get(key)
        overlap=(local<=previous if basis=='recorded_mark' else first<=previous) if previous is not None else False
        if dispositions is not None:
            dispositions.append({'observation_id':row.get('observation_id'),'ticker':row['ticker'],'strategy_id':row['strategy_id'],'observed_at':row['observed_at'],'horizon':horizon,'basis':basis,'disposition':'overlap_suppressed' if overlap else 'selected'})
        if overlap: continue
        end=first+horizon-1
        if basis=='recorded_mark':
            last[key]=datetime.combine(datetime.fromisoformat(calendar[end]).date(),time(16),NY) if end<len(calendar) else datetime.max.replace(tzinfo=NY)
        else: last[key]=end
        result.append(row)
    return result

def partition(row, calendar, split):
    """Common 20-session purge for every horizon, declared in the study manifest."""
    local=datetime.fromisoformat(row['observed_at']).astimezone(NY)
    idx=bisect_left(calendar,local.date().isoformat())
    first=idx+int(idx<len(calendar) and calendar[idx]==local.date().isoformat() and local.time()>=time(9,30))
    if idx>=split: return 'holdout'
    return 'train' if first+19<split else 'purged'

def attach_pm(observations,decisions):
    by_ticker=defaultdict(list)
    for row in decisions:
        try:
            at=datetime.fromisoformat(str(row.get('decision_at') or row.get('generated_at') or row.get('created_at')).replace('Z','+00:00'))
            if at.tzinfo: by_ticker[row.get('ticker')].append((at.timestamp(),row))
        except (TypeError,ValueError): pass
    for values in by_ticker.values(): values.sort(key=lambda p:p[0])
    for row in observations:
        at=datetime.fromisoformat(row['observed_at']).timestamp(); values=by_ticker[row['ticker']]
        start=bisect_left([p[0] for p in values],at-120)
        nearby=[(abs(t-at),r) for t,r in values[start:] if t<=at+600]
        if nearby:
            distance,decision=min(nearby,key=lambda p:p[0])
            row['recorded_pm_action']=decision.get('action') or decision.get('pm_action')
            row['pm_match_seconds']=distance
            row['pm_match_basis']='nearest ticker decision -120/+600 seconds; not per-lane approval'

def csv_write(path,rows,fields):
    iterator=iter(rows); first=next(iterator,None)
    fields=[key for key in ('observation_id','source_doc_key','same_session_close','same_session_return_pct','latest_completed_close','return_to_cutoff_pct') if first and key in first and key not in fields]+fields
    with open(path,'w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore'); writer.writeheader()
        for row in chain([first] if first is not None else [],iterator):
            writer.writerow({key:json.dumps(row[key]) if isinstance(row.get(key),(dict,list)) else row.get(key) for key in fields})

def every_sighting(observations,closes,calendar):
    """Exhaustive mark ledger; no duplicate/episode suppression in this output."""
    for row in observations:
        local=datetime.fromisoformat(row['observed_at']).astimezone(NY)
        day=local.date().isoformat(); idx=bisect_left(calendar,day)
        present=idx<len(calendar) and calendar[idx]==day
        pre=(local.hour,local.minute)<(9,30)
        first=idx if pre or not present else idx+1
        benchmark_idx=idx-1 if pre or not present else idx
        for horizon in HORIZONS:
            end=first+horizon-1
            marked=dict(row,horizon=horizon,status='outside_calendar',terminal_date=None)
            day_close=closes.get(row['ticker'],{}).get(day)
            marked['same_session_close']=day_close
            marked['same_session_return_pct']=(day_close/row['entry_price']-1)*100 if day_close and local.time()<time(16) else None
            cutoff_close=closes.get(row['ticker'],{}).get(CUTOFF)
            cutoff_time=datetime.combine(datetime.fromisoformat(CUTOFF).date(),time(16),NY)
            marked['latest_completed_close']=cutoff_close
            marked['return_to_cutoff_pct']=(cutoff_close/row['entry_price']-1)*100 if cutoff_close and local<cutoff_time else None
            if end<len(calendar):
                terminal=calendar[end]; close=closes.get(row['ticker'],{}).get(terminal)
                marked['terminal_date']=terminal
                marked['status']='missing_terminal' if close is None else 'marked'
                if close is not None:
                    marked['terminal_close']=close
                    marked['gross_return_pct']=(close/row['entry_price']-1)*100
                    if benchmark_idx>=0:
                        start_spy=closes['SPY'].get(calendar[benchmark_idx]); end_spy=closes['SPY'].get(terminal)
                        if start_spy and end_spy:
                            marked['spy_daily_proxy_return_pct']=(end_spy/start_spy-1)*100
                            marked['daily_proxy_excess_pct']=marked['gross_return_pct']-marked['spy_daily_proxy_return_pct']
            yield marked

def load_public(path,first='2026-07-22'):
    data={}; failures=[]
    if path:
        with open(path,encoding='utf-8') as source:
            for line in source:
                rec=json.loads(line)
                if not rec.get('ok'): failures.append({'ticker':rec['symbol'],'reason':rec.get('error_class')}); continue
                bars=[]
                for raw in rec.get('payload',{}).get('bars') or []:
                    day=str(raw.get('timestamp') or raw.get('date') or '')[:10]
                    if len(day)!=10 or day>CUTOFF or day<first: continue
                    bars.append(dict(date=day,**{field:number(raw.get(field)) for field in ('open','high','low','close')}))
                data[rec['symbol']]=bars
    return data,failures

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--input',required=True); parser.add_argument('--public-bars'); parser.add_argument('--output',required=True)
    args=parser.parse_args(); base=json.loads(Path(args.input).read_text(encoding='utf-8')); output=Path(args.output); output.mkdir(parents=True,exist_ok=True)
    observations=base['observations']; attach_pm(observations,base['decisions'])
    first=min(r['observed_at'][:10] for r in observations)
    closes={ticker:{day:number(price) for day,price in record.get('closes',{}).items() if first<=day<=CUTOFF and number(price) is not None and number(price)>0} for ticker,record in base['history'].items()}
    public,failures=load_public(args.public_bars,first)
    if public:
        for ticker,bars in public.items(): closes[ticker]={b['date']:number(b.get('close')) for b in bars if number(b.get('close')) is not None and number(b.get('close'))>0}
    calendar=sorted(day for day in closes.get('SPY',{}) if day>=first)
    if not calendar: raise ValueError('No exact SPY session history')
    split=calendar[int(len(calendar)*.7)]
    csv_write(output/'all-sighting-outcomes.csv',every_sighting(observations,closes,calendar),['ticker','strategy_id','observed_at','entry_price','horizon','terminal_date','terminal_close','status','gross_return_pct','spy_daily_proxy_return_pct','daily_proxy_excess_pct','source_collection','scanner_family','read_only','entry_price_source','entry_quote_timestamp','entry_quote_age_seconds'])
    csv_write(output/'all-raw-source-outcomes.csv',every_sighting(base.get('raw_observations',observations),closes,calendar),['ticker','strategy_id','observed_at','entry_price','horizon','terminal_date','terminal_close','status','gross_return_pct','spy_daily_proxy_return_pct','daily_proxy_excess_pct','source_collection','source_doc_key','scanner_family','read_only','entry_price_source','entry_quote_timestamp','entry_quote_age_seconds'])
    all_rows=[]; summaries=[]; dispositions=[]; helper_exclusions=[]
    for horizon in HORIZONS:
        selected=nonoverlap(observations,calendar,horizon,dispositions=dispositions)
        # Close-only interface is explicit; no artificial open/high/low values.
        study=study_close_marks(selected,{ticker:[{'date':day,'close':price} for day,price in values.items()] for ticker,values in closes.items()},inactivity_sessions=1,sessions=calendar)
        helper_exclusions.extend(dict(r,selected_horizon=horizon,observation_id=selected[r['input_index']].get('observation_id')) for r in study['excluded'])
        rows=[r for r in study['rows'] if r['horizon']==horizon]
        for r in rows:
            original=selected[r['input_index']]
            for key in ['observation_id','source_doc_key','source_collection','scanner_family','read_only','entry_price_source','entry_quote_timestamp','entry_quote_age_seconds','recorded_pm_action','pm_match_basis','case_score','signal_count']:
                r[key]=original.get(key)
            r['entry_close_scale_flag']=None
            local=datetime.fromisoformat(r['observed_at']).astimezone(NY).date().isoformat()
            comparable=closes.get(r['ticker'],{}).get(local)
            if comparable: r['entry_close_scale_flag']=r['entry_price_proxy']/comparable>=2 or r['entry_price_proxy']/comparable<=.5
        all_rows.extend(rows)
        lanes=sorted({r['strategy_id'] for r in observations}|{r['strategy_id'] for r in base.get('exclusions',[])})
        for lane in lanes:
            group=[r for r in rows if r['strategy_id']==lane]
            for cohort,label in [(group,'all'),([r for r in group if r['partition']=='holdout'],'holdout'),([r for r in group if r.get('entry_close_scale_flag') is False],'unflagged_sensitivity'),([r for r in group if r.get('recorded_pm_action') in ('ACCUMULATE','STARTER')],'pm_buy_action'),([r for r in group if r.get('recorded_pm_action') in ('WATCH','REJECT')],'pm_watch_reject')]:
                stat=summarize(cohort); paired=[r for r in cohort if 'paired_spy_alpha' in r]
                row={'strategy_id':lane,'horizon':horizon,'cohort':label,'eligible':len(cohort),'missing_statuses':dict(Counter(r['status'] for r in cohort if r['status']!='marked')),**stat,
                     'paired_n':len(paired),'paired_equity_mean_pct':mean(r['gross_return'] for r in paired)*100 if paired else None,
                     'spy_mean_pct':mean(r['spy_gross_return'] for r in paired)*100 if paired else None,
                     'excess_spy_mean_pct':mean(r['paired_spy_alpha'] for r in paired)*100 if paired else None,
                     'scale_flagged':sum(r.get('entry_close_scale_flag') is True for r in cohort),
                     'net100bps_mean_pct':stat['mean_pct']-1 if stat['mean_pct'] is not None else None}
                summaries.append(row)
    csv_write(output/'signal-outcomes.csv',all_rows,['ticker','strategy_id','observed_at','entry_price_proxy','horizon','terminal_date','status','partition','gross_return','spy_gross_return','paired_spy_alpha','source_collection','scanner_family','read_only','entry_price_source','entry_quote_timestamp','entry_quote_age_seconds','entry_close_scale_flag','recorded_pm_action','pm_match_basis','case_score','signal_count','modeled_net_returns'])
    csv_write(output/'strategy-summary.csv',summaries,['strategy_id','horizon','cohort','eligible','n','unique_tickers','entry_dates','mean_pct','median_pct','win_pct','paired_n','paired_equity_mean_pct','spy_mean_pct','excess_spy_mean_pct','net100bps_mean_pct','scale_flagged','ticker_cluster_ci95_pct','missing_statuses'])
    csv_write(output/'selection-dispositions.csv',dispositions,['ticker','strategy_id','observed_at','horizon','basis','disposition'])
    (output/'helper-exclusions.json').write_text(json.dumps(helper_exclusions,indent=2),encoding='utf-8')
    csv_write(output/'entry-exclusions.csv',base.get('exclusions',[]),['ticker','strategy_id','source_collection','raw_observed_at','reasons','source_doc_key'])
    manifest={'created_at':datetime.now(timezone.utc).isoformat(),'input_sha256':hashlib.sha256(Path(args.input).read_bytes()).hexdigest(),
              'last_close':CUTOFF,'first_signal':min(r['observed_at'] for r in observations),'last_signal':max(r['observed_at'] for r in observations),
              'canonical_sightings':len(observations),'unique_tickers':len({r['ticker'] for r in observations}),
              'raw_valid_source_sightings':len(base.get('raw_observations',observations)),
              'calendar_sessions':len(calendar),'split_session':split,'calendar':calendar,
              'price_basis':'fresh_public_ohlc_closes_plus_explicit_cached_fallback' if public else 'stored_daily_close_cache',
              'provider_failures':failures,'public_symbols':len(public),'normalization':base['audit'],
              'purge_policy':'common 20-session label protection for all horizons; conservative, not horizon-specific',
              'cached_fallback_symbols':sorted(set(closes)-set(public)) if public else sorted(closes),
              'public_bars_sha256':hashlib.sha256(Path(args.public_bars).read_bytes()).hexdigest() if args.public_bars else None,
              'helper_exclusion_counts':dict(Counter(r['reason'] for r in helper_exclusions)),
              'summary':summaries,'signal_rows':len(all_rows),
              'limitations':['Not actual fills or portfolio NAV','Entry/close adjustment compatibility unverified','SPY daily-close entry proxy not execution-time matched','Ticker/date window nonoverlap does not imply cross-lane independence','Retrospective holdout, not prospectively untouched','Current session excluded','No forward filling; missing terminal prices remain missing']}
    if args.public_bars:
        from equity_policy_comparison import compare_policies
        policy_rows=[]; policy_exclusions=[]; policy_dispositions=[]
        for horizon in HORIZONS:
            cohort=nonoverlap(observations,calendar,horizon,basis='next_open',dispositions=policy_dispositions)
            comparison=compare_policies(cohort,public,horizons=(horizon,),sessions=calendar)
            policy_exclusions.extend(comparison['excluded'])
            for row in comparison['rows']:
                row['partition']=partition(row,calendar,int(len(calendar)*.7))
                original=cohort[row['input_index']]
                row['signal_count']=original.get('signal_count')
                row['case_score']=original.get('case_score')
                row['source_collection']=original.get('source_collection')
                row['observation_id']=original.get('observation_id')
            policy_rows.extend(comparison['rows'])
        csv_write(output/'policy-outcomes.csv',policy_rows,list(dict.fromkeys(k for r in policy_rows for k in r)))
        csv_write(output/'policy-selection-dispositions.csv',policy_dispositions,['ticker','strategy_id','observed_at','horizon','basis','disposition'])
        (output/'policy-exclusions.json').write_text(json.dumps(policy_exclusions,indent=2),encoding='utf-8')
        manifest['policy_rows']=len(policy_rows)
        manifest['policy_exclusion_counts']=dict(Counter(r['reason'] for r in policy_exclusions))
        manifest['policy_metadata']=comparison['metadata']
        policy_summaries=[]
        for key,group in __import__('itertools').groupby(sorted(policy_rows,key=lambda r:(r['strategy_id'],r['horizon'],r['policy'])),key=lambda r:(r['strategy_id'],r['horizon'],r['policy'])):
            group=list(group)
            for label,cohort in [('all',group),('train',[r for r in group if r['partition']=='train']),('holdout',[r for r in group if r['partition']=='holdout'])]:
                stat=summarize(cohort)
                policy_summaries.append({'strategy_id':key[0],'horizon':key[1],'policy':key[2],'cohort':label,**stat,
                    'delta_ticker_cluster_ci95_pct':summarize(cohort,field='delta_vs_unstopped')['ticker_cluster_ci95_pct'],
                    'excess_ticker_cluster_ci95_pct':summarize(cohort,field='paired_spy_alpha')['ticker_cluster_ci95_pct'],
                    'spy_mean_pct':mean(r['spy_gross_return'] for r in cohort)*100 if cohort else None,
                    'excess_spy_mean_pct':mean(r['paired_spy_alpha'] for r in cohort)*100 if cohort else None,
                    'delta_vs_unstopped_pct':mean(r['delta_vs_unstopped'] for r in cohort)*100 if cohort else None,
                    'net100bps_mean_pct':stat['mean_pct']-1 if stat['mean_pct'] is not None else None})
        manifest['policy_summary']=policy_summaries
        csv_write(output/'policy-summary.csv',policy_summaries,['strategy_id','horizon','policy','cohort','n','unique_tickers','entry_dates','mean_pct','median_pct','win_pct','best_pct','worst_pct','spy_mean_pct','excess_spy_mean_pct','delta_vs_unstopped_pct','net100bps_mean_pct','ticker_cluster_ci95_pct','delta_ticker_cluster_ci95_pct','excess_ticker_cluster_ci95_pct'])
    (output/'results.json').write_text(json.dumps(manifest,allow_nan=False,indent=2),encoding='utf-8')
    print(json.dumps({k:manifest[k] for k in ['first_signal','last_signal','canonical_sightings','unique_tickers','calendar_sessions','split_session','signal_rows','public_symbols']}))
if __name__=='__main__': main()
