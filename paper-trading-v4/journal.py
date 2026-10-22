#!/usr/bin/env python3
"""Append-only, local audit journal for Hyperliquid read-only candidates.

NO network, brokerage integration, secrets or trading. Hash chain detects
accidental alteration, but is NOT digitally signed or tamper-proof.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from paper_radar import iso

ALLOWED = frozenset({'RADAR_STATUS','WATCH_ONLY','PAPER_SIGNAL','PAPER_QUOTE','PAPER_RESULT'})
GENESIS='0'*64


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def sha(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def verify_records(records):
    prev=GENESIS
    for n, record in enumerate(records, 1):
        if not isinstance(record,dict) or record.get('seq')!=n:
            raise ValueError(f'Bad journal sequence {n}')
        if record.get('prev_hash')!=prev:
            raise ValueError(f'Bad hash chain at {n}')
        if record.get('event_type') not in ALLOWED:
            raise ValueError(f'Unknown event type {n}')
        iso(record['recorded_at_utc'])
        if not isinstance(record.get('payload'),dict):
            raise ValueError('Payload must be an object')
        body={k:v for k,v in record.items() if k!='hash'}
        if sha(body)!=record.get('hash'):
            raise ValueError(f'Hash mismatch at {n}')
        if record['event_type']=='WATCH_ONLY' and record['payload'].get('status')!='WATCH_ONLY':
            raise ValueError('WATCH_ONLY must retain its original status')
        if record['event_type']=='PAPER_SIGNAL' and record['payload'].get('signal_status')!='VALIDATED_PAPER_SIGNAL':
            raise ValueError('Paper signals must be explicitly validated independently')
        prev=record['hash']
    return {'valid':True,'event_count':len(records),'tip_hash':prev}


def load(path):
    if not path.exists():return []
    records=[]
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.strip(): records.append(json.loads(line))
    verify_records(records)
    return records


def append(path, event_type, payload, recorded_at_utc):
    if event_type not in ALLOWED:raise ValueError('Unknown event type')
    iso(recorded_at_utc)
    if not isinstance(payload,dict):raise ValueError('Payload must be an object')
    if event_type=='WATCH_ONLY' and payload.get('status')!='WATCH_ONLY':
        raise ValueError('Never promote a candidate to a trade')
    if event_type=='PAPER_SIGNAL' and payload.get('signal_status')!='VALIDATED_PAPER_SIGNAL':
        raise ValueError('Requires independently validated signal')
    path=Path(path)
    if not path.parent.exists():raise FileNotFoundError('Journal parent folder must exist')
    if path.is_symlink():raise ValueError('Symlink journal refused')
    records=load(path)
    event=dict(seq=len(records)+1, event_type=event_type,
               recorded_at_utc=iso(recorded_at_utc).isoformat(),payload=payload,
               prev_hash=records[-1]['hash'] if records else GENESIS)
    event['hash']=sha(event)
    # One append, plus fsync. Do not overwrite history. A single-writer journal;
    # filesystem locking / concurrency needs a separate implementation.
    with path.open('a',encoding='utf-8') as file:
        file.write(canonical(event)+'\n')
        file.flush();os.fsync(file.fileno())
    return event


def capture_status(path, status, recorded_at_utc):
    """Archive one saved /status JSON, including unhealthy states.

    Never equate /status ages or asynchronous BBO with tick-level execution.
    Only archive WATCH_ONLY candidates. Their market close time is not the
    time someone reviewed or received an actionable alert.
    """
    if status.get('system')!='radar-readonly-v1':
        raise ValueError('Unsupported Render radar status format')
    if not isinstance(status.get('live_ready'),bool):
        raise ValueError('Missing boolean live_ready')
    if not isinstance(status.get('assets'),dict) or not all(k in status['assets'] for k in ('BTC','ETH')):
        raise ValueError('Missing assets')
    if not isinstance(status.get('watch_only_candidates'),list):
        raise ValueError('Missing WATCH_ONLY candidates')
    # Validate all candidates BEFORE changing disk. Otherwise a malformed
    # downloaded status could leave the journal partially updated.
    for item in status['watch_only_candidates']:
        if not isinstance(item,dict) or item.get('status')!='WATCH_ONLY':
            raise ValueError('Unvalidated candidate record in /status')
        ident=item.get('id');coin=item.get('coin');direction=item.get('direction')
        if not isinstance(ident,str) or not ident or coin not in ('BTC','ETH') or direction not in ('LONG','SHORT'):
            raise ValueError('Invalid candidate')
    existing={r['payload'].get('id') for r in load(Path(path)) if r['event_type']=='WATCH_ONLY'}
    event=append(path,'RADAR_STATUS',{'status':status,'source':'SAVED_RENDER_STATUS_NO_TICK_DATA'},recorded_at_utc)
    added=0
    for item in status['watch_only_candidates']:
        ident=item['id']
        if ident in existing:continue
        append(path,'WATCH_ONLY',item,recorded_at_utc)
        existing.add(ident);added+=1
    return {'status_event':event['seq'],'new_watch_only_candidates':added}


def main():
    parser=argparse.ArgumentParser(description='Offline append-only journal, NO API calls')
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('capture-status')
    p.add_argument('--status',type=Path,required=True)
    p.add_argument('--journal',type=Path,required=True)
    p.add_argument('--captured-at-utc',required=True,
                   help='Actual UTC time when status data was saved; never invent a timestamp')
    v=sub.add_parser('verify');v.add_argument('--journal',type=Path,required=True)
    args=parser.parse_args()
    if args.command=='verify':result=verify_records(load(args.journal))
    else:result=capture_status(args.journal,json.loads(args.status.read_text(encoding='utf-8')),args.captured_at_utc)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
