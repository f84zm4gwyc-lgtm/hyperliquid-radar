#!/usr/bin/env python3
"""Read-only BTC/ETH technical snapshots from Hyperliquid public perpetuals API.

No wallet connection, private key, API key or trade execution.
"""
import json
import math
import os
from pathlib import Path
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from time import time, sleep

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'docs'
API = 'https://api.hyperliquid.xyz/info'
PAIRS = ('BTC', 'ETH')
TF_MS = {'1h': 3_600_000, '4h': 14_400_000, '1d': 86_400_000}
NUM_CANDLES = 365  # Enough EMA200 warm-up; <500 cap per API request.


def post_info(payload, max_attempts=3):
    body = json.dumps(payload).encode('utf-8')
    req = Request(API, data=body, headers={'Content-Type': 'application/json', 'User-Agent': 'CryptoRadarReadOnly/1.0'}, method='POST')
    exc = None
    for attempt in range(max_attempts):
        try:
            with urlopen(req, timeout=25) as resp:
                result = json.load(resp)
            return result
        except Exception as err:
            exc = err
            if attempt < max_attempts - 1:
                sleep(2 * (attempt + 1))
    raise RuntimeError(f'Hyperliquid API unavailable: {exc}')


def ema(values, period):
    """Conventional EMA, seeded with an SMA after period observations."""
    out = [None] * len(values)
    if len(values) < period:
        return out
    seed = sum(values[:period]) / period
    out[period - 1] = seed
    alpha = 2 / (period + 1)
    for i in range(period, len(values)):
        seed = alpha * values[i] + (1 - alpha) * seed
        out[i] = seed
    return out


def rsi_wilder(values, period=14):
    out = [None] * len(values)
    if len(values) <= period:
        return out
    diffs = [values[i] - values[i-1] for i in range(1, len(values))]
    gain = sum(max(x, 0) for x in diffs[:period]) / period
    loss = sum(max(-x, 0) for x in diffs[:period]) / period
    def compute(g, l):
        return 50.0 if not g and not l else (100.0 if not l else (100 - 100/(1+g/l)))
    out[period] = compute(gain, loss)
    for i in range(period + 1, len(values)):
        d = values[i] - values[i-1]
        gain = (gain * (period - 1) + max(0, d)) / period
        loss = (loss * (period - 1) + max(0, -d)) / period
        out[i] = compute(gain, loss)
    return out


def macd(values):
    e12, e26 = ema(values, 12), ema(values, 26)
    line = [(a-b) if a is not None and b is not None else None for a,b in zip(e12,e26)]
    valid = [x for x in line if x is not None]
    signal_valid = ema(valid, 9)
    signal = [None] * (len(line) - len(valid)) + signal_valid
    hist = [x-y if x is not None and y is not None else None for x,y in zip(line, signal)]
    return line, signal, hist


def atr_wilder(bars, period=14):
    tr = []
    for i,b in enumerate(bars):
        prev = bars[i-1]['c'] if i else b['c']
        tr.append(max(b['h']-b['l'], abs(b['h']-prev), abs(b['l']-prev)))
    out = [None]*len(bars)
    if len(tr) < period:
        return out
    a = sum(tr[:period])/period
    out[period-1] = a
    for i in range(period, len(tr)):
        a = ((period-1)*a+tr[i])/period
        out[i] = a
    return out


def safe_round(value, digits=4):
    return round(value, digits) if value is not None and math.isfinite(value) else None


def retrieve_bars(coin, tf, now_ms):
    interval = TF_MS[tf]
    raw = post_info({'type':'candleSnapshot', 'req':{'coin':coin,'interval':tf,'startTime':now_ms-(NUM_CANDLES+2)*interval,'endTime':now_ms}})
    if not isinstance(raw, list):
        raise ValueError(f'Unexpected candles {coin} {tf}: {type(raw).__name__}')
    bars = []
    for c in raw:
        if int(c['T']) < now_ms - 3000:  # Remove incomplete candle; avoid signal repainting.
            bars.append({'t':int(c['t']), 'T':int(c['T']), 'o':float(c['o']),'h':float(c['h']), 'l':float(c['l']),'c':float(c['c']),'v':float(c['v'])})
    bars.sort(key=lambda b:b['t'])
    if len(bars) < 230:
        raise RuntimeError(f'Too few confirmed candles for {coin} {tf}: {len(bars)}')
    if now_ms - bars[-1]['T'] > interval*2 + 60_000:
        raise RuntimeError(f'Stale candle data for {coin} {tf}: latest T={bars[-1]["T"]}')
    return bars


def calculate(coin, tf, bars):
    closes = [b['c'] for b in bars]
    vols = [b['v'] for b in bars]
    ema20,ema50,ema200 = ema(closes,20),ema(closes,50),ema(closes,200)
    rsis = rsi_wilder(closes)
    macd_line,macd_signal,macd_hist = macd(closes)
    atrs = atr_wilder(bars)
    # Previous 20 CLOSED candles excluding most recent; usable as breakout boundaries.
    prev20 = bars[-21:-1]
    current = bars[-1]
    avgvol = sum(vols[-21:-1])/20
    result = {
        'symbol': coin,
        'timeframe':tf,
        'last_closed_candle_utc':datetime.fromtimestamp((current['T']+1)/1000,timezone.utc).isoformat(),
        'last_close':safe_round(closes[-1],4),
        'prev_close':safe_round(closes[-2],4),
        'ema20':safe_round(ema20[-1],4),
        'ema50':safe_round(ema50[-1],4),
        'ema200':safe_round(ema200[-1],4),
        'rsi14':safe_round(rsis[-1],2),
        'macd':safe_round(macd_line[-1],4),
        'macd_signal':safe_round(macd_signal[-1],4),
        'macd_histogram':safe_round(macd_hist[-1],4),
        'macd_hist_prev':safe_round(macd_hist[-2],4),
        'atr14':safe_round(atrs[-1],4),
        'prior20_high':safe_round(max(b['h'] for b in prev20),4),
        'prior20_low':safe_round(min(b['l'] for b in prev20),4),
        'last_volume':safe_round(vols[-1],4),
        'volume_vs_previous20':safe_round(vols[-1]/avgvol,3) if avgvol else None,
        'bar_count':len(bars),
        'price_above_ema20':closes[-1]>ema20[-1],
        'price_above_ema50':closes[-1]>ema50[-1],
        'price_above_ema200':closes[-1]>ema200[-1],
        'closed_above_prior20_high':closes[-1] > max(b['h'] for b in prev20),
        'closed_below_prior20_low':closes[-1] < min(b['l'] for b in prev20),
    }
    return result, (ema20,ema50,ema200,rsis,macd_line,macd_signal,macd_hist)


def make_chart(coin, tf, bars, indicators):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    ema20,ema50,ema200,rsis,macd_line,macd_signal,macd_hist = indicators
    n = 115 if tf != '1d' else 150
    start=max(0,len(bars)-n)
    series = bars[start:]
    x=list(range(len(series)))
    fig,axes=plt.subplots(4,1,figsize=(12,9),sharex=True, gridspec_kw={'height_ratios':[4,1,1.4,1.4]}, constrained_layout=True)
    ax=axes[0]
    ax.plot(x,[b['c'] for b in series],label='Clôture',linewidth=1.8)
    for arr,name in ((ema20,'EMA20'),(ema50,'EMA50'),(ema200,'EMA200')):
        ax.plot(x,arr[start:],label=name,linewidth=1.0)
    ax.set_title(f'Hyperliquid perpetuals — {coin} {tf} — bougies clôturées (UTC)')
    ax.legend(loc='upper left',ncol=4,fontsize=8)
    ax.grid(alpha=.2)
    axes[1].bar(x,[b['v'] for b in series],width=.85)
    axes[1].set_ylabel('Volume')
    axes[1].grid(alpha=.2)
    axes[2].plot(x,rsis[start:],label='RSI 14')
    axes[2].axhline(70,linestyle='--',linewidth=.7)
    axes[2].axhline(30,linestyle='--',linewidth=.7)
    axes[2].set_ylim(0,100)
    axes[2].legend(fontsize=8)
    axes[2].grid(alpha=.2)
    axes[3].plot(x,macd_line[start:],label='MACD')
    axes[3].plot(x,macd_signal[start:],label='Signal')
    axes[3].bar(x,[0 if h is None else h for h in macd_hist[start:]],alpha=.35)
    axes[3].legend(fontsize=8)
    axes[3].grid(alpha=.2)
    ticks=list(range(0,len(series),max(1,len(series)//7)))
    if ticks[-1] != len(series)-1:
        ticks.append(len(series)-1)
    axes[-1].set_xticks(ticks, labels=[datetime.fromtimestamp(series[i]['t']/1000,timezone.utc).strftime('%m-%d %Hh') for i in ticks],rotation=15,fontsize=8)
    fig.savefig(OUT/f'{coin}_{tf}.png',dpi=110)
    plt.close(fig)


def build_report(snapshot):
    lines=['# Hyperliquid BTC / ETH — radar technique', '', f'**Mise à jour UTC : {snapshot["updated_at_utc"]}**', '', '> Prix perpétuels Hyperliquid; le BTC spot (UBTC) peut présenter un écart. Cette page ne constitue pas un signal de trading. Vérifier que les données sont fraîches avant décision.', '']
    for coin in PAIRS:
        asset=snapshot['assets'][coin]
        ctx=asset['perp_context']
        lines.extend([f'## {coin} — prix mark {ctx.get("mark_price_usdc")}', '', f'Funding horaire : {ctx.get("funding_hourly_pct")}% | Open interest : {ctx.get("open_interest_base")} {coin} | Notionnel indicatif : {ctx.get("open_interest_usdc_approx")} USD', '', '| UTC bougie | Cours clôture | EMA20 | EMA50 | EMA200 | RSI14 | MACD hist. | Volume / moyenne 20 | Plus haut 20 préc. | Plus bas 20 préc. |', '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|'])
        for tf in TF_MS:
            v=asset['timeframes'][tf]
            lines.append(f'| {tf} — {v["last_closed_candle_utc"]} | {v["last_close"]} | {v["ema20"]} | {v["ema50"]} | {v["ema200"]} | {v["rsi14"]} | {v["macd_histogram"]} | {v["volume_vs_previous20"]}x | {v["prior20_high"]} | {v["prior20_low"]} |')
        lines.extend(['', 'Graphiques (bougies clôturées) : '+ ' · '.join(f'[{tf}]({coin}_{tf}.png)' for tf in TF_MS), ''])
    lines.extend(['## Interprétation / sécurité','', '- EMA, RSI et MACD calculés avec bougies terminées uniquement. Le prix *mark* instantané peut être différent de la dernière clôture.','- Plus haut/bas 20 = limites des 20 bougies clôturées précédentes (exclut la dernière). Un franchissement seul ne valide pas une entrée.','- Les données ETF, news, liquidations, calendrier macro et positions de l’utilisateur ne sont **pas** couvertes par ce rapport.','- En cas de retard ou d’échec GitHub Actions, le rapport reste ancien : toujours vérifier l’horodatage.','- Aucune clé, connexion wallet ni fonction de passage d’ordre.', '', 'Source : [Hyperliquid API publique](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint).'])
    return '\n'.join(lines)+'\n'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    now_ms=int(time()*1000)
    ctx=post_info({'type':'metaAndAssetCtxs'})
    if not isinstance(ctx,list) or len(ctx)!=2:
        raise RuntimeError('Unexpected perp context format')
    names=[x['name'] for x in ctx[0]['universe']]
    snapshot={'updated_at_utc':datetime.fromtimestamp(now_ms/1000,timezone.utc).isoformat(), 'source':'Hyperliquid public perpetuals API (not spot)', 'assets':{}}
    for coin in PAIRS:
        if coin not in names:
            raise RuntimeError(f'{coin} is missing from perp universe')
        context=ctx[1][names.index(coin)]
        mark=float(context['markPx'])
        oi=float(context['openInterest'])
        asset={'perp_context':{'mark_price_usdc':mark,'funding_hourly_pct':safe_round(float(context['funding'])*100,6),'open_interest_base':oi,'open_interest_usdc_approx':safe_round(oi*mark,2),'day_notional_volume_usdc':safe_round(float(context['dayNtlVlm']),2)},'timeframes':{}}
        for tf in TF_MS:
            bars=retrieve_bars(coin,tf,now_ms)
            metrics,indicators=calculate(coin,tf,bars)
            asset['timeframes'][tf]=metrics
            make_chart(coin,tf,bars,indicators)
        snapshot['assets'][coin]=asset
    (OUT/'latest.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (OUT/'report.md').write_text(build_report(snapshot),encoding='utf-8')
    print(f'Updated BTC/ETH radar: {snapshot["updated_at_utc"]}')

if __name__=='__main__':
    main()
