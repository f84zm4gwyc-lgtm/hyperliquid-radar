"""Strict untrusted public GitHub candle cache for read-only Render cold starts.

Fetches only user's pinned PUBLIC GitHub repository. Never calls wallet/private APIs;
never infers fills, entries or pnl. REST fallback remains if archive is unavailable.
A 15m bar that is not recent will still fail the main engine readiness checks.
"""
import gzip
import io
import json
import math
import time
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from engine import PAIRS, TF_MS, check_bar

BOOTSTRAP_URL = ('https://raw.githubusercontent.com/'
                 'f84zm4gwyc-lgtm/hyperliquid-radar/main/'
                 'hyperliquid-radar/docs/candle_bootstrap.json.gz')
MAX_COMPRESSED = 500_000
MAX_EXPANDED = 2_000_000
CACHE_SECONDS = 120
MAX_PUBLICATION_AGE_S = 3600
# A fairly old 15m archive is okay for initialization, but readiness cannot
# be true until the subscription has caught up with the current closed bar.
MAX_BAR_AGE_FACTOR = 6

class BootstrapError(ValueError):
    pass

_memory = None
_cache_time = 0


def parse_archive(data, *, now=None):
    if not isinstance(data, (bytes, bytearray)) or len(data)>MAX_COMPRESSED:
        raise BootstrapError('Invalid/oversized candle archive')
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
            raw=stream.read(MAX_EXPANDED+1)
        if len(raw)>MAX_EXPANDED:
            raise BootstrapError('Decompressed candle archive too large')
        doc=json.loads(raw)
    except (ValueError, OSError, UnicodeError, EOFError, TypeError) as exc:
        raise BootstrapError('Malformed public candle archive') from exc
    if not isinstance(doc,dict) or doc.get('schema_version')!=1 or doc.get('source')!='HYPERLIQUID_OFFICIAL_INFO_CLOSED_CANDLES' or doc.get('pnl_eligible') is not False:
        raise BootstrapError('Unexpected archive schema/provenance')
    try:
        published=datetime.fromisoformat(doc['generated_at_utc'].replace('Z','+00:00'))
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        raise BootstrapError('Invalid publication timestamp') from exc
    if published.tzinfo is None:
        raise BootstrapError('Publication timestamp missing timezone')
    current=time.time() if now is None else now
    age=current-published.timestamp()
    if age < -30 or age>MAX_PUBLICATION_AGE_S:
        raise BootstrapError('Public candle cache publication stale/future')
    assets=doc.get('assets')
    if not isinstance(assets,dict) or set(assets)!=set(PAIRS):
        raise BootstrapError('Missing/unknown cache assets')
    parsed={}
    for coin in PAIRS:
        per_coin=assets[coin]
        if not isinstance(per_coin,dict) or set(per_coin)!= {'15m','1h','4h'}:
            raise BootstrapError('Missing/unknown cached timeframes')
        for tf in ('15m','1h','4h'):
            raw_bars=per_coin[tf]
            if not isinstance(raw_bars,list) or not 230<=len(raw_bars)<=300:
                raise BootstrapError('Cached history length invalid')
            try:
                bars=[check_bar(bar) for bar in raw_bars]
            except (ValueError,TypeError,OverflowError) as exc:
                raise BootstrapError('Invalid cached OHLCV candle') from exc
            step=TF_MS[tf]
            if any(b['s']!=coin or b['i']!=tf for b in bars):
                raise BootstrapError('Cached coin/timeframe mismatch')
            if any(bars[i]['t']-bars[i-1]['t']!=step for i in range(1,len(bars))):
                raise BootstrapError('Discontinuous/duplicate cached history')
            if any(b['T']+1>current*1000-1000 for b in bars):
                raise BootstrapError('Incomplete/future cached candle')
            if (current*1000 - (bars[-1]['T']+1))>MAX_BAR_AGE_FACTOR*step:
                raise BootstrapError('Cached candles too old to initialize')
            parsed[(coin,tf)]=bars
    return parsed


def download_archive(*,now=None):
    req=Request(BOOTSTRAP_URL,headers={
        'User-Agent':'ReadOnlyPublicCandleBootstrap/1.0','Accept':'application/gzip'})
    try:
        with urlopen(req,timeout=12) as response:
            content=response.read(MAX_COMPRESSED+1)
    except (TimeoutError,OSError,ValueError) as exc:
        raise BootstrapError('GitHub archive HTTP fetch failed') from exc
    return parse_archive(content,now=now)


def get_public_bars(coin,tf,*,now=None):
    global _memory, _cache_time
    if coin not in PAIRS or tf not in ('15m','1h','4h'):
        raise BootstrapError('Unsupported cached pair/timeframe')
    current=time.time() if now is None else now
    if _memory is None or current-_cache_time>CACHE_SECONDS or current<_cache_time:
        _memory=download_archive(now=current)
        _cache_time=current
    result=_memory[(coin,tf)]
    # Cached archive can become stale even if it passed download checks.
    step=TF_MS[tf]
    if current*1000-(result[-1]['T']+1)>MAX_BAR_AGE_FACTOR*step:
        raise BootstrapError('Cached historical bars expired')
    return result
