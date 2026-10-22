#!/usr/bin/env python3
"""Prospective WATCH_ONLY quote-quality screen, never a trading signal.

Matches each archived public candidate to its precise capture-time Render /status
snapshot. Checks that the market readings were available no earlier than
capture; rejects stale candles, incomplete bootstraps, missing BBO, bad prices.
It NEVER creates an entry/stop/TP, validated paper signal, order or PnL.
Snapshots 10-30min apart CANNOT establish price paths or simulated fills.
"""
from __future__ import annotations
import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

MAX_CANDIDATE_AGE_S = 180
MAX_QUOTE_AGE_S = 5
MAX_SPREAD_BPS = 30
MAX_MARK_TO_BOOK_BPS = 50
MAX_MARK_TO_SIGNAL_BPS = 50


def parse_utc(value):
    if not isinstance(value, str):
        raise ValueError('Missing UTC timestamp')
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Timezone required')
    return dt.astimezone(timezone.utc)


def number(value):
    if isinstance(value, bool):
        raise ValueError('Boolean is not a price')
    num = float(value)
    if not math.isfinite(num) or num <= 0:
        raise ValueError('Nonfinite or nonpositive price')
    return num


def read_jsonl(path):
    path = Path(path)
    if not path.is_file():
        return []
    if path.is_symlink():
        raise ValueError('Symlink inputs forbidden')
    result = []
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.strip():
            value = json.loads(line, parse_constant=lambda v: (_ for _ in ()).throw(ValueError('Nonfinite JSON value')))
            if not isinstance(value, dict):
                raise ValueError('Expected JSON objects')
            result.append(value)
    return result


def screen(event_record, status_record):
    """Screen a public archived WATCH_ONLY candidate, default reject."""
    event = event_record.get('event')
    if event_record.get('source') != 'RENDER_PUBLIC_WATCH_ONLY' or event_record.get('pnl_eligible') is not False:
        raise ValueError('Untrusted event provenance')
    if not isinstance(event, dict) or event.get('status') != 'WATCH_ONLY':
        raise ValueError('No promotion from WATCH_ONLY allowed')
    coin = event.get('coin')
    if coin not in ('BTC', 'ETH') or event.get('direction') not in ('LONG', 'SHORT'):
        raise ValueError('Invalid candidate market/side')
    ident = event.get('id')
    if not isinstance(ident, str) or not 1 <= len(ident) <= 200:
        raise ValueError('Invalid event ID')
    stamp = event_record.get('captured_at_utc')
    captured = parse_utc(stamp)
    output = {
        'event_id': ident, 'coin': coin, 'direction': event['direction'],
        'screened_from_capture_at_utc': captured.isoformat(),
        'result': 'REJECT', 'reason': 'UNKNOWN',
        'paper_signal_created': False, 'order_created': False,
        'pnl_eligible': False, 'observability': 'SPARSE_PUBLIC_SAMPLES_NOT_FILLS'
    }

    def reject(reason):
        output['reason'] = reason
        return output

    if not isinstance(status_record, dict):
        return reject('MISSING_MATCHING_STATUS')
    if status_record.get('capture_at_utc') != stamp or status_record.get('source') != 'RENDER_PUBLIC_STATUS_SNAPSHOT':
        return reject('SOURCE_OR_CAPTURE_MISMATCH')
    if not status_record.get('live_ready_reported') is True or status_record.get('reason') != 'ok':
        return reject('ENGINE_NOT_READY')
    if status_record.get('candidate_endpoint') != 'LAST_100_EPHEMERAL':
        return reject('INCOMPLETE_EVENT_SOURCE')
    closed_at_ms = event.get('closed_at_ms')
    if isinstance(closed_at_ms, bool) or not isinstance(closed_at_ms, int) or closed_at_ms < 1_600_000_000_000:
        return reject('INVALID_CANDLE_CLOSE')
    lag = captured.timestamp() - closed_at_ms / 1000
    if not 0 <= lag <= MAX_CANDIDATE_AGE_S:
        return reject('CANDIDATE_LATE_OR_FUTURE')
    assets = status_record.get('assets', {})
    if not isinstance(assets, dict) or not isinstance(assets.get(coin), dict):
        return reject('MISSING_ASSET_QUOTE')
    asset = assets[coin]
    try:
        book_age = float(asset['book_age_s'])
        mark_age = float(asset['mark_age_s'])
        if not all(math.isfinite(a) and 0 <= a <= MAX_QUOTE_AGE_S for a in (book_age, mark_age)):
            return reject('STALE_QUOTES')
        bb = asset['book_bid_ask']
        if not isinstance(bb, list) or len(bb) != 2:
            return reject('MISSING_BBO')
        bid, ask = number(bb[0]), number(bb[1])
        mark = number(asset['mark'])
        close = number(event['close'])
        boundary = number(event['boundary'])
    except (ValueError, TypeError, KeyError, OverflowError):
        return reject('INVALID_QUOTE_OR_CANDIDATE_PRICE')
    if bid >= ask:
        return reject('CROSSED_BOOK')
    mid = (bid + ask)/2
    spread_bps = 10000 * (ask-bid)/mid
    if spread_bps > MAX_SPREAD_BPS:
        return reject('SPREAD_TOO_WIDE')
    if 10000*abs(mark-mid)/mid > MAX_MARK_TO_BOOK_BPS:
        return reject('MARK_BOOK_DIVERGENCE')
    if 10000*abs(close-mark)/mark > MAX_MARK_TO_SIGNAL_BPS:
        return reject('PRICE_MOVED_SINCE_CANDLE')
    if event['direction'] == 'LONG' and close <= boundary:
        return reject('INVALID_LONG_BOUNDARY')
    if event['direction'] == 'SHORT' and close >= boundary:
        return reject('INVALID_SHORT_BOUNDARY')
    output.update(result='QUOTE_SCREEN_PASS', reason='PUBLIC_BBO_AND_MARK_FRESH_AT_CAPTURE',
                  candidate_age_s=round(lag, 2), price_at_capture=mark,
                  book_bid=bid, book_ask=ask, spread_bps=round(spread_bps,3),
                  quote_capture_age_s=round(max(mark_age,book_age),2),
                  important='NOT A PAPER OR LIVE TRADE: no entries/stops/TPs or fill evidence')
    return output


def update_archive(observations_path, events_path, output_path):
    """Append idempotent screens only for candidates not yet screened."""
    observations = read_jsonl(observations_path)
    events = read_jsonl(events_path)
    existing = read_jsonl(output_path)
    prior_ids = set()
    for row in existing:
        ident = row.get('event_id')
        if not isinstance(ident,str) or not ident or ident in prior_ids:
            raise ValueError('Output history malformed or duplicate')
        prior_ids.add(ident)
    index = {}
    for snapshot in observations:
        key = snapshot.get('capture_at_utc')
        if isinstance(key, str):
            index[key] = snapshot
    new = []
    local_ids = set()
    for row in events:
        ev = row.get('event')
        if not isinstance(ev, dict) or not isinstance(ev.get('id'),str):
            raise ValueError('Malformed candidate event')
        ident = ev['id']
        if ident in local_ids:
            raise ValueError('Duplicate candidate in source')
        local_ids.add(ident)
        if ident in prior_ids:
            continue
        new.append(screen(row, index.get(row.get('captured_at_utc'))))
        prior_ids.add(ident)
    if new:
        dest = Path(output_path)
        if dest.is_symlink():
            raise ValueError('Symlink output forbidden')
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open('a', encoding='utf-8') as f:
            for row in new:
                f.write(json.dumps(row, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',',':')) + '\n')
    return {'existing_screens': len(existing), 'new_screens': len(new),
            'new_quality_pass': sum(x['result']=='QUOTE_SCREEN_PASS' for x in new),
            'new_rejected': sum(x['result']=='REJECT' for x in new),
            'generated_paper_positions': 0, 'generated_pnl': False,
            'note': 'WATCH_ONLY screening only. No executions or outcome paths recorded.'}


def main():
    p = argparse.ArgumentParser(description='Read-only prospective signal quality audit; no trades')
    p.add_argument('--observations', type=Path, default=Path('radar-audit/data/observations.jsonl'))
    p.add_argument('--candidates', type=Path, default=Path('radar-audit/data/watch_only.jsonl'))
    p.add_argument('--output', type=Path, default=Path('paper-trading-v4/data/preflight_candidates.jsonl'))
    args = p.parse_args()
    print(json.dumps(update_archive(args.observations,args.candidates,args.output),ensure_ascii=False))

if __name__ == '__main__':
    main()
