"""BTC/ETH Hyperliquid *read-only* WebSocket observability service.

Render deployment: Python, build `pip install -r render-radar/requirements.txt`,
start `python render-radar/app.py`, health GET /healthz. No account access.

No broker keys / no private endpoints / no order execution / no monetary advice.
"""
import asyncio
import json
import logging
import math
import os
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from time import sleep

from aiohttp import web
from websockets.asyncio.client import connect

from engine import PAIRS, TF_MS, check_bar, candidate, aligned_bars
from bootstrap import get_public_bars, BootstrapError

LOG=logging.getLogger('radar.ws')
WS_URL='wss://api.hyperliquid.xyz/ws'
API_URL='https://api.hyperliquid.xyz/info'
PORT=int(os.getenv('PORT','10000'))
DB_PATH=Path(os.getenv('RADAR_DB_PATH','/tmp/radar_ws.sqlite3'))
HISTORY_LENGTH=270
# Prioritize the 15m setup and 1h/4h context; 5m arrives via WebSocket.
HISTORY_TFS=('15m','1h','4h')
SIGNAL_MAX_AGE_S=180
QUOTE_MAX_AGE_S=120
BOOK_MAX_SPREAD_BPS=30
# Does NOT promise instant execution and does not send trading notifications.


def utc_seconds(seconds):
    return datetime.fromtimestamp(seconds,timezone.utc).isoformat()


def read_rest_with_backoff(request, *, max_attempts=5):
    """Retry *only* REST HTTP 429, obeying a bounded Retry-After.

    429 can occur on cloud IPs shared by other customers. Never spin in a
    tight loop and never turn a failed bootstrap into a trading signal.
    """
    for attempt in range(max_attempts):
        try:
            with urlopen(request,timeout=20) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code != 429 or attempt==max_attempts-1:
                raise
            header=error.headers.get('Retry-After') if error.headers else None
            try: delay=float(header)
            except (ValueError,TypeError):delay=[5,10,20,30,45][min(attempt,4)]
            if not (0<delay<=120):delay=[5,10,20,30,45][min(attempt,4)]
            LOG.warning('Hyperliquid REST 429; wait %.1fs before retry %d/%d',delay,attempt+2,max_attempts)
            sleep(delay)

def get_bars(coin,tf):
    # Prefer once-collected GitHub candle cache. This prevents repeated REST
    # 429s on shared-IP free hosts; reject stale/invalid cache fail-closed.
    try:
        bars=get_public_bars(coin,tf)
        LOG.info('Historical %s %s loaded from validated PUBLIC GitHub cache',coin,tf)
        return bars
    except BootstrapError as exc:
        LOG.warning('Public candle cache unavailable for %s %s: %s; REST fallback',coin,tf,exc)
    period=TF_MS[tf];now_ms=int(time.time()*1000)
    payload=json.dumps({'type':'candleSnapshot','req':{'coin':coin,'interval':tf,
      'startTime':now_ms-(HISTORY_LENGTH+2)*period,'endTime':now_ms}}).encode()
    request=Request(API_URL,data=payload,headers={'Content-Type':'application/json',
                                               'User-Agent':'RadarWSReadOnly/1.0'},method='POST')
    bars=read_rest_with_backoff(request)
    clean=[]
    for bar in bars:
        if int(bar['T'])+1<=now_ms-1500:clean.append(check_bar(bar))
    clean.sort(key=lambda b:b['t'])
    if len(clean)<230 or any(clean[i]['t']-clean[i-1]['t']!=period for i in range(1,len(clean))):
        raise ValueError('Missing or discontinuous historical candles')
    if now_ms - (clean[-1]['T']+1)>period+90_000:
        raise ValueError(f'Stale {coin} {tf} candle history')
    return clean[-HISTORY_LENGTH:]


class MarketEngine:
    def __init__(self):
        self.connected=False;self.connected_since=None;self.last_ws_at=None
        self.last_history_refresh_at=None;self.history_error=None
        self.bars=defaultdict(dict);self.book={};self.mark={};self.events=[]
        self.conn=None;self.started=time.time();self.reconnects=0
        self.data_errors=0;self.history_refreshes=0
        self.candidate_checks=0;self.candidate_suppressed=0;self.last_candidate_check_at=None

    def open_db(self):
        DB_PATH.parent.mkdir(parents=True,exist_ok=True)
        self.conn=sqlite3.connect(DB_PATH)
        self.conn.execute('CREATE TABLE IF NOT EXISTS candidates(id TEXT PRIMARY KEY, recorded_at REAL NOT NULL, payload TEXT NOT NULL)')
        self.conn.commit()
        self.events=[json.loads(x[0]) for x in self.conn.execute(
            'SELECT payload FROM candidates ORDER BY recorded_at DESC LIMIT 20').fetchall()]

    def _persist_candidate(self,item):
        before=self.conn.total_changes
        self.conn.execute('INSERT OR IGNORE INTO candidates VALUES (?,?,?)',
                         (item['id'],time.time(),json.dumps(item)))
        self.conn.commit()
        if self.conn.total_changes!=before:
            self.events.insert(0,item);self.events=self.events[:20]
            LOG.info('WATCH_ONLY candidate %s',item['id'])

    def merge_bars(self,coin,tf,new):
        # Reject bar regressions (after reconnection earlier bars can be replayed),
        # but allow overwriting recently closed data after bootstrap.
        existing=self.bars[(coin,tf)]
        for bar in new:
            existing[bar['t']]=bar
        if len(existing)>HISTORY_LENGTH:
            for k in sorted(existing)[:-HISTORY_LENGTH]:existing.pop(k,None)

    def verify_candidates(self,coin,now_ms):
        b15=self.bars[(coin,'15m')]
        if len(b15)<35:return
        # Identify the most recent *completed* bar. The WebSocket typically
        # publishes a NEW unfinished bar before this function is called.
        closed=sorted((x for x in b15.values() if x['T']+1+3000<=now_ms),key=lambda x:x['t'])
        if len(closed)<35:return
        recent=closed[-21:]
        if any(recent[i]['t']-recent[i-1]['t']!=TF_MS['15m'] for i in range(1,len(recent))):
            return  # gap on reconnect: never infer a breakout from missing bars
        cut=closed[-1]['T']+1
        self.candidate_checks+=1;self.last_candidate_check_at=time.time()
        # A historical breakout is not an actionable live event. Require a
        # fresh mark, BBO and 15m close, but keep WATCH_ONLY semantics.
        now=time.time()
        book=self.book.get(coin);mark=self.mark.get(coin)
        quote_ok=(book and mark and 0<=now-book['_ts']<=QUOTE_MAX_AGE_S
                  and 0<=now-mark['_ts']<=QUOTE_MAX_AGE_S
                  and math.isfinite(book['ask']) and math.isfinite(book['bid'])
                  and math.isfinite(mark['mark']) and mark['mark']>0
                  and book['ask']>book['bid']>0
                  and 10000*(book['ask']-book['bid'])/((book['ask']+book['bid'])/2)
                      <=BOOK_MAX_SPREAD_BPS)
        if (not quote_ok or not 0<=now_ms-cut<=SIGNAL_MAX_AGE_S):
            self.candidate_suppressed+=1
            return
        h1=aligned_bars(sorted(self.bars[(coin,'1h')].values(),key=lambda x:x['t']),cut)
        h4=aligned_bars(sorted(self.bars[(coin,'4h')].values(),key=lambda x:x['t']),cut)
        if not h1 or not h4 or cut-(h1[-1]['T']+1)>TF_MS['1h']+90_000 or cut-(h4[-1]['T']+1)>TF_MS['4h']+90_000:
            self.candidate_suppressed+=1
            return
        result=candidate(coin,closed,h1,h4)
        if result:self._persist_candidate(result)

    async def refresh_history(self):
        pairs=[(coin,tf) for coin in PAIRS for tf in HISTORY_TFS]
        # Sequential small API requests, run off event loop. No API writes.
        for coin,tf in pairs:
            try:
                history=await asyncio.to_thread(get_bars,coin,tf)
                self.merge_bars(coin,tf,history)
            except Exception as exc:
                self.history_error=f'{coin} {tf}: {type(exc).__name__}: {exc}'
                self.data_errors+=1;LOG.warning('REST history refresh: %s',self.history_error)
                return False
        self.last_history_refresh_at=time.time();self.history_refreshes+=1
        self.history_error=None
        for coin in PAIRS:self.verify_candidates(coin,int(time.time()*1000))
        return True

    def process_message(self,msg):
        channel=msg.get('channel');data=msg.get('data')
        now_ms=int(time.time()*1000)
        if channel in ('subscriptionResponse','pong') or not isinstance(data,(dict,list)):
            return
        if channel=='bbo':
            coin=data.get('coin');bidask=data.get('bbo')
            if coin not in PAIRS or not isinstance(bidask,list) or len(bidask)!=2 or not all(bidask):return
            try:
                b,a=float(bidask[0]['px']),float(bidask[1]['px']);ts=int(data['time'])
                if not (math.isfinite(b) and math.isfinite(a)):return
                if b<=0 or a<=b or abs(now_ms-ts)>30_000:return
                self.book[coin]={'bid':b,'ask':a,'observed_at':utc_seconds(ts/1000),'_ts':time.time()}
            except (ValueError,TypeError,KeyError):self.data_errors+=1
        elif channel=='activeAssetCtx':
            coin=data.get('coin');ctx=data.get('ctx') or data.get('context')
            if coin not in PAIRS or not isinstance(ctx,dict):return
            try:
                mark=float(ctx['markPx'])
                oi=float(ctx.get('openInterest',0))
                funding=float(ctx.get('funding',0))
                if not all(math.isfinite(x) for x in (mark,oi,funding)):return
                if mark<=0 or oi<0:return
                self.mark[coin]={'mark':mark,'open_interest':oi,
                                 'funding_rate':funding,
                                 'observed_at':utc_seconds(time.time()),'_ts':time.time()}
            except (KeyError,ValueError,TypeError):self.data_errors+=1
        elif channel=='candle':
            values=data if isinstance(data,list) else [data]
            for v in values:
                try:bar=check_bar(v)
                except ValueError:self.data_errors+=1;continue
                self.merge_bars(bar['s'],bar['i'],[bar])
                if bar['i']=='15m':self.verify_candidates(bar['s'],now_ms)

    def readiness(self):
        if not self.connected:return False,'ws_disconnected'
        if not self.last_ws_at or time.time()-self.last_ws_at>65:return False,'ws_no_recent_messages'
        if not self.last_history_refresh_at or time.time()-self.last_history_refresh_at>1200:return False,'history_stale'
        if self.history_error:return False,'history_error'
        # BBO messages may be silent when prices do not change. Fail closed.
        now=time.time();now_ms=int(now*1000)
        for coin in PAIRS:
            mark=self.mark.get(coin);book=self.book.get(coin)
            if not mark or not 0<=now-mark['_ts']<=QUOTE_MAX_AGE_S:
                return False,f'{coin}_mark_stale'
            if not math.isfinite(mark['mark']) or mark['mark']<=0:
                return False,f'{coin}_mark_invalid'
            if not book or not 0<=now-book['_ts']<=QUOTE_MAX_AGE_S:
                return False,f'{coin}_book_stale'
            bid,ask=book['bid'],book['ask']
            if not (math.isfinite(bid) and math.isfinite(ask)) or bid<=0 or ask<=bid or 10000*(ask-bid)/((ask+bid)/2)>BOOK_MAX_SPREAD_BPS:
                return False,f'{coin}_book_invalid'
            for tf in HISTORY_TFS:
                bars=self.bars[(coin,tf)]
                if len(bars)<230:return False,f'{coin}_{tf}_candles_missing'
                completed=[v for v in bars.values() if v['T']+1<=now_ms]
                if not completed:return False,f'{coin}_{tf}_closed_candles_missing'
                last_end=max(v['T']+1 for v in completed)
                if not 0<=now_ms-last_end<=TF_MS[tf]+90_000:
                    return False,f'{coin}_{tf}_candle_stale'
                # A recent closing price alone does not prove a continuous
                # history. Reject missing intervals before verifying signals.
                recent=sorted(completed,key=lambda b:b['t'])[-21:]
                if len(recent)<21 or any(
                    recent[i]['t']-recent[i-1]['t']!=TF_MS[tf]
                    for i in range(1,len(recent))
                ):
                    return False,f'{coin}_{tf}_candles_gap'
        return True,'ok'

    def status(self):
        healthy,reason=self.readiness();now=time.time()
        assets={}
        for coin in PAIRS:
            ctx=self.mark.get(coin);b=self.book.get(coin)
            valid_mark=bool(ctx and math.isfinite(ctx['mark']))
            valid_book=bool(b and all(math.isfinite(b[k]) for k in ('bid','ask')))
            assets[coin]={'mark':ctx['mark'] if valid_mark else None,
                          'mark_age_s':round(now-ctx['_ts'],1) if ctx else None,
                          'book_bid_ask':[b['bid'],b['ask']] if valid_book else None,
                          'book_age_s':round(now-b['_ts'],1) if b else None,
                          'closed_candles':{tf:len(self.bars[(coin,tf)]) for tf in TF_MS}}
        return {'system':'radar-readonly-v1','live_ready':healthy,'reason':reason,
                'connected':self.connected,'ws_age_s':round(now-self.last_ws_at,1) if self.last_ws_at else None,
                'history_age_s':round(now-self.last_history_refresh_at,1) if self.last_history_refresh_at else None,
                'up_seconds':round(now-self.started,1),'reconnects':self.reconnects,
                'data_errors':self.data_errors,'history_refreshes':self.history_refreshes,
                'candidate_checks':self.candidate_checks,'candidate_suppressed':self.candidate_suppressed,
                'last_candidate_check_utc':utc_seconds(self.last_candidate_check_at) if self.last_candidate_check_at else None,
                'history_error':self.history_error,'assets':assets,
                'watch_only_candidates':self.events[:5],
                'integrity_policy':'WATCH_ONLY, no entry/stop/TP; book+mark+HTF age checks in live readiness',
                'disclaimer':'READ ONLY; NO ACCOUNT OR WALLET ACCESS; NO ORDER EXECUTION. Not financial advice.'}


async def ping_loop(ws):
    while True:
        await asyncio.sleep(25)
        await ws.send(json.dumps({'method':'ping'}))


async def consume_ws(engine):
    # Uses reconnection backoff from websockets; app does not send orders.
    async for ws in connect(WS_URL,ping_interval=20,ping_timeout=20,max_queue=64,open_timeout=15):
        engine.connected=True;engine.connected_since=time.time();engine.reconnects+=1
        LOG.info('WS connected (#%d)',engine.reconnects)
        try:
            for coin in PAIRS:
                for subscription in [{'type':'bbo','coin':coin},
                                     {'type':'activeAssetCtx','coin':coin},
                                     {'type':'candle','coin':coin,'interval':'5m'},
                                     {'type':'candle','coin':coin,'interval':'15m'},
                                     {'type':'candle','coin':coin,'interval':'1h'},
                                     {'type':'candle','coin':coin,'interval':'4h'}]:
                    await ws.send(json.dumps({'method':'subscribe','subscription':subscription}))
            p=asyncio.create_task(ping_loop(ws))
            try:
                async for msg in ws:
                    engine.last_ws_at=time.time()
                    try:engine.process_message(json.loads(msg))
                    except (ValueError,TypeError) as ex:
                        engine.data_errors+=1;LOG.warning('Malformed WS message: %s',ex)
            finally:
                p.cancel();await asyncio.gather(p,return_exceptions=True)
        except Exception as exc:
            LOG.warning('WS disconnected: %s',exc)
        finally:
            engine.connected=False


async def history_loop(engine):
    while True:
        try:await engine.refresh_history()
        except Exception as exc:
            engine.history_error=f'History loop: {type(exc).__name__}: {exc}'
            engine.data_errors+=1;LOG.exception('History refresh failed')
        await asyncio.sleep(15*60)


async def start_app(app):
    engine=MarketEngine();engine.open_db();app['engine']=engine
    app['history_task']=asyncio.create_task(history_loop(engine))
    app['ws_task']=asyncio.create_task(consume_ws(engine))


async def stop_app(app):
    for name in ('ws_task','history_task'):app[name].cancel()
    await asyncio.gather(app['ws_task'],app['history_task'],return_exceptions=True)
    app['engine'].conn.close()


async def healthz(request):
    eng=request.app['engine'];health,reason=eng.readiness()
    return web.json_response({'ok':health,'reason':reason},status=200 if health else 503)


async def status(request):
    return web.json_response(request.app['engine'].status(),headers={'Cache-Control':'no-store'})


async def watch_only_events(request):
    eng=request.app['engine']
    # SQLite is ephemeral on Render Free. This endpoint is an observation aid,
    # NOT a durable archive or a validated signal feed.
    data=[json.loads(x[0]) for x in eng.conn.execute(
        'SELECT payload FROM candidates ORDER BY recorded_at DESC LIMIT 100').fetchall()]
    return web.json_response({'events':data,'count':len(data),'storage':'EPHEMERAL_RENDER_FREE',
          'type':'WATCH_ONLY','reliable_complete_history':False,'orders_enabled':False},
          headers={'Cache-Control':'no-store'})


def main():
    logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO'),format='%(asctime)s %(levelname)s %(message)s')
    app=web.Application();app.router.add_get('/healthz',healthz);app.router.add_get('/status',status);app.router.add_get('/watch-only',watch_only_events)
    app.on_startup.append(start_app);app.on_cleanup.append(stop_app)
    web.run_app(app,host='0.0.0.0',port=PORT)


if __name__=='__main__':main()
