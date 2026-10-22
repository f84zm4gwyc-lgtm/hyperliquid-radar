#!/usr/bin/env python3
"""Research-only prospective 1m OHLCV proxy paper lab. Never trades.

PROTOCOL: write an immutable plan to a GitHub commit BEFORE any future bars
become eligible. On a later run, use that file's GIT COMMIT timestamp as the
minimum decision-evidence time, and simulate ONLY fully closed 1m bars AFTER
that timestamp. Any missing minute means UNVERIFIABLE, never 'profitable'.

Not mark-triggered fills, not a production execution simulator. Funding not
estimated. The output cannot count as validated paper performance.
"""
from __future__ import annotations
import argparse
import json
import math
import re
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from paper_radar import d, iso, plan_hash, position, simulate

API='https://api.hyperliquid.xyz/info'
MAX_SOURCE_AGE_S=90
MAX_CANDLE_CLOSE_AGE_S=950  # A 15m strategy cannot issue stale close orders.
MAX_ARCHIVE_WINDOW_MIN=4200  # < 5000 most recent 1m candles, ~3 days
MAX_FETCH_CANDLES=4500
ALLOWED_PAIRS=('BTC','ETH')


def fmt(t):
    return t.astimezone(timezone.utc).isoformat().replace('+00:00','Z')


def positive_number(v, name):
    if isinstance(v, bool): raise ValueError(name+' boolean')
    n=float(v)
    if not math.isfinite(n) or n<=0: raise ValueError(name+' not positive finite')
    return n


def _technical_ok(asset, side):
    frames=asset['timeframes'];p=frames['15m'];h1=frames['1h'];h4=frames['4h']
    vol=positive_number(p['volume_vs_previous20'],'volume_ratio')
    if vol<1.3:return False
    c=positive_number(p['last_close'],'close');e1=positive_number(h1['ema20'],'ema20');
    e1b=positive_number(h1['ema50'],'ema50');e4=positive_number(h4['ema20'],'ema20');e4b=positive_number(h4['ema50'],'ema50')
    return ((p['closed_above_prior20_high'] is True and c>positive_number(p['prior20_high'],'boundary') and
             positive_number(h1['last_close'],'1hclose')>e1>e1b and positive_number(h4['last_close'],'4hclose')>e4>e4b)
            if side=='LONG' else
            (p['closed_below_prior20_low'] is True and c<positive_number(p['prior20_low'],'boundary') and
             positive_number(h1['last_close'],'1hclose')<e1<e1b and positive_number(h4['last_close'],'4hclose')<e4<e4b))


def scan(snapshot, now):
    """Solely a tested hypothesis; no ex post scans allowed. No file writes."""
    if snapshot.get('schema_version')!=2:return []
    reported=iso(snapshot['updated_at_utc']);started=iso(snapshot['collection_started_at_utc'])
    if started>reported or (reported-started).total_seconds()>90:return []
    delta=(now-reported).total_seconds()
    if delta < 0 or delta>MAX_SOURCE_AGE_S:return []
    plans=[]
    for coin in ALLOWED_PAIRS:
        asset=snapshot.get('assets',{}).get(coin)
        if not isinstance(asset,dict):continue
        try:
            p=asset['timeframes']['15m'];close=iso(p['last_closed_candle_utc'])
            age=(reported-close).total_seconds()
            if age<0 or age>MAX_CANDLE_CLOSE_AGE_S:continue
            book=asset['order_book'];ctx=asset['perp_context']
            observed=iso(ctx['price_observed_at_utc']);book_observed=iso(book['observed_at_utc'])
            if not (started<=observed<=reported and started<=book_observed<=reported):continue
            if (reported-observed).total_seconds()>45 or (reported-book_observed).total_seconds()>45:continue
            mark=positive_number(ctx['mark_price_usdc'],'mark')
            bid=positive_number(book['best_bid'],'bid');ask=positive_number(book['best_ask'],'ask')
            if bid>=ask:continue
            mid=(bid+ask)/2
            if (ask-bid)/mid>0.003 or abs(mark-mid)/mid>0.005:continue
            atr=positive_number(p['atr14'],'atr')
            if abs(mark/positive_number(p['last_close'],'close')-1)>0.005:continue
            sz=ctx['size_decimals']
            if not isinstance(sz,int) or not 0<=sz<=6:continue
            distance=max(atr*1.5,mark*0.005)
            for side in ('LONG','SHORT'):
                if not _technical_ok(asset,side):continue
                direction=1 if side=='LONG' else -1
                entry=mark
                draft={
                    'trade_id':f"{coin}-{int(close.timestamp())}-{side}-paper-proxy",'symbol':coin,
                    'side':side,'signal_status':'PROSPECTIVE_PAPER_HYPOTHESIS',
                    'source':'HYPERLIQUID_PERP_INFO_SNAPSHOT_V2',
                    'decision_at_utc':fmt(reported),'last_closed_15m_at_utc':fmt(close),
                    'source_book_observed_at_utc':fmt(book_observed),
                    'indicative_entry':entry,'stop':entry-direction*distance,
                    'tp1':entry+direction*2.5*distance,'tp2':entry+direction*5*distance,
                    'sz_decimals':sz,'min_weighted_rr':'1.8',
                    'max_margin_usdc':1000,'max_risk_usdc':52,'leverage':2,
                    'cost_reserve_fraction':0.005,'entry_deadline_minutes':5,
                    'paper_proxy_only':True,'funding_included':False,
                    # Absolute validity starts at observation, never at commit/evaluation.
                    'alert_received_at_utc':fmt(reported),
                }
                if position(draft)['accepted']:
                    plans.append(draft)
                    return plans  # Conservative global paper budget: at most ONE candidate per scan.

        except (KeyError,ValueError,TypeError,OverflowError):
            continue
    return plans


def validate_1m_bars(bars, coin, start, end):
    """Only full, contiguous 1m bars can be used; reject missing minutes."""
    if coin not in ALLOWED_PAIRS:raise ValueError('Unsupported coin')
    if start.tzinfo is None or end.tzinfo is None or start>=end:raise ValueError('Bad window')
    ms=60_000
    first=math.ceil(start.timestamp()/60)*ms
    last=math.floor(end.timestamp()/60)*ms-ms
    if last<first:return [],'WAIT_FOR_FULL_MINUTE'
    if (last-first)//ms+1>MAX_FETCH_CANDLES:return [],'API_WINDOW_TOO_LONG'
    accepted=[]
    for raw in bars:
        if not isinstance(raw,dict):raise ValueError('Invalid candle')
        t=int(raw['t']);T=int(raw['T']);
        if raw.get('i')!='1m' or raw.get('s')!=coin or T!=t+ms-1 or t%ms!=0:raise ValueError('Bad 1m metadata')
        vals={key:positive_number(raw[key],key) for key in ('o','h','l','c')}
        v=float(raw['v'])
        if not math.isfinite(v) or v<0:raise ValueError('Invalid volume')
        if vals['h']<max(vals['o'],vals['c']) or vals['l']>min(vals['o'],vals['c']):raise ValueError('Invalid OHLC')
        if first<=t<=last:
            accepted.append({'start_utc':fmt(datetime.fromtimestamp(t/1000,timezone.utc)),
                             'open':vals['o'],'high':vals['h'],'low':vals['l'],'close':vals['c']})
    expected=list(range(first,last+ms,ms))
    found=[int(iso(x['start_utc']).timestamp()*1000) for x in accepted]
    if found!=expected:return [],'UNVERIFIABLE_MISSING_MINUTE'
    return accepted,'CONTIGUOUS_1M_TRADE_PRICE_PROXY'


def eligibility_time(decision, committed):
    if committed.tzinfo is None:raise ValueError('Missing commit timezone')
    if committed<iso(decision['decision_at_utc']):raise ValueError('Commit predates observation')
    # Wait until the NEXT minute after the whole first minute touching commit.
    minute=math.floor(committed.timestamp()/60)*60
    return datetime.fromtimestamp(minute+120,timezone.utc)


def evaluate(plan, commit_at_utc, candles, *, evaluated_at_utc):
    if plan.get('signal_status')!='PROSPECTIVE_PAPER_HYPOTHESIS' or plan.get('paper_proxy_only') is not True:
        raise ValueError('Not an independently committed prospective hypothesis')
    commit=iso(commit_at_utc);end=iso(evaluated_at_utc)
    eligible=eligibility_time(plan,commit)
    outcome={'trade_id':plan['trade_id'],'source_plan_commit_utc':fmt(commit),
             'first_eligible_1m_candle_utc':fmt(eligible),'evaluation_at_utc':fmt(end),
             'paper_signal_only':True,'pnl_eligible':False,'source':'OHLCV_1M_TRADE_PRICE_NOT_MARK',
             'original_plan_sha256':plan_hash(plan),
             'live_order_created':False,'funding_included':False}
    deadline=iso(plan['alert_received_at_utc'])+timedelta(minutes=float(plan.get('entry_deadline_minutes',5)))
    outcome['original_entry_deadline_utc']=fmt(deadline)
    if eligible>deadline:
        outcome.update(status='OHLCV_PROXY_NO_ENTRY',reason='Original entry deadline expired before eligible data');return outcome
    if end<=eligible:
        outcome.update(status='WAIT_MORE_DATA',reason='No fully closed postcommit candles');return outcome
    if (end-eligible).total_seconds()/60>MAX_ARCHIVE_WINDOW_MIN:
        outcome.update(status='UNVERIFIABLE',reason='1m API retention exceeded');return outcome
    bars,status=validate_1m_bars(candles,plan['symbol'],eligible,end)
    if status!='CONTIGUOUS_1M_TRADE_PRICE_PROXY':
        outcome.update(status='WAIT_MORE_DATA' if status=='WAIT_FOR_FULL_MINUTE' else 'UNVERIFIABLE',reason=status);return outcome
    # Only a post-commit next-minute candle is usable for entry.
    sim_plan=dict(plan)  # Bars are already filtered post-commit; never restart validity.
    sim=simulate(sim_plan,[{'start_utc':iso(r['start_utc']),
                           'open':d(r['open']),'high':d(r['high']),
                           'low':d(r['low']),'close':d(r['close'])} for r in bars])
    # CAUTION: the initial plan hash refers to the persisted, immutable draft.
    outcome.update(status='OHLCV_PROXY_'+sim['status'],bar_count=len(bars),
                   sim_events=sim['events'],estimated_pnl_ex_funding_usdc=sim['estimated_realised_pnl_ex_funding_usdc'],
                   limitations=['Price OHLCV cannot establish mark-based stop/TP triggers',
                                'Fills and slippage are modeled, not actual exchange trades',
                                'Funding is omitted; never count as validated performance'])
    return outcome


def summarize_paper(plan_dir, result_dir):
    """Statistical coverage, no historical-performance or verified-edge claim."""
    status={};closed_proxy_pnl=[]
    planned=len(list(Path(plan_dir).glob('*.json')))
    for path in sorted(Path(result_dir).glob('*.json')):
        if path.name=='summary.json':continue
        doc=json.loads(path.read_text(encoding='utf-8'))
        label=doc.get('status','INVALID')
        status[label]=status.get(label,0)+1
        if label=='OHLCV_PROXY_CLOSED_ESTIMATE':
            pnl=doc.get('estimated_pnl_ex_funding_usdc')
            try:closed_proxy_pnl.append(float(d(pnl)))
            except (ValueError,TypeError):pass
    return {'phase':'PROSPECTIVE_RESEARCH_NOT_VALIDATED_TRADING',
            'immutable_hypotheses_recorded':planned,
            'evaluations_by_status':status,
            'closed_proxy_count':len(closed_proxy_pnl),
            'closed_proxy_estimated_pnl_ex_funding_usdc':round(sum(closed_proxy_pnl),2),
            'closed_proxy_win_rate_pct':round(100*sum(v>0 for v in closed_proxy_pnl)/len(closed_proxy_pnl),2) if closed_proxy_pnl else None,
            'verified_fills':0,'validated_trades':0,'validated_edge':False,
            'funding_included':False,'strict_notice':'1m traded-price bars are NOT mark-trigger observations. Intrabar order path and real fills unknown.'}


def get_candles(coin,start,end):
    if coin not in ALLOWED_PAIRS:raise ValueError('Invalid coin')
    if end<start:return []
    # Hyperliquid officially supports 1m candleSnapshot; IP limits can apply.
    payload={'type':'candleSnapshot','req':{'coin':coin,'interval':'1m',
             'startTime':int(start.timestamp()*1000),'endTime':int(end.timestamp()*1000)}}
    req=Request(API,data=json.dumps(payload).encode('utf-8'),method='POST',headers={
        'Content-Type':'application/json','User-Agent':'RadarProspectiveResearch/1.0'})
    with urlopen(req,timeout=25) as response:
        raw=response.read(2_000_001)
    if len(raw)>2_000_000:raise ValueError('Oversized response')
    data=json.loads(raw)
    if not isinstance(data,list) or len(data)>5000:raise ValueError('Untrusted candleSnapshot reply')
    return data


def commit_time(path):
    """Verify committed plan bytes match the initial file-creation commit.

    A local Git author/committer date is not proof of a GitHub push timestamp.
    The research journal therefore makes no server-verified timestamp claims.
    """
    path=Path(path)
    if not path.is_file():raise FileNotFoundError(path)
    try:
        shallow=subprocess.run(['git','rev-parse','--is-shallow-repository'],
            capture_output=True,text=True,timeout=7,check=True)
        if shallow.stdout.strip()!='false':
            raise ValueError('Full git history required for plan creation evidence')
        original=subprocess.run(['git','log','--diff-filter=A','--format=%H %cI','--',str(path)],
            capture_output=True,text=True,timeout=7,check=True)
    except (subprocess.CalledProcessError,subprocess.TimeoutExpired) as exc:
        raise ValueError('Plan not yet committed') from exc
    lines=original.stdout.strip().splitlines()
    if len(lines)!=1:raise ValueError('Expected exactly one initial plan commit')
    sha,stamp=lines[0].split(' ',1)
    if not re.fullmatch(r'[a-f0-9]{40,64}',sha):raise ValueError('Invalid commit id')
    try:
        # For the expected working path we only support relative paths within repo.
        relative=path.resolve().relative_to(Path.cwd().resolve()).as_posix()
        committed=subprocess.run(['git','show',f'{sha}:{relative}'],
            capture_output=True,timeout=7,check=True)
    except (ValueError,subprocess.CalledProcessError,subprocess.TimeoutExpired) as exc:
        raise ValueError('Committed plan content unavailable') from exc
    if committed.stdout!=path.read_bytes():
        raise ValueError('Plan bytes modified after first commit')
    return stamp


def main():
    p=argparse.ArgumentParser(description='Prospective OHLCV proxy lab, not execution')
    sp=p.add_subparsers(dest='mode',required=True)
    s=sp.add_parser('scan');s.add_argument('--snapshot',type=Path,required=True);s.add_argument('--output-dir',type=Path,required=True);s.add_argument('--results-dir',type=Path,required=True);s.add_argument('--audit-file',type=Path,required=True)
    e=sp.add_parser('evaluate');e.add_argument('--plan',type=Path,required=True);e.add_argument('--output',type=Path,required=True)
    b=sp.add_parser('evaluate-all');b.add_argument('--plans-dir',type=Path,required=True);b.add_argument('--results-dir',type=Path,required=True)
    args=p.parse_args();now=datetime.now(timezone.utc)
    if args.mode=='scan':
        latest=json.loads(args.snapshot.read_text(encoding='utf-8'))
        drafts=scan(latest,now);args.output_dir.mkdir(parents=True,exist_ok=True)
        # Conservative combined paper exposure: no second idea while ANY
        # prior plan is pending, open or unverified (even a different coin).
        active=[]
        for previous in sorted(args.output_dir.glob('*.json')):
            finished=args.results_dir/previous.name
            if not finished.exists():
                active.append(previous.name);continue
            outcome=json.loads(finished.read_text(encoding='utf-8'))
            if outcome.get('status') not in ('OHLCV_PROXY_CLOSED_ESTIMATE','OHLCV_PROXY_NO_ENTRY'):
                active.append(previous.name)
        if active:drafts=[]
        created=[]
        for plan in drafts[:1]:
            ident=plan['trade_id']
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,140}',ident):raise ValueError('Unsafe trade id')
            path=args.output_dir/(ident+'.json')
            if not path.exists():
                with path.open('x',encoding='utf-8') as f:json.dump(plan,f,ensure_ascii=False,indent=2)
                created.append(ident)
        # Persist every scan, including zero-signal scans: no survivorship bias.
        args.audit_file.parent.mkdir(parents=True,exist_ok=True)
        audit={'scanned_at_utc':fmt(now),'source_snapshot_at_utc':latest.get('updated_at_utc'),
               'fresh_enough':0<=(now-iso(latest['updated_at_utc'])).total_seconds()<=MAX_SOURCE_AGE_S,
               'created_hypotheses':created,'suppressed_by_active_plan':bool(active),
               'paper_only':True,'validated_trades':0}
        with args.audit_file.open('a',encoding='utf-8') as f:
            f.write(json.dumps(audit,ensure_ascii=False,allow_nan=False)+'\n')
        print(json.dumps({'new_prospective_hypotheses':created,'no_orders':True}))
    elif args.mode=='evaluate-all':
        args.results_dir.mkdir(parents=True,exist_ok=True)
        summary=[]
        for plan_file in sorted(args.plans_dir.glob('*.json')):
            out=args.results_dir/plan_file.name
            plan=json.loads(plan_file.read_text(encoding='utf-8'))
            prev=json.loads(out.read_text(encoding='utf-8')) if out.exists() else None
            if prev and prev.get('status') in ('OHLCV_PROXY_CLOSED_ESTIMATE','OHLCV_PROXY_NO_ENTRY'):
                continue
            try:
                committed=commit_time(plan_file)
                start=eligibility_time(plan,iso(committed))
                candles=get_candles(plan['symbol'],start,now) if start<now and (now-start).total_seconds()/60<=MAX_ARCHIVE_WINDOW_MIN else []
                result=evaluate(plan,committed,candles,evaluated_at_utc=fmt(now))
            except (ValueError,TypeError,KeyError,OSError,TimeoutError,HTTPError,URLError) as exc:
                # NEVER fabricate PNL after an API/decoding error.
                result={'trade_id':plan['trade_id'],'status':'UNVERIFIABLE',
                        'reason':'DATA_SOURCE_OR_PLAN_ERROR','exception_type':type(exc).__name__,
                        'paper_signal_only':True,'pnl_eligible':False,'live_order_created':False}
            out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            summary.append({'id':plan['trade_id'],'status':result['status']})
        stats=summarize_paper(args.plans_dir,args.results_dir)
        (args.results_dir/'summary.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({'evaluations':summary,'statistics':stats,'validated_pnl':False,'live_orders':0}))
    else:
        plan=json.loads(args.plan.read_text(encoding='utf-8'))
        committed=commit_time(args.plan)
        start=eligibility_time(plan,iso(committed))
        if (now-start).total_seconds()/60>MAX_ARCHIVE_WINDOW_MIN:
            result=evaluate(plan,committed,[],evaluated_at_utc=fmt(now))
        else:
            rows=get_candles(plan['symbol'],start,now) if now>start else []
            result=evaluate(plan,committed,rows,evaluated_at_utc=fmt(now))
        args.output.parent.mkdir(parents=True,exist_ok=True)
        # Results can be revised by adding more *future* candles; plans cannot.
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({'id':plan['trade_id'],'status':result['status'],'validated_pnl':False}))

if __name__=='__main__':main()
