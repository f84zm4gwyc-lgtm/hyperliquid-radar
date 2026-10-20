"""Read-only market signal *candidates*. Never places or recommends executing an order.

The intentionally sparse breakout gate minimizes noise; it is not an edge or
validated strategy. Backtests and out-of-sample paper trading are required.
"""
from dataclasses import dataclass
from math import isfinite

PAIRS = ('BTC', 'ETH')
TF_MS = {'5m':300_000, '15m':900_000, '1h':3_600_000,'4h':14_400_000,'1d':86_400_000}


def check_bar(value):
    try:
        b={k:float(value[k]) for k in ('o','h','l','c','v')}
        t=int(value['t']); T=int(value['T'])
        coin=str(value.get('s',value.get('symbol','')))
        tf=str(value.get('i',value.get('timeframe','')))
    except (KeyError,ValueError,TypeError,OverflowError) as e:
        raise ValueError('Malformed candle') from e
    if not all(isfinite(x) for x in b.values()) or min(b['o'],b['h'],b['l'],b['c'])<=0 or b['v']<0:
        raise ValueError('Invalid OHLCV')
    if b['h'] < max(b['o'], b['c']) or b['l'] > min(b['o'], b['c']) or b['l'] > b['h']:
        raise ValueError('Inconsistent OHLCV')
    if coin not in PAIRS or tf not in TF_MS or t<1_000_000_000_000 or T+1-t!=TF_MS[tf] or t%TF_MS[tf]!=0:
        raise ValueError('Unexpected candle coin/timeframe/duration')
    return {'t':t,'T':T, **b, 's':coin,'i':tf}


def ema(values, n):
    if len(values)<n: return None
    v=sum(values[:n])/n; k=2/(n+1)
    for x in values[n:]:v=k*x+(1-k)*v
    return v


def atr(bars,n=14):
    if len(bars)<n+1:return None
    trs=[max(b['h']-b['l'],abs(b['h']-bars[i-1]['c']),abs(b['l']-bars[i-1]['c'])) for i,b in enumerate(bars) if i]
    if len(trs)<n:return None
    return sum(trs[-n:])/n  # simple, robust local noise proxy, NOT Wilder ATR


def candidate(coin, candle15, h1, h4):
    """Closed 15m breakout candidate, NOT validated trade.

    Inputs must be precisely time-aligned by the caller (no future closes).
    This rule is a baseline hypothesis, not an empirically proven signal.
    """
    if coin not in PAIRS or len(candle15)<35 or len(h1)<55 or len(h4)<55:return None
    current=candle15[-1]
    cutoff=current['T']+1
    if any(a['T']+1>cutoff for a in [h1[-1],h4[-1]]):return None
    if any(v['i']!='15m' or v['s']!=coin for v in candle15[-21:]):return None
    prior=candle15[-21:-1]
    hi=max(x['h'] for x in prior);lo=min(x['l'] for x in prior)
    avg_vol=sum(x['v'] for x in prior)/20
    if avg_vol<=0 or current['v'] / avg_vol<1.3:return None
    trend1=ema([x['c'] for x in h1],20),ema([x['c'] for x in h1],50)
    trend4=ema([x['c'] for x in h4],20),ema([x['c'] for x in h4],50)
    direction=None
    if current['c']>hi and h1[-1]['c']>trend1[0]>trend1[1] and h4[-1]['c']>trend4[0] and trend4[0]>trend4[1]:
        direction='LONG'
    elif current['c']<lo and h1[-1]['c']<trend1[0]<trend1[1] and h4[-1]['c']<trend4[0] and trend4[0]<trend4[1]:
        direction='SHORT'
    if direction is None:return None
    return {'id':f'{coin}:15m:{current["t"]}:{direction}', 'coin':coin, 'direction':direction,
            'closed_at_ms':cutoff,'close':current['c'],'boundary':hi if direction=='LONG' else lo,
            'volume_ratio':round(current['v']/avg_vol,3),'status':'WATCH_ONLY',
            'reason':'Closed 15m breakout with volume >1.3x and 1h/4h trend; no validated edge',
            'warning':'NE PAS TRADER: aucun ordre, entrée, stop ni TP validé ici.'}


def aligned_bars(bars, cutoff_ms):
    """Forbid lookahead when matching HTF closes with a 15m signal."""
    return [b for b in bars if b['T']+1<=cutoff_ms]
