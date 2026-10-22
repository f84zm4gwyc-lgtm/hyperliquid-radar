"""Canonical paper risk calculations; copied from audited V4, no orders or network."""
from __future__ import annotations

import hashlib

import json

import math

from datetime import datetime, timezone, timedelta

from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING

from pathlib import Path

def d(x):
    if isinstance(x, bool):
        raise ValueError('Boolean is not a price or quantity')
    v = Decimal(str(x))
    if not v.is_finite():
        raise ValueError('Nombre non fini')
    return v

def iso(x):
    y = datetime.fromisoformat(x.replace('Z', '+00:00'))
    if y.tzinfo is None:
        raise ValueError('Horodatage UTC/offset obligatoire')
    return y.astimezone(timezone.utc)

def money(v):
    return round(float(v), 2)

def round_size_down(quantity, decimals):
    if isinstance(decimals, bool) or not isinstance(decimals, int) or not 0 <= decimals <= 8:
        raise ValueError('szDecimals invalide')
    step = Decimal(1).scaleb(-decimals)
    return d(quantity).quantize(step, rounding=ROUND_FLOOR)

def tick_for_price(price, size_decimals):
    """Pas conservateur qui respecte les règles prix Hyperliquid PERP.

    5 chiffres significatifs ET au plus 6-szDecimals décimales.
    En réalité un prix entier de >5 chiffres est également autorisé.
    """
    p = d(price)
    if p <= 0:
        raise ValueError('Prix négatif ou nul')
    if isinstance(size_decimals, bool) or not isinstance(size_decimals, int) or not 0 <= size_decimals <= 6:
        raise ValueError('szDecimals PERP invalide')
    from_sig = Decimal(1).scaleb(p.adjusted() - 4)
    from_dec = Decimal(1).scaleb(-(6-size_decimals))
    return max(from_sig, from_dec)

def compliant_price(price, size_decimals, direction):
    step = tick_for_price(price, size_decimals)
    p = d(price) / step
    mode = ROUND_CEILING if direction == 'up' else ROUND_FLOOR if direction == 'down' else None
    if mode is None:
        raise ValueError('direction=up/down')
    return p.to_integral_value(rounding=mode) * step

def validate(plan):
    required = ['trade_id','symbol','side','alert_received_at_utc','indicative_entry',
                'stop','tp1','tp2','sz_decimals']
    for key in required:
        if key not in plan:
            raise ValueError(f'Champ manquant: {key}')
    if plan['symbol'] not in ('BTC', 'ETH') or plan['side'] not in ('LONG','SHORT'):
        raise ValueError('BTC/ETH LONG/SHORT uniquement dans le prototype')
    iso(plan['alert_received_at_utc'])
    if not isinstance(plan['trade_id'],str) or not plan['trade_id'].strip():
        raise ValueError('trade_id manquant')
    for name in ('indicative_entry','stop','tp1','tp2'):
        if d(plan[name])<=0:raise ValueError('Prix <=0')
    a,b,c,e=map(lambda k:d(plan[k]),('indicative_entry','stop','tp1','tp2'))
    if not ((b<a<c<e) if plan['side']=='LONG' else (e<c<a<b)):
        raise ValueError('Direction entrée/stop/TP incohérente')
    sd=plan['sz_decimals']
    if isinstance(sd,bool) or not isinstance(sd,int) or not 0<=sd<=6:raise ValueError('sz_decimals invalide')
    for key, default in [('tp1_fraction','0.5'),('max_margin_usdc','1000'),
                         ('max_risk_usdc','52'),('leverage','2'),
                         ('cost_reserve_fraction','0.005'),('entry_slippage_fraction','0.0005'),
                         ('tp_slippage_fraction','0.0005'),('stop_slippage_fraction','0.0015'),
                         ('fee_per_side_fraction','0.00045'),('entry_tolerance_fraction','0.0025'),
                         ('entry_deadline_minutes','30')]:
        val=d(plan.get(key,default))
        if val<0: raise ValueError(f'{key} négatif')
    if not 0<d(plan.get('tp1_fraction','0.5'))<1:raise ValueError('Fraction TP1 invalide')
    if d(plan.get('leverage',2))>2 or d(plan.get('max_margin_usdc',1000))>1000 or d(plan.get('max_risk_usdc',52))>52:
        raise ValueError('Limites risque/marge/levier dépassées')
    if d(plan.get('cost_reserve_fraction','0.005'))>Decimal('.05'):
        raise ValueError('Réserve coûts aberrante')
    if any(d(plan.get(k,v)) <= 0 for k,v in (('max_margin_usdc',1000),('max_risk_usdc',52),('leverage',2))):
        raise ValueError('Limite de capital/levier nulle')
    if d(plan.get('min_weighted_rr','1.8')) < d('1.8'):
        raise ValueError('Ratio minimum non conforme aux règles')
    if d(plan.get('cost_reserve_fraction','0.005')) < d('0.005'):
        raise ValueError('Réserve conservatrice de 0,5% minimum dans ce prototype')
    if d(plan.get('entry_deadline_minutes','30'))>Decimal(240):
        raise ValueError('Délai de validité trop long')

def normalized_levels(plan, actual_entry):
    sd=plan['sz_decimals']; long=plan['side']=='LONG'
    return {'entry':compliant_price(actual_entry,sd,'up' if long else 'down'),
            'stop':compliant_price(plan['stop'],sd,'down' if long else 'up'),
            'tp1':compliant_price(plan['tp1'],sd,'down' if long else 'up'),
            'tp2':compliant_price(plan['tp2'],sd,'down' if long else 'up')}

def position(plan, actual_entry=None):
    validate(plan)
    E=d(actual_entry if actual_entry is not None else plan['indicative_entry'])
    p=normalized_levels(plan,E)
    E,S,T1,T2=p['entry'],p['stop'],p['tp1'],p['tp2']
    long=plan['side']=='LONG'
    if not ((S<E<T1<T2) if long else (T2<T1<E<S)):
        return {'accepted':False, 'reason':'Niveaux invalidés par arrondis'}
    cost=d(plan.get('cost_reserve_fraction','0.005'))
    fraction=d(plan.get('tp1_fraction','0.5'))
    maxrisk=d(plan.get('max_risk_usdc',52))
    margin=d(plan.get('max_margin_usdc',1000))
    leverage=d(plan.get('leverage',2))
    stop_fraction=abs(E-S)/E
    raw_nominal=min(margin*leverage,maxrisk/(stop_fraction+cost))
    quantity=round_size_down(raw_nominal/E,plan['sz_decimals'])
    nominal=quantity*E
    if quantity <= 0 or nominal < 10:
        return {'accepted':False, 'reason':'Quantité trop faible pour exécuter les règles de taille'}
    # Deux TP : sous-positions à quantité autorisée, sans surestimer le TP2.
    q1=round_size_down(quantity*fraction,plan['sz_decimals']);q2=quantity-q1
    if q1<=0 or q2<=0 or min(q1,q2)*E < 10:
        return {'accepted':False, 'reason':'Quantité sous la taille minimale sur un des TP'}
    realised_fraction=q1/quantity
    loss=nominal*(stop_fraction+cost)
    profit1=q1*abs(T1-E)-q1*E*cost
    profit2=q2*abs(T2-E)-q2*E*cost
    profit_both=profit1+profit2
    rr=profit_both/loss if loss>0 else Decimal(-1)
    result={'accepted':rr>=d(plan.get('min_weighted_rr','1.8')), 'ready_for_real_trade':False,
      'entry':float(E),'stop':float(S),'tp1':float(T1),'tp2':float(T2),
      'nominal_usdc':money(nominal),'quantity':float(quantity),
      'tp1_quantity':float(q1),'tp2_quantity':float(q2),
      'tp1_share_real':float(realised_fraction),'margin_usdc':money(nominal/leverage),
      'leverage':float(leverage),'estimated_max_stop_loss_usdc':money(loss),
      'tp1_gain_est_usdc':money(profit1),'tp2_gain_est_usdc':money(profit2),
      'weighted_total_gain_est_usdc':money(profit_both),
      'weighted_rr_net_est':round(float(rr),3),'cost_reserve_fraction':float(cost)}
    if not result['accepted']:result['reason']='R/R pondéré net insuffisant (TP1+TP2)'
    return result

def plan_hash(plan):
    return hashlib.sha256(json.dumps(plan,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
