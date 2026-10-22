#!/usr/bin/env python3
"""Public read-only Render observation -> durable GitHub JSONL archive.

No account/wallet credentials; no trades. Deliberately does NOT convert
WATCH_ONLY candidates to real/paper trading signals. GitHub scheduling is
best effort. Treat every long gap as unknown, never as no signal.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

BASE='https://radar-crypto-amaury-readonly-2026.onrender.com'
AGENT='CryptoRadarObserver/1.0 (+read-only)'


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')


def fetch_json(url):
    if url not in (BASE+'/status',BASE+'/watch-only'):
        raise ValueError('Only fixed read-only observation endpoints supported')
    with urlopen(Request(url,headers={'User-Agent':AGENT,'Accept':'application/json'}),timeout=110) as r:
        if int(r.headers.get('Content-Length','0'))>2000000:
            raise ValueError('Public status payload too large')
        raw=r.read(2000001)
        if len(raw)>2000000:raise ValueError('Public status payload too large')
        return json.loads(raw)


def validate_status(status):
    if not isinstance(status,dict) or status.get('system')!='radar-readonly-v1':
        raise ValueError('Unsupported or invalid radar status')
    if not isinstance(status.get('live_ready'),bool):raise ValueError('No live_ready boolean')
    if not isinstance(status.get('assets'),dict):raise ValueError('No asset data')
    for coin in ('BTC','ETH'):
        if not isinstance(status['assets'].get(coin),dict):raise ValueError('Missing asset '+coin)
    if not isinstance(status.get('watch_only_candidates'),list):
        raise ValueError('Missing WATCH_ONLY candidates list')
    return status


def validate_event(evt):
    if not isinstance(evt,dict) or evt.get('status')!='WATCH_ONLY':
        raise ValueError('Only WATCH_ONLY events allowed')
    ident=evt.get('id')
    if not isinstance(ident,str) or not (1<=len(ident)<=200):raise ValueError('Invalid event id')
    if evt.get('coin') not in ('BTC','ETH') or evt.get('direction') not in ('LONG','SHORT'):
        raise ValueError('Invalid event market/direction')
    if not isinstance(evt.get('closed_at_ms'),int):raise ValueError('Missing exchange candle close')
    return evt


def record_status(status, stamp):
    validate_status(status)
    return {'capture_at_utc':stamp,'source':'RENDER_PUBLIC_STATUS_SNAPSHOT',
            'system':status['system'],'live_ready_reported':status['live_ready'],
            'reason':status.get('reason'),'connected_reported':status.get('connected'),
            'ws_age_s':status.get('ws_age_s'),'history_age_s':status.get('history_age_s'),
            'reconnects':status.get('reconnects'),'history_refreshes':status.get('history_refreshes'),
            'up_seconds':status.get('up_seconds'),
            'history_error':status.get('history_error'),'data_errors':status.get('data_errors'),
            'candidate_checks':status.get('candidate_checks'),
            'candidate_suppressed':status.get('candidate_suppressed'),
            'assets':{c:{'mark':status['assets'][c].get('mark'),
                         'mark_age_s':status['assets'][c].get('mark_age_s'),
                         'book_age_s':status['assets'][c].get('book_age_s'),
                         'closed_candles':status['assets'][c].get('closed_candles')}
                      for c in ('BTC','ETH')},
            'completeness':'SAMPLE_ONLY_MISSED_INTERVALS_UNKNOWN',
            'pnl_eligible':False}


def safe_append(path,row):
    path=Path(path)
    if path.is_symlink():raise ValueError('Symlink file refused')
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a',encoding='utf-8') as f:
        f.write(json.dumps(row,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':'))+'\n')


def existing_ids(path):
    path=Path(path)
    if not path.exists():return set()
    ids=set()
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.strip():
            entry=json.loads(line)
            ids.add(entry['event']['id'])
    return ids


def store(status, events_payload, *,observations, events, captured_at):
    row=record_status(status,captured_at)
    if events_payload is None:
        candidates=status['watch_only_candidates']
        row['candidate_endpoint']='UNAVAILABLE_STATUS_LAST_5_ONLY'
    else:
        if not isinstance(events_payload,dict) or events_payload.get('type')!='WATCH_ONLY':
            raise ValueError('Unsupported event endpoint')
        candidates=events_payload.get('events')
        if not isinstance(candidates,list):raise ValueError('Malformed events payload')
        row['candidate_endpoint']='LAST_100_EPHEMERAL'
    # Validate BEFORE writing any files. The archive is best-effort but fail-closed.
    for e in candidates:validate_event(e)
    seen=existing_ids(events)
    novel=[]
    for e in candidates:
        if e['id'] not in seen:
            novel.append(e)
            seen.add(e['id'])
    safe_append(observations,row)
    for evt in novel:
        safe_append(events,{'captured_at_utc':captured_at,'source':'RENDER_PUBLIC_WATCH_ONLY',
                            'event':evt,'pnl_eligible':False,'verified_executable_signal':False,
                            'completeness':'CANDIDATE_ARCHIVE_NOT_COMPLETE'} )
        seen.add(evt['id'])
    return {'capture_at_utc':captured_at,'observed_ready':row['live_ready_reported'],
            'new_watch_only':len(novel),'candidate_endpoint':row['candidate_endpoint']}


def run(observations,events,stamp=None,*,status=None,event_payload=None):
    stamp=stamp or utc_now()
    if status is None:
        try:status=fetch_json(BASE+'/status')
        except Exception as exc:
            safe_append(observations,{'capture_at_utc':stamp,'source':'RENDER_PUBLIC_STATUS_SNAPSHOT',
                     'live_ready_reported':False,'reason':'FETCH_FAILED',
                     'error_type':type(exc).__name__,'completeness':'NO_DATA',
                     'pnl_eligible':False})
            return {'capture_at_utc':stamp,'observed_ready':False,'error_type':type(exc).__name__}
        try:event_payload=fetch_json(BASE+'/watch-only')
        except Exception:event_payload=None  # V1.1 compatibility
    return store(status,event_payload,observations=observations,events=events,captured_at=stamp)


def main():
    parser=argparse.ArgumentParser(description='Archive read-only Render status; never place orders')
    parser.add_argument('--observations',default='radar-audit/data/observations.jsonl',type=Path)
    parser.add_argument('--events',default='radar-audit/data/watch_only.jsonl',type=Path)
    args=parser.parse_args()
    print(json.dumps(run(args.observations,args.events),ensure_ascii=False))

if __name__=='__main__':main()
