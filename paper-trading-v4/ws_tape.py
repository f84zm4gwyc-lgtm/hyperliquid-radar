#!/usr/bin/env python3
"""Read-only Hyperliquid WS messages -> conservatively paired paper quotes.

IMPORTANT: activeAssetCtx has NO exchange timestamp. Its mark timestamp
is the LOCAL RECEIVE time, marked as such. Thus quotes are deliberately
UNREVIEWED and cannot validate fill quality / edge without further audit.
Never obtains credentials and never executes orders. No network in this module.
"""
from __future__ import annotations
import argparse
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from paper_radar import d, iso

PAIRS=('BTC','ETH')


def stamp(dt):
    return dt.isoformat().replace('+00:00','Z')


def finite_positive(v):
    price=d(v)
    if price<=0:raise ValueError('Nonpositive price')
    return price


class QuoteAssembler:
    def __init__(self, max_age_seconds=3, max_skew_seconds=3, min_interval_seconds=1):
        self.max_age=max_age_seconds
        self.max_skew=max_skew_seconds
        self.min_interval=min_interval_seconds
        if not (0<self.max_age<=30 and 0<self.max_skew<=30 and 0<=self.min_interval<=60):
            raise ValueError('Invalid quote collection limits')
        self.books={};self.marks={};self.last_captured={};self.last_incoming=None
        self.rejected=0;self.accepted=0;self.ignored=0

    def _reject(self):
        self.rejected+=1
        return None

    def process(self,envelope):
        """Consume {captured_at_utc, ws:{channel,data}} sequentially.

        captured_at_utc must come from the actual collector RECEIVE event, not
        an inferred exchange time. Replayed dumps must retain their source tag.
        """
        try:
            arrived=iso(envelope['captured_at_utc'])
            ws=envelope['ws']
            channel=ws['channel'];data=ws.get('data')
        except (ValueError,TypeError,KeyError,AttributeError):return self._reject()
        if self.last_incoming is not None and arrived<=self.last_incoming:
            return self._reject()
        self.last_incoming=arrived
        if channel not in ('bbo','activeAssetCtx'):
            self.ignored+=1;return None
        if not isinstance(data,dict):return self._reject()
        coin=data.get('coin')
        if coin not in PAIRS:return self._reject()
        try:
            if channel=='bbo':
                sides=data['bbo']
                if not isinstance(sides,list) or len(sides)!=2 or any(not isinstance(z,dict) for z in sides):
                    return self._reject()
                bid,ask=finite_positive(sides[0]['px']),finite_positive(sides[1]['px'])
                event_dt=datetime.fromtimestamp(int(data['time'])/1000,tz=timezone.utc)
                # Reject negative latency and old WS events (clock skew tolerance 0.5s).
                if event_dt>arrived+timedelta(milliseconds=500) or (arrived-event_dt).total_seconds()>self.max_age:
                    return self._reject()
                if bid>=ask or (ask-bid)/((ask+bid)/2)>Decimal('0.01'):
                    return self._reject()
                old=self.books.get(coin)
                if old and event_dt<=old['event']:return self._reject()
                self.books[coin]={'bid':bid,'ask':ask,'event':event_dt,'received':arrived}
            else:
                ctx=data['ctx']
                if not isinstance(ctx,dict):return self._reject()
                mark=finite_positive(ctx['markPx'])
                self.marks[coin]={'mark':mark,'received':arrived}
        except (ValueError,TypeError,KeyError,OverflowError,OSError):return self._reject()
        book=self.books.get(coin);mark=self.marks.get(coin)
        if not book or not mark:return None
        if (arrived-book['event']).total_seconds()>self.max_age:return None
        if (arrived-mark['received']).total_seconds()>self.max_age:return None
        if abs((book['event']-mark['received']).total_seconds())>self.max_skew:return None
        last=self.last_captured.get(coin)
        if last is not None and (arrived-last).total_seconds()<self.min_interval:return None
        self.last_captured[coin]=arrived
        self.accepted+=1
        # mark_observed_at_utc is a LOCAL RECEIVE timestamp, not exchange-event time.
        # Data source labels must always accompany rows; NEVER auto-promote to reviewed.
        return {
            'symbol':coin,
            'observed_at_utc':stamp(max(book['event'],mark['received'])),
            'captured_at_utc':stamp(arrived),
            'book_observed_at_utc':stamp(book['event']),
            'book_received_at_utc':stamp(book['received']),
            'mark_observed_at_utc':stamp(mark['received']),
            'mark_time_basis':'LOCAL_RECEIPT_NO_EXCHANGE_TIMESTAMP',
            'book_time_basis':'EXCHANGE_EVENT_TIMESTAMP',
            'source_integrity':'RAW_WS_NEEDS_INDEPENDENT_REVIEW',
            'bid':str(book['bid']), 'ask':str(book['ask']), 'mark':str(mark['mark'])}


def convert(input_path,output_path,*,source):
    if source not in ('SYNTHETIC_EXAMPLE','RECORDED_PUBLIC_WS_UNREVIEWED'):
        raise ValueError('Explicit allowed provenance required')
    if input_path.resolve()==output_path.resolve():raise ValueError('Source/destination cannot match')
    if not input_path.is_file():raise FileNotFoundError(input_path)
    # Refuse partial output when an input line has invalid JSON/schema.
    state=QuoteAssembler();rows=[]
    for n,line in enumerate(input_path.read_text(encoding='utf-8').splitlines(),1):
        if not line.strip():continue
        envelope=json.loads(line)
        if not isinstance(envelope,dict) or envelope.get('raw_feed_provenance')!=source:
            raise ValueError(f'Unverified/mismatched input provenance at line {n}')
        quote=state.process(envelope)
        if quote:
            quote['data_provenance']=source
            rows.append(quote)
    # No output overwrite; this archive must be produced once and retained.
    with output_path.open('x',encoding='utf-8') as out:
        for row in rows:
            out.write(json.dumps(row,allow_nan=False,separators=(',',':'))+'\n')
    return {'quotes':len(rows),'rejected_ws_messages':state.rejected,
            'ignored_ws_messages':state.ignored,'data_provenance':source,
            'paper_pnl_eligible':False,
            'note':'Neither recorded WS receipts nor /status snapshots prove execution or trading edge'}


def main():
    parser=argparse.ArgumentParser(description='Offline conversion of recorded public WS to unreviewed paper quotes')
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--source',required=True,choices=['SYNTHETIC_EXAMPLE','RECORDED_PUBLIC_WS_UNREVIEWED'])
    args=parser.parse_args()
    print(json.dumps(convert(args.input,args.output,source=args.source),indent=2))


if __name__=='__main__':main()
