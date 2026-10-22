#!/usr/bin/env python3
"""Bridge archived public WATCH_ONLY candidates into V4 append-only journal.

This NEVER creates a PAPER_SIGNAL or PAPER_RESULT and does not trade.
Source samples can be incomplete due to GitHub/Render downtime.
"""
from pathlib import Path
import argparse, json
from journal import append,load
from paper_radar import iso


def bridge(src, dst):
    src,dst=Path(src),Path(dst)
    if not src.is_file():return {'new':0,'source_missing':True,'pnl_eligible':False}
    raw=[json.loads(x) for x in src.read_text(encoding='utf-8').splitlines() if x.strip()]
    checked=[]
    for row in raw:
        if row.get('source')!='RENDER_PUBLIC_WATCH_ONLY' or row.get('pnl_eligible') is not False:
            raise ValueError('Provenance invalid')
        event=row.get('event')
        if not isinstance(event,dict) or event.get('status')!='WATCH_ONLY':
            raise ValueError('Event cannot be promoted to a trade')
        if event.get('coin') not in ('BTC','ETH') or event.get('direction') not in ('LONG','SHORT'):
            raise ValueError('Unknown asset/direction')
        if not isinstance(event.get('id'),str) or not event['id']:
            raise ValueError('Missing event ID')
        captured_at=row['captured_at_utc']
        iso(captured_at)
        checked.append((event,captured_at))
    previous=load(dst)
    seen={r['payload'].get('id') for r in previous if r['event_type']=='WATCH_ONLY'}
    new=0
    for event,captured_at in checked:
        if event['id'] in seen:continue
        append(dst,'WATCH_ONLY',event,captured_at)
        seen.add(event['id']);new+=1
    return {'new':new,'all_archived_candidates':len(checked),
            'source_missing':False,'pnl_eligible':False,
            'info':'Candidates archived ONLY, no trading signals or performance measured'}


def main():
    p=argparse.ArgumentParser(description='Read-only bridge public WATCH_ONLY to V4 journal')
    p.add_argument('--source',type=Path,default=Path('radar-audit/data/watch_only.jsonl'))
    p.add_argument('--journal',type=Path,default=Path('paper-trading-v4/data/watch_only_journal.jsonl'))
    args=p.parse_args()
    args.journal.parent.mkdir(parents=True,exist_ok=True)
    print(json.dumps(bridge(args.source,args.journal),ensure_ascii=False))

if __name__=='__main__':main()
