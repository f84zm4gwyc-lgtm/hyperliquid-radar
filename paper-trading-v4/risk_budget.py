#!/usr/bin/env python3
"""Offline prospective risk budget for simultaneous paper positions.

Conservative: reserves the original estimated stop loss for each outstanding
paper position, even after TP1. Never reads a broker or places an order.
"""
from __future__ import annotations
from decimal import Decimal
from paper_radar import d,position

MAX_AGGREGATE_RISK_USDC=Decimal('52')
OPEN_STATES=frozenset({'OPEN_PAPER','PARTIAL_PAPER'})


def check_combined_risk(plan, existing, *, risk_ceiling=52, real_positions_known_flat=False):
    """Reject new paper entries on unknown real exposure or risk budget breach.

    existing are documented OPEN paper positions. This does not establish that
    the user's actual trading account is flat. A human must confirm separately.
    """
    ceiling=d(risk_ceiling)
    if ceiling<=0 or ceiling>MAX_AGGREGATE_RISK_USDC:
        raise ValueError('Combined risk limit cannot exceed 52 USDC')
    if not isinstance(existing,list):raise ValueError('Existing positions must be a list')
    preview=position(plan)
    if not preview.get('accepted'):
        return {'accepted':False,'reason':'Prospective plan rejected: '+preview.get('reason','unknown'),
                'paper_preview':preview,'real_order_authorized':False}
    if real_positions_known_flat is not True:
        return {'accepted':False,'reason':'Unknown live portfolio exposure (human review required)',
                'paper_preview':preview,'real_order_authorized':False}
    ids=set();reserved=d(0)
    for r in existing:
        if not isinstance(r,dict):raise ValueError('Malformed prior position')
        ident=r.get('trade_id')
        if not ident or ident in ids or ident==plan['trade_id']:
            raise ValueError('Missing/duplicate trade ID')
        ids.add(ident)
        state=r.get('state')
        if state not in OPEN_STATES:
            raise ValueError('Only OPEN_PAPER/PARTIAL_PAPER records accepted; filter closed items explicitly')
        risk=d(r.get('reserved_initial_stop_risk_usdc',-1))
        if risk<0 or risk>MAX_AGGREGATE_RISK_USDC:
            raise ValueError('Invalid reserved paper risk')
        reserved+=risk
    new_risk=d(preview['estimated_max_stop_loss_usdc'])
    combined=reserved+new_risk
    allowed=combined<=ceiling
    return {'accepted':allowed,
            'reason':'Within combined paper risk budget' if allowed else 'Combined stop loss budget exceeded',
            'new_risk_usdc':float(new_risk),
            'existing_reserved_risk_usdc':float(reserved),
            'combined_estimated_stop_loss_usdc':float(combined),
            'combined_limit_usdc':float(ceiling),
            'paper_preview':preview,
            'real_order_authorized':False,
            'caveat':'Approximate stop losses, execution gaps may exceed estimates; live portfolio confirmation required'}
