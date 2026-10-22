#!/usr/bin/env python3
"""Conservative evaluation for offline quote-replay results; NO trading.

Separates synthetic demos, unreviewed market captures, open signals and
low-confidence replays from the small, still-hypothetical reviewed cohort.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from statistics import mean
from paper_radar import iso,d


def evaluate(results, *, explicitly_reviewed_ids=None):
    # CRITICAL: a result JSON can set its OWN data_provenance string. Never
    # trust self-attested CAPTURED_LIVE_REVIEWED without separate human review.
    # The CLI intentionally has no bypass: offline estimates remain uncounted.
    reviewed=set(explicitly_reviewed_ids or ())
    seen=set();statuses={};excluded={};eligible=[]
    for r in results:
        trade_id=r.get('trade_id')
        if not isinstance(trade_id,str) or not trade_id or trade_id in seen:
            raise ValueError('Missing/duplicate trade_id')
        seen.add(trade_id)
        status=r.get('status','UNKNOWN');statuses[status]=statuses.get(status,0)+1
        if status!='CLOSED_ESTIMATE': reason='NOT_CLOSED_HIGH_CONFIDENCE'
        elif r.get('data_provenance')!='CAPTURED_LIVE_REVIEWED': reason='NOT_REVIEWED_LIVE_DATA'
        elif trade_id not in reviewed: reason='NO_INDEPENDENT_OPERATOR_REVIEW'
        elif r.get('incomplete_market_data') or not r.get('independent_quote_times_checked'):
            reason='MISSING_OR_DESYNCHRONIZED_QUOTES'
        elif r.get('funding_included') is not False or r.get('hypothetical_fills_only') is not True:
            reason='MISSING_COST_AND_FILL_DISCLOSURES'
        else: reason=None
        if reason:
            excluded[reason]=excluded.get(reason,0)+1
            continue
        events=r.get('events')
        if not isinstance(events,list) or not events or events[-1].get('event') not in ('TP2_ESTIMATE','STOP_ESTIMATE'):
            excluded['UNVERIFIABLE_FINAL_EVENT']=excluded.get('UNVERIFIABLE_FINAL_EVENT',0)+1
            continue
        at=iso(events[-1]['at_utc'])
        pnl=d(r['estimated_realised_pnl_ex_funding_usdc'])
        eligible.append((at,trade_id,pnl))
    # Chronological closure order, never caller-supplied file order.
    eligible.sort(key=lambda x:(x[0],x[1]))
    wins=sum(value>0 for _,_,value in eligible)
    positive=sum((v for _,_,v in eligible if v>0),start=d(0))
    negative=-sum((v for _,_,v in eligible if v<0),start=d(0))
    balance=peak=drawdown=d(0)
    for _,_,v in eligible:
        balance+=v;peak=max(peak,balance);drawdown=max(drawdown,peak-balance)
    n=len(eligible)
    return {'status':'EXPLORATORY_PAPER_ESTIMATE_NOT_LIVE_PNL',
            'all_results_count':len(results),'statuses':statuses,
            'counted_closed_reviewed_quotes':n,'excluded':excluded,
            'sample_sufficient_to_validate_edge':False,
            'sample_note':'No fixed count proves profitability; self-labeled JSON ignored until independent operator review',
            'estimated_win_rate_pct':round(100*wins/n,2) if n else None,
            'estimated_pnl_ex_funding_usdc':round(float(sum((v for _,_,v in eligible),start=d(0))),2),
            'estimated_expectancy_usdc_per_trade':round(float(mean(v for _,_,v in eligible)),2) if n else None,
            'estimated_profit_factor':round(float(positive/negative),3) if negative else None,
            'estimated_peak_to_trough_drawdown_usdc':round(float(drawdown),2),
            'closing_order_utc':[{'trade_id':trade_id,'closed_at_utc':at.isoformat()} for at,trade_id,_ in eligible],
            'disclaimer':'Hypothetical mark triggers and BBO fills; fees estimated; funding omitted; not investment performance'}


def main():
    p=argparse.ArgumentParser(description='Conservative paper metrics, no orders')
    p.add_argument('files',nargs='+',type=Path)
    args=p.parse_args()
    results=[json.loads(f.read_text(encoding='utf-8')) for f in args.files]
    print(json.dumps(evaluate(results),indent=2,ensure_ascii=False))


if __name__=='__main__':main()
