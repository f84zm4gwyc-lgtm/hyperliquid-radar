#!/usr/bin/env python3
"""Analyse des sorties paper, résultats ESTIMÉS hors funding, sans ordre réel."""
import argparse
import json
from pathlib import Path
from statistics import mean


def score(records):
    seen=set()
    for rec in records:
        key=rec.get('trade_id')
        if not key or key in seen: raise ValueError('Trade ID vide/dupliqué')
        seen.add(key)
    closed=[r for r in records if r.get('status')=='CLOSED_ESTIMATE']
    pnl=[float(r['estimated_realised_pnl_ex_funding_usdc']) for r in closed]
    pos=sum(v for v in pnl if v>0);loss=-sum(v for v in pnl if v<0)
    balance=peak=0.0; drawdown=0.0
    for v in pnl:
        balance+=v;peak=max(peak,balance);drawdown=max(drawdown,peak-balance)
    statuses={}
    for r in records:statuses[r.get('status','UNKNOWN')]=statuses.get(r.get('status','UNKNOWN'),0)+1
    return {'status':'PAPER_ONLY_ESTIMATE_NOT_VERIFIED','total_alerts':len(records),'statuses':statuses,
            'closed_trade_count':len(closed), 'wins':sum(v>0 for v in pnl),'losses':sum(v<0 for v in pnl),
            'win_rate_pct':round(100*sum(v>0 for v in pnl)/len(pnl),2) if pnl else None,
            'estimated_net_pnl_ex_funding_usdc':round(sum(pnl),2),
            'estimated_expectancy_usdc_per_closed_trade':round(mean(pnl),2) if pnl else None,
            'profit_factor':round(pos/loss,2) if loss>0 else None,
            'estimated_max_closed_trade_drawdown_usdc':round(drawdown,2),
            'critical_limitations':['Résultats OHLC sans ticks ni fills réels', 'Sorties ambiguës traitées au pire cas',
              'Coûts hypothétiques et funding exclu', 'Performances non probantes sans échantillon suffisant',
              'Un rapport de tests papier ne prouve aucune rentabilité future']}


def main():
    p=argparse.ArgumentParser()
    p.add_argument('files',nargs='+',type=Path)
    args=p.parse_args()
    rec=[json.loads(x.read_text(encoding='utf-8')) for x in args.files]
    print(json.dumps(score(rec),ensure_ascii=False,indent=2))
if __name__=='__main__':main()
