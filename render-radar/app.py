"""BTC/ETH Hyperliquid *read-only* WebSocket observability service.

Render deployment: Python, build `pip install -r render-radar/requirements.txt`,
start `python render-radar/app.py`, health GET /healthz. No account access.

No broker keys / no private endpoints / no order execution / no monetary advice.
"""
import asyncio
import json
import logging
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

LOG=logging.getLogger('radar.ws')
WS_URL='wss://api.hyperliquid.xyz/ws'
API_URL='https://api.hyperliquid.xyz/info'
PORT=int(os.getenv('PORT','10000'))
DB_PATH=Path(os.getenv('RADAR_DB_PATH','/tmp/radar_ws.sqlite3'))
HISTORY_LENGTH=270
# Prioritize the 15m setup and 1h/4h context; 5m arrives via WebSocket.
HISTORY_TFS=('15m','1h','4h')
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

    def open_db(self):
        DB_PATH.parent.mkdir(parents=True,exist_ok=True)
        self.conn=sqlite3.connect(DB_PATH)
        self.conn.execute('CREATE TABLE IF NOT EXISTS candidates(id TEXT PRIMARY KEY, recorded_at REAL NOT NULL, payload TEXT NOT NULL)')
        self.conn.commit()
        self.events=[json.loads(x[0]) for x in self.conn.execute(
            'SELECT payload FROM candidates ORDER BY recorded_at DESC LIMIT 20').fetchall()]

    def _persist_candidate(self,item):
        before=self.conn.total_changes
