#!/usr/bin/env python3
"""Summarize sampled health: no uptime guarantees and NO financial PnL."""
import argparse,json
from datetime import datetime,timezone,timedelta
from pathlib import Path
from statistics import median


def parse_dt(value):
    d=datetime.fromisoformat(value.replace('Z','+00:00'))
    if d.tzinfo is None:raise ValueError('Timezone required')
    return d.astimezone(timezone.utc)


def summarize(records, *, now=None, hours=24):
    if not 1<=hours<=168:raise ValueError('Invalid window')
    now=now or datetime.now(timezone.utc)
    start=now-timedelta(hours=hours)
    items=sorted((parse_dt(r['capture_at_utc']),r) for r in records if start<=parse_dt(r['capture_at_utc'])<=now)
    if not items:
        return {'window_hours':hours,'snapshots':0,'observed_ready_snapshots':0,
                'coverage':'NO_OBSERVATIONS','pnl_evaluated':False}
    gaps=[round((items[i][0]-items[i-1][0]).total_seconds()/60,2) for i in range(1,len(items))]
    ready=sum(1 for _,r in items if r.get('live_ready_reported') is True)
    failed=sum(1 for _,r in items if r.get('reason')=='FETCH_FAILED')
    resets=0
    for (_,a),(_,b) in zip(items,items[1:]):
        x,y=a.get('up_seconds'),b.get('up_seconds')
        if isinstance(x,(int,float)) and isinstance(y,(int,float)) and y+10<x:
            resets+=1
    return {'window_hours':hours,'first_capture_utc':items[0][0].isoformat(),
            'last_capture_utc':items[-1][0].isoformat(),
            'snapshots':len(items),'observed_ready_snapshots':ready,
            'observed_unready_snapshots':len(items)-ready,
            'fetch_failure_snapshots':failed,
            'sample_ready_fraction_pct':round(100*ready/len(items),2),
            'median_gap_minutes':round(median(gaps),2) if gaps else None,
            'max_gap_minutes':max(gaps) if gaps else None,
            'gaps_over_20min':sum(g>20 for g in gaps),
            'uptime_reset_indications':resets,
            'latest_reason':items[-1][1].get('reason'),
            'coverage':'SPARSE_SAMPLES_NOT_CONTINUOUS_UPTIME',
            'pnl_evaluated':False,
            'critical_note':'GitHub delays and Render sleep can cause invisible outages or missed signals.'}


def main():
    p=argparse.ArgumentParser(description='Read-only audit report for sampled Render status')
    p.add_argument('--input',type=Path,default=Path('radar-audit/data/observations.jsonl'))
    p.add_argument('--output',type=Path,default=Path('radar-audit/data/report.json'))
    p.add_argument('--hours',type=int,default=24)
    args=p.parse_args()
    rows=[]
    if args.input.exists():
        rows=[json.loads(x) for x in args.input.read_text(encoding='utf-8').splitlines() if x.strip()]
    data=summarize(rows,hours=args.hours)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(data,ensure_ascii=False))

if __name__=='__main__':main()
