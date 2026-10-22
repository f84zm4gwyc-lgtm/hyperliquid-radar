#!/usr/bin/env python3
"""Offline BTC/ETH paper replay on timestamped mark and bid/ask quotes.

No network, API credentials, order placement or future data in decisions.
A quote stream is NOT historical fills. Trigger on mark; approximate execution
using bid/ask + declared nonnegative slippage and taker fees. Funding excluded.
The program deliberately refuses WATCH_ONLY candidate records as trade signals.
"""
from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path
from decimal import Decimal

from paper_radar import d, iso, position, plan_hash, money


def parse_feed(records, coin, max_capture_delay_seconds=10):
    if not isinstance(records, list) or not records:
        raise ValueError('No quotes')
    last = None
    last_capture = None
    parsed = []
    for row in records:
        if row.get('symbol') != coin:
            raise ValueError('Wrong quote symbol')
        t = iso(row['observed_at_utc'])
        arrival = iso(row['captured_at_utc'])
        delay = (arrival - t).total_seconds()
        if delay < 0 or delay > max_capture_delay_seconds:
            raise ValueError('Quote arrival latency missing or too old')
        if last is not None and t <= last:
            raise ValueError('Unsorted, duplicated quotes')
        if last_capture is not None and arrival <= last_capture:
            raise ValueError('Unsorted or repeated capture timestamps')
        last = t
        last_capture = arrival
        bid, ask, mark = (d(row[k]) for k in ('bid', 'ask', 'mark'))
        if not (bid > 0 and bid < ask and mark > 0):
            raise ValueError('Bad bid/ask/mark')
        # Spreads above 1% are treated as unreliable. This is intentionally
        # strict for the early paper experiment, not a market invariant.
        if (ask-bid)/((ask+bid)/2) > d('0.01'):
            raise ValueError('Abnormal spread')
        # Each sampled mark and BBO may originate from independent WS messages.
        # Capturing their values in one JSON object does NOT make them synchronous.
        mark_at = iso(row['mark_observed_at_utc']) if 'mark_observed_at_utc' in row else None
        book_at = iso(row['book_observed_at_utc']) if 'book_observed_at_utc' in row else None
        if mark_at and (mark_at>arrival or (arrival-mark_at).total_seconds()>5):
            raise ValueError('Stale/asynchronous mark')
        if book_at and (book_at>arrival or (arrival-book_at).total_seconds()>5):
            raise ValueError('Stale/asynchronous order book')
        if mark_at and book_at and abs((mark_at-book_at).total_seconds())>3:
            raise ValueError('Mark/book desynchronized')
        parsed.append(dict(at=t,captured_at=arrival, observed_at_utc=row['observed_at_utc'],
                           captured_at_utc=row['captured_at_utc'],
                           mark_observed_at_utc=row.get('mark_observed_at_utc'),
                           book_observed_at_utc=row.get('book_observed_at_utc'),
                           bid=bid, ask=ask, mark=mark, delay_s=delay))
    return parsed


def side_exit(q, side, slip):
    """Estimated taker exit: LONG sells at bid, SHORT buys at ask."""
    px = q['bid'] if side == 'LONG' else q['ask']
    return px * (1-slip if side == 'LONG' else 1+slip)


def replay(plan, records, *, provenance='UNVERIFIED_EXTERNAL_QUOTES', max_gap_seconds=60):
    """Reject speculative signal statuses; never claim a real fill.

    Signal must be timestamped independently from the quote feed. The replay
    does not decide which signals to emit, and never upgrades WATCH_ONLY.
    """
    if plan.get('signal_status') != 'VALIDATED_PAPER_SIGNAL':
        raise ValueError('Requires VALIDATED_PAPER_SIGNAL, not WATCH_ONLY candidate')
    if 'alert_sent_at_utc' not in plan:
        raise ValueError('Must record real alert sent timestamp')
    sent = iso(plan['alert_sent_at_utc'])
    received = iso(plan['alert_received_at_utc'])
    if received < sent:
        raise ValueError('Receipt cannot precede alert emission')
    if max_gap_seconds <= 0:
        raise ValueError('Invalid maximum data gap')
    quotes = parse_feed(records, plan['symbol'])
    if provenance == 'CAPTURED_LIVE_REVIEWED' and any(
        not (q['mark_observed_at_utc'] and q['book_observed_at_utc']) for q in quotes):
        raise ValueError('Live capture requires independent mark and book timestamps')
    # WebSocket activeAssetCtx provides NO exchange event timestamp. A local
    # receipt clock must never masquerade as independent exchange mark time.
    if provenance == 'CAPTURED_LIVE_REVIEWED' and any(
        row.get('mark_time_basis') == 'LOCAL_RECEIPT_NO_EXCHANGE_TIMESTAMP' or
        row.get('source_integrity') == 'RAW_WS_NEEDS_INDEPENDENT_REVIEW'
        for row in records):
        raise ValueError('Local mark receipt timestamp cannot be reviewed exchange event time')
    # The source tag must not be relabeled at replay time.
    if any('data_provenance' in row and row['data_provenance'] != provenance
           for row in records):
        raise ValueError('Input quote provenance mismatch')
    side = plan['side']
    is_long = side == 'LONG'
    entry_slip = d(plan.get('entry_slippage_fraction', '0.0005'))
    tp_slip = d(plan.get('tp_slippage_fraction', '0.0005'))
    stop_slip = d(plan.get('stop_slippage_fraction', '0.0015'))
    fees = d(plan.get('fee_per_side_fraction', '0.00045'))
    deadline = received + timedelta(minutes=float(plan.get('entry_deadline_minutes', 30)))
    qty_remaining = Decimal(0)
    entry = None
    pos = None
    events = []
    pnl = Decimal(0)
    last_processed_quote = None
    degraded = False
    for q in quotes:
        # A price is actionable only after it was CAPTURED, not merely when
        # the exchange had theoretically observed it. Guard against lookahead.
        t = q['captured_at']
        if q['at'] < received or t < received: continue
        if entry is None:
            if t > deadline:
                events.append(dict(event='SKIP', at_utc=t.isoformat(), reason='Alert expired before first usable quote'))
                break
            quote_entry = (q['ask'] * (1+entry_slip) if is_long else q['bid'] * (1-entry_slip))
            tolerance = d(plan.get('entry_tolerance_fraction', '0.0025'))
            if abs(quote_entry / d(plan['indicative_entry'])-1) > tolerance:
                events.append(dict(event='SKIP', at_utc=t.isoformat(), reason='Entry price moved beyond locked tolerance'))
                break
            pos = position(plan, quote_entry)
            if not pos['accepted']:
                events.append(dict(event='SKIP', at_utc=t.isoformat(), reason=pos['reason']))
                break
            entry = d(pos['entry'])
            qty_remaining = d(pos['quantity'])
            pnl -= entry * qty_remaining * fees
            events.append(dict(event='ENTRY_ESTIMATE', at_utc=t.isoformat(), estimated_fill=float(entry),
                               quantity=float(qty_remaining), quote_delay_s=q['delay_s'],
                               market_observed_at_utc=q['observed_at_utc']))
        elif last_processed_quote and (t-last_processed_quote).total_seconds() > max_gap_seconds:
            degraded = True
            events.append(dict(event='UNOBSERVED_GAP', at_utc=t.isoformat(),
                               seconds=(t-last_processed_quote).total_seconds()))
        last_processed_quote = t
        mark = q['mark']
        stop = d(pos['stop'])
        t1 = d(pos['tp1'])
        t2 = d(pos['tp2'])
        hit_stop = mark <= stop if is_long else mark >= stop
        hit_t1 = mark >= t1 if is_long else mark <= t1
        hit_t2 = mark >= t2 if is_long else mark <= t2
        if hit_stop:
            fill = side_exit(q, side, stop_slip)
            pnl += qty_remaining * ((fill-entry) if is_long else (entry-fill)) - qty_remaining*fill*fees
            events.append(dict(event='STOP_ESTIMATE', at_utc=t.isoformat(), estimated_fill=float(fill),
                               quantity=float(qty_remaining), trigger_mark=float(mark)))
            qty_remaining=Decimal(0)
            break
        # At first eligible later quote, a large gap over TP2 does NOT imply
        # an achievable TP1 fill at exactly its limit. Use contemporaneous BBO.
        already_tp1 = any(x['event']=='TP1_ESTIMATE' for x in events)
        if hit_t1 and not already_tp1:
            fill = side_exit(q, side, tp_slip)
            take=d(pos['tp1_quantity'])
            pnl += take * ((fill-entry) if is_long else (entry-fill)) - take*fill*fees
            qty_remaining -= take
            events.append(dict(event='TP1_ESTIMATE', at_utc=t.isoformat(), estimated_fill=float(fill),
                               quantity=float(take), trigger_mark=float(mark)))
        if hit_t2 and qty_remaining>0:
            fill = side_exit(q, side, tp_slip)
            take=qty_remaining
            pnl += take * ((fill-entry) if is_long else (entry-fill)) - take*fill*fees
            qty_remaining=Decimal(0)
            events.append(dict(event='TP2_ESTIMATE', at_utc=t.isoformat(), estimated_fill=float(fill),
                               quantity=float(take), trigger_mark=float(mark)))
            break
    closed = entry is not None and qty_remaining == 0
    result = dict(trade_id=plan['trade_id'], plan_sha256=plan_hash(plan),
                  status=('CLOSED_ESTIMATE' if not degraded else 'CLOSED_LOW_CONFIDENCE') if closed else
                         ('OPEN_UNREALIZED' if entry is not None else 'NO_ENTRY'),
                  events=events, position=pos,
                  estimated_realised_pnl_ex_funding_usdc=money(pnl),
                  funding_included=False, hypothetical_fills_only=True,
                  data_provenance=provenance, incomplete_market_data=degraded,
                  independent_quote_times_checked=all(q['mark_observed_at_utc'] and q['book_observed_at_utc'] for q in quotes),
                  alert_latency_seconds=(received-sent).total_seconds(),
                  caveats=['Mark triggers are estimated; BBO + slippage is not an execution receipt',
                           'Fees assumed; funding not included; stop execution not guaranteed',
                           'Missing ticks between quotes can hide path and worsen actual execution',
                           'Quote observation and capture times must not be conflated',
                           'WATCH_ONLY candidates are NOT paper trades, no real orders'])
    return result


def main():
    parser=argparse.ArgumentParser(description='Offline quote replay only — no network, no orders')
    parser.add_argument('--signal',type=Path,required=True)
    parser.add_argument('--quotes',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--source',default='UNVERIFIED_EXTERNAL_QUOTES')
    args=parser.parse_args()
    if args.out.exists():
        parser.error('Refusing to overwrite an existing paper result')
    plan=json.loads(args.signal.read_text(encoding='utf-8'))
    records=[json.loads(s) for s in args.quotes.read_text(encoding='utf-8').splitlines() if s.strip()]
    result=replay(plan,records,provenance=args.source)
    args.out.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2,ensure_ascii=False))


if __name__=='__main__':main()
