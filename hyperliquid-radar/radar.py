#!/usr/bin/env python3
"""Read-only Hyperliquid BTC/ETH market radar, v2. No credentials or order execution.

Outputs technical data for human review. Never interprets a candidate as an order.
"""
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from time import sleep, time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'docs'
API = 'https://api.hyperliquid.xyz/info'
PAIRS = ('BTC', 'ETH')
TF_MS = {'5m': 300_000, '15m': 900_000, '1h': 3_600_000,
         '4h': 14_400_000, '1d': 86_400_000}
CHART_TFS = ('1h', '4h', '1d')
NUM_CANDLES = 370
RETRY_COUNT = 3


def utc(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).isoformat()


def finite_float(value, label='number'):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f'Non-finite {label}')
    return value


def rounded(value, digits=4):
    return round(value, digits) if value is not None and math.isfinite(value) else None


def post_info(payload, max_attempts=RETRY_COUNT):
    body = json.dumps(payload).encode('utf-8')
    req = Request(API, data=body, method='POST', headers={
        'Content-Type': 'application/json', 'User-Agent': 'HyperliquidReadOnlyRadar/2.0'})
    err = None
    for n in range(max_attempts):
        try:
            with urlopen(req, timeout=20) as response:
                if response.status != 200:
                    raise RuntimeError(f'HTTP {response.status}')
                return json.load(response)
        except Exception as exc:
            err = exc
            if n < max_attempts - 1:
                sleep(2 ** n)
    raise RuntimeError(f'Hyperliquid info query failed ({payload.get("type")}): {err}')


def ema(values, period):
    result = [None] * len(values)
    if len(values) < period:
        return result
    current = sum(values[:period]) / period
    result[period - 1] = current
    k = 2 / (period + 1)
    for i in range(period, len(values)):
        current = values[i] * k + current * (1 - k)
        result[i] = current
    return result


def rsi_wilder(values, period=14):
    result = [None] * len(values)
    if len(values) <= period:
        return result
    delta = [values[i] - values[i-1] for i in range(1, len(values))]
    gain = sum(max(x, 0) for x in delta[:period]) / period
    loss = sum(max(-x, 0) for x in delta[:period]) / period
    def rsi(g, l):
        return 50.0 if not g and not l else (100.0 if not l else 100-100/(1+g/l))
    result[period] = rsi(gain, loss)
    for i in range(period + 1, len(values)):
        change = delta[i-1]
        gain = (gain*(period-1)+max(change, 0))/period
        loss = (loss*(period-1)+max(-change, 0))/period
        result[i] = rsi(gain, loss)
    return result


def macd(values):
    fast, slow = ema(values, 12), ema(values, 26)
    line = [a-b if a is not None and b is not None else None for a,b in zip(fast,slow)]
    valid = [v for v in line if v is not None]
    sig = [None]*(len(line)-len(valid)) + ema(valid, 9)
    histogram = [a-b if a is not None and b is not None else None for a,b in zip(line,sig)]
    return line, sig, histogram


def atr_wilder(bars, period=14):
    tr=[]
    for i, bar in enumerate(bars):
        prev=bars[i-1]['c'] if i else bar['c']
        tr.append(max(bar['h']-bar['l'], abs(bar['h']-prev), abs(bar['l']-prev)))
    out=[None]*len(bars)
    if len(tr)<period:
        return out
    current=sum(tr[:period])/period
    out[period-1]=current
    for i in range(period,len(tr)):
        current=(current*(period-1)+tr[i])/period
        out[i]=current
    return out


def retrieve_bars(coin, tf, now_ms):
    interval=TF_MS[tf]
    raw=post_info({'type':'candleSnapshot', 'req':{'coin':coin, 'interval':tf,
                  'startTime':now_ms-(NUM_CANDLES+2)*interval,'endTime':now_ms}})
    if not isinstance(raw,list):
        raise ValueError(f'Invalid candle payload {coin} {tf}')
    bars=[]
    for b in raw:
        t, finish=int(b['t']),int(b['T'])
        # Only fully closed candles, do not use the live/current candle.
        if finish+1 > now_ms - 1000:
            continue
        if finish+1-t != interval:
            raise ValueError(f'Unexpected candle interval {coin} {tf}')
        bar={'t':t,'T':finish,'o':finite_float(b['o']),'h':finite_float(b['h']),
             'l':finite_float(b['l']),'c':finite_float(b['c']), 'v':finite_float(b['v'])}
        if min(bar['o'],bar['h'],bar['l'],bar['c']) <= 0 or bar['v'] < 0:
            raise ValueError(f'Invalid OHLCV {coin} {tf}')
        if bar['l'] > min(bar['o'],bar['c']) or bar['h'] < max(bar['o'],bar['c']):
            raise ValueError(f'Inconsistent OHLC {coin} {tf}')
        bars.append(bar)
    bars.sort(key=lambda b:b['t'])
    if len(bars) < 225:
        raise RuntimeError(f'Insufficient EMA200 candle warm-up: {coin} {tf} {len(bars)}')
    if any(bars[i]['t'] - bars[i-1]['t'] != interval for i in range(1,len(bars))):
        raise RuntimeError(f'Gaps or duplicate candles: {coin} {tf}')
    age = now_ms-(bars[-1]['T']+1)
    # A last completed bar is 0-1 interval old (plus API latency).
    if age < 0 or age > interval+120_000:
        raise RuntimeError(f'Stale candles: {coin} {tf}, age={age} ms')
    return bars


def calculate(coin, tf, bars):
    closes=[b['c'] for b in bars]
    vols=[b['v'] for b in bars]
    e20,e50,e200=ema(closes,20),ema(closes,50),ema(closes,200)
    rsis=rsi_wilder(closes)
    line,sig,hist=macd(closes)
    atrs=atr_wilder(bars)
    prev20=bars[-21:-1]
    lo=min(b['l'] for b in prev20)
    hi=max(b['h'] for b in prev20)
    av=sum(vols[-21:-1])/20
    last=bars[-1]
    info={
        'symbol':coin, 'timeframe':tf,
        'last_closed_candle_utc':utc(last['T']+1),
        'last_close':rounded(closes[-1]),'prev_close':rounded(closes[-2]),
        'last_open':rounded(last['o']), 'last_high':rounded(last['h']), 'last_low':rounded(last['l']),
        'ema20':rounded(e20[-1]),'ema50':rounded(e50[-1]),'ema200':rounded(e200[-1]),
        'rsi14':rounded(rsis[-1],2),'macd':rounded(line[-1]),
        'macd_signal':rounded(sig[-1]),'macd_histogram':rounded(hist[-1]),
        'macd_hist_prev':rounded(hist[-2]),'atr14':rounded(atrs[-1]),
        'prior20_high':rounded(hi),'prior20_low':rounded(lo),
        'last_volume':rounded(vols[-1]),
        'volume_vs_previous20':rounded(vols[-1]/av,3) if av>0 else None,
        'bar_count':len(bars),
        'price_above_ema20':bool(closes[-1]>e20[-1]),
        'price_above_ema50':bool(closes[-1]>e50[-1]),
        'price_above_ema200':bool(closes[-1]>e200[-1]),
        'closed_above_prior20_high':bool(closes[-1]>hi),
        'closed_below_prior20_low':bool(closes[-1]<lo),
        'last_candle_bullish':bool(closes[-1]>last['o']),
        'macd_momentum_rising':bool(hist[-1]>hist[-2]),
    }
    series=(e20,e50,e200,rsis,line,sig,hist)
    return info,series


def retrieve_book(coin, now_ms):
    raw=post_info({'type':'l2Book','coin':coin})
    if not isinstance(raw,dict) or len(raw.get('levels',[]))!=2:
        raise ValueError(f'Invalid L2 book {coin}')
    bids,asks=raw['levels']
    if not bids or not asks:
        raise ValueError(f'Empty L2 book {coin}')
    bid,ask=finite_float(bids[0]['px']),finite_float(asks[0]['px'])
    if bid<=0 or ask<=bid:
        raise ValueError(f'Crossed/invalid L2 book {coin}')
    stamp=int(raw['time'])
    age=now_ms-stamp
    if age < -10_000 or age > 120_000:
        raise ValueError(f'Stale L2 book {coin}: {age} ms')
    mid=(bid+ask)/2
    def top5(side):
        return sum(finite_float(l['px'])*finite_float(l['sz']) for l in side[:5])
    bid_depth=top5(bids);ask_depth=top5(asks)
    return {'observed_at_utc':utc(stamp),'age_seconds_at_collection':max(0,round(age/1000,1)),
            'best_bid':bid,'best_ask':ask,'mid_price':rounded(mid),
            'spread_bps':rounded((ask-bid)/mid*10_000,2),
            'bid_depth_top5_usdc':rounded(bid_depth,2),'ask_depth_top5_usdc':rounded(ask_depth,2),
            'depth_imbalance_top5':rounded((bid_depth-ask_depth)/(bid_depth+ask_depth),4) if bid_depth+ask_depth>0 else None}


def size_position(entry, stop, tp1, tp2, max_margin=1000.0,
                  max_risk=52.0, conservative_cost_fraction=0.005):
    """Conservative indicative futures sizing, not an order or fee quotation."""
    if not all(math.isfinite(float(x)) for x in (entry,stop,tp1,tp2,max_margin,max_risk,conservative_cost_fraction)):
        raise ValueError('Nonfinite sizing input')
    if entry <= 0 or stop <= 0 or tp1 <= 0 or tp2 <= 0 or max_margin <= 0 or max_risk <= 0 or conservative_cost_fraction < 0 or conservative_cost_fraction > 0.05:
        raise ValueError('Invalid sizing input')
    if not ((stop<entry<tp1<tp2) or (stop>entry>tp1>tp2)):
        raise ValueError('Invalid direction: entry, stop, TP1, TP2')
    risk_frac=abs(stop-entry)/entry
    nominal=min(2*max_margin,max_risk/(risk_frac+conservative_cost_fraction))
    loss_price=nominal*risk_frac
    costs=nominal*conservative_cost_fraction
    tp1_gross=nominal*abs(tp1-entry)/entry
    tp2_gross=nominal*abs(tp2-entry)/entry
    rr=(tp2_gross-costs)/(loss_price+costs) if loss_price+costs>0 else None
    return {'margin_usdc':rounded(nominal/2,2),'notional_usdc':rounded(nominal,2),
            'quantity':rounded(nominal/entry,8),'stop_loss_usdc_est':rounded(loss_price+costs,2),
            'costs_reserve_usdc':rounded(costs,2),
            'tp1_net_est_usdc':rounded(tp1_gross-costs,2),
            'tp2_net_est_usdc':rounded(tp2_gross-costs,2),
            'rr_tp2_net_est':rounded(rr,2),
            'warning':'Estimation indicative: réserve frais+slippage 0,5% du nominal; pertes réelles possiblement supérieures.'}


def make_chart(coin,tf,bars,indicators):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    e20,e50,e200,rsis,line,sig,hist=indicators
    start=max(0,len(bars)-(150 if tf=='1d' else 115))
    series=bars[start:];x=list(range(len(series)))
    fig,axes=plt.subplots(4,1,figsize=(12,9),sharex=True,
          gridspec_kw={'height_ratios':[4,1,1.4,1.4]},constrained_layout=True)
    for arr,name in (([b['c'] for b in bars],'Clôture'),(e20,'EMA20'),(e50,'EMA50'),(e200,'EMA200')):
        axes[0].plot(x,arr[start:],label=name,linewidth=1.3)
    axes[0].set_title(f'{coin} perp — {tf} — bougies clôturées UTC')
    axes[0].legend(loc='upper left',fontsize=8,ncol=4)
    axes[1].bar(x,[b['v'] for b in series])
    axes[2].plot(x,rsis[start:],label='RSI14');axes[2].axhline(70,ls='--');axes[2].axhline(30,ls='--')
    axes[2].set_ylim(0,100)
    axes[3].plot(x,line[start:],label='MACD');axes[3].plot(x,sig[start:],label='Signal')
    axes[3].bar(x,[0 if v is None else v for v in hist[start:]],alpha=.4)
    axes[3].legend(fontsize=8)
    for ax in axes:ax.grid(alpha=.2)
    ticks=list(range(0,len(series),max(1,len(series)//7)))
    if ticks[-1]!=len(series)-1:ticks.append(len(series)-1)
    axes[3].set_xticks(ticks,labels=[datetime.fromtimestamp(series[i]['t']/1000,timezone.utc).strftime('%m-%d %Hh') for i in ticks],rotation=15,fontsize=8)
    fig.savefig(OUT/f'{coin}_{tf}.png',dpi=100)
    plt.close(fig)


def build_report(snapshot):
    lines=['# Hyperliquid BTC / ETH — Radar v2', '',
           f'**Snapshot : {snapshot["updated_at_utc"]}** — collecte {snapshot["collection_duration_seconds"]}s', '',
           '**Sécurité** : cours perpétuels, sans clé, sans exécution. Les chiffres ne sont pas des ordres. Vérifier horodatage et conditions de liquidité avant toute entrée.', '']
    for coin in PAIRS:
        a=snapshot['assets'][coin];c=a['perp_context'];b=a.get('order_book') or {}
        lines += [f'## {coin} — mark {c["mark_price_usdc"]} | mid {c.get("mid_price_usdc")}', '',
                  f'Prix relevé {c["price_observed_at_utc"]} | funding horaire {c["funding_hourly_pct"]}% | OI {c["open_interest_base"]} {coin} | OI Δ depuis dernier contrôle : {c.get("oi_change_pct_since_previous_snapshot")}% | spread {b.get("spread_bps")} bps', '',
                  '| Clôture UTC | Clôture | EMA20 | EMA50 | EMA200 | RSI | MACD hist | vol. relat. | ATR | résistance 20 | support 20 |',
                  '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
        for tf in TF_MS:
            p=a['timeframes'][tf]
            lines.append(f'| {tf} {p["last_closed_candle_utc"]} | {p["last_close"]} | {p["ema20"]} | {p["ema50"]} | {p["ema200"]} | {p["rsi14"]} | {p["macd_histogram"]} | {p["volume_vs_previous20"]}x | {p["atr14"]} | {p["prior20_high"]} | {p["prior20_low"]} |')
        lines += ['', 'Graphiques indicatifs (actualisés moins fréquemment que les chiffres) : '+ ' · '.join(f'[{tf}]({coin}_{tf}.png)' for tf in CHART_TFS), '']
    lines += ['## Limites', '', '- Carnet : photographie ponctuelle, profondeur top5 non garantie lors de l’exécution.',
              '- Les annonces macro, ETF, news et soldes de portefeuille ne sont pas fournis par Hyperliquid.',
              '- Une cassure, un RSI ou une hausse de volume isolés ne valident pas un trade.',
              '- Les timestamps des dernières bougies clôturées ne sont pas des timestamps du prix mark.',
              '- Les arrêts de GitHub Actions peuvent rendre ce rapport périmé : contrôler `updated_at_utc`.',
              '- Le coût de 0,5% utilisé par le calculateur indicatif est une réserve prudente, pas un tarif officiel.',
              '', 'Source : [API Hyperliquid](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint).']
    return '\n'.join(lines)+'\n'


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    started=int(time()*1000)
    previous=None
    latest=OUT/'latest.json'
    if latest.exists():
        try:previous=json.loads(latest.read_text(encoding='utf-8'))
        except (ValueError,OSError):pass
    data=post_info({'type':'metaAndAssetCtxs'})
    if not isinstance(data,list) or len(data)!=2 or not isinstance(data[0],dict):
        raise ValueError('Unexpected perp metadata')
    universe={a['name']:(i,a) for i,a in enumerate(data[0]['universe'])}
    snap={'schema_version':2, 'updated_at_utc':None,
          'source':'Hyperliquid official info API — perpetual BTC/ETH, NOT spot',
          'collection_started_at_utc':utc(started), 'assets':{},
          'quality':{'validation':'All required price/context/candle/book requests must pass; no trades executed.'}}
    charts_enabled=os.getenv('CHARTS_ENABLED','0').lower() in ('1','true','yes')
    chart_sets=[]
    for coin in PAIRS:
        if coin not in universe:raise RuntimeError(f'{coin} missing from Hyperliquid universe')
        index,meta=universe[coin]
        ctx=data[1][index]
        mark=finite_float(ctx['markPx'])
        if mark<=0:raise ValueError(f'{coin} invalid mark')
        mid=finite_float(ctx.get('midPx') or mark)
        oi=finite_float(ctx['openInterest'])
        if oi<0:raise ValueError('Negative OI')
        old_oi=None
        try:old_oi=previous['assets'][coin]['perp_context']['open_interest_base']
        except (TypeError,KeyError):pass
        oi_delta=(oi/old_oi-1)*100 if old_oi and old_oi>0 else None
        per={'mark_price_usdc':mark,'mid_price_usdc':mid,
             'price_observed_at_utc':utc(started),
             'funding_hourly_pct':rounded(finite_float(ctx['funding'])*100,7),
             'open_interest_base':oi,
             'open_interest_usdc_approx':rounded(oi*mark,2),
             'oi_change_pct_since_previous_snapshot':rounded(oi_delta,4),
             'day_notional_volume_usdc':rounded(finite_float(ctx['dayNtlVlm']),2),
             'size_decimals':meta.get('szDecimals')}
        asset={'perp_context':per,'timeframes':{},'order_book':None}
        for tf in TF_MS:
            bars=retrieve_bars(coin,tf,int(time()*1000))
            metrics,indicators=calculate(coin,tf,bars)
            asset['timeframes'][tf]=metrics
            if charts_enabled and tf in CHART_TFS:
                chart_sets.append((coin,tf,bars,indicators))
        asset['order_book']=retrieve_book(coin,int(time()*1000))
        snap['assets'][coin]=asset
    if charts_enabled:
        for args in chart_sets:make_chart(*args)
    finished=int(time()*1000)
    snap['updated_at_utc']=utc(finished)
    snap['collection_duration_seconds']=rounded((finished-started)/1000,1)
    snap['quality']['price_sample_age_seconds_at_publish']=rounded((finished-started)/1000,1)
    if finished-started>180_000:
        raise RuntimeError('Snapshot collection too slow for reliable alerts (>3 min)')
    # Atomic files: preserve previous valid JSON in case of API or processing failures.
    tmp=latest.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(snap,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    os.replace(tmp,latest)
    report=OUT/'report.md'; temp=report.with_suffix('.md.tmp')
    temp.write_text(build_report(snap),encoding='utf-8')
    os.replace(temp,report)
    print(f'Radar v2 BTC ETH updated: {snap["updated_at_utc"]}; charts={charts_enabled}')

if __name__=='__main__':main()
