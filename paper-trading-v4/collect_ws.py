#!/usr/bin/env python3
"""Optional local public WebSocket recorder. NO auth, NO orders.

Only runs when the operator explicitly executes this file. Writes local
raw feed receipts with time from the receiving host. On ephemeral hosts
these files DISAPPEAR at restart; this is NOT a cloud persistence solution.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

WS='wss://api.hyperliquid.xyz/ws'


def to_envelope(raw,received_at_utc):
    msg=json.loads(raw)
    if not isinstance(msg,dict) or not isinstance(msg.get('channel'),str):
        raise ValueError('Unexpected public WebSocket message')
    return {'raw_feed_provenance':'RECORDED_PUBLIC_WS_UNREVIEWED',
            'captured_at_utc':received_at_utc,'ws':msg}


async def record(path:Path,duration_seconds:int):
    if not 5<=duration_seconds<=3600:raise ValueError('Duration must be from 5 to 3600 seconds')
    if not path.parent.exists():raise FileNotFoundError('Output folder does not exist')
    if path.is_symlink():raise ValueError('Refusing symlink output')
    from websockets.asyncio.client import connect
    stop=time.monotonic()+duration_seconds
    with path.open('x',encoding='utf-8') as output:
        async with connect(WS,ping_interval=20,ping_timeout=20,max_queue=128,open_timeout=20) as ws:
            for coin in ('BTC','ETH'):
                for kind in ('bbo','activeAssetCtx'):
                    await ws.send(json.dumps({'method':'subscribe','subscription':{'type':kind,'coin':coin}}))
            count=0
            while time.monotonic()<stop:
                try:raw=await asyncio.wait_for(ws.recv(),timeout=min(5,max(.01,stop-time.monotonic())))
                except asyncio.TimeoutError:continue
                seen=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
                if len(raw)>100_000:continue
                try:row=to_envelope(raw,seen)
                except (ValueError,TypeError):continue
                if row['ws']['channel'] not in ('bbo','activeAssetCtx','subscriptionResponse','pong'):continue
                output.write(json.dumps(row,allow_nan=False,separators=(',',':'))+'\n')
                output.flush();os.fsync(output.fileno())
                count+=1
                if count>=100000:break  # resource/size cap
    return count


def main():
    p=argparse.ArgumentParser(description='OPTIONAL public market recording on your machine, never orders')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--duration-seconds',type=int,default=120)
    args=p.parse_args()
    print('Saved public WS events:',asyncio.run(record(args.output,args.duration_seconds)))


if __name__=='__main__':main()
