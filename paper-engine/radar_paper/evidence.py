"""Freeze public candle inputs before entry; keep incomplete bars out."""
import math
from datetime import timedelta
from .core import stamp

MINUTES = {"15m": 15, "1h": 60, "4h": 240}


def candle_evidence(provider, pair, cutoff):
    histories = {}
    for timeframe, minutes in MINUTES.items():
        raw = provider.get_pair_dataframe(pair=pair, timeframe=timeframe)
        closed = raw[raw["date"]+timedelta(minutes=minutes) <= cutoff].copy()
        if len(closed) < 230 or len(closed) > 5000:
            raise ValueError("Insufficient or oversized public candle evidence")
        recent = closed.iloc[-230:]
        if not recent["date"].diff().iloc[1:].dt.total_seconds().eq(minutes*60).all():
            raise ValueError("Gapped public candle evidence")
        rows = []
        for _, row in closed.iterrows():
            values = [float(row[k]) for k in ("open", "high", "low", "close", "volume")]
            o, h, l, c, v = values
            if (not all(math.isfinite(x) for x in values) or min(o,h,l,c) <= 0 or v < 0 or
                    h < max(o,c) or l > min(o,c) or h < l):
                raise ValueError("Invalid public candle evidence")
            rows.append([stamp(row["date"]), *values])
        histories[timeframe] = rows
    return {"source": "FREQTRADE_CCXT_HYPERLIQUID_PUBLIC_OHLCV_CACHE",
            "columns": ["open_at_utc", "open", "high", "low", "close", "volume"],
            "cutoff_utc": stamp(cutoff), "histories": histories,
            "notice": "Trade-price candles, not mark or exchange fills"}
